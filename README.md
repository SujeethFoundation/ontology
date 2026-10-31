# Sujeeth Foundation Ontology Function App

Python Azure Functions v2 API. `ontology/ontology.json` is the canonical source for ontology concepts, relationships, rules, terminology, Dataverse mappings, program structure, and domain governance.

## Decisions
- GitHub and `github-wiki-see.page` are removed. Continue using the existing Wiki Search service separately.
- Domain crawling and search permissions are controlled only by the ontology.
- The crawler reads `GET /api/ontology/crawl-manifest`; search reads `GET /api/ontology/search-manifest`.
- `crawl_enabled` and `search_enabled` are independent.
- All domains are initially `crawl_enabled=false` pending domain-specific review; enable crawling only by changing the ontology after review.
- `portal.sujeeth.org` is disabled for both crawling and search until its public-content boundary and authorization are established.
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
Edit `ontology/ontology.json`, increment `ontology_version`, review, test, and deploy. Do not expose write endpoints. The crawler should periodically retrieve manifests and reconcile indexed content when domains are disabled or policies change. `max_pages` is an ontology-governed operational limit, not a separately maintained URL list. Confirm domain ownership, relevance, public access, robots.txt, and site terms before production crawling. Never crawl private/authenticated content without explicit authorization.
