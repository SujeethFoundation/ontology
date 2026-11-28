# Sujeeth Foundation Ontology Function App

Python Azure Functions v2 API. `ontology/ontology.json` is the canonical source for ontology concepts, relationships, rules, terminology, Dataverse mappings, program structure, and domain governance.

## Platform architecture

The Sujeeth Foundation helps college students network into their first job through Career Circles, a six-week mentorship program. Students, mentors and partners use two front ends, the static site at sujeeth.org and a Power Pages portal, both on one Dataverse environment. Azure Function apps add chat, knowledge search, payments, short links and texting. The Dataverse side (Base Camp, circles, book requests, surveys, credentials, the Career Ascent course) ships as Dataverse solutions and has no repo.

| Repo (README) | Role |
|---|---|
| [`sujeethfoundation.github.io`](https://github.com/SujeethFoundation/sujeethfoundation.github.io/blob/main/README.md) | Website at sujeeth.org, wiki, Discussions, course player and Power Pages CSS |
| [`chat`](https://github.com/SujeethFoundation/chat/blob/main/README.md) | Chat API behind the site's assistant widget |
| [`mcp`](https://github.com/SujeethFoundation/mcp/blob/main/README.md) | MCP server (the "Sujeeth Foundation Graph") that AI assistants use to search Foundation knowledge |
| [`ontology`](https://github.com/SujeethFoundation/ontology/blob/main/README.md) | Concept graph and governance: concepts, approved domains, Feelings API |
| [`websearch`](https://github.com/SujeethFoundation/websearch/blob/main/README.md) | Crawler and hybrid search over ontology-approved websites |
| [`wikisearch`](https://github.com/SujeethFoundation/wikisearch/blob/main/README.md) | Keyword search over the GitHub wiki |
| [`payments`](https://github.com/SujeethFoundation/payments/blob/main/README.md) | PayPal checkout and webhook for online courses |
| [`shortener`](https://github.com/SujeethFoundation/shortener/blob/main/README.md) | Link shortener for go.sujeeth.org |
| [`acs-sms-bot`](https://github.com/SujeethFoundation/acs-sms-bot/blob/main/readme.md) | Texting through Azure Communication Services |

Architecture docs, in the website repo's `architecture/` folder: [platform architecture](https://github.com/SujeethFoundation/sujeethfoundation.github.io/blob/main/architecture/platform-architecture.md) (repos, Azure apps, how they connect), [data model and portal](https://github.com/SujeethFoundation/sujeethfoundation.github.io/blob/main/architecture/platform-data-model-and-portal.md) (Dataverse tables, solutions, Power Pages) and [business architecture](https://github.com/SujeethFoundation/sujeethfoundation.github.io/blob/main/architecture/business-architecture.md) (vision, assumptions, tests). This block is the same in every repo's README; the canonical copy is in the platform architecture doc.

**This repo:** the source of truth the other services read. "What this app does" below lists who reads it and how.

## What this app does

It serves the Foundation's ontology read-only. Three other services depend on it:

| Caller | Uses |
|---|---|
| `mcp` | Reads `ontology/ontology.json` from `main` to resolve a question to concepts and approved domains. Admin tools call this app's routes |
| `websearch` | Reads `/api/ontology/crawl-manifest` and `/api/ontology/search-manifest` to decide what to crawl and search, and caches the answer |
| `chat` | Reads a nightly copy of the ontology to enrich questions before they reach the agent |

The chat endpoint that used to live in this app is now in the `chat` repo.

**Current state** (read from `ontology/ontology.json` on `main`): version `1.15.6-proposed`, 182 classes, 1,161 relationships, 19 domains. The ontology file, not this README, is the source of truth for counts. Release history is in [CHANGELOG.md](CHANGELOG.md).

**Known gaps.** The ontology has no concepts yet for the Career Ascent online course, certificates, mentor training or Base Camp. A question about any of them returns the out-of-scope message from the MCP server. Add the concepts and source pages first.

## Decisions
- GitHub and `github-wiki-see.page` are removed. Continue using the existing Wiki Search service separately.
- Domain crawling and search permissions are controlled only by the ontology.
- The crawler reads `GET /api/ontology/crawl-manifest`; search reads `GET /api/ontology/search-manifest`.
- `mcp`, `chat` and `websearch` match questions to concepts through `GET /api/ontology/catalog`, the slim ontology (below), so none of them downloads the 850 KB file.
- `crawl_enabled` and `search_enabled` are independent.
- A domain is crawled only when `crawl_enabled` is true, and searched only when `search_enabled` is true. Turn a domain off by changing its flags here after review.
- `manual_only` domains (`app.candid.org` and `portal.sujeeth.org`) are `search_enabled` but not `crawl_enabled`. Their pages are added by hand to the websearch index. Establish a public-content boundary and authorization before turning crawl on for `portal.sujeeth.org`.
- A domain's `associated_concepts` must be exact class IDs (for example `donation`, not "donations"). Names that match no class are silently ignored by the services. The test suite enforces this.
- Crawl size limits (page cap, depth) are not part of the ontology. They are settings of the websearch app.
- A domain's `crawl_policy.exclude_paths` lists path prefixes the crawler must not fetch, for example `["/job", "/jobs"]`. A rule matches that path and everything beneath it on whole path segments (`/job` matches `/job/x`, not `/jobs`). A `*` matches any characters within one path segment, so `/career-paths/*-certifications` matches `/career-paths/snowflake-developer-certifications` but not `/career-paths/certifications-guide`. Use it to keep a section of an approved site out of the index.
- Ontology is a conceptual model, not live operational Dataverse data.
- Crawler must still respect robots.txt, site terms, privacy, rate limits, and safe URL restrictions.

## Routes
All routes are prefixed by `/api` and require a Function key by default:
- GET `/api/health`, `/api/ontology`, `/api/ontology/summary`
- GET `/api/ontology/catalog` (the slim ontology for matching questions; see below)
- GET `/api/ontology/classes`, `/api/ontology/classes/{name}`
- GET `/api/ontology/relationships`, `/api/ontology/relationships/summary`
- GET `/api/ontology/concepts/{concept}/relationships?depth=2`
- GET `/api/ontology/rules`, `/api/ontology/terminology`
- GET `/api/ontology/program-structure`, `/api/ontology/dataverse-mappings`, `/api/ontology/schema-gaps`
- GET `/api/ontology/domains?crawl_enabled=true`
- GET `/api/ontology/domains?search_enabled=true`
- GET `/api/ontology/domains?concept=resume_resource`
- GET `/api/ontology/domains/{domain}`, `/api/ontology/domains/{domain}/concepts`
- POST `/api/ontology/domain-resolution`
- POST `/api/ontology/validate-domains`
- GET `/api/ontology/crawl-manifest`, `/api/ontology/search-manifest`
- GET `/api/ontology/quick-links`
- GET `/api/ontology/feelings/{feeling}`, POST `/api/ontology/feelings/resolve` (see Feelings API below)

## Tests
```powershell
pip install pytest
python -m pytest -q tests
```
The tests check that every domain concept maps to a class ID, that no domain record has a `notes` field, and that every domain is crawl-enabled unless it is marked `manual_only`. Update them if you intentionally change those rules.

## Local setup (Windows PowerShell)
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item local.settings.example.json local.settings.json
func start
```
Install Azure Functions Core Tools v4. Use Azurite or configure a development storage account.

## Test requests
```powershell
Invoke-RestMethod "http://localhost:7071/api/health"
Invoke-RestMethod "http://localhost:7071/api/ontology/summary"
Invoke-RestMethod "http://localhost:7071/api/ontology/crawl-manifest"
Invoke-RestMethod "http://localhost:7071/api/ontology/domains?search_enabled=true"
```

Domain resolution:
```powershell
$body = @{ concepts = @("Resume Resource", "Week 3"); purpose = "search" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:7071/api/ontology/domain-resolution" -ContentType "application/json" -Body $body
```

## Slim catalog

`GET /api/ontology/catalog` returns what a service needs to match a question to concepts, about 255 KB (28 KB gzipped) against 850 KB for `ontology.json`. It is built by `catalog.py` from the same effective data the other routes serve, so edge defaults and overrides are applied and every service sees the same edges.

| Kept | Dropped |
| --- | --- |
| Every class, whole, except the Mood Meter grid (a feeling keeps `mood_meter: {quadrant}`) | The Mood Meter grid, which only the Feelings API reads |
| Every relationship: `id`, `subject`, `predicate`, `object`, `search_behavior`, and the retrieval flags `general_search_expand` and `search_expansion` | Relationship definitions, audit notes, provenance, cardinality |
| `predicates`, `preferred_terminology`, `ambiguous_terms`, `source_precedence`; personas as `id` and signal language | Program structure, domains and crawl policy, content gaps, Dataverse mappings, and the rest. Ask the specific route for those |

The response has an `ETag` that changes only when something in the slim content changes, so an edit to program structure or crawl policy does not make the services download again. Send `If-None-Match` to get a 304 with no body.

## Feelings API

The Azure Function App serves deterministic Mood Meter resolution from the deployed `ontology/ontology.json`. These routes do not call an LLM or search service.

- `GET /api/ontology/feelings/{feeling}`: exact ID, name, or alias lookup. Returns the feeling, quadrant, and curated explanatory frameworks. Returns 404 for unknown feelings.
- `POST /api/ontology/feelings/resolve`: accepts `{"feeling":"disappointment","intent":"setback","persona":"student","max_results":3}` or `{"feelings":["nervous","proud"],"intent":"interview"}`. Returns up to 10 ranked framework matches and their guardrails, along with unresolved terms. Returns 400 for invalid input and 404 if no feeling matches.

The MCP should detect feeling(s) and intent from the user's question and pass canonical intent tags such as `setback`, `reflection`, `feedback`, `interview`, `values`, or `regulation`. The API ranks exact intent-tag matches before editorial relevance weights. It does **not** infer intent from natural-language questions, personalize ranking by persona, perform web search, or verify framework guidance against live sources. Persona is validated and returned as context, not used for access control or scoring. The MCP must apply its own privacy and retrieval policies.


Deployment: the MCP server calls `POST /api/ontology/feelings/resolve` and returns the ranking and guardrails to the agent. Restart the websearch app after an ontology deploy, since it caches the ontology.

### Emotion-aware search routing

The 326 emotion-to-framework `illustrates` edges remain in the ontology and remain available to the Feelings API. They now carry `retrieval.general_search_expand=false` and `retrieval.search_expansion="feelings_api_only"`. The other 41 general-purpose `illustrates` edges retain their existing search eligibility.

**In the MCP server:** the resolver asks the model to identify feelings and one intent, and `mcp_server.py` skips edges marked `general_search_expand=false` or `search_expansion=feelings_api_only` when it expands concepts for generic search (`is_feelings_api_only_edge`, with tests in `mcp/tests/test_feelings_routing.py`). After the model names the feelings, `add_feelings_frameworks` calls `POST /api/ontology/feelings/resolve`, appends the ranked frameworks to the matched concepts, and returns the route (frameworks and guardrails, or a degraded flag if the call fails) in the search response. Websearch does not need these flags, because the MCP does the expansion.

## Governance and deployment
Edit `ontology/ontology.json`, increment `ontology_version`, review, test, and deploy. Do not expose write endpoints. The crawler should periodically retrieve manifests and reconcile indexed content when domains are disabled or policies change. Crawl size limits are not set in the ontology; the websearch app's `MAX_PAGES_PER_DOMAIN` setting controls them, and the ontology only decides which domains may be crawled. Confirm domain ownership, relevance, public access, robots.txt, and site terms before production crawling. Never crawl private/authenticated content without explicit authorization.

