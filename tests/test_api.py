"""API smoke test with Supabase replaced by a local folder and Claude replaced by the Unit 115 mock.
Run:  python tests/test_api.py <folder-with-uploads-layout>"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PLJ_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from app import llm, main, pipeline, supa  # noqa: E402
from mock_unit115 import mock  # noqa: E402
from run_local import LocalStore  # noqa: E402

ROOT = sys.argv[1]
store = LocalStore(ROOT)
llm.set_override(mock)

# fake Supabase
supa.get_lead = lambda lid: {"leadid": lid, "custname": "JAS", "createdt": "2026-10-01T10:00:00"}
supa.read_json = store.get_json
pipeline.SupabaseStore = lambda: type("S", (), {
    "list": lambda self, p: store.list(p), "get": lambda self, p: store.get(p),
    "put": lambda self, p, d, m: store.put(p, d, m), "get_json": lambda self, p: store.get_json(p),
    "save_legalinfo": lambda self, lid, li: True, "delete": lambda self, p: store.delete(p)})()

c = TestClient(main.app)
H = {"x-api-key": "test-key"}
body = {"year": "local", "month": "x", "bank_name": "Bank of India", "bank_branch": "Vasai Branch"}

assert c.get("/health").json()["status"] == "ok"
assert c.post("/jobs/513", json=body).status_code == 401, "auth must be enforced"
assert c.get("/jobs/999", headers=H, params={"year": "local", "month": "x"}).json()["status"] == "not_started"

r = c.post("/jobs/513", json=body, headers=H)
print("start:", r.status_code, r.json().get("status"))
assert r.status_code == 202
# TestClient runs background tasks before returning, so the job is finished here
st = c.get("/jobs/513", headers=H).json()
print("status:", st["status"], st.get("step"), st.get("error", ""))
assert st["status"] == "done", st
print(json.dumps(st["outputs"]["summary"], indent=1))

# json mode wrote legalinfo only; workflow-2 endpoint builds the two DOCX files separately
import os as _os
li_path = _os.path.join(ROOT, "local/x/513/legalinfo_ai.json")
assert _os.path.exists(li_path)
rr = c.post("/reports/513", json={"prefix": "local/x/513"}, headers=H)
assert rr.status_code == 200, rr.text
j = rr.json()
assert j["chain_of_title"].endswith("reports/chain_of_title_513.docx") and j["search_report"].endswith("reports/search_report_513.docx"), j
assert c.post("/reports/513", json={"prefix": "local/x/999"}, headers=H).status_code == 400   # wrong lead/folder
print("reports:", j["chain_of_title"], "|", j["search_report"])

# lock: a running job is not started twice
pipeline._jobs[513]["status"] = "running"
r2 = c.post("/jobs/513", json=body, headers=H)
assert r2.json().get("already_running") is True
pipeline._jobs[513]["status"] = "done"

# reports-only mode reuses the saved analysis
r3 = c.post("/jobs/513", json={**body, "request_type": "reports"}, headers=H)
assert r3.status_code == 202
assert c.get("/jobs/513", headers=H).json()["status"] == "done"

# restart simulation: memory cleared → persisted status file is read
pipeline._jobs.clear()
st2 = c.get("/jobs/513", headers=H, params={"year": "local", "month": "x"}).json()
assert st2["status"] == "done", st2
assert c.post("/webhook/storage", json={"record": {"name": "a/b/c.pdf"}}).json()["skipped"] is True
# n8n-upload mode: backend writes NOTHING, n8n fetches /result and uploads; /render returns the DOCX bytes
import shutil
shutil.rmtree(_os.path.join(ROOT, "local/x/513/reports"), ignore_errors=True)
for f in ("legalinfo_ai.json", "legal_analysis_ai.json", "ai_status.json"):
    try: _os.remove(_os.path.join(ROOT, "local/x/513", f))
    except FileNotFoundError: pass
pipeline._jobs.clear()
r4 = c.post("/jobs/513", json={**body, "upload": False}, headers=H)
assert r4.status_code == 202, r4.text
st4 = c.get("/jobs/513", headers=H).json()
assert st4["status"] == "done" and "result" not in st4, st4
written = [f for f in _os.listdir(_os.path.join(ROOT, "local/x/513")) if f.endswith(".json")]
assert written == [], f"backend must not write in upload=false mode: {written}"
res = c.get("/jobs/513/result", headers=H).json()
assert res["files"]["legalinfo"] == "local/x/513/legalinfo_ai.json" and res["legalinfo"]["owners"] and res["analysis"]["documents"]
for kind in ("chain_of_title", "search_report"):
    rd = c.post(f"/render/{kind}", json=res["analysis"], headers=H)
    assert rd.status_code == 200 and rd.content[:2] == b"PK", (kind, rd.status_code, rd.text[:200])
    assert rd.headers["x-file-name"] == f"{kind}_513.docx"
assert c.post("/render/other", json=res["analysis"], headers=H).status_code == 404
assert c.post("/render/chain_of_title", json={"x": 1}, headers=H).status_code == 400
print("n8n-upload mode OK: result + 2 rendered DOCX")
# current n8n workflow 1 (no api key): /analyse-lead + /status
pipeline._jobs.clear()
la = c.post("/analyse-lead/513")
assert la.status_code == 202 and la.json()["lead_id"] == 513, la.text
assert _os.path.exists(_os.path.join(ROOT, "2026/oct/513/legalinfo_ai.json")), "backend must save legalinfo_ai.json for the Supabase trigger"
ls = c.get("/status/513").json()
assert ls["status"] == "done", ls
pipeline._jobs[513]["status"] = "running"
assert c.get("/status/513").json()["status"] == "pending or not started"
assert c.post("/analyse-lead/513").json().get("lead_id") == 513      # n8n retries → no duplicate job
print("current n8n flow endpoints OK")
print("ALL API TESTS PASSED")
# every report-facing value must be English
import re as _re
for _f in ("2026/oct/513/legalinfo_ai.json",):
    _t = open(_os.path.join(ROOT, _f), encoding="utf-8").read()
    assert not _re.search(r"[ऀ-ॿ]", _t), f"Devanagari left in {_f}"
print("legalinfo is fully English")
