"""v2 HTTP endpoints — mounted ONLY when V2_ENABLED=1 (see app/main.py). Production endpoints are untouched.

POST /v2/jobs/{lead_id}            (x-api-key)  run v2 on the case's saved reading + search files → {prefix}/v2/*
GET  /v2/jobs/{lead_id}            (x-api-key)  status / summary of the last v2 run
GET  /v2/review/{lead_id}?t=TOKEN               the advocate's review page (signed link, no API key needed)
POST /v2/review/{lead_id}?t=TOKEN               the advocate's choices → saved (kept as test data) → final reports

No Claude API call is made here: v2 reads the documents the live system has already read
({prefix}/legal_analysis_ai.json). Files written: {prefix}/v2/search_report.html, chain_of_title.html, review.html,
result.json, review_decisions.json and review_history/<time>.json.
"""
import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import config, supa

from .engine import SEARCH_EXT, analyse

router = APIRouter(prefix="/v2", tags=["v2"])
_STATUS: Dict[int, dict] = {}


def _secret() -> str:
    return os.environ.get("V2_REVIEW_SECRET") or config.api_key()


def review_token(lead_id: int) -> str:
    s = _secret()
    if not s:
        return ""
    return hmac.new(s.encode(), f"v2-review:{lead_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _check_token(lead_id: int, t: str):
    good = review_token(lead_id)
    if not good or not hmac.compare_digest(good, t or ""):
        raise HTTPException(403, "invalid review link")


def require_key(x_api_key: Optional[str] = Header(default=None)):
    expected = config.api_key()
    if expected and x_api_key != expected:
        raise HTTPException(401, "invalid or missing x-api-key")


def _prefix(lead_id: int, year: str = "", month: str = "") -> str:
    return supa.lead_prefix(supa.get_lead(lead_id), year, month)


def _base_url(request: Optional[Request]) -> str:
    env = os.environ.get("V2_PUBLIC_URL", "").rstrip("/")
    if env:
        return env
    return str(request.base_url).rstrip("/") if request else ""


def run_lead(lead_id: int, prefix: str, base_url: str = "") -> dict:
    """The whole v2 run for one case, from storage to storage."""
    analysis = supa.read_json(f"{prefix}/legal_analysis_ai.json")
    if not analysis:
        raise RuntimeError(f"no saved reading at {prefix}/legal_analysis_ai.json — run the live analysis first")
    files = []
    for f in supa.list_folder(f"{prefix}/uploads/search_docs"):
        name = f["name"]
        if name.lower().endswith(SEARCH_EXT + (".pdf",)):
            files.append((name, supa.download(f"{prefix}/uploads/search_docs/{name}")))
    decisions = supa.read_json(f"{prefix}/v2/review_decisions.json")
    action = f"{base_url}/v2/review/{lead_id}?t={review_token(lead_id)}" if base_url and review_token(lead_id) else ""
    res = analyse(analysis, files, str(lead_id), decisions, review_action=action)
    for k in ("search_report", "chain_of_title", "review"):
        supa.upload(f"{prefix}/v2/{k}.html", res[k].encode("utf-8"), "text/html; charset=utf-8")
    supa.upload_json(f"{prefix}/v2/result.json", {k: res[k] for k in ("summary", "entries", "flags", "decisions_applied")})
    return {"summary": res["summary"], "review_link": action,
            "files": [f"{prefix}/v2/{k}.html" for k in ("search_report", "chain_of_title", "review")]}


def _job(lead_id: int, prefix: str, base_url: str):
    _STATUS[lead_id] = {"status": "running", "prefix": prefix, "started": datetime.utcnow().isoformat()}
    try:
        out = run_lead(lead_id, prefix, base_url)
        _STATUS[lead_id] = {"status": "done", "prefix": prefix, **out, "finished": datetime.utcnow().isoformat()}
    except Exception as e:                       # reported in the status, never raised into the server
        _STATUS[lead_id] = {"status": "error", "prefix": prefix, "error": str(e)[:500]}


@router.post("/jobs/{lead_id}", dependencies=[Depends(require_key)])
async def start(lead_id: int, background: BackgroundTasks, request: Request, year: str = "", month: str = ""):
    prefix = await asyncio.to_thread(_prefix, lead_id, year, month)
    if _STATUS.get(lead_id, {}).get("status") == "running":
        return {"status": "running", "lead_id": lead_id}
    background.add_task(asyncio.to_thread, _job, lead_id, prefix, _base_url(request))
    return {"status": "started", "lead_id": lead_id, "prefix": prefix}


@router.get("/jobs/{lead_id}", dependencies=[Depends(require_key)])
async def status(lead_id: int):
    return _STATUS.get(lead_id) or {"status": "not started"}


@router.get("/review/{lead_id}", response_class=HTMLResponse)
async def review(lead_id: int, t: str = "", year: str = "", month: str = ""):
    _check_token(lead_id, t)
    prefix = await asyncio.to_thread(_prefix, lead_id, year, month)
    try:
        page = await asyncio.to_thread(supa.download, f"{prefix}/v2/review.html")
    except Exception:
        raise HTTPException(404, "no v2 review yet for this case — start /v2/jobs first")
    return HTMLResponse(page.decode("utf-8"))


@router.post("/review/{lead_id}")
async def submit_review(lead_id: int, request: Request, background: BackgroundTasks, t: str = "",
                        year: str = "", month: str = ""):
    _check_token(lead_id, t)
    body = await request.json()
    entries = {str(k): v for k, v in (body.get("entries") or {}).items() if v in ("include", "exclude")}
    record = {"entries": entries, "note": str(body.get("note") or "")[:4000], "saved": datetime.utcnow().isoformat()}
    prefix = await asyncio.to_thread(_prefix, lead_id, year, month)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    await asyncio.to_thread(supa.upload_json, f"{prefix}/v2/review_decisions.json", record)
    await asyncio.to_thread(supa.upload_json, f"{prefix}/v2/review_history/{stamp}.json", record)   # learning data
    background.add_task(asyncio.to_thread, _job, lead_id, prefix, _base_url(request))
    return {"status": "saved", "lead_id": lead_id, "decisions": len(entries)}
