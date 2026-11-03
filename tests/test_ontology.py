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

def test_all_remaining_domains_are_crawl_enabled():
    assert ONTOLOGY["domains"]
    assert all(item.get("crawl_enabled") is True for item in ONTOLOGY["domains"])

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
