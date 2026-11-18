"""Re-run the CODE stages (IGR linking, chain, owner, flags, legalinfo, HTML) on cases already read by the AI in
sample_out/ — no API cost. Uses the documents the AI extracted and its IGR classes saved in legal_analysis_ai.json.

  python tests/recheck_saved.py                 # every case in sample_out/
  python tests/recheck_saved.py bindumati_yadav
Writes sample_out/<case>/recheck/ (legal_analysis_ai.json, legalinfo_ai.json, HTML reports)."""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from app import english, igr, legalinfo, merge  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "sample_out")
english._ai_map = lambda strings: {}          # no network: names already in English in the saved documents


def recheck(lead):
    base = os.path.join(OUT, lead)
    an0 = json.load(open(os.path.join(base, "legal_analysis_ai.json"), encoding="utf-8"))
    search = os.path.join(base, "local", "x", lead, "uploads", "search_docs")
    rows, files = [], []
    for f in sorted(glob.glob(os.path.join(search, "*"))):
        if f.lower().endswith((".xls", ".xlsx", ".html", ".htm")):
            found = igr.parse_igr_file(open(f, "rb").read(), os.path.basename(f))
            if found:
                rows += found
                files.append(os.path.basename(f))
    rows = igr.normalise_rows(rows)
    keep = ("ref", "doc_type", "category", "level", "exec_date", "reg_date", "doc_no_raw", "doc_number", "doc_year", "sro",
            "parties", "issued_by", "reference_no", "consideration", "market_value", "stamp_duty", "registration_fee",
            "property_text", "furnished", "execution_status", "pages", "parent_ref", "key_terms", "short_description", "file")
    papers = []
    for d in an0["documents"]:
        if d.get("source") == "igr":
            continue
        x = {k: d.get(k) for k in keep if k in d}
        x["execution_status"] = "draft" if d.get("draft") else (x.get("execution_status") or "")
        papers.append(x)
    classes = {e["id"]: {k: e.get(k) for k in ("id", "class", "level", "doc_type_en", "property_en", "unit_no", "building",
                                                  "reason", "discrepancy")} | {"parties": e.get("parties_en") or []}
               for e in an0["igr"]["entries"] + an0["igr"].get("project_entries", [])}
    subject = igr.set_row_stats(dict(an0["subject_property"]), rows)
    # re-apply the CURRENT code checks to the AI's own verdict (kept in the reason as 'code check: A→D')
    by_id = {r["id"]: r for r in rows}
    for c in classes.values():
        if "cited by number" in (c.get("reason") or ""):
            continue                 # linked by the papers' citation — kept as linked
        m = re.search(r"code check: ([A-F])→", c.get("reason") or "")
        if m and c["id"] in by_id:
            c["class"] = m.group(1)
            c["reason"] = c["reason"][:m.start()].rstrip(" |")
        # a link made AFTER the check (a deed cited by number in the papers) is applied again by build_analysis
        if c["id"] in by_id and "cited by number" not in (c.get("reason") or ""):
            igr.verify_class(by_id[c["id"]], c, subject)
    lead_info = {"leadid": lead, "custname": an0.get("applicant") or lead}
    # files the AI run set aside as another property keep that observation
    foreign = [{"file": m.group(1), "description": m.group(2)} for f in an0.get("flags", [])
               for m in [re.match(r"VERIFY: the document\(s\) in (.+?) relate to another property \((.*)\) and have not", f)] if m]
    an = merge.build_analysis(lead_info, {"search_from_year": an0["igr"].get("period_from"),
                                          "search_to_year": an0["igr"].get("period_to"), "foreign_files": foreign},
                              [{"documents": papers}], subject, rows, classes, an0["igr"].get("receipts", []), files)
    an = english.to_english(an)
    li = english.to_english(legalinfo.build_legalinfo(an, {"opinion_paragraph": ""}))
    out = os.path.join(base, "recheck")
    os.makedirs(out, exist_ok=True)
    for fn, obj in (("legal_analysis_ai.json", an), ("legalinfo_ai.json", li)):
        json.dump(obj, open(os.path.join(out, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    subprocess.run(["node", os.path.join(ROOT, "tools", "run_workflow2_offline.js"),
                    os.path.join(ROOT, "n8n", "2 PLJ Chain of Title + Search Report.json"), os.path.join(out, "legalinfo_ai.json")],
                   cwd=out, env={**os.environ, "CASE": lead}, capture_output=True)
    print(f"{lead}: owner={[o['name'] for o in li['owners']]} deal={li.get('deal_type')} "
          f"status='{li['status_of_owner']}' IGR A/B={sorted(f'{e['docno']}/{e['year']}' for e in an['igr']['entries'] if e['class'] in 'AB')}")


if __name__ == "__main__":
    for lead in sys.argv[1:] or sorted(d for d in os.listdir(OUT) if os.path.exists(os.path.join(OUT, d, "legal_analysis_ai.json"))):
        recheck(lead)
