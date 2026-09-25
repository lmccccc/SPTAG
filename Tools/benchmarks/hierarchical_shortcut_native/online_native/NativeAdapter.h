#pragma once
#include "FullHooks.h"
#include "OnlineNavigation.h"
#include "inc/Core/BKT/Index.h"

namespace OnlineOwners {
inline bool Enabled() { return ShortcutFull::mode == "online" || ShortcutFull::mode == "admit"; }

template<class T, class Catalogs, class Postings, class Admit, class Own>
void NativeSearch(SPTAG::BKT::Index<T>* bkt, SPTAG::COMMON::QueryResultSet<T>* result,
                  const Catalogs& catalogs, const Postings& postings,
                  Admit admission, Own own) {
    struct Local {
        const void* key = nullptr;
        Model model;
        Search search;
    };
    static thread_local Local local;
    if (local.key != bkt) {
        local = Local{};
        local.key = bkt;
        if (postings.size() != 2 || catalogs.size() != 2 ||
            postings[0].ReplicaCount() != 8 || postings[1].ReplicaCount() != 8)
            throw std::runtime_error("Online prototype requires original two eight-owner CSRs");
        local.model.counts = {bkt->GetNumSamples(), catalogs[0]->Count(), catalogs[1]->Count()};
        local.model.children = [&postings](int level, int id) -> Model::Children {
            const auto& p = postings[level - 1];
            return {p.Begin(id), p.End(id)};
        };
        local.model.graph = [bkt](int id) {
            return std::make_pair(bkt->BenchmarkGraphRow(id), bkt->BenchmarkGraphWidth());
        };
        local.model.BuildOwners();
    }
    auto original = bkt->BenchmarkGetDistance();
    auto& engine = local.search;
    result->Reset();
    std::uint64_t eligible = 0;
    engine.Reset(local.model, ShortcutFull::cap, ShortcutFull::onlineBatch,
                 ShortcutFull::mode == "online", ShortcutFull::profile, ShortcutFull::capture,
        [&](int level, int id) {
            const void* sample = level == 0 ? bkt->GetSample(id) : catalogs[level - 1]->GetVector(id);
            if (!sample) throw std::runtime_error("Missing native hierarchy representative");
            return original(result->GetQuantizedTarget(), static_cast<const T*>(sample), bkt->GetFeatureDim());
        },
        [&](int id, float distance) {
            if (own) own(id, &distance);
            if (admission(id)) { ++eligible; result->AddPoint(id, distance); }
        });

    const auto base = reinterpret_cast<std::uintptr_t>(bkt->GetSample(0));
    const std::size_t stride = sizeof(T) * bkt->GetFeatureDim();
    ShortcutBench::State seedState;
    ShortcutBench::active = &seedState;
    bkt->BenchmarkRestore([&](const T*, const T* sample, SPTAG::DimensionType) {
        const auto address = reinterpret_cast<std::uintptr_t>(sample);
        if (address < base || (address - base) % stride)
            throw std::runtime_error("Non-contiguous seed sample");
        const auto id = (address - base) / stride;
        if (id >= static_cast<std::size_t>(bkt->GetNumSamples()) || bkt->GetSample(id) != sample)
            throw std::runtime_error("Unmapped native BKT seed callback");
        if (engine.stats.used >=
            std::min<std::uint64_t>(ShortcutFull::onlineSeed, ShortcutFull::cap))
            throw ShortcutBench::BudgetExhausted{};
        return engine.Score(0, id, 0, true);
    });
    try {
        SPTAG::COMMON::QueryResultSet<T> seed(result->GetTarget(), 24);
        if (bkt->SearchIndex(seed) != SPTAG::ErrorCode::Success)
            throw std::runtime_error("Native BKT bootstrap failed");
    } catch (...) {
        bkt->BenchmarkRestore(std::move(original));
        ShortcutBench::active = nullptr;
        throw;
    }
    // The seed workspace has been returned; this one-pass algorithm continues
    // its own scored H1 frontier, never rerunning the tree or an SSD query.
    bkt->BenchmarkRestore(original);
    ShortcutBench::active = nullptr;
    engine.Run();
    result->SortResult();
    result->SetScanned(static_cast<int>(engine.stats.used));
    auto& out = ShortcutFull::Last();
    const auto& s = engine.stats;
    out.invoked = true;
    out.calls = ShortcutFull::profile ? s.used : 0;
    out.adjacency = ShortcutFull::profile ? s.graphEntries : 0;
    out.csrDistances = s.parentCalls + s.childCalls;
    out.csrAssignments = s.memberEntries;
    out.graphChecked = s.graphCalls;
    out.exhausted = s.used == ShortcutFull::cap;
    out.onlineGraph = s.graphCalls; out.onlineParent = s.parentCalls; out.onlineChild = s.childCalls;
    out.onlineCache = s.cacheHits; out.onlineBatches = s.batches;
    out.onlineUpH1 = s.ups[0]; out.onlineUpH2 = s.ups[1];
    out.onlineRowsH2 = s.rows[0]; out.onlineRowsH3 = s.rows[1];
    out.onlineCandidates = s.h1Candidates; out.onlineEligible = eligible;
    out.onlineTrace = s.trace;
}
}
