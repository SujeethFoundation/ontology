"""The slim ontology: what the services read to match questions, not the whole file.

ontology.json is about 850 KB, and most of it is for people and for the Feelings API: relationship definitions, audit
notes, provenance, the full Mood Meter grid, program structure, domain crawl policy. The services that match a question
to concepts (mcp, chat, websearch) read none of that. slim_catalog() keeps the fields they read, built from the same
effective data the other routes serve (edge defaults and overrides applied), so every service sees the same edges.

Kept:
  classes                 every field except the Mood Meter grid, which shrinks to its quadrant (a class is a
                          feeling when mood_meter is an object)
  predicates              as is
  relationships           id, subject, predicate, object, search_behavior and the two retrieval flags that decide
                          whether an edge may expand a generic search
  preferred_terminology   as is
  personas                id and the language that signals each one
  ambiguous_terms, source_precedence   as is

A consumer that needs more asks a specific route (feelings, domains, crawl-manifest) instead.
"""
import hashlib
import json

RETRIEVAL_FLAGS = ("general_search_expand", "search_expansion")
SLIM_VERSION = 1


def _class(item):
    out = {key: value for key, value in item.items() if key != "mood_meter"}
    mood = item.get("mood_meter")
    if isinstance(mood, dict):
        out["mood_meter"] = {"quadrant": mood.get("quadrant")}
    return out


def _relationship(item):
    out = {key: item[key] for key in ("id", "subject", "predicate", "object", "search_behavior") if key in item}
    retrieval = item.get("retrieval")
    if isinstance(retrieval, dict):
        flags = {key: retrieval[key] for key in RETRIEVAL_FLAGS if key in retrieval}
        if flags:
            out["retrieval"] = flags
    return out


def _persona(item):
    language = (item.get("signals") or {}).get("language")
    out = {"id": item.get("id")}
    if language:
        out["signals"] = {"language": language}
    return out


def slim_catalog(data):
    """The slim ontology as a dict. data is the effective ontology (load_ontology())."""
    return {
        "catalog_version": SLIM_VERSION,
        "ontology_version": data.get("ontology_version"),
        "classes": [_class(c) for c in data.get("classes") or [] if isinstance(c, dict)],
        "predicates": data.get("predicates") or [],
        "relationships": [_relationship(r) for r in data.get("relationships") or [] if isinstance(r, dict)],
        "preferred_terminology": data.get("preferred_terminology") or [],
        "personas": [_persona(p) for p in data.get("personas") or [] if isinstance(p, dict) and p.get("id")],
        "ambiguous_terms": data.get("ambiguous_terms") or [],
        "source_precedence": data.get("source_precedence") or [],
    }


def slim_body(data):
    """(body bytes, etag). The etag changes only when the slim content does, so an edit to a part of the ontology the
    services do not read (program structure, crawl policy) does not make them download again."""
    body = json.dumps(slim_catalog(data), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
