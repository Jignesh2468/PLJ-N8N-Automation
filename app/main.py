"""PLJ Legal AI backend — FastAPI.

POST /jobs/{lead_id}     n8n workflow 1: start the analysis (background). Body: {year, month, mode: json|full|reports,
                         bank_name, bank_branch, search_from_year, search_to_year, ref_no}. Writes legalinfo_ai.json.
POST /reports/{lead_id}  n8n workflow 2: build chain_of_title_{lead}.docx + search_report_{lead}.docx (synchronous).
                         Body: {prefix: "2026/oct/513"} (folder of legalinfo_ai.json) or {year, month}
GET  /jobs/{lead_id}     job status (?year=&month= to read the persisted status after a restart)
GET  /health             no auth
Legacy aliases kept so old callers don't break: POST /analyse-lead/{id}, GET /status/{id}, POST /webhook/storage (no-op)
"""
import asyncio
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import config, pipeline, supa

app = FastAPI(title="PLJ Legal AI", version="1.0-final")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

# v2 engine (evidence-based) — mounted only when explicitly switched on; production endpoints below are unchanged
if config.env("V2_ENABLED") == "1":
    from v2.api import router as _v2_router
    app.include_router(_v2_router)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    expected = config.api_key()
    if expected and x_api_key != expected:
        raise HTTPException(401, "invalid or missing x-api-key")


class JobRequest(BaseModel):
    year: Optional[str] = ""
    month: Optional[str] = ""
    mode: Optional[str] = "json"          # json (default) | full | reports
    request_type: Optional[str] = ""      # accepted from the portal payload; "reports" → mode=reports
    bank_name: Optional[str] = ""
    bank_branch: Optional[str] = ""
    search_from_year: Optional[int] = None
    search_to_year: Optional[int] = None
    ref_no: Optional[str] = ""
    force: Optional[bool] = False
    upload: Optional[bool] = True          # false = n8n uploads the files; fetch them from /jobs/{id}/result


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version,
            "anthropic_key": bool(config.anthropic_key()), "supabase": bool(config.supabase_url() and config.supabase_key()),
            "openai_key": bool(config.openai_key()), "api_key_required": bool(config.api_key()),
            "models": {"claude": config.CLAUDE_MODEL, "openai": config.OPENAI_MODEL},
            "routing": {"pdf_ocr": "claude", "image_ocr": config.LLM_EXTRACT_IMAGE,
                        "docx_text": config.LLM_EXTRACT_TEXT, "igr_matching": config.LLM_IGR,
                        "drafting": config.LLM_DRAFT, "fallback": config.LLM_FALLBACK}}


@app.post("/jobs/{lead_id}", dependencies=[Depends(require_key)])
async def start_job(lead_id: int, background: BackgroundTasks, req: Optional[JobRequest] = None):
    req = req or JobRequest()
    try:
        lead = await asyncio.to_thread(supa.get_lead, lead_id)
    except supa.SupabaseError as e:
        raise HTTPException(404 if "not found" in str(e) else 502, str(e))
    prefix = supa.lead_prefix(lead, req.year or "", req.month or "")
    mode = "reports" if (req.mode == "reports" or req.request_type == "reports") else (
        "full" if req.mode == "full" else "json")
    claimed, status = pipeline.claim(lead_id, prefix, mode)
    if not claimed:
        return JSONResponse({"accepted": False, "already_running": True, **status}, status_code=200)
    opts = {"prefix": prefix, "mode": mode, "bank_name": req.bank_name or "", "bank_branch": req.bank_branch or "",
            "search_from_year": req.search_from_year, "search_to_year": req.search_to_year, "ref_no": req.ref_no or "",
            "upload": req.upload is not False}
    background.add_task(pipeline.run_job, lead, opts)  # sync → runs in threadpool
    return JSONResponse({"accepted": True, **status}, status_code=202)


class ReportRequest(BaseModel):
    prefix: Optional[str] = ""
    year: Optional[str] = ""
    month: Optional[str] = ""
    bank_name: Optional[str] = ""
    bank_branch: Optional[str] = ""
    ref_no: Optional[str] = ""
    redraft: Optional[bool] = False


