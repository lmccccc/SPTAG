#!/bin/bash
# =============================================================================
# Deterministic prep of the SIFT1B limited-tag attribute input.
#
# Produces, under $OUT (default <SIFT1B_ROOT>/sift1b_build):
#   sift1b_zipf200_sparse10000_numeric_attrs.u32
#     headerless [N,2] uint32 = [categorical tag | numeric]
#   sift1b_zipf200_sparse10000_numeric_attrs.npy
#   counts TSV and a hash-bound generation manifest
#
# The extreme tag has floor(N * 1e-5) members. An optional first argument
# generates a prefix subset for smoke tests without changing the ratio.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)

DS=${SIFT1B_ROOT:-/mnt/nvme/baotonglu/mocheng/datasets/sift1b}
OUT=${SIFT1B_BUILD_OUT:-$DS/sift1b_build}
N="${1:--1}"
PYTHON=${PYTHON:-python3}

mkdir -p "$OUT"
echo "[prep-sift1b] DS=$DS OUT=$OUT N=$N"

ARGS=(
  "$ROOT/Tools/benchmarks/gen_sift1b_attrs.py"
  "$DS"
  --output-dir "$OUT"
  --extreme-tag-ratio 0.00001
)
if [ "$N" != "-1" ]; then
  ARGS+=(--vector-count "$N")
fi
"$PYTHON" "${ARGS[@]}" "${@:2}"

echo "[prep-sift1b] done."
