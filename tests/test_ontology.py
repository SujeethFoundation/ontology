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


def test_spotlight_illustrates_the_career_skills_it_shows():
    class_ids = {item["id"] for item in ONTOLOGY["classes"]}
    skills = {"career_development", "networking", "interviewing", "job_application_strategy", "salary_negotiation",
              "volunteer", "career_mentor"}
    illustrated = {r["object"] for r in ONTOLOGY["relationships"]
                   if r["subject"] == "spotlight" and r["predicate"] == "illustrates"}
    assert skills <= illustrated and illustrated <= class_ids


def test_relationship_ids_are_unique_and_point_at_real_classes():
    class_ids = {item["id"] for item in ONTOLOGY["classes"]}
    ids = [r["id"] for r in ONTOLOGY["relationships"]]
    assert len(ids) == len(set(ids))
    assert all(r["subject"] in class_ids and r["object"] in class_ids for r in ONTOLOGY["relationships"])


def test_relationship_audit_metadata_is_normalized_without_changing_loaded_edges():
    from collections import Counter
    import function_app

    raw = json.loads((Path(__file__).parents[1] / "ontology" / "ontology.json").read_text(encoding="utf-8"))
    audit = raw["relationship_audit"]
    assert audit["edge_defaults"] == {
        "audit_status": "consistent_with_current_definitions",
        "audit_note": "Subject, predicate, and object are internally consistent with the current class definitions and predicate meaning.",
    }

    loaded = function_app.load_ontology()
    counts = Counter(r["audit_status"] for r in loaded["relationships"])
    assert counts == audit["review_status_counts"]
    assert sum(counts.values()) == len(loaded["relationships"])
    proposed = {r["id"] for r in raw["relationships"] if r.get("audit_status") == "proposed_for_editorial_review"}
    assert proposed and {r["id"] for r in loaded["relationships"] if r["audit_status"] == "proposed_for_editorial_review"} == proposed
    by_id = {r["id"]: r for r in loaded["relationships"]}
    assert by_id["networking_supports_referral"]["audit_status"] == "revised"
    assert by_id["networking_supports_referral"]["audit_note"] == audit["edge_overrides"]["networking_supports_referral"]["audit_note"]
    for entry in audit["needs_confirmation"]:
        edge = by_id[entry["relationship"]]
        assert edge["audit_status"] == "needs_confirmation"
        assert edge["audit_note"] == entry["reason"]


def test_relationship_audit_storage_is_smaller_than_repeated_edge_metadata():
    audit = ONTOLOGY["relationship_audit"]
    assert len(audit["needs_confirmation"]) == 12
    assert set(audit["edge_overrides"]) == {"spotlight_features_volunteer", "networking_supports_referral"}


def test_referral_is_a_networking_skill_with_phrase_aliases():
    referral = next(c for c in ONTOLOGY["classes"] if c["id"] == "referral")
    assert referral["category"] == "skill"
    assert set(referral["audiences"]) == {"student", "mentor"}
    assert "ask for a referral" in referral["aliases"]
    # A bare common word would match unrelated questions, so every alias is a phrase.
    assert all(len(alias.split()) >= 2 for alias in referral["aliases"])
    related = {(r["subject"], r["predicate"], r["object"]) for r in ONTOLOGY["relationships"]}
    assert ("referral", "is_part_of", "networking") in related
    # The relationship audit (1.13.x) corrected this edge: networking is the ground a referral grows from, not an
    # illustration of one.
    assert ("networking", "supports", "referral") in related
    assert ("networking", "illustrates", "referral") not in related


def test_referral_resolves_to_approved_domains():
    carrying = {d["domain"] for d in ONTOLOGY["domains"] if "referral" in d["associated_concepts"]}
    assert {"sujeeth.org", "youspot.com", "www.themuse.com"} <= carrying


def test_interviewing_matches_a_plain_interview_question_without_tagging_pages_on_it():
    interviewing = next(c for c in ONTOLOGY["classes"] if c["id"] == "interviewing")
    # query_phrases match questions only; the page tagger skips them, so a bare "interview" cannot tag content.
    assert {"interview", "interviews"} <= set(interviewing["query_phrases"])
    assert "interview" not in [a.lower() for a in interviewing["aliases"]]


def test_career_research_questions_map_to_the_existing_explore_concept():
    """These are question-only phrases: they recover career exploration without broadening page tags."""
    explore = next(c for c in ONTOLOGY["classes"] if c["id"] == "explore")
    phrases = set(explore["query_phrases"])
    assert {
        "research roles and companies",
        "research companies",
        "compare employers",
        "learn what a job role is really like",
    } <= phrases
    assert not ({"research companies", "compare employers"} & set(explore["aliases"]))


