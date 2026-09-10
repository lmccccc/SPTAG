#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/baotonglu/mocheng/SPTAG
DATA=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_4tag_demo
BUILD="$DATA/sift1m_build"

cd "$ROOT"
python3 Tools/benchmarks/prep_sift1m_4tag_demo.py "$@"
mkdir -p "$BUILD"
# Explicit NPY inputs; output is the legacy generic-library four-categorical
# headerless uint32 schema, not single-label spannbuilder input.
"$ROOT/Release/spannbuilder" --merge-tags5 \
    --tags-npy "$DATA/multitenant/tags.npy" \
    --num-npy "$DATA/multitenant/num_attr.npy" \
    --out-tags5 "$BUILD/sift1m_tags5.u32" \
    --acl-cols 4 --n 1000000
