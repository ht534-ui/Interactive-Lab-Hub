"""Offline checks of the deterministic ledger boundary, not food recognition."""
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speech-scripts"))
from food_coach import Ledger, RUBRIC, TOOLS, rubric_definition, rubric_instructions


def entry(identity="meal", **changes):
    value = dict(entry_id=identity, action="add", food="Broccoli", portion="one bowl",
                 category="vegetable", separate_serving=False)
    value.update(changes)
    return value


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "record.json"
        self.ledger = Ledger(self.path)
        self.counter = itertools.count()

    def call(self, args, ledger=None):
        return (ledger or self.ledger).execute(str(next(self.counter)), "log_food", args)

    def assert_auditable(self, state):
        breakdown = state["score_breakdown"]
        for category in RUBRIC:
            raw = sum(e["raw_points"] for e in breakdown["entries"] if e["category"] == category)
            self.assertEqual(raw, breakdown["category_raw_points"][category])
            self.assertEqual(raw + breakdown["category_cap_adjustments"][category],
                             state["category_points"][category])
        if state["score"] is not None:
            self.assertEqual(state["base"] + sum(state["category_points"].values()), breakdown["before_final_clamp"])
            self.assertEqual(breakdown["before_final_clamp"] + breakdown["final_clamp_adjustment"], state["score"])

    def test_v1_definition_and_model_schema_share_source(self):
        definition = rubric_definition()
        self.assertEqual(definition["version"], "menu-game-v1")
        self.assertEqual(definition["points_per_entry"], {
            "vegetable": 10, "fruit": 10, "protein": 5, "staple": 5,
            "dessert": -5, "fried": -10, "other": 0})
        self.assertEqual(TOOLS[1]["parameters"]["properties"]["category"]["enum"], list(RUBRIC))
        self.assertIn(json.dumps(definition, sort_keys=True), rubric_instructions())
        definition["points_per_entry"]["vegetable"] = 100
        self.assertEqual(self.call(entry())["score"], 60)

    def test_every_category_matches_documented_points(self):
        for category, points in RUBRIC.items():
            with self.subTest(category=category):
                result = self.call(entry(action="correct" if self.ledger.data["entries"] else "add", category=category))
                self.assertEqual(result["score"], 50 + points)
                self.assert_auditable(result)

    def test_order_replay_restart_and_portion_size_do_not_change_score(self):
        reports = [entry("veg"), entry("cake", food="Cake", portion="half a slice", category="dessert"),
                   entry("chicken", food="Fried chicken", portion="two pieces", category="fried")]
        for order_index, order in enumerate(itertools.permutations(reports)):
            ledger = Ledger(Path(self.tmp.name) / f"order-{order_index}.json")
            for index, report in enumerate(order):
                result = ledger.execute(f"call-{index}", "log_food", report)
                self.assertTrue(result["ok"])
                ledger = Ledger(ledger.path)
                self.assertEqual(ledger.execute(f"call-{index}", "log_food", report), result)
            state = ledger.snapshot()
            self.assertEqual(state["score"], 45)
            self.assert_auditable(state)
            for report in reports:
                self.assertTrue(ledger.execute(f"resize-{report['entry_id']}", "log_food",
                                              {**report, "action": "correct", "portion": "100 bowls"})["ok"])
            self.assertEqual(ledger.snapshot()["score"], 45)

    def test_pending_entry_is_visible_but_not_scored(self):
        pending = self.call(entry(portion=None))
        self.assertIsNone(pending["score"])
        self.assertFalse(pending["score_breakdown"]["entries"][0]["counted"])
        self.assertIsNone(pending["score_breakdown"]["before_final_clamp"])
        corrected = self.call(entry(action="correct", portion="half a bowl"))
        self.assertEqual(corrected["score"], 60)
        self.assertEqual(len(corrected["entries"]), 1)
        returned_pending = self.call(entry(action="correct", portion=None))
        self.assertIsNone(returned_pending["score"])

    def test_other_known_portion_is_fifty_not_unknown(self):
        state = self.call(entry(category="other"))
        self.assertEqual(state["score"], 50)
        self.assertTrue(state["score_breakdown"]["entries"][0]["counted"])

    def test_caps_and_final_clamp_are_explained_without_order_allocation(self):
        # 2 vegetables, 2 fruit, 4 protein, 4 staple -> 50 + 80 -> 100.
        for category, count in [("vegetable", 2), ("fruit", 2), ("protein", 4), ("staple", 4)]:
            for index in range(count):
                state = self.call(entry(f"{category}-{index}", food=f"{category} {index}", category=category))
                self.assertTrue(state["ok"])
        self.assertEqual(state["score"], 100)
        self.assertEqual(state["score_breakdown"]["final_clamp_adjustment"], -30)
        self.assert_auditable(state)
        self.assertFalse(self.call(entry("overflow", food="Another food"))["ok"])

    def test_positive_and_negative_category_caps(self):
        for category, expected in [("vegetable", 20), ("fried", -20), ("dessert", -20)]:
            ledger = Ledger(Path(self.tmp.name) / f"{category}.json")
            for index in range(5):
                result = ledger.execute(str(index), "log_food", entry(str(index), category=category, separate_serving=True))
                self.assertTrue(result["ok"])
            state = ledger.snapshot()
            self.assertEqual(state["category_points"][category], expected)
            self.assert_auditable(state)

    def test_new_id_cannot_duplicate_food_by_changing_case_portion_or_category(self):
        self.call(entry(portion=None))
        for changes in [dict(food="  bRoCcOlI  "), dict(food="Ｂｒｏｃｃｏｌｉ"),
                        dict(portion="two bowls"), dict(category="other")]:
            with self.subTest(changes=changes):
                result = self.call(entry("new-id", **changes))
                self.assertFalse(result["ok"])
                self.assertEqual(len(self.ledger.snapshot()["entries"]), 1)
        self.assertTrue(self.call(entry(action="correct"))["ok"])
        self.assertEqual(self.ledger.snapshot()["score"], 60)

    def test_additional_serving_retains_saved_classification(self):
        self.call(entry())
        self.assertFalse(self.call(entry("second", category="fruit", separate_serving=True))["ok"])
        self.assertTrue(self.call(entry("second", portion="two bowls", separate_serving=True))["ok"])
        self.assertEqual(self.ledger.snapshot()["score"], 70)

    def test_explicit_category_correction_remains_supported(self):
        self.call(entry())
        result = self.call(entry(action="correct", category="other"))
        self.assertEqual(result["score"], 50)
        self.assertEqual(len(result["entries"]), 1)

    def test_correcting_two_names_to_same_food_requires_merge_or_distinct_serving(self):
        self.call(entry())
        self.call(entry("second", food="Roasted vegetable"))
        self.assertFalse(self.call(entry("second", action="correct"))["ok"])
        self.assertTrue(self.call(entry("second", action="remove"))["ok"])
        self.assertTrue(self.call(entry(action="correct", portion="two bowls"))["ok"])
        self.assertEqual(self.ledger.snapshot()["score"], 60)

    def test_removed_id_cannot_be_resurrected_by_new_call_after_restart(self):
        first = self.ledger.execute("original", "log_food", entry())
        self.call(entry(action="remove"))
        ledger = Ledger(self.path)
        self.assertEqual(ledger.execute("original", "log_food", entry()), first)
        self.assertEqual(ledger.snapshot()["entries"], [])
        self.assertFalse(self.call(entry(), ledger)["ok"])
        self.assertEqual(ledger.snapshot()["removed_ids"], ["meal"])
        self.assertTrue(self.call(entry("genuinely-new", separate_serving=True), ledger)["ok"])

    def test_model_cannot_supply_score_or_change_rubric(self):
        for extra in [{"score": 100}, {"rubric": "easy-mode"}, {"points": 99}, {"separate_serving": "true"}, {"category": []}]:
            self.assertFalse(self.call(entry(**extra))["ok"])
        self.assertEqual(self.ledger.snapshot()["entries"], [])
        self.assertFalse(self.ledger.execute("read", "get_food_log", {"score": 100})["ok"])
        for bad_id in [None, [], " "]:
            self.assertFalse(self.ledger.execute(bad_id, "log_food", entry())["ok"])

    def test_result_mutation_does_not_change_saved_call_or_ledger(self):
        result = self.ledger.execute("saved", "log_food", entry())
        result["score"] = 999
        result["entries"][0]["category"] = "fried"
        self.assertEqual(self.ledger.execute("saved", "log_food", entry())["score"], 60)
        self.assertEqual(self.ledger.snapshot()["score"], 60)

    def test_legacy_record_score_and_recap_contract_are_preserved(self):
        legacy = {"rubric": "menu-game-v1", "entries": {
            "old id": {"entry_id": "old id", "food": "Broccoli", "portion": "one bowl", "category": "vegetable"}},
            "calls": {}, "closed": False}
        self.path.write_text(json.dumps(legacy))
        ledger = Ledger(self.path)
        self.assertEqual(ledger.snapshot()["score"], 60)
        self.assertTrue(self.call(entry("old id", action="correct", portion="two bowls"), ledger)["ok"])
        template, values = ledger.summary_template()
        self.assertEqual(values["[[SCORE]]"], 60)
        self.assertEqual(values["[[FOOD_0]]"], "Broccoli")
        self.assertIn("[[SCORE]]", template)
        ledger.freeze(True)
        self.assertFalse(self.call(entry("new", food="Cake", category="dessert"), ledger)["ok"])
        self.assertEqual(Ledger(self.path).snapshot()["score"], 60)

    def test_unrecognized_saved_version_does_not_get_reinterpreted(self):
        self.path.write_text(json.dumps({"rubric": "future-v2"}))
        with self.assertRaisesRegex(ValueError, "Unsupported saved rubric"):
            Ledger(self.path)



class CountedFoodPortionTests(unittest.TestCase):
    def test_whole_food_count_is_a_stated_portion(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / "record.json")
            result = ledger.execute("counted-food", "log_food", {
                "entry_id": "apple-1", "action": "add", "food": "apple",
                "portion": "one apple", "category": "fruit", "separate_serving": False})
            self.assertTrue(result["ok"])
            self.assertEqual(result["score"], 60)
            self.assertEqual(result["pending_portions"], 0)

if __name__ == "__main__":
    unittest.main()
