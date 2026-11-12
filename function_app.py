import json, os, re, socket, time
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from urllib.parse import unquote
import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
DATA_PATH = Path(__file__).parent / "ontology" / "ontology.json"

_PROCESS_STARTED = time.time()


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def _rss_mb():
    """Resident memory of this process in MB (Linux only; None elsewhere)."""
    try:
        return round(int((_read("/proc/self/statm") or "").split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except Exception:
        return None


def _cgroup():
    """The container's CPU limit (cores), memory limit (MB) and total time its CPU was throttled, from cgroup v2 or v1.
    A value the platform does not expose is None, so an unknown limit never reads as 'no limit'."""
    out = {"cpu_limit_cores": None, "mem_limit_mb": None, "cpu_throttled_seconds": None}
    try:
        quota = _read("/sys/fs/cgroup/cpu.max")                       # v2: "max 100000" or "200000 100000"
        if quota:
            part = quota.split()
            if len(part) == 2 and part[0] != "max":
                out["cpu_limit_cores"] = round(int(part[0]) / int(part[1]), 2)
        else:                                                         # v1
            q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
            if q and p and int(q) > 0:
                out["cpu_limit_cores"] = round(int(q) / int(p), 2)
        mem = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if mem and mem.isdigit() and int(mem) < (1 << 50):
            out["mem_limit_mb"] = round(int(mem) / 1048576)
        stat = _read("/sys/fs/cgroup/cpu.stat") or _read("/sys/fs/cgroup/cpu/cpu.stat")
        if stat:
            vals = dict(line.split()[:2] for line in stat.splitlines() if len(line.split()) >= 2)
            if "throttled_usec" in vals:
                out["cpu_throttled_seconds"] = round(int(vals["throttled_usec"]) / 1e6, 2)
            elif "throttled_time" in vals:
                out["cpu_throttled_seconds"] = round(int(vals["throttled_time"]) / 1e9, 2)
    except Exception:
        pass
    return out


def process_info():
    """Who is answering, how long it has run, and how much memory and CPU it uses, so a restart shows as a new pid or a
    drop in uptime and a busy instance shows as CPU time climbing faster than the clock."""
    return {
        "pid": os.getpid(),
        "instance": os.getenv("WEBSITE_INSTANCE_ID") or socket.gethostname(),
        "started_at": datetime.fromtimestamp(_PROCESS_STARTED, timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - _PROCESS_STARTED),
        "rss_mb": _rss_mb(),
        "cpu_seconds": round(time.process_time(), 2),   # CPU time used by every thread of this process since it started
        "cpu_count": os.cpu_count(),
        **_cgroup(),
    }


def load_ontology():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)

def respond(data, status=200):
    return func.HttpResponse(json.dumps(data, ensure_ascii=False), status_code=status,
        mimetype="application/json", headers={"Cache-Control":"public, max-age=60"})

def norm(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")

def host(s):
    return re.sub(r"^https?://", "", str(s).strip().lower()).split("/")[0].split(":")[0].removeprefix("www.")

def find_domain(d, requested):
    return next((x for x in d["domains"] if host(x["domain"]) == host(requested)), None)

@app.route(route="health", methods=["GET"])
def health(req):
    d=load_ontology()
    # Not respond(): that adds Cache-Control max-age=60, and a watcher must see the live answer every time.
    return func.HttpResponse(json.dumps({"status":"healthy","service":"sujeeth-ontology","ontology_version":d["ontology_version"],
        "process":process_info()}), status_code=200, mimetype="application/json", headers={"Cache-Control":"no-store"})

@app.route(route="ontology", methods=["GET"])
def ontology_all(req):
    return respond(load_ontology())

@app.route(route="ontology/summary", methods=["GET"])
def summary(req):
    d=load_ontology()
    return respond({"ontology_version":d["ontology_version"],"class_count":len(d["classes"]),
      "relationship_count":len(d["relationships"]),"business_rule_count":len(d["business_rules"]),
      "domain_count":len(d["domains"]),"crawl_enabled_domain_count":sum(x["crawl_enabled"] for x in d["domains"]),
      "search_enabled_domain_count":sum(x["search_enabled"] for x in d["domains"])})

@app.route(route="ontology/classes", methods=["GET"])
def classes(req):
    d=load_ontology(); q=(req.params.get("q") or "").lower()
    rows=[x for x in d["classes"] if not q or q in x["id"].lower() or q in x["name"].lower()]
    return respond({"ontology_version":d["ontology_version"],"classes":rows,"count":len(rows)})

@app.route(route="ontology/classes/{name}", methods=["GET"])
def class_detail(req):
    d=load_ontology(); key=norm(unquote(req.route_params["name"]))
    item=next((x for x in d["classes"] if key in (norm(x["id"]),norm(x["name"]))),None)
    if not item: return respond({"error":"Class not found"},404)
    rel=[r for r in d["relationships"] if r["subject"]==item["id"] or r["object"]==item["id"]]
    return respond({"ontology_version":d["ontology_version"],"class":item,"relationships":rel})

@app.route(route="ontology/relationships", methods=["GET"])
def relationships(req):
    d=load_ontology(); c=norm(req.params.get("concept","")) if req.params.get("concept") else None
    rows=d["relationships"]
    if c:
        cls=next((x for x in d["classes"] if c in (norm(x["id"]),norm(x["name"]))),None)
        rows=[r for r in rows if r["subject"]==cls["id"] or r["object"]==cls["id"]] if cls else [r for r in rows if c in norm(r["definition"])]
    return respond({"ontology_version":d["ontology_version"],"relationships":rows,"count":len(rows)})

@app.route(route="ontology/relationships/summary", methods=["GET"])
def relationship_summary(req):
    d=load_ontology(); names={x["id"]:x["name"] for x in d["classes"]}
    triples=[{"subject":r["subject"],"predicate":r["predicate"],"object":r["object"],"cardinality":r["cardinality"]} for r in d["relationships"]]
    summary="\n".join(f'{names.get(r["subject"],r["subject"])} {r["predicate"].replace("_"," ")} {names.get(r["object"],r["object"])} ({r["cardinality"]}). {r["definition"]}' for r in d["relationships"])
    return respond({"ontology_version":d["ontology_version"],"triples":triples,"agent_readable_summary":summary,
      "business_rules":d["business_rules"],"note":"Conceptual domain model, not live operational records."})

@app.route(route="ontology/concepts/{concept}/relationships", methods=["GET"])
def concept_relationships(req):
    d=load_ontology(); key=norm(unquote(req.route_params["concept"]))
    cls=next((x for x in d["classes"] if key in (norm(x["id"]),norm(x["name"]))),None)
    if not cls: return respond({"error":"Concept not found"},404)
    try: depth=max(1,min(3,int(req.params.get("depth","2"))))
    except ValueError: return respond({"error":"depth must be 1, 2, or 3"},400)
    adj={}
    for r in d["relationships"]:
        adj.setdefault(r["subject"],[]).append(r); adj.setdefault(r["object"],[]).append(r)
    seen={cls["id"]}; q=deque([(cls["id"],0)]); found={}
    while q:
        node,level=q.popleft()
        if level>=depth: continue
        for r in adj.get(node,[]):
            found[r["id"]]=r; other=r["object"] if r["subject"]==node else r["subject"]
            if other not in seen: seen.add(other); q.append((other,level+1))
    return respond({"ontology_version":d["ontology_version"],"concept":cls,"depth":depth,
      "related_concepts":[x for x in d["classes"] if x["id"] in seen],"relationships":list(found.values())})

def simple_route(route, key):
    def handler(req):
        d=load_ontology()
        return respond({"ontology_version":d["ontology_version"],key:d[key]})
    handler.__name__ = "get_" + key
    app.route(route=route, methods=["GET"])(handler)

simple_route("ontology/rules","business_rules")
simple_route("ontology/terminology","preferred_terminology")
simple_route("ontology/program-structure","program_structure")
simple_route("ontology/dataverse-mappings","dataverse_mappings")
simple_route("ontology/schema-gaps","schema_gaps")
simple_route("ontology/quick-links","quick_links")

@app.route(route="ontology/domains", methods=["GET"])
def domains(req):
    d=load_ontology(); rows=d["domains"]
    for param,field in (("crawl_enabled","crawl_enabled"),("search_enabled","search_enabled")):
        value=req.params.get(param)
        if value is not None: rows=[x for x in rows if x[field] == (value.lower()=="true")]
    concept=req.params.get("concept")
    if concept:
        c=norm(concept); rows=[x for x in rows if c in {norm(v) for v in x["associated_concepts"]}]
    return respond({"ontology_version":d["ontology_version"],"domains":rows,"count":len(rows)})

@app.route(route="ontology/domains/{domain}", methods=["GET"])
def domain_detail(req):
    d=load_ontology(); item=find_domain(d,unquote(req.route_params["domain"]))
    return respond({"ontology_version":d["ontology_version"],"domain":item} if item else {"error":"Domain not found"},200 if item else 404)

@app.route(route="ontology/domains/{domain}/concepts", methods=["GET"])
def domain_concepts(req):
    d=load_ontology(); item=find_domain(d,unquote(req.route_params["domain"]))
    if not item: return respond({"error":"Domain not found"},404)
    ids=set(item["associated_concepts"]); concepts=[x for x in d["classes"] if x["id"] in ids]
    return respond({"ontology_version":d["ontology_version"],"domain":item["domain"],"concepts":concepts})

@app.route(route="ontology/domain-resolution", methods=["POST"])
def domain_resolution(req):
    d=load_ontology()
    try: body=req.get_json()
    except ValueError: return respond({"error":"Body must be valid JSON"},400)
    concepts=body.get("concepts",[]); purpose=body.get("purpose","search")
    if not isinstance(concepts,list) or not all(isinstance(x,str) for x in concepts): return respond({"error":"concepts must be an array of strings"},400)
    if purpose not in ("search","crawl"): return respond({"error":"purpose must be search or crawl"},400)
    enabled="search_enabled" if purpose=="search" else "crawl_enabled"
    wanted={norm(x) for x in concepts}
    class_ids={x["id"] for x in d["classes"] if norm(x["id"]) in wanted or norm(x["name"]) in wanted}
    wanted |= {norm(x) for x in class_ids}
    rows=[x for x in d["domains"] if x[enabled] and (not concepts or wanted.intersection({norm(v) for v in x["associated_concepts"]}))]
    return respond({"ontology_version":d["ontology_version"],"purpose":purpose,"requested_concepts":concepts,"domains":rows,"count":len(rows)})

@app.route(route="ontology/validate-domains", methods=["POST"])
def validate_domains(req):
    d=load_ontology()
    try: body=req.get_json()
    except ValueError: return respond({"error":"Body must be valid JSON"},400)
    requested=body.get("domains",[]); purpose=body.get("purpose","search")
    if not isinstance(requested,list) or not all(isinstance(x,str) for x in requested): return respond({"error":"domains must be an array of strings"},400)
    if purpose not in ("search","crawl"): return respond({"error":"purpose must be search or crawl"},400)
    flag="search_enabled" if purpose=="search" else "crawl_enabled"; approved=[]; rejected=[]
    for value in requested:
        item=find_domain(d,value)
        if item and item[flag]: approved.append(item)
        else: rejected.append({"domain":value,"reason":"Unknown domain or permission not enabled"})
    return respond({"ontology_version":d["ontology_version"],"purpose":purpose,"approved":approved,"rejected":rejected})

@app.route(route="ontology/crawl-manifest", methods=["GET"])
def crawl_manifest(req):
    d=load_ontology(); rows=[x for x in d["domains"] if x["crawl_enabled"]]
    return respond({"ontology_version":d["ontology_version"],"crawl_domains":rows,
      "crawler_instructions":{"source_of_truth":"ontology","discover_urls_from":"sitemaps_and_same_domain_links",
      "respect_robots_txt":True,"do_not_crawl_private_or_authenticated_content":True,"reconcile_disabled_domains":True}})

@app.route(route="ontology/search-manifest", methods=["GET"])
def search_manifest(req):
    d=load_ontology()
    return respond({"ontology_version":d["ontology_version"],"search_domains":[x for x in d["domains"] if x["search_enabled"]]})

# Optional chat route for the sujeeth.org chat window (see chat_blueprint.py). Off unless CHAT_ENABLED=true.
if os.getenv("CHAT_ENABLED", "").strip().lower() in ("1", "true", "yes"):
    from chat_blueprint import bp as chat_bp
    app.register_functions(chat_bp)
