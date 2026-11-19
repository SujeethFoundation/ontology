"""Deterministic Mood Meter lookups and intent-aware framework ranking.

The caller (MCP) identifies feeling and intent. No model calls or inference here.
"""
import re

def normalize(value):
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

def find_feeling(data, value):
    key = normalize(value)
    for item in data["classes"]:
        if "mood_meter" not in item:
            continue
        terms = [item["id"], item["name"], *item.get("aliases", [])]
        if any(normalize(term) == key for term in terms):
            return item
    return None

def feeling_detail(data, feeling):
    classes = {item["id"]: item for item in data["classes"]}
    links = []
    for edge in data["relationships"]:
        if edge["object"] != feeling["id"] or edge["predicate"] != "illustrates":
            continue
        meta = edge.get("retrieval", {})
        if meta.get("semantic_relation") != "helps_interpret":
            continue
        framework = classes.get(edge["subject"])
        if framework:
            links.append({"framework_id": framework["id"], "framework_name": framework["name"],
                          "definition": framework["definition"], "relationship_id": edge["id"],
                          "explanation": edge["definition"], "intent_tags": meta.get("intent_tags", []),
                          "relevance_weight": meta.get("relevance_weight", 0),
                          "guardrail": meta.get("guardrail"), "source_status": meta.get("source_status")})
    return {"feeling": feeling, "frameworks": links}

def resolve_feelings(data, values, intent=None, persona=None, max_results=3):
    matched = []
    for value in values:
        feeling = find_feeling(data, value)
        if feeling and feeling["id"] not in {f["id"] for f in matched}:
            matched.append(feeling)
    if not matched:
        return {"feelings": [], "frameworks": [], "unresolved": values}
    grouped = {}
    for feeling in matched:
        for link in feeling_detail(data, feeling)["frameworks"]:
            fid = link["framework_id"]
            intent_match = bool(intent and intent in link["intent_tags"])
            # Intent match takes precedence over editorial prior; no probabilistic interpretation.
            score = round((1 if intent_match else 0) + link["relevance_weight"], 3)
            candidate = {**link, "matched_feelings": [feeling["id"]], "intent_match": intent_match,
                         "ranking_score": score}
            if fid not in grouped or score > grouped[fid]["ranking_score"]:
                grouped[fid] = candidate
            elif feeling["id"] not in grouped[fid]["matched_feelings"]:
                grouped[fid]["matched_feelings"].append(feeling["id"])
    ranked = sorted(grouped.values(), key=lambda x: (-x["ranking_score"], x["framework_id"]))
    return {"feelings": matched, "frameworks": ranked[:max_results],
            "unresolved": [v for v in values if not find_feeling(data, v)],
            "intent": intent, "persona": persona, "ranking": "intent_match_then_editorial_weight",
            "note": "Ontology guidance only. Scores are ranking signals, not probabilities; no model inference or live search performed."}
