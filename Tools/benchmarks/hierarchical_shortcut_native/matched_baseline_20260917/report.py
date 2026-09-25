"""Finalize the bounded smoke report; never launch a query or curve stage."""
import json
import shutil
from pathlib import Path
from prepare import HERE, OUTPUT, TOOL, DATA, sha, write
from run import verify_protected, require


def main():
    registration = verify_protected()
    rows = json.loads((OUTPUT / "summary.smoke.json").read_text())
    runs = json.loads((OUTPUT / "operations.smoke.json").read_text())
    provenance = json.loads((OUTPUT / "cores.json").read_text())
    frozen_binary = DATA / "build_runs/20260909T141212Z_lightweight_rescue/snapshot/Release/spannaclbench"
    frozen_sha = sha(frozen_binary)
    require(frozen_sha == "64598aad5ff23bd42a0fd6370f626f2903bef0cc294be5353c54a77c6eb7ae14",
            "Authenticated original frozen executable changed")
    require(len(rows) == 8 and len(runs) == 16, "Wrong smoke shape")
    status = json.loads((OUTPUT / "status.json").read_text())
    require(status["smoke_complete"] and not status["completed_stages"] and
            not (OUTPUT / "summary.json").exists(), "Curve boundary violated")
    totals = {name: sum(p["native"][name] for run in runs for p in run["points"])
              for name in ("warmup_seconds", "ordinary_seconds", "capture_seconds")}
    totals["native_process_seconds"] = sum(run["process_seconds"] for run in runs)
    files = []
    for run in runs:
        directory = Path(run["directory"])
        require(json.loads((directory / "exit.json").read_text())["returncode"] == 0, "Native exit failure")
        io = json.loads((directory / "io.json").read_text())
        require(io["buffered"] and all(not r["flags"] & 0x4000 for r in io["observations"]), "Actual IO mismatch")
        for point in run["points"]:
            n = point["native"]["nprobe"]
            for suffix, field in ((".ids.i32", "ids_sha256"), (".dist.f32", "distances_sha256"),
                                  (".work.u64", "work_sha256")):
                path = directory / f"nprobe_{n}{suffix}"
                require(sha(path) == point[field], "Native payload changed")
                files.append({"path": str(path), "sha256": point[field]})
    paired = {}
    for scenario in ("unfilter", "broad_tag"):
        table = {r["case"]: r for r in rows if r["scenario"] == scenario}
        a, b = table["h1_original"], table["h1"]
        require(all(a[k] == b[k] for k in
                    ("result_ids_sha256", "result_distances_sha256", "native_work_sha256")), "Original parity")
        paired[scenario] = {"original_current_final_ids_distances_work_exact": True,
            "current_latency_change_percent": 100 * (b["mean_latency_ms"] / a["mean_latency_ms"] - 1)}
    unfilter = [r for r in runs if r["case"] == "supplier" and r["scenario"] == "unfilter"]
    require(all(r["points"][0]["native"]["native_default_queries_capture"] == 1000 for r in unfilter),
            "Unfilter certificate was not actually exercised")
    projection = {
        "measured_smoke_process_seconds": totals["native_process_seconds"],
        "stage_at_constant_nprobe24_cost_upper_simple_seconds": 11 * totals["native_process_seconds"],
        "stage_at_constant_nprobe24_cost_with_single_load_approx_seconds":
            totals["native_process_seconds"] + 10 * (2 * totals["ordinary_seconds"] + totals["capture_seconds"]),
        "not_measured_full_grid_runtime": True,
        "limits": "Higher nprobe changes native work; this is a constant-nprobe24 planning projection, "
                  "not a promised runtime or a lower/upper bound on the real grid. Other four scenarios unmeasured."}
    failed = [{"directory": p.name, "exit": json.loads((p / "exit.json").read_text())}
              for p in sorted(OUTPUT.glob("failed_*"))]
    report = ["# Matched original/current buffered smoke", "",
        "**Only nprobe24 unfilter+broad smoke completed. No11-grid stage ran.**",
        "", "| scenario | case | final Recall@10 | mean ms | QPS | repetition ms |",
        "|---|---|---:|---:|---:|---|"]
    for row in rows:
        report.append(f'| {row["scenario"]} | {row["case"]} | {row["recall"]:.4f} | '
                      f'{row["mean_latency_ms"]:.6f} | {row["qps"]:.2f} | '
                      + "/".join(f"{v:.6f}" for v in row["mean_latency_ms_runs"]) + " |")
    report += ["", "## What is now matched", "",
        "All four cases use one owned buffered loader view of exactly the current H1 graph/tree, "
        "hierarchy CSR/catalogs, SSD records and metadata. Only private IndexDirectory and UseDirectIO=false "
        "differ from the existing view; original loaders remain untouched. All16 accepted processes "
        "have observed buffered posting handles, one LoadAll and one load of each actual native component.",
        "", "Every case uses1000warmup+1000measured queries at offset0, topk10, MaxCheck2048, hierarchy512, "
        "hierarchy ratio0.666666, pages15, one query thread and NUMA CPU/memory node2. SearchSweep.NProbe=[24] "
        "is the sole probe authority in this smoke. Checked-in full-stage INIs contain the11-probe grid and "
        "its reverse. The same benchmark source and ordinary timed body are compiled for both cores; "
        "configuration/capture adapters are outside that body.",
        "", "Original is authenticated Sep9 pre-supplier source, rebuilt after the proposed frozen-array "
        "executable was found to contain supplier-era symbols/objects.648 original sources plus the seven "
        "independent audit files match. It is not claimed to be the precise historical blue executable. "
        "Current h1/h3/supplier link the unchanged accepted native-default core (summary6917dd...), not fusion "
        "or bound-fix cores. No current core rebuild and no search-policy changes.",
        "", "Both scenarios have exact original/current final IDs, float distances and all eight per-query "
        "deterministic native work fields across1000 queries. Current-core overhead relative to original is "
        f'{paired["unfilter"]["current_latency_change_percent"]:.3f}% unfilter and '
        f'{paired["broad_tag"]["current_latency_change_percent"]:.3f}% broad. '
        "Unfilter supplier also matches original final outputs/work, with actual default-admission captures "
        "for all1000 queries and zero helper/parent/child/CSR work. H3 capture fields specific to H1 are "
        "unavailable; its SSD work and final outputs remain measured.",
        "", "Broad supplier91.80% recall versus H172.25% is not a matched-recall QPS comparison. "
        "These isolated points do not establish full curves or a speedup at equal quality. Historical "
        "different-graph/budget/window/direct-IO points are not mixed into this dataset.",
        "", "## Artifacts and execution boundary", "",
        "`summary.smoke.json` is a flat eight-row list. Each row contains case/scenario/nprobe, "
        "recall and recall_at_10, mean_latency_ms, qps, two repetitions, min/max, underfill/meanreturned, "
        "actual SSD work, common protocol/index/timed-body fingerprints and per-core fingerprint. "
        "Final ID/distance and native-work payload hashes are included. `cores.json` contains actual library/"
        "wrapper-object paths and SHA256s. `operations.smoke.json` contains process and batch timings.",
        "", "Re-run `python3 run.py smoke` only to resume/reconcile its existing records. "
        "After review, an explicitly authorized `python3 run.py stage --stage unfilter_broad` runs that "
        "one group; medium_extreme and numeric_mixed require separate releases. "
        "A separate finalize command requires all264 paired points. No plotting files were edited.",
        "", "## Measured cost and runtime projection", "",
        f'Sixteen accepted native processes total {totals["native_process_seconds"]:.3f}s: '
        f'ordinary {totals["ordinary_seconds"]:.3f}s, warmup {totals["warmup_seconds"]:.3f}s, '
        f'untimed correctness capture {totals["capture_seconds"]:.3f}s. '
        "Warmup includes deferred physical loading; the tiny LoadAll API timer is not total load time.",
        "", "At unchanged nprobe24 cost, a16-load/176-batch unfilter_broad stage projects to roughly "
        f'{projection["stage_at_constant_nprobe24_cost_with_single_load_approx_seconds"] / 60:.1f}-'
        f'{projection["stage_at_constant_nprobe24_cost_upper_simple_seconds"] / 60:.1f}min. '
        "That is a planning extrapolation, not a measurement or guarantee: larger probes increase work, "
        "and the four unmeasured scenarios cannot be timed from this smoke.",
        "", "Two discarded attempts remain: the initial executable-copy permission failure (no queries), "
        "and a completed process whose procfs exit race lost its IO observation. The latter was not used "
        "as timing evidence and only that missing batch was repeated. Executable permissions are now "
        "preserved, IO observations are persisted incrementally, and exit races require observed handles "
        "plus independently successful native exit; denied procfs reads are not retried.",
        "", f'Protocol SHA256: `{registration["protocol_sha256"]}`',
        f'Index fingerprint: `{registration["index_fingerprint"]}`',
        f'Original core fingerprint: `{provenance["cores"]["original"]["fingerprint"]}`',
        f'Current core fingerprint: `{provenance["cores"]["current"]["fingerprint"]}`',
        f'Common timed body SHA256: `{provenance["common_timed_body_sha256"]}`',
        f'Authenticated original frozen executable SHA256: `{frozen_sha}`',
        f'Smoke summary SHA256: `{sha(OUTPUT / "summary.smoke.json")}`']
    with (OUTPUT / "report.smoke.md").open("x") as stream:
        stream.write("\n".join(report) + "\n")
    write(OUTPUT / "reconciliation.smoke.json", {"paired_parity": paired, "timings": totals,
        "runtime_projection": projection, "discarded_attempts": failed, "payload_files_reverified": files,
        "valid_native_loads": 16, "valid_ordinary_batches": 16, "points": 8, "full_curve_stages_run": 0,
        "authenticated_original_frozen_binary": {"path": str(frozen_binary), "sha256": frozen_sha},
        "summary_sha256": sha(OUTPUT / "summary.smoke.json")})
    final = OUTPUT / "final_provenance"
    shutil.copytree(HERE, final, ignore=shutil.ignore_patterns("__pycache__"))
    write(OUTPUT / "final_provenance_manifest.json", {
        str(p.relative_to(final)): sha(p) for p in sorted(final.rglob("*")) if p.is_file()})
    print(json.dumps({"paired": paired, "timings": totals, "summary": sha(OUTPUT / "summary.smoke.json")}, indent=2))


if __name__ == "__main__":
    main()
