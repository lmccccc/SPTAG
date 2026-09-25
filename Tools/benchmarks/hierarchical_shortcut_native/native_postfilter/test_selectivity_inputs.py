import tempfile
import unittest
from pathlib import Path

import numpy as np

from prepare_selectivity import scalar_top_distances
from run_full import quality
from selectivity_common import SelectedPredicate, identity, validate_identities, verify_truth


class GatherOnlyAttributes:
    def __init__(self):
        self.requested = []

    def __len__(self):
        return 1_000_000_000

    def __getitem__(self, ids):
        if isinstance(ids, slice):
            raise AssertionError("A full attribute slice must never be requested")
        self.requested.append(ids.copy())
        return np.column_stack((ids % 2, ids)).astype(np.uint32)


class SelectivityInputsTest(unittest.TestCase):
    def test_only_returned_ids_are_gathered(self):
        attributes = GatherOnlyAttributes()
        predicate = {"and": [{"categorical_eq": [0, 1]}, {"numeric_le": [1, 5]}]}
        selected = SelectedPredicate(attributes, predicate)
        np.testing.assert_array_equal(selected[np.array([1, 3, 6])], [True, True, False])
        self.assertEqual(len(selected), 1_000_000_000)
        self.assertEqual(len(attributes.requested), 1)
        self.assertEqual(attributes.requested[0].tolist(), [1, 3, 6])

    def test_unfilter_does_not_read_attributes(self):
        attributes = GatherOnlyAttributes()
        selected = SelectedPredicate(attributes, None)
        np.testing.assert_array_equal(selected[np.array([0, 999_999_999])], [True, True])
        self.assertFalse(attributes.requested)

    def test_invalid_ids_fail_before_gather(self):
        attributes = GatherOnlyAttributes()
        selected = SelectedPredicate(attributes, None)
        for ids in (np.array([-1]), np.array([1_000_000_000]), np.array([1.0])):
            with self.assertRaises(RuntimeError):
                selected[ids]
        self.assertFalse(attributes.requested)

    def test_existing_quality_accepts_bounded_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ids = np.arange(10, dtype="<i4").reshape(1, 10)
            ids.tofile(root / "ids.i32")
            np.arange(10, dtype="<f4").tofile(root / "dist.f32")
            np.zeros((1, 8), dtype="<u8").tofile(root / "work.u64")
            np.save(root / "truth.npy", ids)
            result = quality(root, {"ids": str(root / "truth.npy")},
                             SelectedPredicate(GatherOnlyAttributes(), None), 1)
            self.assertEqual(result["recall"], 1)
            self.assertEqual(result["underfilled_queries"], 0)

    def test_integer_exhaustive_distances_and_truth(self):
        base = np.arange(1280, dtype=np.int32).reshape(10, 128).astype(np.uint8)
        query = np.zeros((1, 128), dtype=np.uint8)
        distance = np.einsum("ij,ij->i", base.astype(np.int64), base.astype(np.int64))
        order = np.argsort(distance).reshape(1, 10)
        attributes = np.zeros((10, 2), dtype=np.uint32)
        expected = np.sort(distance)
        np.testing.assert_array_equal(scalar_top_distances(base, query[0], chunk=3), expected)
        np.testing.assert_array_equal(verify_truth(order, expected[None, :], base, query,
                                      SelectedPredicate(attributes, None)), expected[None, :])
        with self.assertRaises(RuntimeError):
            verify_truth(order, expected[None, :] + 1, base, query, SelectedPredicate(attributes, None))

    def test_file_identity_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input"
            path.write_bytes(b"first")
            expected = {str(path): identity(path)}
            validate_identities(expected)
            path.write_bytes(b"changed")
            with self.assertRaises(RuntimeError):
                validate_identities(expected)


if __name__ == "__main__":
    unittest.main()