def test_illustrates_keeps_its_subject_to_object_meaning_and_retrieval_direction():
    illustrates = next(p for p in ONTOLOGY["predicates"] if p["id"] == "illustrates")
    behavior = illustrates["search_behavior"]
    assert illustrates["symmetric"] is False
    assert behavior["matched_endpoint"] == "object"
    assert behavior["retrieve_endpoint"] == "subject"
    assert behavior["traversal_direction"] == "object_to_subject"


def test_a_week_question_reaches_the_foundations_own_site():
    """"What does each week cover?" matched only the concept `week`. With no domain attached, search ran nowhere and the answer was empty."""
    sujeeth = next(item for item in ONTOLOGY["domains"] if item["domain"] == "sujeeth.org")
    assert {"week", "agenda"} <= set(sujeeth["associated_concepts"])
    program = next(item for item in ONTOLOGY["classes"] if item["id"] == "career_circle_program")
    week = next(item for item in ONTOLOGY["classes"] if item["id"] == "week")
    assert "syllabus" in program["aliases"]
    assert "curriculum" in week["aliases"]


def test_what_the_program_covers_matches_the_weeks_directly():
    """"What is covered in the program?" returned only the Week 1 page. The question now matches Week as well as the
    program, through phrases on both. It does not use an `illustrates` edge: the audit keeps that predicate for
    illustration, and the program contains its weeks."""
    related = {(r["subject"], r["predicate"], r["object"]) for r in ONTOLOGY["relationships"]}
    assert ("career_circle_program", "contains", "week") in related
    assert ("week", "illustrates", "career_circle_program") not in related
    by_id = {c["id"]: c for c in ONTOLOGY["classes"]}
    assert {"covered in the program", "what does the program cover", "topics covered"} <= set(by_id["career_circle_program"]["query_phrases"])
    assert {"each week", "weekly topics"} <= set(by_id["week"]["query_phrases"])
    assert "syllabus" in by_id["career_circle_program"]["aliases"]
    assert "curriculum" in by_id["week"]["aliases"]


# The Search App's filler words (services/concept_resolver.py, STOPWORDS) plus question words. A phrase made only of
# these half-matches unrelated questions: "what's covered" matched "what's a good vodka tonic recipe".
FILLER = set("""a an the and or but if of to in on at by for from with without into about as is are was were be been am do
does did have has had can could should would will may might must i me my mine we us our you your yours he she it its they
them their this that these those there here what which who whom whose when where why how not no yes so than then too very
just also any some more most other such only own same up out over under again all each both few make makes made making give
gives gave giving tell tells told telling show shows showing get gets got getting take takes taking let lets need needs want
wants like use using go goes going know help helps teach teaches way ways thing things please tips tip s t covered cover
covers learn taught""".split())


# Kept on purpose for "how do I get into consulting". They also match "get into shape", so do not add more like them.
FILLER_ONLY_ALLOWED = {("career_development", "getting into"), ("career_development", "get into")}


def test_every_query_phrase_carries_a_topic_word():
    import re
    for item in ONTOLOGY["classes"]:
        for phrase in item.get("query_phrases", []):
            if (item["id"], phrase) in FILLER_ONLY_ALLOWED:
                continue
            words = re.sub(r"[^a-z0-9]+", " ", phrase.lower()).split()
            assert any(word not in FILLER for word in words), (item["id"], phrase)


def test_questions_that_used_to_miss_have_a_phrase():
    by_id = {c["id"]: set(p.lower() for p in c.get("query_phrases", [])) for c in ONTOLOGY["classes"]}
    assert "career circle" in by_id["career_circle_program"]            # "What is a Career Circle?" matched only Circle
    assert "partner with the foundation" in by_id["partnership"]
    assert "who can cover my session" in by_id["substitute_request"]
    assert "job openings" in by_id["job_application_strategy"]
    assert {"elevator pitch", "career fair"} <= by_id["networking"]
    assert "job offer" in by_id["salary_negotiation"]
    assert "thank you email" in by_id["follow_up"]


def test_a_rejection_question_has_a_domain_to_search():
    carrying = {d["domain"] for d in ONTOLOGY["domains"] if "receiving_feedback" in d["associated_concepts"] and d["search_enabled"]}
    assert "sujeeth.org" in carrying


def test_terminology_entries_name_their_class():
    """The Search App reads a terminology entry's aliases only when the entry names its class (class_id)."""
    class_ids = {c["id"] for c in ONTOLOGY["classes"]}
    names = {c["name"].lower() for c in ONTOLOGY["classes"]}
    for term in ONTOLOGY["preferred_terminology"]:
        if str(term.get("preferred", "")).lower() in names:
            assert term.get("class_id") in class_ids, term["preferred"]

