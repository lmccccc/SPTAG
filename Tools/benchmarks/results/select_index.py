#!/usr/bin/env python3
"""Pin one physical index and retire old observations in an append-only transaction."""

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path

import update_results as store


def select(directory, dataset, algorithm, index, cells):
    records = store.load_history(directory / "history.jsonl")
    current = store.project(records)
    key = dataset, algorithm, index
    retired = store.retired_indexes(records)
    store.require(key not in retired, "A retired index cannot be selected")
    references = [p["event_id"] for p in current["points"]
                  if (p["dataset"], p["algorithm"], p["version"]["index"]) == key
                  and p["complete_repeats_same_client"]]
    store.require(references, "Selected index has no accepted complete measurements")
    completed_at = datetime.now(timezone.utc).isoformat()
    template = dict(schema_version=store.SCHEMA, kind="index_retirement",
                    completed_at=completed_at, dataset=dataset, algorithm=algorithm,
                    index="selection-validation" if index != "selection-validation" else "old",
                    replacement_index=index, replacement_event_ids=references, required_cells=cells,
                    source=dict(description="Explicit single-index selection; older observations remain in history"))
    store.validate_event(template)
    replacements = [p for p in current["points"] if p["event_id"] in references]
    for cell in cells:
        matches = [p for p in replacements if all(p.get(k) == value for k, value in cell.items())]
        store.require(len({p["search_value"] for p in matches}) >= 2,
                      "Selection requires a complete replacement curve for every required cell")
    catalog = copy.deepcopy(current["catalog"])
    policies = catalog.get("selected_indexes", [])
    selection = dict(dataset=dataset, algorithm=algorithm, index=index)
    catalog["selected_indexes"] = [p for p in policies if (p["dataset"], p["algorithm"]) != key[:2]] + [selection]
    old = {store.index_key(r["event"]) for r in records if r["event"]["kind"] in ("measurement", "table")}
    old = sorted(k[2] for k in old if k[:2] == key[:2] and k != key and k not in retired)
    events = []
    if catalog != current["catalog"]:
        revision = max(r["event"]["revision"] for r in records if r["event"]["kind"] == "catalog") + 1
        events.append(dict(schema_version=store.SCHEMA, kind="catalog", revision=revision,
                           completed_at=completed_at, catalog=catalog, source=template["source"]))
    events.extend(dict(template, index=value) for value in old)
    return store.update(directory, events, expected_history_head=current["history"]["head"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--required-cells", type=Path, required=True)
    args = parser.parse_args()
    current = select(args.data_dir.resolve(), args.dataset, args.algorithm, args.index,
                     json.loads(args.required_cells.read_text()))
    print(json.dumps(dict(current=str(args.data_dir / "current.json"),
                          events=current["history"]["records"], points=len(current["points"]))))


if __name__ == "__main__":
    main()
