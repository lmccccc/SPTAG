"""Schema-only synthetic tests; no synthetic points are exported as measurements."""
import copy
import json
import unittest
from prepare import OUTPUT
from renderer_contract import build_protocol, convert_rows, digest, validate_rows


class RendererContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = build_protocol()

    def complete_scenario_fixture(self):
        p = self.protocol
        return [{"scenario": p["scenarios"][0], "case": case, "nprobe": probe,
                 "recall_at_10": 0.5, "ordinary_ms": 1.0, "ordinary_ms_runs": [1.0, 1.0],
                 "qps": 1000.0, "protocol_id": p["protocol_id"],
                 "index_fingerprint": p["index_fingerprint"], "harness_fingerprint": p["harness_fingerprint"],
                 "core_fingerprint": p["original_core_fingerprint" if case == "h1_original"
                                       else "current_core_fingerprint"],
                 "sweep_execution": "single_load_nprobe_array"}
                for case in p["cases"] for probe in p["nprobe"]]

    def test_full_included_scenario_requires_explicit_partial_mode(self):
        rows = self.complete_scenario_fixture()
        validate_rows(rows, self.protocol, partial_scenarios=True)
        with self.assertRaisesRegex(ValueError, "Missing scenarios"):
            validate_rows(rows, self.protocol)

    def test_partial_flag_never_relaxes_grid_or_cases(self):
        rows = self.complete_scenario_fixture()
        with self.assertRaisesRegex(ValueError, "full declared grid"):
            validate_rows(rows[:-1], self.protocol, partial_scenarios=True)
        with self.assertRaisesRegex(ValueError, "full declared grid"):
            validate_rows([r for r in rows if r["case"] != "h3"], self.protocol, partial_scenarios=True)

    def test_actual_smoke_remains_incomplete(self):
        smoke = json.loads((OUTPUT / "summary.smoke.json").read_text())
        converted = convert_rows(smoke, self.protocol)
        self.assertEqual(len(converted), 8)
        self.assertEqual({r["nprobe"] for r in converted}, {24})
        with self.assertRaisesRegex(ValueError, "full declared grid"):
            validate_rows(converted, self.protocol, partial_scenarios=True)

    def test_identity_and_qps_fail_closed(self):
        for key, value in (("core_fingerprint", "0" * 64), ("protocol_id", "0" * 64),
                           ("harness_fingerprint", "0" * 64), ("qps", 1001)):
            rows = self.complete_scenario_fixture()
            rows[0][key] = value
            with self.assertRaises(ValueError):
                validate_rows(rows, self.protocol, partial_scenarios=True)

    def test_protocol_identity_covers_workload_and_objects(self):
        p = {key: value for key, value in self.protocol.items() if key != "protocol_id"}
        self.assertEqual(digest(p), self.protocol["protocol_id"])
        for key in ("workload_definition_sha256", "dataset_input_manifest_sha256",
                    "original_core_fingerprint", "current_core_fingerprint", "harness_fingerprint"):
            changed = copy.deepcopy(p)
            changed[key] = "0" * 64
            self.assertNotEqual(digest(changed), self.protocol["protocol_id"])
        self.assertTrue(self.protocol["original_reference_verified"])
        self.assertTrue(self.protocol["timed_body_shared"])


if __name__ == "__main__":
    unittest.main()
