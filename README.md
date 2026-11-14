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
- A domain's `crawl_policy.exclude_paths` lists path prefixes the crawler must not fetch, for example `["/job", "/jobs"]`. A rule matches that path and everything beneath it on whole path segments (`/job` matches `/job/x`, not `/jobs`). A `*` matches any characters within one path segment, so `/career-paths/*-certifications` matches `/career-paths/snowflake-developer-certifications` but not `/career-paths/certifications-guide`. Use it to keep a section of an approved site out of the index.
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

## Version 1.3.6 changes

- Replaced `danielpink.substack.com` with `amycedmondson.com`, linked to `growth_mindset`, `leadership`, and `week_6`. The Substack site's `robots.txt` refused the crawler, so its job finished with one page skipped as `robots_denied` and nothing stored.

## Version 1.3.7 changes

- Tightened the index to fit its size limits (the 300 MB vector quota and 1 GB storage quota of the search service). A count of stored chunks showed 76,841, with `tealhq.com` at 57,680.
- `tealhq.com` now also excludes `/resume-examples` and `/cover-letter-examples` (about 17,200 chunks of near-duplicate per-job-title examples), `/resume-template`, `/resume-templates`, `/job-categories`, `/tool` (about 800), and `/career-paths/*-certifications` (6,838 chunks of per-role certification guides). Interview-question and career-guide pages under `/career-paths`, and `/post`, are kept.
- `mike-robbins.com` excludes `/tag`, `/category`, and `/linked_podcast_category`, which are archive pages that repeat post excerpts (about 800 chunks).

**After deploying an ontology change:** restart the websearch app (it caches the ontology in memory). If `exclude_paths` changed, run the websearch purge to remove what was stored before. If aliases or domain concepts changed, run the websearch re-tag (`POST /api/index/retag-preview`, then `POST /api/index/retag`) so existing chunks pick up the new tags.

## Version 1.5.7 changes

- Changed medicine.yale.edu's include_paths from /childstudy/news-article/ to /childstudy/ so the crawler can visit the Child Study section and discover its news article pages.

## Version 1.4.2 changes

- Connected `spotlight` and `career_development`. Added the relationship `spotlight_illustrates_career_development` (N:N) and added `career_development` to the `sujeeth.org` domain's `associated_concepts`, so career questions search Spotlight stories on sujeeth.org. Domain resolution reads only `associated_concepts`; the relationship documents the link and appears in the concept-relationship endpoints.
- Added the aliases `sales career`, `sales jobs`, `get a job in sales`, and `entry-level sales` to `career_development`.

**After deploying:** restart the websearch app (it caches the ontology in memory). If the search service stores aliases, reindex them.

## Version 1.10.0 changes

- Added an optional `index_policy` to a domain record: `{"text_in_memory": false}` keeps that domain's chunk text in the websearch table instead of in each instance's memory. The domain is still searched by keywords and vectors, and the text of the few chunks a search returns is read from the table. Absent means true. `/api/ontology/search-manifest` carries it with the rest of the record, and websearch applies a change on its next index check: the ontology answer is cached for `ONTOLOGY_CACHE_SECONDS` (2 minutes), and an instance reloads at most once per `LOCAL_INDEX_MIN_RELOAD_SECONDS` (10 minutes).
- Set it to false for `www.themuse.com` (40,344 chunks) and `medicine.yale.edu` (22,824 chunks), about 73% of the index. A chunk without text in memory gets no phrase-match bonus in ranking.

## Version 1.11.0 changes

- Spotlight stories now surface on the career skills they show, not only on career development. Added the `illustrates` relationships `spotlight_illustrates_networking`, `spotlight_illustrates_interviewing`, `spotlight_illustrates_job_application_strategy`, `spotlight_illustrates_salary_negotiation`, `spotlight_illustrates_volunteer` and `spotlight_illustrates_career_mentor` (N:N). A question that matches one of these concepts (including questions about volunteering and mentoring) now also searches Spotlight, and the websearch app reserves its Spotlight places for the question. No domain change was needed: `sujeeth.org` already lists `spotlight` in its `associated_concepts`, and domain resolution reads only that list.

**After deploying:** restart the websearch app (it caches the ontology in memory).

## Version 1.12.0 changes

- Added the `referral` class (category `skill`, audiences student and mentor): asking someone you know to recommend or introduce you. Aliases are phrases such as `ask for a referral`, `get a referral`, `referral from a mentor`, `warm introduction`; none is a bare common word.
- Tied it to networking: `referral_is_part_of_networking` (`is_part_of`), and two `illustrates` relationships, `networking_illustrates_referral` and `week_4_illustrates_referral`, so a referral question also searches pages tagged Networking or Week 4 without waiting for a reindex.
- Added `referral` to the student's current and past stages, to the skills in `source_precedence`, and to the `associated_concepts` of `sujeeth.org`, `youspot.com` and `www.themuse.com`.

**After deploying:** restart the websearch app (it caches the ontology in memory). Pages are tagged `referral` when they are indexed, so reindex or recrawl `sujeeth.org` to tag existing pages that mention referrals directly.

## Version 1.12.1 changes

- Added `query_phrases` to `interviewing`: `interview`, `interviews`, `prepare for an interview`, `interview preparation`. Before, a plain question such as "how do I prepare for an interview" scored `networking` (via "informational interview") and `interviewing` (via "job interview") at the same weak 0.33, so the search asked "Are you asking about Networking or Interviewing?". `interviewing` now scores 1.4 and no question is asked. Query phrases never tag page content, so no new pages are tagged `interviewing`.
- A question that names an informational interview still matches both concepts outright (1.2 and 1.1) and searches both.

**After deploying:** restart the websearch app (it caches the ontology).
