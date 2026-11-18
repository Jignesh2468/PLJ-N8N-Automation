"""Job runner: one lead → legalinfo_ai.json + legal_analysis_ai.json + Search Report + Title Report.

Storage layout (unchanged from the portal):
  {year}/{month}/{lead_id}/uploads/prop_docs/*.pdf|docx|jpg|png
  {year}/{month}/{lead_id}/uploads/search_docs/*.xls|xlsx|pdf   (IGR exports + e-Search receipts/challans)
Outputs:
  {year}/{month}/{lead_id}/legalinfo_ai.json
  {year}/{month}/{lead_id}/legal_analysis_ai.json
  {year}/{month}/{lead_id}/ai_status.json
  {year}/{month}/{lead_id}/reports/search_report_{lead}.docx
  {year}/{month}/{lead_id}/reports/title_report_{lead}.docx
"""
import json
import threading
import traceback
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from . import english, config, drafting, extract, igr, legalinfo, merge, reports

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_jobs: Dict[int, dict] = {}
_lock = threading.Lock()


# ── status ────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def is_running(lead_id: int) -> bool:
    j = _jobs.get(lead_id)
    if not j or j.get("status") not in ("queued", "running"):
        return False
    started = datetime.fromisoformat(j["started_at"].rstrip("Z"))
    return datetime.utcnow() - started < timedelta(minutes=config.JOB_STALE_MINUTES)


def claim(lead_id: int, prefix: str, mode: str) -> Tuple[bool, dict]:
    """Atomically mark a lead as queued. Returns (claimed, status)."""
    with _lock:
        if is_running(lead_id):
            return False, _jobs[lead_id]
        _jobs[lead_id] = {"lead_id": lead_id, "status": "queued", "step": "queued", "mode": mode,
                          "prefix": prefix, "started_at": _now(), "updated_at": _now(), "outputs": {}, "error": ""}
        return True, _jobs[lead_id]


def get_status(lead_id: int) -> Optional[dict]:
    j = _jobs.get(lead_id)
    return {k: v for k, v in j.items() if k != "result"} if j else None


def get_result(lead_id: int) -> Optional[dict]:
    j = _jobs.get(lead_id)
    return j.get("result") if j else None


# ── the pipeline (pure; storage passed in so it can run locally too) ─────────
class Storage:
    """Interface: list(prefix)->[names], get(path)->bytes, put(path, bytes, mime)->str, get_json(path)."""


