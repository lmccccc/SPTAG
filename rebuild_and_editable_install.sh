#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$PROJECT_ROOT/build"
PY310_PYTHON="${SPTAG_PYTHON:-$(command -v python3 || command -v python)}"
BUILD_JOBS="${BUILD_JOBS:-$(nproc)}"

if [[ -z "$PY310_PYTHON" || ! -x "$PY310_PYTHON" ]]; then
    echo "[ERROR] python not found; set SPTAG_PYTHON to a valid interpreter"
    exit 1
fi

if [[ ! -d "$BUILD_DIR" || ! -f "$BUILD_DIR/CMakeCache.txt" ]]; then
    echo "[1/3] Configure build directory from README defaults"
    cmake -S "$PROJECT_ROOT" -B "$BUILD_DIR" -DSPDK=OFF -DROCKSDB=OFF
else
    echo "[1/3] Reuse existing CMake configuration in $BUILD_DIR"
fi

echo "[2/3] Build in $BUILD_DIR via cmake --build (${BUILD_JOBS} jobs)"
cmake --build "$BUILD_DIR" --parallel "$BUILD_JOBS"

echo "[3/3] Editable install in $PROJECT_ROOT (py310)"
cd "$PROJECT_ROOT"
"$PY310_PYTHON" -m pip install -e .

echo "[DONE] Build + editable install completed."
