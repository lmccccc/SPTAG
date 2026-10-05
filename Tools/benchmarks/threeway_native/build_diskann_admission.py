#!/usr/bin/env python3
"""Build the no-search all-label admission executable using the guarded linker."""

from pathlib import Path

from build_diskann_client import main


if __name__ == "__main__":
    main(target="diskann_build_admission", build_entry=Path(__file__), extra_libraries=("-lcrypto",))