def run_pipeline(lead: dict, opts: dict, store, progress: Callable[[str], None] = lambda s: None) -> dict:
    """mode = json    → analysis + legalinfo_ai.json only (n8n workflow 2 then builds the reports)
       mode = full    → json + both DOCX reports in one go
       mode = reports → only rebuild DOCX reports from the saved analysis"""
    prefix = opts["prefix"]
    mode = opts.get("mode", "json")
    if mode == "reports":
        return render_reports(prefix, store, opts, progress)

    lead_id = lead["leadid"]
    outputs = {}
    # 1. list inputs
    progress("listing files")
    prop_files = [n for n in store.list(f"{prefix}/uploads/prop_docs")
                  if n.lower().endswith((".pdf", ".docx", ".jpg", ".jpeg", ".png"))]
    search_files = store.list(f"{prefix}/uploads/search_docs")
    if not prop_files:
        raise RuntimeError(f"No property documents in {prefix}/uploads/prop_docs")

    # 2. read every property document
    extractions = []
    for i, name in enumerate(sorted(prop_files), 1):
        progress(f"reading document {i}/{len(prop_files)}: {name}")
        extractions.append(extract.extract_file(store.get(f"{prefix}/uploads/prop_docs/{name}"), name))
    subject = extract.merge_subjects_ranked(extractions)     # the registered deed's description wins

    # 3. IGR search data + receipts
    progress("parsing IGR search data")
    rows, receipts, igr_files = [], [], []
    for name in sorted(search_files):
        data = store.get(f"{prefix}/uploads/search_docs/{name}")
        if name.lower().endswith((".xls", ".xlsx", ".html", ".htm")):
            found = igr.parse_igr_file(data, name)
            if found:                                  # an earlier report or a tracker saved here is not search data
                rows += found
                igr_files.append(name)
        elif name.lower().endswith(".pdf"):
            r = extract.parse_search_receipt(data, name)
            if r.get("receipt_no") or r.get("grn"):
                receipts.append(r)
            else:                                      # an Index-II or another document uploaded with the search
                progress(f"reading document from the search folder: {name}")
                extractions.append(extract.extract_file(data, name))
    # a file about ANOTHER property (a stray agreement in the bundle) must never enter the chain or the subject
    extractions, foreign = extract.split_foreign(extractions, lead.get("custname") or "")
    opts = {**opts, "foreign_files": foreign}
    subject = extract.merge_subjects_ranked(extractions)
    rows = igr.normalise_rows(rows)
    chain_parties = sorted({p["name"] for e in extractions for d in e["documents"] for p in d.get("parties") or []
                            if isinstance(p, dict) and p.get("name")})
    subject = igr.set_row_stats(subject, rows)          # layout-wide CTS / common names can't identify the property
    keys = igr.build_keys(subject, chain_parties)
    candidates, eligible = igr.prefilter(rows, keys)
    progress(f"classifying {len(candidates)} IGR entries (of {len(rows)})")
    classes = igr.classify(candidates, subject, chain_parties) if candidates else {}

    # 4. merge (code only)
    progress("building chain of title")
    analysis = merge.build_analysis(lead, opts, extractions, subject, rows, classes, receipts, igr_files)
    ai_el, ai_sent = getattr(igr.classify, "ai_eligible", 0), getattr(igr.classify, "ai_sent", 0)
    if ai_el > ai_sent:
        analysis["flags"].append(f"VERIFY: {ai_el - ai_sent} of {ai_el} undecided IGR entries were matched by the printed "
                                 f"survey / unit / building identifiers only (AI limit IGR_MAX_CANDIDATES = "
                                 f"{config.IGR_MAX_CANDIDATES}); those entries to be checked with the search data.")
    analysis["prefix"] = prefix
    analysis["ref_no"] = opts.get("ref_no") or ""

    # 4b. everything report-facing in English (Marathi IGR names / descriptions / document types)
    progress("converting Marathi text to English")
    analysis = english.to_english(analysis)

    # 5. drafting (AI, from verified data only)
    progress("drafting legal text")
    drafted = english.to_english(drafting.draft(analysis))
    analysis["drafted"] = drafted

    # 6. outputs — final guard: no Devanagari may reach legalinfo_ai.json
    li = english.to_english(legalinfo.build_legalinfo(analysis, drafted))
    if not opts.get("upload", True):
        # n8n uploads the files itself (exact Supabase storage URLs); hand the content back
        outputs["summary"] = _summary(analysis)
        outputs["files"] = {"analysis": f"{prefix}/{config.ANALYSIS_FILE}",
                            "legalinfo": f"{prefix}/{config.LEGALINFO_FILE}"}
        outputs["_result"] = {"analysis": json.loads(_json_bytes(analysis)), "legalinfo": li}
        return outputs
    # backend uploads (local runs / legacy) — analysis FIRST, legalinfo LAST (it fires the report trigger)
    progress("saving legalinfo_ai.json")
    outputs["analysis"] = store.put(f"{prefix}/{config.ANALYSIS_FILE}", _json_bytes(analysis), "application/json")
    if mode == "full":
        outputs.update(render_reports(prefix, store, opts, progress, analysis=analysis))
    if hasattr(store, "delete"):
        store.delete(f"{prefix}/{config.LEGALINFO_FILE}")  # so the upload is always an INSERT event
    outputs["legalinfo"] = store.put(f"{prefix}/{config.LEGALINFO_FILE}", _json_bytes(li), "application/json")
    if hasattr(store, "save_legalinfo"):
        outputs["lead_db_updated"] = store.save_legalinfo(lead_id, li)
    outputs["summary"] = _summary(analysis)
    return outputs


def _summary(analysis: dict) -> dict:
    d = analysis.get("drafted") or {}
    return {"owner": analysis["current_owner"]["names"], "documents": len(analysis["documents"]),
            "igr_entries": len(analysis["igr"]["entries"]), "missing_years": analysis["igr"]["missing_years"],
            "flags": len(analysis["flags"]), "title_status": d.get("title_status"),
            "unverified_numbers": d.get("unverified_numbers", [])}


