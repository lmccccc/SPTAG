"""Reuse the bounded, audited Phase1 runner with Phase2 scope and exact reference checks."""
from pathlib import Path
from integrate import once

HERE = Path(__file__).resolve().parent


def main():
    path = HERE / "run.py"
    if path.exists():
        raise RuntimeError("Do not overwrite the frozen runner")
    t = (HERE.parent / "ratio_degree_phase1/run.py").read_text()
    t = t.replace("Phase1", "Phase2").replace("phase1", "phase2")
    t = t.replace('"1_only_no_full_sweep"', '"2_only_no_full_sweep"')
    t = once(t, "if not self.root.exists():", "if not self.snapshot.exists():")
    t = once(t, '("calls", "adjacency", "supplyTrace")',
             '("calls", "adjacency", "supplyTrace", "supplyIntrinsicBytes")')
    t = t.replace('("h1", "control", "supplier", "reference")', '("h1", "reference", "supplier")')
    t = t.replace('("h1", "control", "supplier")', '("supplier",)')
    t = t.replace('("control", "reference")', '("reference",)')
    t = t.replace('"native_processes": 10', '"native_processes": 6')
    t = t.replace('"ordinary_native_processes": 8, "profile_native_processes": 4, "small_fixture_processes": 10',
                  '"ordinary_native_processes": 6, "profile_native_processes": 3, "small_fixture_processes": 6')
    anchor = '            require(abs(v["recall_at_10"] - result["recall"]) < 1e-12, "Capture/timing recall mismatch")'
    t = once(t, anchor, anchor + """
            prior_name = name.replace("_reference", "_supplier")
            prior_path = self.root.parent / "h1_ratio_phase1_20260917" / prior_name / "record.json"
            prior = json.loads(prior_path.read_text())
            expected = next(p for p in prior["points"] if p["native"]["nprobe"] == result["nprobe"])
            require(v["payload_sha256"] == expected["validation"]["payload_sha256"],
                    "Phase2 changed Phase1 exact native payload: " + name)
            require(core(result) == core(expected["native"]),
                    "Phase2 changed Phase1 core native work: " + name)
""")
    t = once(t, '"ratio_semantics_valid": True,',
             '"ratio_semantics_valid": True, "exact_phase1_payload_and_core_work_parity": True,')
    t = once(t, '"profile_ordinary_repetition_exact_payload_work_parity": True,',
             '"profile_ordinary_repetition_exact_payload_work_parity": True,\n'
             '            "every_capture_exact_phase1_payload_and_core_work_parity": True,')
    path.write_text(t)


if __name__ == "__main__":
    main()
