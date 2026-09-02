#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT=$(pwd)
DATA=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse_numeric
INDEX="$DATA/index_limited_tag_h2_15pct"
QUERY="$DATA/query"
OUT="$DATA/h1_h2_curve_h2_15pct_r8"
BENCH="$ROOT/Release/spannaclbench"
NPROBES=(16 24 32 48 62 80 96 128 192 256 384)
MODES=("$@")
if [[ ${#MODES[@]} -eq 0 ]]; then
    MODES=(H1Only H2Only)
fi

[[ -x "$BENCH" ]] || { echo "Missing benchmark binary: $BENCH" >&2; exit 1; }
[[ -d "$INDEX/tenant_0" ]] || { echo "Missing index: $INDEX" >&2; exit 1; }
[[ -f "$QUERY/workloads.json" ]] || { echo "Missing workloads: $QUERY" >&2; exit 1; }

mkdir -p "$OUT/ini" "$OUT/log"
: > "$OUT/results.jsonl"
export LD_LIBRARY_PATH="$ROOT/Release:${LD_LIBRARY_PATH:-}"

write_overlay() {
    local mode=$1
    local nprobe=$2
    local path="$OUT/ini/${mode}_n${nprobe}.ini"
    printf '%s\n' \
        '[SearchSSDIndex]' \
        'isExecute=true' \
        'BuildSsdIndex=false' \
        "HeadNavigationMode=${mode}" \
        "InternalResultNum=${nprobe}" \
        'ResultNum=10' \
        'NumberOfThreads=1' \
        'MaxCheck=8192' \
        'SecondLevelInitialProbeRatio=0.666666' \
        'SecondLevelMaxCheck=512' \
        'SearchPostingPageLimit=12' \
        > "$path"
    printf '%s\n' "$path"
}

run_case() {
    local mode=$1
    local nprobe=$2
    local workload=$3
    local truth=$4
    shift 4
    local overlay
    overlay=$(write_overlay "$mode" "$nprobe")
    local log="$OUT/log/${mode}_n${nprobe}_${workload}.log"
    "$BENCH" \
        --index "$INDEX" \
        --queries "$QUERY/query_vectors.npy" \
        --truth "$truth" \
        --search-sweep-ini "$overlay" \
        --value-type Float \
        --topk 10 \
        --warmup 100 \
        --measure-offset 100 \
        --max-queries 1000 \
        "$@" > "$log" 2>&1
    local rows
    rows=$(grep -c '^{' "$log" || true)
    [[ "$rows" -eq 1 ]] || {
        echo "Unexpected benchmark output in $log" >&2
        tail -40 "$log" >&2
        exit 1
    }
    sed "s/^{/{\"workload\":\"${workload}\",\"mode\":\"${mode}\",\"nprobe\":${nprobe},/" \
        "$log" | grep '^{' >> "$OUT/results.jsonl"
}

for mode in "${MODES[@]}"; do
    [[ "$mode" == H1Only || "$mode" == H2Only ]] ||
        { echo "Unsupported navigation mode: $mode" >&2; exit 2; }
    for nprobe in "${NPROBES[@]}"; do
        run_case "$mode" "$nprobe" unfilter \
            "$QUERY/groundtruth_unfilter_local_ids.npy"
        run_case "$mode" "$nprobe" broad_tag \
            "$QUERY/groundtruth_broad_tag_local_ids.npy" \
            --query-tags "$QUERY/query_tags_broad.npy" --tag-column 0
        run_case "$mode" "$nprobe" medium_tag \
            "$QUERY/groundtruth_medium_tag_local_ids.npy" \
            --query-tags "$QUERY/query_tags_medium.npy" --tag-column 0
        run_case "$mode" "$nprobe" sparse_tag \
            "$QUERY/groundtruth_extreme_tag_local_ids.npy" \
            --query-tags "$QUERY/query_tags_extreme.npy" --tag-column 0
        run_case "$mode" "$nprobe" numeric \
            "$QUERY/groundtruth_numeric_local_ids.npy" \
            --query-dnf "$QUERY/query_dnf_numeric.npy"
        run_case "$mode" "$nprobe" mixed_dnf \
            "$QUERY/groundtruth_mixed_dnf_local_ids.npy" \
            --query-dnf "$QUERY/query_dnf_mixed.npy"
    done
done

Rscript Tools/benchmarks/plot_sift1m_h1_h2_curve.R \
    "$OUT/results.jsonl" "$OUT/sift1m_h1_h2_recall_qps"
