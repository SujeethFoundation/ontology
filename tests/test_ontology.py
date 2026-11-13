import json
from pathlib import Path

ONTOLOGY = json.loads(
    (Path(__file__).parents[1] / "ontology" / "ontology.json").read_text(encoding="utf-8")
)

def test_all_domain_concepts_resolve_to_classes():
    class_ids = {item["id"] for item in ONTOLOGY["classes"]}
    unresolved = {
        concept
        for domain in ONTOLOGY["domains"]
        for concept in domain.get("associated_concepts", [])
        if concept not in class_ids
    }
    assert not unresolved, f"Unresolved domain concepts: {sorted(unresolved)}"

def test_all_remaining_domains_are_crawl_enabled_unless_manual_only():
    assert ONTOLOGY["domains"]
    for item in ONTOLOGY["domains"]:
        if item.get("manual_only") is True:
            # Pages come from POST /api/index/page, so the crawler must never visit the domain.
            assert item.get("crawl_enabled") is False, item["domain"]
        else:
            assert item.get("crawl_enabled") is True, item["domain"]

def test_portal_domain_is_removed():
    assert not any(item.get("domain") == "portal.sujeeth.org" for item in ONTOLOGY["domains"])

def test_domain_notes_are_removed():
    assert all("notes" not in item for item in ONTOLOGY["domains"])

def test_all_remaining_domains_are_search_enabled():
    assert all(item.get("search_enabled") is True for item in ONTOLOGY["domains"])


def test_exclude_paths_are_clean_path_prefixes():
    for domain in ONTOLOGY["domains"]:
        paths = domain.get("crawl_policy", {}).get("exclude_paths", [])
        assert isinstance(paths, list), domain["domain"]
        for path in paths:
            assert isinstance(path, str) and path.startswith("/"), (domain["domain"], path)
            assert path.strip("/"), f"{domain['domain']}: a bare '/' would exclude the whole site"

def test_quick_links_are_well_formed():
    class_ids = {item["id"] for item in ONTOLOGY["classes"]}
    links = ONTOLOGY.get("quick_links", [])
    assert links
    ids = [link["id"] for link in links]
    assert len(ids) == len(set(ids)), "quick_links ids must be unique"
    for link in links:
        assert link["title"].strip() and link["description"].strip(), link["id"]
        assert link["url"].startswith("https://"), link["id"]
        assert link["concepts"], link["id"]
        unresolved = [c for c in link["concepts"] if c not in class_ids]
        assert not unresolved, (link["id"], unresolved)


def test_spotlight_connects_to_career_development():
    class_ids = {item["id"] for item in ONTOLOGY["classes"]}
    assert {"spotlight", "career_development"} <= class_ids
    assert any(
        r["subject"] == "spotlight" and r["object"] == "career_development"
        for r in ONTOLOGY["relationships"]
    )
    sujeeth = next(d for d in ONTOLOGY["domains"] if d["domain"] == "sujeeth.org")
    assert {"spotlight", "career_development"} <= set(sujeeth["associated_concepts"])


def test_index_policy_only_says_whether_chunk_text_stays_in_memory():
    for item in ONTOLOGY["domains"]:
        policy = item.get("index_policy")
        if policy is not None:
            assert set(policy) <= {"text_in_memory"}, item["domain"]
            assert isinstance(policy.get("text_in_memory", True), bool), item["domain"]


def test_the_large_advice_domains_keep_their_text_out_of_memory():
    cold = {d["domain"] for d in ONTOLOGY["domains"] if (d.get("index_policy") or {}).get("text_in_memory") is False}
    assert cold == {"www.themuse.com", "medicine.yale.edu"}
