"""Run the real FastAPI app on localhost with Supabase → local folder and Claude → Unit 115 mock (for n8n tests)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("PLJ_API_KEY", "test-key")
import uvicorn
from app import llm, main, pipeline, supa
from mock_unit115 import mock
from run_local import LocalStore
store = LocalStore(sys.argv[1]); llm.set_override(mock)
supa.get_lead = lambda lid: {"leadid": lid, "custname": "JAS", "createdt": "2026-10-01T10:00:00"}
supa.read_json = store.get_json
pipeline.SupabaseStore = lambda: type("S", (), {"list": lambda s, p: store.list(p), "get": lambda s, p: store.get(p),
    "put": lambda s, p, d, m: store.put(p, d, m), "get_json": lambda s, p: store.get_json(p),
    "save_legalinfo": lambda s, l, li: True, "delete": lambda s, p: store.delete(p)})()
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]) if len(sys.argv) > 2 else 8077, log_level=os.environ.get("LOG_LEVEL","warning"))
