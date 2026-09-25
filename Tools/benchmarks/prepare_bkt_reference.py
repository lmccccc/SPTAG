#!/usr/bin/env python3
"""Extract authenticated upstream function bodies for the isolated native control."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected = {
    "BKTIndex.cpp": "77ecce4fc742d396431cde34f3fff70c52e044d9",
    "BKTree.h": "1586cb8463864d2bc3036799a6346d84f3de50ec",
    "WorkSpace.h": "50e52794fcb975e9e4af63e80332f24fca008c40",
}
for name, fingerprint in expected.items():
    data = (root / name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if actual != fingerprint:
        raise RuntimeError(f"Official blob mismatch: {name}")


def method(text, signature):
    start = text.index(signature)
    begin = text.rfind("template <typename T>", 0, start)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


tree = (root / "BKTree.h").read_text()
functions = [method(tree, "void InitSearchTrees("), method(tree, "void SearchTrees(")]
search = method((root / "BKTIndex.cpp").read_text(), "void Index<T>::Search(")
search = search.replace("Index<T>::Search(", "PinnedReference<T>::Search(", 1)
iterator = method((root / "BKTIndex.cpp").read_text(), "int Index<T>::SearchIterative(")
iterator = iterator.replace("Index<T>::SearchIterative(", "PinnedReference<T>::SearchIterative(", 1)
license_header = "// Copyright (c) Microsoft Corporation. All rights reserved.\n// Licensed under the MIT License.\n"
(root / "PinnedTree.inc").write_text(license_header + "\n".join(functions) + "\n")
(root / "PinnedSearch.inc").write_text(license_header + search + "\n")
(root / "PinnedIterator.inc").write_text(license_header + iterator + "\n")
(root / "reference-provenance.json").write_text(json.dumps({
    "commit": "2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b",
    "verified_git_blobs": expected,
    "method_changes": "Only Index<T>::Search/SearchIterative class qualifications renamed; function bodies unchanged",
    "scope": "Pinned official tree initialization, tree search and ordinary BKT search; project common containers/SIMD, query-sized workspace retained; not a pristine upstream executable",
    "generated_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                         for name in ("PinnedTree.inc", "PinnedSearch.inc", "PinnedIterator.inc")},
}, indent=2) + "\n")
