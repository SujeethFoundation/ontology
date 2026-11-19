"""Pure-function tests for the feelings API's resolution logic."""
import json
import unittest
from pathlib import Path
from feeling_resolution import find_feeling, feeling_detail, resolve_feelings

DATA = json.loads((Path(__file__).resolve().parents[1] / "ontology" / "ontology.json").read_text(encoding="utf-8"))

class FeelingsApiTests(unittest.TestCase):
    def test_alias_and_exact_name(self):
        self.assertEqual("disappointment", find_feeling(DATA, "Disappointed")["id"])
        self.assertEqual("disappointment", find_feeling(DATA, "feeling disappointed")["id"])
        self.assertIsNone(find_feeling(DATA, "not a feeling"))

    def test_detail(self):
        result = feeling_detail(DATA, find_feeling(DATA, "disappointment"))
        self.assertEqual("blue", result["feeling"]["mood_meter"]["quadrant"])
        self.assertIn("right_kind_of_wrong", {x["framework_id"] for x in result["frameworks"]})

    def test_intent_ranking(self):
        for intent, expected in [("setback", "right_kind_of_wrong"),
                                 ("reflection", "learning_from_regret"),
                                 ("feedback", "receiving_feedback")]:
            with self.subTest(intent=intent):
                result = resolve_feelings(DATA, ["disappointment"], intent=intent)
                self.assertEqual(expected, result["frameworks"][0]["framework_id"])
                self.assertLessEqual(len(result["frameworks"]), 3)

    def test_multiple_feelings_and_dedup(self):
        result = resolve_feelings(DATA, ["nervous", "proud", "nervous"], intent="interview", max_results=5)
        self.assertEqual(2, len(result["feelings"]))
        self.assertEqual("interviewing", result["frameworks"][0]["framework_id"])
        self.assertEqual(len(result["frameworks"]), len({x["framework_id"] for x in result["frameworks"]}))

    def test_unresolved(self):
        result = resolve_feelings(DATA, ["unknown", "disappointed"], intent="setback")
        self.assertEqual(["unknown"], result["unresolved"])
        self.assertEqual(1, len(result["feelings"]))

if __name__ == "__main__":
    unittest.main()
