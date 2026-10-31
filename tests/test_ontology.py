import json, unittest
from pathlib import Path
DATA=json.loads((Path(__file__).parents[1]/"ontology"/"ontology.json").read_text(encoding="utf-8"))
class OntologyTests(unittest.TestCase):
    def test_relationship_endpoints(self):
        ids={x["id"] for x in DATA["classes"]}
        for r in DATA["relationships"]:
            self.assertIn(r["subject"],ids); self.assertIn(r["object"],ids)
    def test_github_removed(self):
        domains={x["domain"] for x in DATA["domains"]}
        self.assertNotIn("github.com",domains); self.assertNotIn("github-wiki-see.page",domains)
    def test_portal_disabled(self):
        p=next(x for x in DATA["domains"] if x["domain"]=="portal.sujeeth.org")
        self.assertFalse(p["crawl_enabled"]); self.assertFalse(p["search_enabled"])
    def test_permissions_and_policies(self):
        for x in DATA["domains"]:
            self.assertIn("crawl_enabled",x); self.assertIn("search_enabled",x)
            if x["crawl_enabled"]: self.assertIn("crawl_policy",x)
if __name__=="__main__": unittest.main()
