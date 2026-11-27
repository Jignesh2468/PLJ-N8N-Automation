"""The v2 engine: one call for one case, from data (no file paths, no API).

    analyse(analysis, search_files, label, decisions=None) → {
        "search_report": html, "chain_of_title": html, "review": html,
        "flags": [...], "entries": [...], "summary": {...}}

  analysis      the saved reading of the case's documents (the live system's legal_analysis_ai.json), or v2
                documents already in v2 format (list) — e.g. from the evidence reader
  search_files  [(file name, bytes)] — the IGR search exports (.xls/.xlsx/.html) and the e-search receipts (.pdf)
  decisions     the advocate's review decisions {"entries": {"9664/2010": "include" | "exclude"}} — applied before the
                reports are written; the final reports follow the advocate, never the other way round
"""
from datetime import date
from typing import Dict, List, Optional, Tuple, Union

from app.common import docno_key
from app.extract import parse_search_receipt
from app.igr import coverage_years, normalise_rows, parse_igr_file

from .checks import chain_steps, group_flags, run_checks
from .from_saved import docs_from_analysis
from .report import English, chain_of_title, search_report
from .review_page import review_page
from .search import reconcile
from .stage1 import run_stage1

SEARCH_EXT = (".xls", ".xlsx", ".htm", ".html")


def read_search(files: List[Tuple[str, bytes]]):
    """IGR rows, the files they came from, the period stated on an e-search receipt, and the fee receipts."""
    rows, used, period, receipts = [], [], None, []
    for name, data in files:
        low = name.lower()
        if low.endswith(SEARCH_EXT):
            found = parse_igr_file(data, name)
            if found:
                rows += found
                used.append(name)
        elif low.endswith(".pdf"):
            r = parse_search_receipt(data, name)
            if r.get("period_from") and r.get("period_to"):
                period = (r["period_from"], r["period_to"])
            if r.get("grn") or r.get("receipt_no"):
                receipts.append(r)
    return normalise_rows(rows), used, period, receipts


def search_period(years: List[int], stated) -> Tuple[int, int]:
    """The period stated on the e-search receipt — or the standard 30-year search ending with the latest year —
    widened to every year for which search data was actually supplied."""
    end = max(years) if years else date.today().year
    period = stated or (end - 30, end)
    if years:
        period = (min(period[0], min(years)), max(period[1], max(years)))
    return period


def apply_decisions(stage2: dict, decisions: Optional[dict]) -> List[dict]:
    """The advocate decides: 'include' makes an entry part of the report, 'exclude' removes it. Returns the log."""
    log = []
    wanted = (decisions or {}).get("entries") or {}
    for d in stage2["decisions"].values():
        r = d["row"]
        key = f"{r['docno']}/{r['year']}"
        choice = wanted.get(key)
        if choice == "include" and d["tier"] != "confirmed":
            log.append({"entry": key, "from": d["tier"], "to": "confirmed"})
            d["tier"], d["why"] = "confirmed", d["why"] + ["included by the advocate on review"]
        elif choice == "exclude" and d["tier"] != "not":
            log.append({"entry": key, "from": d["tier"], "to": "not"})
            d["tier"], d["why"] = "not", d["why"] + ["excluded by the advocate on review"]
    return log


def analyse(analysis: Union[dict, list], search_files: List[Tuple[str, bytes]], label: str,
            decisions: Optional[dict] = None, review_action: str = "") -> dict:
    docs = analysis if isinstance(analysis, list) else docs_from_analysis(analysis)
    stage1 = run_stage1(docs)
    rows, used, stated, receipts = read_search(search_files)
    years = coverage_years(rows, used)
    period = search_period(years, stated)
    stage2 = reconcile(rows, stage1)
    decision_log = apply_decisions(stage2, decisions)
    steps = chain_steps(stage1, stage2)
    en = English(stage1)
    flags = group_flags(run_checks(stage1, stage2, years, period, nm=en.name, tx=en.text))
    offices = sorted({r.get("sro") for r in rows if r.get("sro")})
    entries = [{"entry": f"{d['row']['docno']}/{d['row']['year']}", "tier": d["tier"], "why": d["why"],
                "type": en.text(d["row"]["kind"]["label"]), "date": d["row"]["exec_date"] or d["row"]["reg_date"],
                "from": [en.name(p["name"]) for p in d["row"]["sellers"] if not p["confirming"]],
                "to": [en.name(p["name"]) for p in d["row"]["buyers"] if not p["confirming"]]}
               for d in stage2["decisions"].values() if d["tier"] in ("confirmed", "possible")]
    entries.sort(key=lambda e: (int(e["entry"].split("/")[1] or 0), e["entry"]))
    sr = search_report(label, stage1, stage2, steps, years, period, receipts, offices)
    ct = chain_of_title(label, stage1, steps, flags, period)
    return {
        "search_report": sr, "chain_of_title": ct,
        "review": review_page(label, entries, flags, review_action),
        "flags": flags, "entries": entries, "decisions_applied": decision_log,
        "summary": {"documents": len(docs), "search_rows": len(rows), "search_files": used, "period": list(period),
                    "confirmed": sum(1 for e in entries if e["tier"] == "confirmed"),
                    "to_verify": sum(1 for e in entries if e["tier"] == "possible"),
                    "observations": len(flags)},
    }
