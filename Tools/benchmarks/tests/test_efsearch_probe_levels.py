import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import numpy as np


SPEC = importlib.util.spec_from_file_location(
    "efsearch_probe_levels", Path(__file__).resolve().parents[1] / "efsearch_probe_levels.py"
)
BENCHMARK = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"SPTAG": types.ModuleType("SPTAG")}):
    SPEC.loader.exec_module(BENCHMARK)


class RecallAccountingTest(unittest.TestCase):
    def run_benchmark(self, results, truth):
        truth = np.asarray(truth, dtype=np.int64)
        queries = np.zeros((len(truth), 4), dtype=np.float32)
        tags = np.zeros((len(truth), 4), dtype=np.uint32)
        manager = mock.Mock()
        manager.LoadAll.return_value = True
        manager.SearchWithACL.side_effect = results
        manager.GetLastPostingReadCount.return_value = 1
        manager.GetLastScannedVectors.return_value = 2
        manager.GetLastMatchedVectors.return_value = 0
        values = {
            "MaxCheck": "32", "ValueType": "Float", "ResultNum": "2",
            "RerankL": "0", "SearchInternalResultNum": "8",
        }
        arrays = {
            "query_vectors.npy": queries, "query_tags.npy": tags,
            "groundtruth_org_local_ids.npy": truth,
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, {
            "INDEX_DIR": "/unused/index", "QUERY_DIR": "/unused/queries",
            "WARMUP": "0", "NUM_QUERIES": str(len(truth)),
            "MEASURE_OFFSET": "0", "LEVELS": "org", "TOPK": "2", "TENANT": "0",
        }, clear=True), mock.patch.object(
            BENCHMARK, "read_ini_value", side_effect=lambda _, __, key: values[key]
        ), mock.patch.object(
            BENCHMARK.SPTAG, "CreateTenantIndexManager", return_value=manager, create=True
        ), mock.patch.object(
            BENCHMARK.np, "load", side_effect=lambda path: arrays[path.name]
        ), contextlib.redirect_stdout(output):
            BENCHMARK.main()
        return json.loads(output.getvalue().removeprefix("RESULT ").strip())

    def test_empty_and_partial_results_remain_in_denominator(self):
        result = self.run_benchmark(
            [([-1, -1], [float("inf"), float("inf")]), ([7, -1], [0.0, float("inf")])],
            [[4, 5], [7, 8]],
        )
        self.assertEqual(result["recall"], 0.25)
        self.assertEqual(result["recall_denominator"], 4)
        self.assertEqual(result["num_queries"], 2)
        self.assertEqual(result["empty_results"], 1)
        self.assertEqual(result["underfilled_results"], 2)

    def test_all_empty_results_are_valid_zero_recall(self):
        result = self.run_benchmark(
            [([-1, -1], [float("inf"), float("inf")])], [[4, 5]]
        )
        self.assertEqual(result["recall"], 0.0)
        self.assertEqual(result["recall_denominator"], 2)
        self.assertEqual(result["empty_results"], 1)

    def test_fewer_groundtruth_matches_do_not_inflate_denominator(self):
        result = self.run_benchmark([([7, -1], [0.0, float("inf")])], [[7, -1]])
        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["recall_denominator"], 1)
        self.assertEqual(result["underfilled_results"], 0)

    def test_failed_native_search_still_raises(self):
        with self.assertRaisesRegex(RuntimeError, "SearchWithACL failed for 1"):
            self.run_benchmark([None], [[4, 5]])


if __name__ == "__main__":
    unittest.main()
