import unittest

import update_results as store
import test_update_results as fixtures


def disk(index=1, scenario="sel50", search=100, legacy=False):
    event = fixtures.measurement(generation=index, scenario=scenario)
    event.update(algorithm="Filtered_DiskANN", study="ablation" if legacy else "main")
    event["version"].update(index=f"L{index}", topk=100)
    event["point"].update(topk=100, search_value=search)
    for row in event["measurements"]:
        row.update(search_value=search, topk=100)
    return event


def replacements():
    return [disk(100, scenario, L) for scenario in ("sel50", "sel20", "sel10", "sel5", "sel1", "sel01")
            for L in (100, 192)]


def retirement(events):
    return dict(schema_version=1, kind="index_retirement", completed_at="2026-10-04T00:00:00Z",
                source=dict(description="Authorized replacement of all old index observations"),
                dataset="SIFT1B", algorithm="Filtered_DiskANN", index="L1", replacement_index="L100",
                replacement_event_ids=[store.digest(e) for e in events],
                required_cells=[dict(study="main", variant="default", scenario=s, topk=100,
                                     cohort_queries=1000, phase="single", threads=1, expected_repeats=2)
                                for s in ("sel50", "sel20", "sel10", "sel5", "sel1", "sel01")])


class RetirementTest(unittest.TestCase):
    setUp = fixtures.ResultsTest.setUp
    tearDown = fixtures.ResultsTest.tearDown

    def test_retirement_preserves_history_unrelated_data_and_is_idempotent(self):
        before = store.update(self.directory, [fixtures.measurement(), disk(), disk(legacy=True)])
        prefix = (self.directory / "history.jsonl").read_bytes()
        events = replacements()
        events.append(retirement(events))
        after = store.update(self.directory, events)
        self.assertEqual([p for p in before["points"] if p["algorithm"] == "SPTAG"],
                         [p for p in after["points"] if p["algorithm"] == "SPTAG"])
        self.assertTrue((self.directory / "history.jsonl").read_bytes().startswith(prefix))
        self.assertEqual(len([p for p in after["points"] if p["algorithm"] == "Filtered_DiskANN"]), 12)
        self.assertFalse(any(v["version"]["index"] == "L1" for v in after["latest_versions"]))
        content = (self.directory / "history.jsonl").read_bytes()
        self.assertEqual(store.update(self.directory, events), after)
        self.assertEqual(content, (self.directory / "history.jsonl").read_bytes())
        late = disk(legacy=True, search=320)
        late["completed_at"] = "2026-10-05T00:00:00Z"
        after = store.update(self.directory, [late])
        self.assertFalse(any(p["version"]["index"] == "L1" for p in after["points"]))
        self.assertFalse(any(v["version"]["index"] == "L1" for v in after["latest_versions"]))

    def test_invalid_replacement_never_appends(self):
        store.update(self.directory, [disk()])
        prefix = (self.directory / "history.jsonl").read_bytes()
        current = (self.directory / "current.json").read_bytes()
        for problem in ("missing", "incomplete", "wrong-index", "wrong-cohort", "single-point"):
            with self.subTest(problem=problem):
                events = replacements()
                removal = retirement(events)
                if problem == "missing":
                    events.pop()
                elif problem == "single-point":
                    events.pop()
                    removal = retirement(events)
                else:
                    changed = events[-1]
                    if problem == "incomplete":
                        changed["measurements"].pop()
                        changed["point"].update(repeats=1, complete_repeats_same_client=False)
                    elif problem == "wrong-index":
                        changed["version"]["index"] = "different"
                    else:
                        changed["point"]["cohort_queries"] = 32
                        for row in changed["measurements"]:
                            row["cohort_queries"] = 32
                    removal = retirement(events)
                with self.assertRaises(ValueError):
                    store.update(self.directory, events + [removal])
                self.assertEqual(prefix, (self.directory / "history.jsonl").read_bytes())
                self.assertEqual(current, (self.directory / "current.json").read_bytes())

    def test_retirement_cannot_reference_future_events(self):
        store.update(self.directory, [disk()])
        events = replacements()
        with self.assertRaisesRegex(ValueError, "precede"):
            store.update(self.directory, [retirement(events)] + events)

    def test_stale_publication_preflight_never_appends(self):
        before = store.update(self.directory, [disk()])
        store.update(self.directory, [fixtures.measurement()])
        history = (self.directory / "history.jsonl").read_bytes()
        events = replacements()
        events.append(retirement(events))
        with self.assertRaisesRegex(ValueError, "preflight"):
            store.update(self.directory, events, expected_history_head=before["history"]["head"])
        self.assertEqual(history, (self.directory / "history.jsonl").read_bytes())


if __name__ == "__main__":
    unittest.main()
