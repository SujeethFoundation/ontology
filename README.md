# Sujeeth Foundation Ontology Function App

Python Azure Functions v2 API. `ontology/ontology.json` is the canonical source for ontology concepts, relationships, rules, terminology, Dataverse mappings, program structure, and domain governance.

## Decisions
- GitHub and `github-wiki-see.page` are removed. Continue using the existing Wiki Search service separately.
- Domain crawling and search permissions are controlled only by the ontology.
- The crawler reads `GET /api/ontology/crawl-manifest`; search reads `GET /api/ontology/search-manifest`.
- `crawl_enabled` and `search_enabled` are independent.
- As of 1.3.0 all 13 domains in the registry are `crawl_enabled` and `search_enabled`. Turn a domain off by changing its flags here after review.
- `portal.sujeeth.org` is not in the registry. Add it back only after its public-content boundary and authorization are established.
- A domain's `associated_concepts` must be exact class IDs (for example `donation`, not "donations"). Names that match no class are silently ignored by the services. The test suite enforces this.
- Crawl size limits (page cap, depth) are not part of the ontology. They are settings of the websearch app.
- A domain's `crawl_policy.exclude_paths` lists path prefixes the crawler must not fetch, for example `["/job", "/jobs"]`. A rule matches that path and everything beneath it on whole path segments (`/job` matches `/job/x`, not `/jobs`). Use it to keep a section of an approved site out of the index.
- Ontology is a conceptual model, not live operational Dataverse data.
- Crawler must still respect robots.txt, site terms, privacy, rate limits, and safe URL restrictions.

## Routes
All routes are prefixed by `/api` and require a Function key by default:
- GET `/api/health`, `/api/ontology`, `/api/ontology/summary`
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

## Tests
```powershell
pip install pytest
python -m pytest -q tests
```
The tests check that every domain concept maps to a class ID, that the registry contains no `portal.sujeeth.org` or `notes` fields, and that every domain is crawl- and search-enabled. Update them if you intentionally change those rules.

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

## Governance and deployment
Edit `ontology/ontology.json`, increment `ontology_version`, review, test, and deploy. Do not expose write endpoints. The crawler should periodically retrieve manifests and reconcile indexed content when domains are disabled or policies change. Crawl size limits are not set in the ontology; the websearch app's `MAX_PAGES_PER_DOMAIN` setting controls them, and the ontology only decides which domains may be crawled. Confirm domain ownership, relevance, public access, robots.txt, and site terms before production crawling. Never crawl private/authenticated content without explicit authorization.


## Version 1.2.0 changes

- Added canonical ontology classes for every concept referenced by domain records.
- Added Resume Resource, Career Development, Leadership, Growth Mindset, Grit,
  Purpose, Networking, Innovation, Motivation, Appreciation, and Week 1–Week 6.
- Added aliases and preferred terminology to support query-to-concept matching.
- Added ontology integrity tests ensuring every domain-associated concept maps
  to a canonical class ID.
- Preserved all existing domain search/crawl permissions. Crawl remains disabled
  for all domains pending review; the portal remains disabled for search and crawl.


## Version 1.3.0 changes

- Enabled `crawl_enabled` for all 13 remaining domain records.
- Removed `portal.sujeeth.org` from the ontology domain registry.
- Removed the `notes` field from every domain record.
- Preserved the existing `search_enabled` values and per-domain crawl policies.
- Confirmed that every domain-associated concept references a defined ontology class.

**Operational caution:** Enabling crawl permission in ontology authorizes the crawler to attempt these domains. It does not itself start a crawl. The crawler must still respect each site's robots.txt, terms, rate limits, and technical safeguards. Review the domain policies before running a full refresh.


## Version 1.3.1 changes

- Added aliases to the `donation` class (donate, donates, donated, donating, donations, gofundme, make a gift, monthly gift), so queries and page text using those words map to it.
- Replaced display names in `sujeeth.org` and `topics.sujeeth.org` `associated_concepts` with class IDs: `student`, `career_mentor`, `donor`, `donation`, `resource`, and `session`. Before this, those concepts were ignored, so content could not be tagged with them and queries about them found no domain.
- Removed "weekly meetings" from both domains. No class has that name, so it was already ignored. Add a class first if you want it back.

## Version 1.3.2 changes

- Removed `max_pages` from every domain's `crawl_policy`. Page limits are now set only in the websearch app.

## Version 1.3.3 changes

- Removed `refresh_interval_days` from every `crawl_policy`. Nothing read it. Crawl timing is set by the schedules in the websearch app.
- `crawl_policy` now holds `mode` and `respect_robots_txt`.

## Version 1.3.4 changes

- Added `crawl_policy.exclude_paths` for `tealhq.com`: `/job` and `/jobs`. TealHQ's job listings were about 78% of the indexed pages and are not relevant to students. The websearch app skips these paths when crawling and can purge what was already stored (`POST /api/index/purge-preview`, then `POST /api/index/purge`).

## Version 1.3.5 changes

- Extended `tealhq.com`'s `exclude_paths` to `/job`, `/jobs`, `/cv-examples`, and `/resume-synonyms`. A count of stored chunks showed job and jobs pages at about 45,000 chunks, CV examples at about 4,500, and resume synonym lists at about 5,500, out of 113,000 for the site.

**After deploying an ontology change:** restart the websearch app (it caches the ontology in memory). If `exclude_paths` changed, run the websearch purge to remove what was stored before. If aliases or domain concepts changed, run the websearch re-tag (`POST /api/index/retag-preview`, then `POST /api/index/retag`) so existing chunks pick up the new tags.