"""Tools a student connects or opens themselves: classes with a `tool` object and `helps_with` edges to skills."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from catalog import slim_catalog  # noqa: E402

FULL = json.loads((Path(__file__).parents[1] / "ontology" / "ontology.json").read_text(encoding="utf-8"))
CLASSES = {c["id"]: c for c in FULL["classes"]}
TOOLS = {cid: c for cid, c in CLASSES.items() if c.get("category") == "tool"}
EDGES = [r for r in FULL["relationships"] if r["predicate"] == "helps_with"]


def test_the_four_tools_exist():
    assert set(TOOLS) == {"youspot", "teal", "indeed", "foundation_mcp"}
    assert "tool" in [c["id"] for c in FULL["class_categories"]]
    assert "helps_with" in [p["id"] for p in FULL["predicates"]]


def test_every_tool_says_where_it_is_and_how_to_start():
    for cid, tool in TOOLS.items():
        detail = tool["tool"]
        assert detail["url"].startswith("https://"), cid
        assert detail["kind"] in {"app", "mcp", "connector"}, cid
        assert detail["connect"].strip() and detail["fit_when"].strip(), cid
        if detail["kind"] == "mcp":
            assert detail["mcp_url"].startswith("https://"), cid
        assert all(isinstance(x, str) for x in detail.get("available_in", [])), cid


def test_the_mcp_addresses_are_the_published_ones():
    assert TOOLS["foundation_mcp"]["tool"]["mcp_url"] == "https://mcp.sujeeth.org/mcp"
    assert TOOLS["youspot"]["tool"]["mcp_url"] == "https://youspot.com/mcp"


def test_tool_edges_run_from_a_tool_to_a_skill_with_a_weight_and_a_reason():
    assert EDGES
    for edge in EDGES:
        assert edge["subject"] in TOOLS, edge["id"]
        assert CLASSES[edge["object"]]["category"] in {"skill", "outcome", "curriculum"}, edge["id"]
        assert 0 <= edge["weight"] <= 1 and edge["reason"].strip(), edge["id"]
    assert {e["subject"] for e in EDGES} == set(TOOLS)


def test_the_tool_edges_never_widen_a_search():
    predicate = next(p for p in FULL["predicates"] if p["id"] == "helps_with")
    assert predicate["expands_search"] is False and "search_behavior" not in predicate


def test_youspot_leads_for_researching_people():
    best = max((e for e in EDGES if e["object"] == "researching_people"), key=lambda e: e["weight"])
    assert best["subject"] == "youspot"


def test_the_slim_catalog_keeps_what_the_mcp_server_reads():
    slim = slim_catalog(FULL)
    classes = {c["id"]: c for c in slim["classes"]}
    for cid, tool in TOOLS.items():
        assert classes[cid]["tool"] == tool["tool"] and classes[cid]["category"] == "tool"
    kept = [r for r in slim["relationships"] if r["predicate"] == "helps_with"]
    assert len(kept) == len(EDGES) and all("weight" in r and "reason" in r for r in kept)
    assert not any("definition" in r for r in kept)          # the prose is still dropped


def test_a_tool_is_found_by_its_own_domain_only_where_there_is_one():
    domains = {row["domain"]: row["associated_concepts"] for row in FULL["domains"]}
    assert "youspot" in domains["youspot.com"] and "teal" in domains["tealhq.com"]
    assert not any(t in concepts for concepts in domains.values() for t in ("indeed", "foundation_mcp"))
