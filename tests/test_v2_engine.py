"""v2 engine + endpoints, fully offline: a fake storage built from a local case stands in for Supabase.
No API call, nothing uploaded.   python tests/test_v2_engine.py <case>   (a case listed in sample_out/v2_cases.json)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("PLJ_API_KEY", "test-key")


class FakeStore:
    """The few storage calls v2 uses, served from memory."""
    def __init__(self, analysis, files):
        self.files = {"P/legal_analysis_ai.json": json.dumps(analysis).encode("utf-8")}
        for name, data in files:
            self.files[f"P/uploads/search_docs/{name}"] = data
        self.uploads = []

    def get_lead(self, lead_id):
        return {"leadid": lead_id, "createdt": "2026-11-01"}

    def lead_prefix(self, lead, year="", month=""):
        return "P"

    def read_json(self, path):
        return json.loads(self.files[path]) if path in self.files else None

    def list_folder(self, prefix):
        return [{"name": k[len(prefix) + 1:], "id": k} for k in self.files if k.startswith(prefix + "/")]

    def download(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def upload(self, path, data, ctype):
        self.files[path] = data
        self.uploads.append(path)

    def upload_json(self, path, obj):
        self.upload(path, json.dumps(obj).encode("utf-8"), "application/json")


def main(case: str):
    from fastapi.testclient import TestClient

    import app.main as m
    assert not [r for r in m.app.routes if getattr(r, "path", "").startswith("/v2")], "v2 must be OFF by default"
    print("ok: v2 endpoints are absent unless V2_ENABLED=1")

    from v2 import api
    from v2.run_local import search_files
    from v2.run_stage2 import OUT
    store = FakeStore(json.load(open(os.path.join(OUT, case, "legal_analysis_ai.json"), encoding="utf-8")), search_files(case))
    api.supa = store
    m.app.include_router(api.router)
    c = TestClient(m.app)

    assert c.post("/v2/jobs/1").status_code == 401, "API key must be required"
    r = c.post("/v2/jobs/1", headers={"x-api-key": "test-key"})
    assert r.status_code == 200, r.text
    st = c.get("/v2/jobs/1", headers={"x-api-key": "test-key"}).json()
    assert st["status"] == "done", st
    print("ok: run", st["summary"])
    assert {"P/v2/search_report.html", "P/v2/chain_of_title.html", "P/v2/review.html", "P/v2/result.json"} <= set(store.uploads)

    assert c.get("/v2/review/1?t=wrong").status_code == 403, "review link must be signed"
    tok = api.review_token(1)
    page = c.get(f"/v2/review/1?t={tok}")
    assert page.status_code == 200 and "Save decisions" in page.text
    print("ok: signed review page served")

    entries = json.loads(store.files["P/v2/result.json"])["entries"]
    first = next(e["entry"] for e in entries if e["tier"] == "confirmed")
    r = c.post(f"/v2/review/1?t={tok}", json={"entries": {first: "exclude"}, "note": "test"})
    assert r.status_code == 200, r.text
    assert any(p.startswith("P/v2/review_history/") for p in store.uploads), "decisions must be kept as test data"
    final = json.loads(store.files["P/v2/result.json"])
    assert any(d["entry"] == first and d["to"] == "not" for d in final["decisions_applied"]), final["decisions_applied"]
    assert f"Sr. No. {first}" not in store.files["P/v2/search_report.html"].decode("utf-8")
    print(f"ok: advocate removed {first} → final report regenerated without it; decision kept in review_history")
    print("ALL V2 ENGINE TESTS PASSED")


if __name__ == "__main__":
    main(sys.argv[1])