@app.post("/reports/{lead_id}", dependencies=[Depends(require_key)])
async def build_reports(lead_id: int, req: Optional[ReportRequest] = None):
    req = req or ReportRequest()
    prefix = (req.prefix or "").strip("/")
    if not prefix:
        try:
            lead = await asyncio.to_thread(supa.get_lead, lead_id)
        except supa.SupabaseError as e:
            raise HTTPException(404, str(e))
        prefix = supa.lead_prefix(lead, req.year or "", req.month or "")
    if not prefix.endswith(f"/{lead_id}"):
        raise HTTPException(400, f"prefix '{prefix}' does not belong to lead {lead_id}")
    opts = {"bank_name": req.bank_name or "", "bank_branch": req.bank_branch or "", "ref_no": req.ref_no or "",
            "redraft": bool(req.redraft)}
    try:
        out = await asyncio.to_thread(pipeline.render_reports, prefix, pipeline.SupabaseStore(), opts)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"status": "done", "lead_id": lead_id, "prefix": prefix, **out}


@app.get("/jobs/{lead_id}/result", dependencies=[Depends(require_key)])
async def job_result(lead_id: int):
    """legalinfo + analysis JSON of a finished job started with upload=false (n8n uploads them to Supabase)."""
    st = pipeline.get_status(lead_id)
    if not st or st.get("status") != "done":
        raise HTTPException(409, f"job for lead {lead_id} is not done (status: {st.get('status') if st else 'not_started'})")
    res = pipeline.get_result(lead_id)
    if not res:
        raise HTTPException(404, "job finished with upload=true — files are already in storage")
    return {"lead_id": lead_id, "files": st["outputs"].get("files", {}), **res}


@app.post("/render/{kind}", dependencies=[Depends(require_key)])
async def render(kind: str, request: Request, bank_name: str = "", bank_branch: str = "", ref_no: str = ""):
    """Body = legal_analysis_ai.json content. Returns ONE .docx (kind = chain_of_title | search_report)."""
    if kind not in ("chain_of_title", "search_report"):
        raise HTTPException(404, "kind must be chain_of_title or search_report")
    try:
        analysis = await request.json()
    except Exception:
        raise HTTPException(400, "body must be the legal_analysis_ai.json content")
    if not isinstance(analysis, dict) or "documents" not in analysis:
        raise HTTPException(400, "body is not a legal_analysis_ai.json (no 'documents')")
    try:
        data, fname = await asyncio.to_thread(pipeline.render_one, analysis, kind,
                                              {"bank_name": bank_name, "bank_branch": bank_branch, "ref_no": ref_no})
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return Response(content=data, media_type=pipeline.DOCX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-File-Name": fname})


@app.get("/jobs/{lead_id}", dependencies=[Depends(require_key)])
async def job_status(lead_id: int, year: str = "", month: str = ""):
    st = pipeline.get_status(lead_id)
    if st:
        return st
    # after a restart: read the persisted status file
    try:
        lead = await asyncio.to_thread(supa.get_lead, lead_id)
        prefix = supa.lead_prefix(lead, year, month)
        saved = await asyncio.to_thread(supa.read_json, f"{prefix}/{config.STATUS_FILE}")
    except supa.SupabaseError as e:
        raise HTTPException(502, str(e))
    if saved:
        if saved.get("status") in ("queued", "running"):
            saved["status"] = "error"
            saved["error"] = "Server restarted while the job was running — start the job again."
        return saved
    return {"lead_id": lead_id, "status": "not_started"}


# ── endpoints called by the CURRENT n8n "PLj ai json" workflow (unchanged flow) ────────────
# That flow sends no x-api-key, so these two stay open. It loops while status == "pending or not started".
@app.post("/analyse-lead/{lead_id}")
async def legacy_analyse(lead_id: int, background: BackgroundTasks):
    # mode json + upload=True: backend saves legal_analysis_ai.json then legalinfo_ai.json to Supabase;
    # the legalinfo upload fires the Supabase trigger → "PLJ — Chain of Title" workflow (chain of title + search report)
    return await start_job(lead_id, background, JobRequest(mode="json", upload=True))


@app.get("/status/{lead_id}")
async def legacy_status(lead_id: int):
    st = await job_status(lead_id)
    st = dict(st)
    if st.get("status") in ("queued", "running", "not_started"):
        st["status"] = "pending or not started"
    return st


@app.post("/webhook/storage")
async def storage_webhook(request: Request):
    # Intentionally does nothing: per-file triggers caused one full AI run per uploaded file.
    # Jobs now start only from the portal → n8n → POST /jobs/{lead_id}.
    return {"skipped": True, "reason": "storage-triggered runs are disabled; start jobs via /jobs/{lead_id}"}
