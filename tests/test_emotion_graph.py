"""Structural and retrieval-contract checks for the Mood Meter ontology.

Run from repository root: python -m unittest discover -s tests -p 'test_emotion_graph.py'
No network or third-party dependencies required.
"""
import json
import unittest
from pathlib import Path

ONTOLOGY = Path(__file__).resolve().parents[1] / "ontology" / "ontology.json"


class EmotionGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
        cls.classes = {c["id"]: c for c in cls.data["classes"]}
        cls.edges = cls.data["relationships"]
        cls.feelings = [c for c in cls.data["classes"] if "mood_meter" in c]

    def test_relationship_integrity(self):
        seen = set()
        predicates = {p["id"] for p in self.data["predicates"]}
        for edge in self.edges:
            with self.subTest(edge=edge["id"]):
                self.assertIn(edge["subject"], self.classes)
                self.assertIn(edge["object"], self.classes)
                self.assertIn(edge["predicate"], predicates)
                key = (edge["subject"], edge["predicate"], edge["object"])
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_graph_connected_without_leaves(self):
        adj = {key: set() for key in self.classes}
        for edge in self.edges:
            adj[edge["subject"]].add(edge["object"])
            adj[edge["object"]].add(edge["subject"])
        self.assertFalse([key for key, links in adj.items() if len(links) <= 1])
        seen = set()
        stack = [next(iter(adj))]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adj[current] - seen)
        self.assertEqual(set(adj), seen)

    def test_all_hundred_feelings_have_ranked_lenses(self):
        self.assertEqual(100, len(self.feelings))
        for feeling in self.feelings:
            lenses = [
                edge for edge in self.edges
                if edge["object"] == feeling["id"]
                and edge["predicate"] == "illustrates"
                and edge.get("retrieval", {}).get("semantic_relation") == "helps_interpret"
            ]
            with self.subTest(feeling=feeling["id"]):
                self.assertTrue(lenses)
                for lens in lenses:
                    self.assertTrue(lens["retrieval"]["intent_tags"])
                    self.assertGreater(lens["retrieval"]["relevance_weight"], 0)
                    self.assertLessEqual(lens["retrieval"]["relevance_weight"], 1)
                    self.assertTrue(lens["retrieval"]["guardrail"])

    def test_disappointment_has_specific_frameworks(self):
        sources = {
            edge["subject"] for edge in self.edges
            if edge["object"] == "disappointment" and edge["predicate"] == "illustrates"
        }
        self.assertTrue({"learning_from_regret", "right_kind_of_wrong"} <= sources)

    def test_search_direction_and_ranking_contract(self):
        illustrates = next(p for p in self.data["predicates"] if p["id"] == "illustrates")
        self.assertTrue(illustrates["expands_search"])
        self.assertEqual("object_to_subject", illustrates["search_behavior"]["traversal_direction"])
        mapping = self.data["retrieval_configuration"]["emotion_concept_mapping"]
        self.assertIn("ranking_policy", mapping)
        self.assertIn("helps_interpret", mapping["semantic_model"])

    def test_intent_examples(self):
        cases = [
            ("disappointment", "setback", "right_kind_of_wrong"),
            ("disappointment", "reflection", "learning_from_regret"),
            ("disappointment", "feedback", "receiving_feedback"),
            ("nervous", "interview", "interviewing"),
            ("proud", "values", "purpose"),
        ]
        for feeling, intent, expected in cases:
            candidates = [
                e for e in self.edges
                if e["object"] == feeling
                and intent in e.get("retrieval", {}).get("intent_tags", [])
            ]
            with self.subTest(feeling=feeling, intent=intent):
                self.assertIn(expected, {e["subject"] for e in candidates})


if __name__ == "__main__":
    unittest.main()
