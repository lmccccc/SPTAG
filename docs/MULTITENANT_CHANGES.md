# Multi-Tenant SPANN — Change Notes

This branch (`feature/multi-tenant-spann-no-tag`) adds multi-tenant serving on
top of upstream SPANN. It is scoped narrowly so it can be reviewed and merged
before any tag / ACL-filter work.

- **Baseline**: `b2748d9` (upstream `microsoft/SPTAG` main, merge of PR #439)
- **Tip**: `fc97907` — `feat: dirty flag for conditional checkpoint on ShutDown`
- **Diff size** (excluding `ThirdParty/`): `+2260 / −37` lines, 14 files touched

The aim is: **N independent per-tenant SPANN indices behind one Python object**,
served lazily with a byte-bounded LRU cache, a shared Linux AIO pool to remove
`io_destroy` from the eviction hot path, and a dirty flag so read-only tenants
skip the 50 ms checkpoint on shutdown. No search-algorithm changes.

---

## 1. Summary Of Changes

| Area | What changes | Motivation |
| --- | --- | --- |
| New wrapper class `TenantIndexManager` | One-per-tenant `AnnIndex` owned by a manager; lazy load, LRU evict, byte-budget cache, unified save/load, shared Linux AIO pool. | Serve many tenants from one process with bounded RAM and no per-query tenant-selection overhead. |
| `AnnIndex` convenience entry points | `SearchWithTenantFilter`, `BatchSearchWithTenantFilter`. | Let the existing `AnnIndex` API filter by tenant metadata for callers not ready to move to the manager. |
| SPANN core — filter hook | `Index<T>::SearchIndexWithFilter` now dispatches into `m_extraSearcher` instead of returning `Fail`. `ExtraWorkSpace` carries `m_pFilterSource` so the filter function can look up metadata by posting VID. The dynamic searcher honours the filter during posting scan. | This is the minimal change that lets per-tenant metadata be enforced on the disk side of SPANN. The algorithm is unchanged — we only let the existing filter plumbing actually run. |
| SPANN — posting offset | New option `PostingOffset` (`m_postingOffset`, default `0`). `SearchDiskIndex`/`SearchDiskIndexIterative` now pass `vid + m_postingOffset` to `CheckValidPosting`. | Supports tenants that share one posting address space with non-overlapping ID ranges. Default 0 is a no-op for single-tenant users. |
| Read-only path hardening | `ExtraDynamicSearcher` no longer dereferences `m_splitThreadPool` when it is null (read-only serving never constructs it). `ExtraFileController` gets a read-only constructor that skips blockpool load + compaction thread and defaults `m_disableCheckpoint = true`. | Required once the same binary has to run in pure read-only mode for many tenants. |
| Dirty flag on shutdown | `ExtraFileController` gains `std::atomic<bool> m_dirty`. `ShutDown` only writes back block mapping / blockpool when dirty. Any `Put/Delete/Merge` marks dirty. | Read-only tenants evicted from the cache now take **~2 ms** instead of ~50 ms per eviction. This is what makes cache churn viable. |
| Shared Linux AIO pool | New singleton `Helper::AsyncFileReader::SharedAIOPool` (4 contexts by default). `AsyncFileIO::InitializeFileIo` reuses the pool contexts instead of calling `io_setup` per tenant; `ShutDown` only `close(fd)` and skips `io_destroy`. | `io_destroy` was 100–930 ms per tenant on the eviction path. Pooled contexts cut it to 2–4 ms total, which is the dominant win of the branch. |
| `Dataset` destructor safety | Only free `incBlocks` contents when it is owned by a single `Dataset`. | Multi-tenant paths can briefly alias `incBlocks`; the old unconditional free caused double-frees during shutdown. |

## 2. Files Touched

New:

- `AnnService/inc/Core/Cache/HeadIndexCache.h`
- `AnnService/inc/Core/Cache/ChunkedHeadIndexCache.h`

> Note: these two header files define an S3-FIFO style cache that was written
> in parallel with the wrapper-side LRU. The shipped query path currently uses
> the simple wrapper LRU, not these classes. They are kept in-tree because they
> are needed by a later follow-up and because no code outside the branch
> references them. Reviewers can treat them as opt-in additions.

Modified:

- `AnnService/inc/Core/Common/Dataset.h` — destructor guard
- `AnnService/inc/Core/SPANN/ExtraDynamicSearcher.h` — filterFunc in posting scan; null-safe splitThreadPool
- `AnnService/inc/Core/SPANN/ExtraFileController.h` / `.cpp` — read-only ctor, disableCheckpoint, dirty flag
- `AnnService/inc/Core/SPANN/IExtraSearcher.h` — `m_pFilterSource` in `ExtraWorkSpace`
- `AnnService/inc/Core/SPANN/Options.h` + `ParameterDefinitionList.h` — `PostingOffset`
- `AnnService/inc/Helper/AsyncFileReader.h` — `SharedAIOPool`, pool-aware `AsyncFileIO`
- `AnnService/src/Core/SPANN/SPANNIndex.cpp` — `SearchIndexWithFilter` real impl, `postingOffset` plumbing
- `AnnService/src/Core/SPANN/ExtraFileController.cpp` — dirty-flag branch in ShutDown / IOStatistics
- `Wrappers/CMakeLists.txt` — Python module link deps
- `Wrappers/inc/CoreInterface.h` — public `TenantIndexManager` + new `AnnIndex` methods
- `Wrappers/inc/PythonCommon.i` / `PythonCore.i` — SWIG bindings, `TenantIndexManager.BuildFromNumpy`, `CreateTenantIndexManager`
- `Wrappers/src/CoreInterface.cpp` — entire `TenantIndexManager` implementation

## 3. Public API Added

### C++

```cpp
// Wrappers/inc/CoreInterface.h
class TenantIndexManager {
 public:
    TenantIndexManager(DimensionType dim, const char* algoType = "BKT",
                       const char* valueType = "Float");

    // Build one independent SPANN per tenant from a flat (N, D) vector block
    // plus an N-line metadata buffer that carries the tenant id per row.
    bool BuildFromData(ByteArray vectors, ByteArray metadata, SizeType n,
                       bool withMetaIndex, bool normalized);

    // Routing
    std::shared_ptr<QueryResult> Search(ByteArray q, int tenantId, int k);
    std::shared_ptr<QueryResult> SearchByTenant(ByteArray q,
                                                const char* tenantStr, int k);
    std::shared_ptr<QueryResult> BatchSearch(ByteArray q, int vectorNum,
                                             int tenantId, int k);

    // Identity
    int  RegisterTenantId(const char* tenantStr);
    int  GetInternalTenantId(const char* tenantStr) const;
    const char* GetTenantIdStr(int internalId) const;
    void GetTenantIds(int* outBuf, int* count) const;
    int  GetTenantCount() const;
    int  GetTenantVectorCount(int tenantId) const;

    // Persistence (two layouts; pick one per deployment)
    bool SaveAll(const char* baseDir);
    bool LoadAll(const char* baseDir);
    bool SaveUnifiedStorage(const char* baseDir);
    bool LoadUnifiedStorage(const char* baseDir);

    // Params (applied to every tenant; persistent across lazy load)
    void SetBuildParam (const char* name, const char* value, const char* section);
    void SetSearchParam(const char* name, const char* value, const char* section);

    // Cache control
    void     SetHeadIndexCacheLimit(uint64_t bytesLimit);
    uint64_t GetHeadIndexCacheUsage() const;
    bool     UnloadTenant(int tenantId);
    void     SetDropPageCacheOnEvict(bool enable);    // benchmark hook
    void     SetStorageBackend(const char* backend);  // "local" | "unified"
};

// Convenience additions on AnnIndex (tenant-filter via metadata)
std::shared_ptr<QueryResult>
AnnIndex::SearchWithTenantFilter(ByteArray data, int k, const char* tenantId);
std::shared_ptr<QueryResult>
AnnIndex::BatchSearchWithTenantFilter(ByteArray data, int vectorNum, int k,
                                      const char* tenantId);
```

### Python

```python
from sptag import SPTAG

mgr = SPTAG.CreateTenantIndexManager(dim, "SPANN", "Float")
mgr.SetBuildParam("IndexAlgoType", "BKT", "Base")
mgr.BuildFromNumpy(vectors, tenant_ids)   # numpy helper
mgr.SaveAll(path)

mgr.LoadAll(path)
mgr.SetHeadIndexCacheLimit(64 * 1024 * 1024)
result = mgr.Search(query, tenant_id, topk)
```

## 4. Backwards Compatibility

- No public signature in `AnnIndex` is changed or removed; the branch only adds
  two new methods and leaves every existing call site untouched.
- `Index<T>::SearchIndexWithFilter` used to return `ErrorCode::Fail` on SPANN
  with a `LL_Error` log line. It now returns the filtered result. Callers that
  relied on the failure as a negative signal must be updated, but no upstream
  caller does.
- `PostingOffset` defaults to 0, so single-tenant users see no behavioural
  change.
- `ExtraFileController::m_disableCheckpoint` defaults to `true` (read-only
  behaviour) to match the multi-tenant serving assumption. Write-heavy users of
  the controller should set it explicitly to `false` — this is the one behaviour
  change upstream maintainers need to flag for the SPFresh write path.
- The `Dataset` destructor now skips freeing `incBlocks` when the shared_ptr is
  aliased. This is strictly safer than the old behaviour; no known caller
  depends on the previous double-free.

## 5. Performance Notes (for context only; no benchmark is part of this commit)

Measured on AMD EPYC 24 vCPU, SIFT-1M split across 100 tenants, HeadIndex cache
budget 75 % of total head size:

- Eviction latency: ~930 ms → **2–4 ms** (SharedAIOPool + dirty flag combined).
- Cold reload with warm page cache: 16–60 ms.
- Warm-cache query latency: 2.8–4.3 ms at `topk=10`.
- Recall@10 stays ≥ 0.996 across tenant sizes from 1.2 K to 276 K vectors.

## 6. Suggested Review Order

1. `Wrappers/inc/CoreInterface.h` — public surface, read this first.
2. `Wrappers/src/CoreInterface.cpp::TenantIndexManager` — the whole new class;
   most of the logic lives here.
3. `AnnService/inc/Helper/AsyncFileReader.h` — `SharedAIOPool` and the
   pool-aware `AsyncFileIO` changes; this is the most invasive platform piece.
4. `AnnService/inc/Core/SPANN/ExtraFileController.{h,cpp}` — dirty flag +
   read-only constructor + `disableCheckpoint` default.
5. `AnnService/src/Core/SPANN/SPANNIndex.cpp` — `SearchIndexWithFilter` now
   implemented; `postingOffset` plumbing.
6. `AnnService/inc/Core/SPANN/ExtraDynamicSearcher.h` — filter hook + null-safe
   thread-pool guards.
7. `AnnService/inc/Core/Common/Dataset.h` — small destructor safety patch.
8. `Wrappers/inc/PythonCore.i` — SWIG bindings for the new class.

## 7. What Is Deliberately **Not** In This Branch

- No tag / ACL filtering, no posting signatures, no head-bundle routing. All of
  that lives in later commits (`06ae744` onward) and is not part of this
  merge candidate.
- No changes to SPANN's graph-search algorithm, index layout, or posting
  format.
- No new third-party dependencies.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
