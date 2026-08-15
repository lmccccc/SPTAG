#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/baotonglu/mocheng
BINARY="$ROOT/SPTAG/Release/spannaclbench"
LIBRARY_DIR="$ROOT/SPTAG/Release"
INDEX="$ROOT/pipeann/audits/sift1b_static_stm1_qps_cpu_isolation_20260726/overlays/nprobe96_one_thread"
QUERY_DIR=/mnt/nvme/baotonglu/mocheng/datasets/sift1b/multitenant/query
OUTPUT_DIR="${1:-$ROOT/pipeann/audits/sift1b_turbopuffer_control_filtered_curve_20260811}"
REUSE_SWEEP="${2:-}"
SEARCH_INI_DIR="$OUTPUT_DIR/search_ini"
SWEEP_LOG="$OUTPUT_DIR/sweep.log"
TIME_LOG="$OUTPUT_DIR/time.log"
CURVE="$OUTPUT_DIR/curve.jsonl"
UNFILTER_CURVE="$OUTPUT_DIR/unfilter_curve.jsonl"
PROTOCOL="$OUTPUT_DIR/protocol.json"

NPROBES=(50 64 80 96 112 128 160 200 256 320 400 600 800 1200 1600 2400 3200)

mkdir -p "$SEARCH_INI_DIR"

write_search_ini() {
    local path=$1
    local nprobe=$2
    local maxcheck=$((nprobe > 1024 ? nprobe : 1024))
    printf '%s\n' \
        '[SearchSSDIndex]' \
        'EnableUnfilterTail=true' \
        'UnfilterPurePages=false' \
        'UnfilterExtraTailPages=0' \
        'UnfilterPureDistanceScanPercent=100' \
        "InternalResultNum=$nprobe" \
        "MaxCheck=$maxcheck" \
        'ResultNum=10' \
        'NumberOfThreads=1' \
        'IOThreadsPerHandler=1' \
        'ForceDenseTagSearch=true' \
        'EnableHierPostingFilter=true' \
        > "$path"
}

write_search_ini "$SEARCH_INI_DIR/prewarm_nprobe96.search.ini" 96

search_args=(
    --search-sweep-ini "$SEARCH_INI_DIR/prewarm_nprobe96.search.ini"
)
for nprobe in "${NPROBES[@]}"; do
    ini="$SEARCH_INI_DIR/nprobe${nprobe}.search.ini"
    write_search_ini "$ini" "$nprobe"
    search_args+=(--search-sweep-ini "$ini")
done

sha256sum "$BINARY" "$LIBRARY_DIR/libSPTAGLib.so" > "$OUTPUT_DIR/binaries.sha256"

if [[ "$REUSE_SWEEP" != "--reuse-sweep" ]]; then
    /usr/bin/time -v -o "$TIME_LOG" \
        numactl --cpunodebind=2 --membind=2 \
        env -i \
            PATH=/usr/local/bin:/usr/bin:/bin \
            LD_LIBRARY_PATH="$LIBRARY_DIR:/usr/local/cuda/lib64" \
            "$BINARY" \
            --index "$INDEX" \
            --queries "$QUERY_DIR/query_vectors.npy" \
            --truth-dir "$QUERY_DIR" \
            --query-tags "$QUERY_DIR/query_tags.npy" \
            --all-acl-levels \
            "${search_args[@]}" \
            --value-type UInt8 \
            --tenant 0 \
            --topk 10 \
            --warmup 120 \
            --measure-offset 20 \
            --max-queries 100 \
            > "$SWEEP_LOG" 2>&1
elif [[ ! -s "$SWEEP_LOG" || ! -s "$TIME_LOG" ]]; then
    echo "--reuse-sweep requires existing sweep.log and time.log" >&2
    exit 1
fi

: > "$CURVE"
: > "$UNFILTER_CURVE"
while IFS= read -r row; do
    search_ini=$(jq -r '.search_ini' <<< "$row")
    basename=$(basename "$search_ini")
    [[ "$basename" == prewarm_* ]] && continue

    level=$(jq -r '.level' <<< "$row")
    nprobe=${basename#nprobe}
    nprobe=${nprobe%.search.ini}
    maxcheck=$((nprobe > 1024 ? nprobe : 1024))

    if [[ "$level" == unfilter ]]; then
        jq -c \
            --arg method "SPANN global-BKT signature control" \
            --arg parameter_source "native SearchSSDIndex INIs only" \
            --arg unfilter_policy "single global BKT with no subset routing or tail replicas" \
            --argjson nprobe "$nprobe" \
            --argjson maxcheck "$maxcheck" \
            '. + {
                dataset: "sift1b",
                method: $method,
                nprobe: $nprobe,
                maxcheck: $maxcheck,
                topk: 10,
                measured_queries: 100,
                warmup_queries: 120,
                warmup_query_range: [0, 120],
                measurement_query_range: [20, 120],
                threads: 1,
                parameter_source: $parameter_source,
                unfilter_policy: $unfilter_policy,
                force_dense_tag_search: true,
                hierarchical_posting_filter: true
            }' <<< "$row" >> "$UNFILTER_CURVE"
        continue
    fi

    jq -c \
        --arg method "SPANN global-BKT signature control" \
        --arg parameter_source "native SearchSSDIndex INIs only" \
        --arg filter_policy "global head routing plus hierarchical posting-signature prefilter and exact record filtering" \
        --argjson nprobe "$nprobe" \
        --argjson maxcheck "$maxcheck" \
        '. + {
            dataset: "sift1b",
            method: $method,
            nprobe: $nprobe,
            maxcheck: $maxcheck,
            topk: 10,
            measured_queries: 100,
            warmup_queries: 120,
            warmup_query_range: [0, 120],
            measurement_query_range: [20, 120],
            threads: 1,
            parameter_source: $parameter_source,
            filter_policy: $filter_policy,
            force_dense_tag_search: true,
            hierarchical_posting_filter: true
        }' <<< "$row" >> "$CURVE"