def render_one(analysis: dict, kind: str, opts: Optional[dict] = None) -> Tuple[bytes, str]:
    """Render a single DOCX from an analysis dict (no storage access). kind: chain_of_title | search_report."""
    opts = opts or {}
    if not analysis.get("drafted"):
        analysis["drafted"] = drafting.draft(analysis)
    bank = analysis.get("bank") or {}
    analysis["bank"] = {"name": opts.get("bank_name") or bank.get("name", ""),
                        "branch": opts.get("bank_branch") or bank.get("branch", "")}
    lead_id = analysis.get("lead_id") or "lead"
    now = datetime.now()
    if kind == "chain_of_title":
        ref = opts.get("ref_no") or analysis.get("ref_no") or f"PLJ/{lead_id}/{now.strftime('%b').upper()}/{now.year}"
        return reports.title_report(analysis, analysis["drafted"], ref), f"chain_of_title_{lead_id}.docx"
    if kind == "search_report":
        return reports.search_report(analysis, analysis["drafted"]), f"search_report_{lead_id}.docx"
    raise ValueError(f"unknown report kind '{kind}'")


def render_reports(prefix: str, store, opts: dict, progress: Callable[[str], None] = lambda s: None,
                   analysis: Optional[dict] = None) -> dict:
    """Build the two DOCX files, separately, in {prefix}/reports/:
         chain_of_title_{lead}.docx  (Special Report on Title + Certificate + Chain of Title)
         search_report_{lead}.docx   (year-wise IGR search report)"""
    if analysis is None:
        analysis = store.get_json(f"{prefix}/{config.ANALYSIS_FILE}")
        if not analysis:
            raise RuntimeError(f"{config.ANALYSIS_FILE} not found in {prefix} — run the analysis (workflow 1) first.")
    bank = analysis.get("bank") or {}
    analysis["bank"] = {"name": opts.get("bank_name") or bank.get("name", ""),
                        "branch": opts.get("bank_branch") or bank.get("branch", "")}
    if not analysis.get("drafted") or opts.get("redraft"):
        progress("drafting legal text")
        analysis["drafted"] = drafting.draft(analysis)
        store.put(f"{prefix}/{config.ANALYSIS_FILE}", _json_bytes(analysis), "application/json")
    drafted = analysis["drafted"]
    lead_id = analysis.get("lead_id") or prefix.rstrip("/").split("/")[-1]
    now = datetime.now()
    ref = opts.get("ref_no") or analysis.get("ref_no") or f"PLJ/{lead_id}/{now.strftime('%b').upper()}/{now.year}"
    progress("rendering reports")
    out = {
        "chain_of_title": store.put(f"{prefix}/{config.REPORTS_DIR}/chain_of_title_{lead_id}.docx",
                                    reports.title_report(analysis, drafted, ref), DOCX_MIME),
        "search_report": store.put(f"{prefix}/{config.REPORTS_DIR}/search_report_{lead_id}.docx",
                                   reports.search_report(analysis, drafted), DOCX_MIME),
    }
    out["summary"] = _summary(analysis)
    return out


def _json_bytes(obj) -> bytes:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")


# ── Supabase-backed store + background runner ─────────────────────────────────
class SupabaseStore:
    def __init__(self):
        from . import supa
        self.s = supa

    def list(self, prefix: str) -> List[str]:
        return [f["name"] for f in self.s.list_folder(prefix)]

    def get(self, path: str) -> bytes:
        return self.s.download(path)

    def put(self, path: str, data: bytes, mime: str) -> str:
        return self.s.upload(path, data, mime)

    def get_json(self, path: str):
        return self.s.read_json(path)

    def delete(self, path: str):
        self.s.delete(path)

    def save_legalinfo(self, lead_id: int, li: dict) -> bool:
        return self.s.save_legalinfo_to_lead(lead_id, li)


def _persist_status(store, job: dict):
    try:
        store.put(f"{job['prefix']}/{config.STATUS_FILE}", _json_bytes(job), "application/json")
    except Exception as e:  # status persistence must never kill the job
        print(f"[STATUS] could not persist: {e}")


def run_job(lead: dict, opts: dict):
    lead_id = lead["leadid"]
    job = _jobs[lead_id]
    store = SupabaseStore()
    persist = opts.get("upload", True)   # when n8n does the uploads, the backend writes nothing to storage

    def progress(step: str):
        job.update(status="running", step=step, updated_at=_now())
        print(f"[JOB {lead_id}] {step}")
        if persist:
            _persist_status(store, job)

    try:
        outputs = run_pipeline(lead, opts, store, progress)
        job["result"] = outputs.pop("_result", None)
        job.update(status="done", step="completed", outputs=outputs, updated_at=_now(), finished_at=_now())
    except Exception as e:
        traceback.print_exc()
        job.update(status="error", step=job.get("step", ""), error=f"{type(e).__name__}: {e}",
                   updated_at=_now(), finished_at=_now())
    if persist:
        _persist_status(store, job)
