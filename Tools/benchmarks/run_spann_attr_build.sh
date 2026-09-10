#!/bin/bash
# =============================================================================
# Thin launcher for the native-config attribute SPANN build.
#
#   ./Tools/benchmarks/run_spann_attr_build.sh [config.ini]
#
# All BUILD parameters live in the .ini (the single source of truth, read by
# spannbuilder -c). This launcher only carries what is NOT a build param:
#   * process-loader env (jemalloc, static-TLS) -- runtime, not config;
#   * the post-build cross-graph step (augmentheadgraph) -- a separate tool;
#   * copying the OPQ codebook into the tenant dir for search.
# Paths are derived FROM the ini so nothing is duplicated here.
# =============================================================================
set -e
cd "$(dirname "$0")/../.."          # -> repo root (SPTAG/)
ROOT=$(pwd)

CFG="${1:-$ROOT/Script_AE/iniFile/build_spann_attr_spacev_opq25.ini}"
[[ "$CFG" = /* ]] || CFG="$ROOT/$CFG"
[ -f "$CFG" ] || { echo "config not found: $CFG"; exit 2; }
python3 "$ROOT/Tools/benchmarks/validate_spann_hierarchy_config.py" "$CFG"

# --- process loader (NOT build params) ---
# jemalloc reduces fragmentation/RSS at billion scale. Preload ONLY if present so
# the build doesn't spew "cannot be preloaded" on boxes without it; override the
# location via JEMALLOC_SO. (Drop libjemalloc.so.2 at this path to enable it.)
JEMALLOC_SO="${JEMALLOC_SO:-/usr/lib/x86_64-linux-gnu/libjemalloc.so.2}"
if [ -f "$JEMALLOC_SO" ]; then
  export LD_PRELOAD="$JEMALLOC_SO"
  echo "[launcher] LD_PRELOAD=$JEMALLOC_SO"
else
  echo "[launcher] jemalloc not found at $JEMALLOC_SO -- using system malloc (set JEMALLOC_SO to enable)"
fi
export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=2000000

# --- derive paths from the ini (single source of truth) ---
ini_section() {
  awk -F= -v section="$1" -v key="$2" '
    BEGIN {
      wanted_section = "[" tolower(section) "]";
      wanted_key = tolower(key);
    }
    /^[[:space:]]*\[/ {
      header = $0;
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", header);
      in_section = (tolower(header) == wanted_section);
      next;
    }
    in_section && NF >= 2 {
      name = $1;
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name);
      if (tolower(name) == wanted_key) {
        value = substr($0, index($0, "=") + 1);
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value);
        print value;
        exit;
      }
    }
  ' "$CFG"
}
OUT=$(ini_section Base IndexDirectory)
[ -n "$OUT" ] || { echo "[launcher] missing [Base] IndexDirectory"; exit 2; }
STORAGE=$(ini_section BuildSSDIndex Storage); [ -z "$STORAGE" ] && STORAGE=FILEIO
TMPROOT=$(ini_section BuildSSDIndex TmpDir)
QFILE=$(ini_section BuildSSDIndex PostingQuantizerFile) # optional; e.g. .../opq_codes_m25.bin
SID=""
if [ -n "$QFILE" ]; then
  SID=$(dirname "$QFILE")
fi
PIPEPQ_PIVOTS=$(ini_section BuildSSDIndex PipePQPivotsFile)

# (1) existing cross-graph knobs.
#   CrossEdges       : 1 = build/reuse head_cross_edges.bin. New STATIC bundle
#                      builds create it before Phase 4; this post-build fallback
#                      supports older/non-STATIC builders.
#   CrossExtraEdges  : -m, cross-subgraph edges kept per head (augmentheadgraph
#                      clamps <=0 back to 10). Default 10.
CROSS_EDGES=$(ini_section BuildSSDIndex CrossEdges);            [ -z "$CROSS_EDGES" ] && CROSS_EDGES=0
CROSS_EXTRA_EDGES=$(ini_section BuildSSDIndex CrossExtraEdges); [ -z "$CROSS_EXTRA_EDGES" ] && CROSS_EXTRA_EDGES=10
case "$CROSS_EXTRA_EDGES" in
  *[!0-9]*|'') CROSS_EXTRA_EDGES=10 ;;
esac
CROSS_EDGE_SEARCH_TOPK=$CROSS_EXTRA_EDGES
[ "$CROSS_EDGE_SEARCH_TOPK" -lt 15 ] && CROSS_EDGE_SEARCH_TOPK=15
CROSS_EDGE_BUILD_THREADS=$(ini_section BuildSSDIndex NumberOfThreads)
[ -z "$CROSS_EDGE_BUILD_THREADS" ] && CROSS_EDGE_BUILD_THREADS=1
ORDERED_PAGE_START=$(ini_section BuildSSDIndex EnableOrderedPageStart); [ -z "$ORDERED_PAGE_START" ] && ORDERED_PAGE_START=false
HYBRID_ENABLED=$(ini_section BuildSSDIndex EnableHybridDistance)
[ -z "$HYBRID_ENABLED" ] && HYBRID_ENABLED=false

# (2) SelectHead resume checkpoint knobs ([MultiTenant], single source of truth).
#   PersistSelectHead : 1 = after SelectHead, write head_select_state.bin and keep
#                       per-node head vector files, so a failed BuildHead/BuildSSDIndex
#                       can be retried WITHOUT re-running the BKT head selection.
#                       Default 0 (off; original behavior, smaller index).
#   ResumeBuild       : 1 = reuse an existing index dir + head_select_state.bin and
#                       skip the BKT (requires PersistSelectHead). Default 0.
PERSIST_SELECTHEAD=$(ini_section MultiTenant PersistSelectHead); [ -z "$PERSIST_SELECTHEAD" ] && PERSIST_SELECTHEAD=0
RESUME_BUILD=$(ini_section MultiTenant ResumeBuild);             [ -z "$RESUME_BUILD" ] && RESUME_BUILD=0

# (3) In-place build knob ([MultiTenant], single source of truth).
#   InPlaceBuild : 1 = build the SPANN index DIRECTLY into the final IndexDirectory
#                  (<OUT>/tenant_<id>) instead of staging in /tmp (or
#                  SPTAG_SPANN_WORK_DIR) and copying at the end. The SSD block pool
#                  is pre-allocated + incrementally flushed in the final dir, and
#                  SaveAll skips the copy. Avoids the transient 2x disk footprint
#                  and the copy time -- essential at billion scale. Default 0.
INPLACE_BUILD=$(ini_section MultiTenant InPlaceBuild); [ -z "$INPLACE_BUILD" ] && INPLACE_BUILD=0

is_true() {
  case "$1" in
    1|true|True|TRUE|yes|Yes|YES|on|On|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if is_true "$RESUME_BUILD"; then
  [ -d "$OUT" ] ||
    { echo "[launcher] ResumeBuild requires an existing IndexDirectory: $OUT"; exit 2; }
  echo "[launcher] RESUME: keeping existing index dir; only compatible SelectHead checkpoints may resume"
else
  if [ -e "$OUT" ] || [ -L "$OUT" ]; then
    echo "[launcher] refusing fresh build: IndexDirectory already exists: $OUT"
    echo "[launcher] choose a new output path or explicitly set [MultiTenant] ResumeBuild=true"
    exit 2
  fi
  mkdir -p -- "$(dirname -- "$OUT")"
  mkdir -- "$OUT"
fi

if [ -n "$TMPROOT" ]; then
  mkdir -p "$TMPROOT/work"
  export TMPDIR="$TMPROOT"
  export SPTAG_SPANN_WORK_DIR="$TMPROOT/work"
  echo "[launcher] work dir = $SPTAG_SPANN_WORK_DIR"
fi

# BuildSignatures scans every posting and retains its filtering sidecars in
# memory. Run it in a fresh process after the memory-intensive index build.
BUILD_SIGNATURES=$(ini_section Build BuildSignatures); [ -z "$BUILD_SIGNATURES" ] && BUILD_SIGNATURES=false
PRIMARY_CFG="$CFG"
if is_true "$BUILD_SIGNATURES"; then
  PRIMARY_CFG=$(python3 - "$CFG" "${TMPROOT:-$ROOT/build/primary-build-configs}" <<'PY'
import os
import re
import sys
import uuid
from pathlib import Path

with Path(sys.argv[1]).open(encoding="utf-8", newline="") as source:
    lines = source.readlines()
section = ""
replaced = 0
for index, line in enumerate(lines):
    header = re.fullmatch(r"\s*\[([^\]]+)\]\s*", line)
    if header:
        section = header.group(1).lower()
    elif section == "build" and re.match(r"\s*buildsignatures\s*=", line, re.IGNORECASE):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        lines[index] = line.split("=", 1)[0] + "=false" + ending
        replaced += 1
if replaced != 1:
    raise SystemExit("[launcher] primary config requires exactly one [Build] BuildSignatures entry")
directory = Path(sys.argv[2]).resolve()
directory.mkdir(parents=True, exist_ok=True)
destination = directory / f"spann-primary-build-{uuid.uuid4().hex}.ini"
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
    output.writelines(lines)
print(destination)
PY
)
fi
echo "[launcher] primary build config (preserved): $PRIMARY_CFG"

validate_runtime_config() {
  local runtime_ini="$OUT/tenant_0/indexloader.ini"
  [ -f "$runtime_ini" ] || { echo "[launcher] missing runtime config: $runtime_ini"; exit 1; }
  python3 "$ROOT/Tools/benchmarks/validate_spann_hierarchy_config.py" "$CFG" "$runtime_ini"
  if [ "${STORAGE^^}" = "STATIC" ]; then
    [ -s "$OUT/tenant_0/SPTAGFullList.bin" ] ||
      { echo "[launcher] missing static posting snapshot"; exit 1; }
    if is_true "$ORDERED_PAGE_START"; then
      [ -s "$OUT/tenant_0/ordered_page_starts.bin" ] ||
        { echo "[launcher] missing ordered page-start directory"; exit 1; }
    else
      [ ! -e "$OUT/tenant_0/ordered_page_starts.bin" ] ||
        { echo "[launcher] unexpected ordered page-start directory"; exit 1; }
    fi
    if is_true "$HYBRID_ENABLED"; then
        python3 - "$CFG" "$runtime_ini" \
          "$OUT/tenant_0/SPTAGFullList.bin" \
          "$OUT/tenant_0/SPTAGFullList.bin.hybrid.stats" \
          "$OUT/tenant_0/HeadIndex/head_cross_edges.bin" \
          "$OUT/tenant_0/SPTAGHybridList.bin" \
          "$OUT/tenant_0/HeadIndex/head_hybrid_edges.bin" <<'PY'
import struct
import sys
from pathlib import Path

(
    config_path,
    runtime_path,
    primary_path,
    stats_path,
    cross_path,
    legacy_posting_path,
    legacy_graph_path,
) = map(Path, sys.argv[1:])

def parse_ini(path):
    section = ""
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
          line = raw.strip()
          if not line or line.startswith(";"):
              continue
          if line.startswith("[") and line.endswith("]"):
              section = line[1:-1]
          elif "=" in line:
              key, value = line.split("=", 1)
              values[(section.lower(), key.strip().lower())] = value.strip()
    return values

config = parse_ini(config_path)
runtime = parse_ini(runtime_path)
if config.get(("buildssdindex", "enablehybriddistance"), "false").lower() not in {
    "1", "true", "yes", "on"
}:
    raise SystemExit(0)

if config.get(("selecthead", "selectheadtype"), "").lower() != "bkt":
    raise SystemExit("[launcher] hybrid distance requires canonical BKT head selection")
for obsolete_key in ("hybridheadgraphfile", "hybridpostingfile"):
    if ("buildssdindex", obsolete_key) in config:
        raise SystemExit(f"[launcher] obsolete hybrid option: {obsolete_key}")
for path in (primary_path, stats_path, cross_path):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"[launcher] missing hybrid artifact: {path}")
if ("buildssdindex", "hybridpostingfile") in runtime:
    raise SystemExit("[launcher] runtime unexpectedly persists HybridPostingFile")
for obsolete_path in (legacy_posting_path, legacy_graph_path):
    if obsolete_path.exists():
        raise SystemExit(f"[launcher] unexpected obsolete hybrid artifact: {obsolete_path}")

generation = runtime.get(("buildssdindex", "hybridgenerationfingerprint"))
if generation is None or int(generation) == 0:
    raise SystemExit("[launcher] runtime HybridGenerationFingerprint is missing or zero")

with primary_path.open("rb") as f:
    primary = struct.unpack("<11i", f.read(44))
    layouts = [struct.unpack("<iHiHi", f.read(16)) for _ in range(primary[2])]
if primary[0] != 0x314D5453:
    raise SystemExit("[launcher] primary posting is not an STM1 snapshot")
if primary[1] != 3:
    raise SystemExit("[launcher] hybrid posting is not self-contained H|O STM1 v3")
primary_generation = (primary[9] & 0xffffffff) << 32 | (primary[8] & 0xffffffff)
if primary_generation != int(generation):
    raise SystemExit(
        "[launcher] generation mismatch: "
        f"runtime={generation} primary={primary_generation}"
    )
pure_records = 0
total_records = 0
for _, _, total, _, pure in layouts:
    if pure < 0 or total < pure:
        raise SystemExit(
            "[launcher] invalid hybrid/tail layout: "
            f"pure={pure} total={total}"
        )
    pure_records += pure
    total_records += total
tail_records = total_records - pure_records
if pure_records == 0 or tail_records == 0:
    raise SystemExit(
        "[launcher] expected non-empty pure and deduplicated-tail segments, "
        f"got pure={pure_records} tail={tail_records}"
    )
with stats_path.open("rb") as f:
    stats = struct.unpack("<IIiiiiQ", f.read(32))
if (
    stats[0] != 0x53525948
    or stats[1] != 3
    or stats[5] != primary[2]
    or stats[6] != primary_generation
):
    raise SystemExit("[launcher] hybrid routing stats generation mismatch")
with cross_path.open("rb") as f:
    cross = struct.unpack("<Iiiiii", f.read(24))
    if cross[0] != 0x48434548 or cross[1] != 2:
        raise SystemExit("[launcher] hybrid cross-edge format mismatch")
    if cross[2] != primary[2] or cross[3] != 16 or cross[5] != 0x48594252:
        raise SystemExit(
            "[launcher] hybrid cross-edge topology mismatch: "
            f"heads={cross[2]} degree={cross[3]} marker={cross[5]:#x}"
        )
    cross_generation, cross_content = struct.unpack(
        "<QQ", f.read(16)
    )
    if cross_generation != primary_generation or cross_content == 0:
        raise SystemExit(
            "[launcher] hybrid cross-edge generation/content mismatch: "
            f"cross={cross_generation} primary={primary_generation} "
            f"content={cross_content}"
        )
    edge_total = 0
    for _ in range(cross[2]):
        source, edge_count = struct.unpack("<ii", f.read(8))
        if source < 0 or edge_count < 0 or edge_count > cross[3]:
            raise SystemExit("[launcher] invalid hybrid cross-edge record")
        payload = f.read(edge_count * 8)
        if len(payload) != edge_count * 8:
            raise SystemExit("[launcher] truncated hybrid cross-edge record")
        edge_total += edge_count
    if f.read(1) or edge_total == 0:
        raise SystemExit("[launcher] invalid hybrid cross-edge payload")
print(
    "[launcher] verified BKT base graph + degree-16 hybrid runtime suffix "
    "and one contiguous posting: "
    f"generation={primary_generation} pure={pure_records} "
    f"tail={tail_records} physical={total_records} edges={edge_total} "
    f"content={cross_content}"
)
PY
    fi
    local key expected actual
    for key in TailReplicaCount UnfilterTailBufferLength; do
      expected=$(ini_section BuildSSDIndex "$key")
      [ -z "$expected" ] && continue
      actual=$(sed -n "s/^[[:space:]]*$key[[:space:]]*=[[:space:]]*//Ip" "$runtime_ini" | head -1)
      [ "$actual" = "$expected" ] ||
        { echo "[launcher] $key mismatch: expected $expected, got ${actual:-<missing>}"; exit 1; }
    done
    echo "[launcher] verified static posting snapshot"
    return
  fi
  python3 - "$CFG" "$runtime_ini" "$OUT/tenant_0/head_role.bin" <<'PY'
import math
import sys
from pathlib import Path

config_path, runtime_path, head_role_path = map(Path, sys.argv[1:])

def parse_ini(path):
    section = ""
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line:
            key, value = line.split("=", 1)
            values[(section, key.strip())] = value.strip()
    return values

config = parse_ini(config_path)
runtime = parse_ini(runtime_path)
required = {
    "TailReplicaCount": config.get(("BuildSSDIndex", "TailReplicaCount")),
    "UnfilterTailBufferLength": config.get(("BuildSSDIndex", "UnfilterTailBufferLength")),
    "PostingQuantizer": config.get(("BuildSSDIndex", "PostingQuantizer")),
    "PostingQuantM": config.get(("BuildSSDIndex", "PostingQuantM")),
    "RerankL": config.get(("BuildSSDIndex", "RerankL")),
}
for key, expected in required.items():
    if expected is None:
        continue
    actual = next((value for (_, candidate), value in runtime.items() if candidate == key), None)
    if actual is None:
        raise SystemExit(f"[launcher] runtime config is missing {key}")
    if actual != expected:
        raise SystemExit(f"[launcher] {key} mismatch: expected {expected}, got {actual}")

dual_pool = config.get(("SelectHead", "DualPoolAugment"), "0").lower()
enabled = dual_pool in {"1", "true", "yes", "on"}
if enabled != head_role_path.exists():
    state = "present" if head_role_path.exists() else "absent"
    raise SystemExit(
        f"[launcher] DualPoolAugment={dual_pool} but head_role.bin is {state}"
    )

expected_ratio = config.get(("SelectHead", "Ratio"))
vector_count = None
with (runtime_path.parent.parent / "manifest.txt").open() as manifest:
    for line in manifest:
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["tenant", "0"]:
            vector_count = int(fields[2])
            break
if vector_count is None or vector_count <= 0:
    raise SystemExit("[launcher] missing native built vector count in manifest")
if expected_ratio is not None and vector_count is not None:
    import struct
    with (runtime_path.parent / "ssdinfo").open("rb") as f:
        total_heads = struct.unpack("<i", f.read(4))[0]
    h1_heads = total_heads
    if head_role_path.exists():
        h1_heads = 0
        with head_role_path.open("rb") as f:
            while chunk := f.read(1 << 20):
                h1_heads += chunk.count(0)
    achieved_ratio = h1_heads / int(vector_count)
    expected = float(expected_ratio)
    if not math.isclose(achieved_ratio, expected, rel_tol=0.1, abs_tol=0.005):
        raise SystemExit(
            f"[launcher] selected-head ratio mismatch: expected {expected:.6f}, "
            f"got {achieved_ratio:.6f} ({h1_heads}/{vector_count})"
        )
    print(
        f"[launcher] verified selected-head ratio={achieved_ratio:.6f} "
        f"({h1_heads}/{vector_count}), runtime tail/PQ settings, and U_extra state"
    )
else:
    print("[launcher] verified runtime tail/PQ settings and U_extra artifact state")
PY
}

if is_true "$PERSIST_SELECTHEAD"; then
  export SPTAG_PERSIST_SELECTHEAD=1
else
  unset SPTAG_PERSIST_SELECTHEAD
fi
if is_true "$RESUME_BUILD"; then
  export SPTAG_RESUME_BUILD=1
else
  unset SPTAG_RESUME_BUILD
fi
if is_true "$INPLACE_BUILD"; then
  export SPTAG_SPANN_INPLACE_DIR="$OUT"
  echo "[launcher] IN-PLACE build: SPTAG_SPANN_INPLACE_DIR=$OUT (no final copy)"
else
  unset SPTAG_SPANN_INPLACE_DIR
fi

echo "[launcher] config = $CFG"
echo "[launcher] index  = $OUT"

# --- build: ALL params from the ini ---
/usr/bin/time -v "$ROOT/Release/spannbuilder" -c "$PRIMARY_CFG" 2>&1
if is_true "$BUILD_SIGNATURES"; then
  echo "[launcher] BuildSignatures in a fresh process"
  /usr/bin/time -v "$ROOT/Release/spannbuilder" -c "$CFG" --build-signatures-only 2>&1
  if [ "${STORAGE^^}" != "STATIC" ]; then
    [ -s "$OUT/tenant_0/signatures_bitmask.bin" ] ||
      { echo "[launcher] missing signatures_bitmask.bin after BuildSignatures"; exit 1; }
  fi
  [ -s "$OUT/tenant_0/HeadIndex/head_node_meta.bin" ] ||
    { echo "[launcher] missing head_node_meta.bin after BuildSignatures"; exit 1; }
fi
validate_runtime_config

# --- (1) cross-graph: new STATIC bundle builds created this sidecar before
#     BuildSSD. Retain the post-build tool only as a fallback/rebuild path. ---
if [ "$CROSS_EDGES" = "1" ] || [ "$CROSS_EDGES" = "true" ]; then
  CROSS_EDGE_FILE="$OUT/tenant_0/HeadIndex/head_cross_edges.bin"
  CROSS_EDGE_DIRTY="$OUT/tenant_0/HeadIndex/head_cross_edges.dirty"
  if [ -s "$CROSS_EDGE_FILE" ] && [ ! -e "$CROSS_EDGE_DIRTY" ]; then
    echo "[launcher] reusing pre-BuildSSD cross-edge sidecar"
  else
    echo "[launcher] cross-graph fallback: augmentheadgraph -k $CROSS_EDGE_SEARCH_TOPK -m $CROSS_EXTRA_EDGES -t $CROSS_EDGE_BUILD_THREADS (CrossEdges=$CROSS_EDGES)"
    "$ROOT/Release/augmentheadgraph" \
      -d "$OUT/tenant_0/HeadIndex" \
      -k "$CROSS_EDGE_SEARCH_TOPK" -m "$CROSS_EXTRA_EDGES" \
      -t "$CROSS_EDGE_BUILD_THREADS" -w true
  fi
else
  echo "[launcher] cross-graph DISABLED (CrossEdges=$CROSS_EDGES) -- skipping augmentheadgraph; using configured native routing without cross edges"
fi

if [ -n "$SID" ] && [ -f "$SID/opq_quantizer.bin" ]; then
  cp "$SID/opq_quantizer.bin" "$OUT/tenant_0/opq_quantizer.bin"
  echo "[launcher] copied opq_quantizer.bin into tenant_0"
else
  echo "[launcher] no posting quantizer codebook to copy"
fi
if [ -n "$PIPEPQ_PIVOTS" ] && [ -f "$PIPEPQ_PIVOTS" ]; then
  cp "$PIPEPQ_PIVOTS" "$OUT/tenant_0/pipepq_pivots.bin"
  RUNTIME_INI="$OUT/tenant_0/indexloader.ini"
  [ -f "$RUNTIME_INI" ] ||
    { echo "[launcher] missing runtime config after PipePQ pivot copy"; exit 1; }
  # The build needs the external pivot source, while the deployed index must
  # resolve the immutable copied pivot sidecar from its own tenant directory.
  sed -i 's|^PipePQPivotsFile=.*$|PipePQPivotsFile=pipepq_pivots.bin|' "$RUNTIME_INI"
  grep -qx 'PipePQPivotsFile=pipepq_pivots.bin' "$RUNTIME_INI" ||
    { echo "[launcher] failed to repoint PipePQ pivots to tenant_0"; exit 1; }
  echo "[launcher] copied pipepq_pivots.bin into tenant_0"
fi
echo "[launcher] done. Posting reads use the persisted SearchPostingPageLimit for every predicate."