done < <(grep '^{' "$SWEEP_LOG")

expected_rows=$((4 * ${#NPROBES[@]}))
jq -s -e \
    --argjson expected_rows "$expected_rows" \
    --argjson expected_grid "$(printf '%s\n' "${NPROBES[@]}" | jq -s '.')" \
    '
      . as $rows |
      length == $expected_rows and
      all(.[];
        .queries == 100 and
        .measure_offset == 20 and
        .measured_queries == 100 and
        .warmup_queries == 120 and
        .topk == 10 and
        .value_type == "UInt8" and
        .failed_queries == 0 and
        .force_dense_tag_search == true and
        .hierarchical_posting_filter == true
      ) and
      (["org", "dept", "team", "project"] |
        map(. as $level |
          ([ $rows[] | select(.level == $level) | .nprobe ] | sort) == $expected_grid
        ) |
        all
      )
    ' "$CURVE" >/dev/null

jq -s -e \
    --argjson expected_rows "${#NPROBES[@]}" \
    --argjson expected_grid "$(printf '%s\n' "${NPROBES[@]}" | jq -s '.')" \
    '
      . as $rows |
      length == $expected_rows and
      all(.[];
        .level == "unfilter" and
        .queries == 100 and
        .measure_offset == 20 and
        .measured_queries == 100 and
        .warmup_queries == 120 and
        .topk == 10 and
        .value_type == "UInt8" and
        .failed_queries == 0 and
        .threads == 1 and
        .parameter_source == "native SearchSSDIndex INIs only"
      ) and
      ([ $rows[] | .nprobe ] | sort) == $expected_grid
    ' "$UNFILTER_CURVE" >/dev/null

jq -n \
    --arg binary "$BINARY" \
    --arg binary_sha256 "$(sha256sum "$BINARY" | awk '{print $1}')" \
    --arg library_sha256 "$(sha256sum "$LIBRARY_DIR/libSPTAGLib.so" | awk '{print $1}')" \
    --arg index "$INDEX" \
    --arg index_ini_sha256 "$(sha256sum "$INDEX/tenant_0/indexloader.ini" | awk '{print $1}')" \
    --arg queries "$QUERY_DIR/query_vectors.npy" \
    --arg query_tags "$QUERY_DIR/query_tags.npy" \
    --arg truth_dir "$QUERY_DIR" \
    --arg curve "$CURVE" \
    --arg curve_sha256 "$(sha256sum "$CURVE" | awk '{print $1}')" \
    --arg unfilter_curve "$UNFILTER_CURVE" \
    --arg unfilter_curve_sha256 "$(sha256sum "$UNFILTER_CURVE" | awk '{print $1}')" \
    --arg sweep_log "$SWEEP_LOG" \
    --arg time_log "$TIME_LOG" \
    --argjson nprobes "$(printf '%s\n' "${NPROBES[@]}" | jq -s '.')" \
    '{
      dataset: "SIFT1B",
      metric: "Recall@10 versus single-thread QPS",
      scenarios: ["unfilter", "org", "dept", "team", "project"],
      binary: $binary,
      binary_sha256: $binary_sha256,
      library_sha256: $library_sha256,
      index: $index,
      index_ini_sha256: $index_ini_sha256,
      queries: $queries,
      query_tags: $query_tags,
      truth_dir: $truth_dir,
      topk: 10,
      threads: 1,
      numa_binding: "numactl --cpunodebind=2 --membind=2",
      warmup_query_range: [0, 120],
      measurement_query_range: [20, 120],
      measured_queries: 100,
      nprobes: $nprobes,
      maxcheck_policy: "max(1024, nprobe)",
      parameter_source: "native SearchSSDIndex INIs only",
      filter_policy: "global head routing plus hierarchical posting-signature prefilter and exact record filtering",
      curve: $curve,
      curve_sha256: $curve_sha256,
      unfilter_policy: "single global BKT with no subset routing or tail replicas",
      unfilter_curve: $unfilter_curve,
      unfilter_curve_sha256: $unfilter_curve_sha256,
      sweep_log: $sweep_log,
      time_log: $time_log
    }' > "$PROTOCOL"

printf 'curve=%s\nunfilter_curve=%s\nprotocol=%s\n' \
    "$CURVE" "$UNFILTER_CURVE" "$PROTOCOL"
