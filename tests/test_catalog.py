import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from catalog import slim_body, slim_catalog  # noqa: E402

FULL = json.loads((Path(__file__).parents[1] / "ontology" / "ontology.json").read_text(encoding="utf-8"))
SLIM = slim_catalog(FULL)


def test_slim_is_a_fraction_of_the_file():
    body, _ = slim_body(FULL)
    assert len(body) < 0.4 * len(json.dumps(FULL, separators=(",", ":")))


def test_every_class_and_relationship_survives():
    assert [c["id"] for c in SLIM["classes"]] == [c["id"] for c in FULL["classes"]]
    assert [r["id"] for r in SLIM["relationships"]] == [r["id"] for r in FULL["relationships"]]
    assert SLIM["ontology_version"] == FULL["ontology_version"]


def test_matching_fields_are_kept():
    full = {c["id"]: c for c in FULL["classes"]}
    for c in SLIM["classes"]:
        for key in ("name", "definition", "aliases", "query_phrases"):
            assert c.get(key) == full[c["id"]].get(key)


def test_feelings_stay_feelings_without_the_grid():
    feelings = [c for c in FULL["classes"] if isinstance(c.get("mood_meter"), dict)]
    assert feelings
    slim = {c["id"]: c for c in SLIM["classes"]}
    for c in feelings:
        assert slim[c["id"]]["mood_meter"] == {"quadrant": c["mood_meter"].get("quadrant")}


def test_edges_keep_what_decides_expansion_and_drop_the_prose():
    full = {r["id"]: r for r in FULL["relationships"]}
    for r in SLIM["relationships"]:
        source = full[r["id"]]
        assert (r["subject"], r["predicate"], r["object"]) == (source["subject"], source["predicate"], source["object"])
        assert r.get("search_behavior") == source.get("search_behavior")
        for flag in ("general_search_expand", "search_expansion"):
            assert (r.get("retrieval") or {}).get(flag) == (source.get("retrieval") or {}).get(flag)
        assert not {"definition", "audit_status", "provenance", "cardinality"} & set(r)


def test_etag_ignores_changes_the_services_do_not_read():
    _, before = slim_body(FULL)
    changed = json.loads(json.dumps(FULL))
    changed["program_structure"] = {"edited": True}
    changed["relationships"][0]["definition"] = "reworded"
    assert slim_body(changed)[1] == before
    changed["classes"][0]["aliases"] = ["a new alias"]
    assert slim_body(changed)[1] != before
