"""Stages 2–4 for a case: reconcile the search, run the checks, write the Search Report and Chain of Title (English).
No API use.

  python -m v2.run_reports                 # every case with a Stage-1 result
  python -m v2.run_reports <case>
Writes sample_out/<case>/v2_reports/{search_report.html, chain_of_title.html, flags.json}.
"""
import glob
import json
import os
import sys

from app.extract import parse_search_receipt
from app.igr import coverage_years

from .checks import chain_steps, run_checks
from .report import chain_of_title, search_report
from .run_stage2 import CASES, OUT, ROOT, load_search
from .search import reconcile


def run_case(case: str):
    stage1 = json.load(open(os.path.join(OUT, case, "v2_stage1", "stage1.json"), encoding="utf-8"))
    folder = os.path.join(ROOT, "sample cases", CASES[case])
    rows, files, period = load_search(folder)
    receipts = [r for f in glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True)
                for r in [parse_search_receipt(open(f, "rb").read(), os.path.basename(f))] if r.get("grn")]
    years = coverage_years(rows, files)
    # the period stated on the e-search receipt; otherwise the standard 30-year search ending with the latest year
    from datetime import date as _date
    end = max(years) if years else _date.today().year
    period = period or (end - 30, end)
    if years:                              # search files supplied for years outside the receipt's period: include them
        period = (min(period[0], min(years)), max(period[1], max(years)))
    stage2 = reconcile(rows, stage1)
    steps = chain_steps(stage1, stage2)
    from .report import English
    en = English(stage1)
    flags = run_checks(stage1, stage2, years, period, nm=en.name, tx=en.text)
    d = os.path.join(OUT, case, "v2_reports")
    os.makedirs(d, exist_ok=True)
    offices = sorted({r.get("sro") for r in rows if r.get("sro")})
    open(os.path.join(d, "search_report.html"), "w", encoding="utf-8").write(
        search_report(case, stage1, stage2, steps, years, period, receipts, offices))
    open(os.path.join(d, "chain_of_title.html"), "w", encoding="utf-8").write(chain_of_title(case, stage1, steps, flags, period))
    json.dump(flags, open(os.path.join(d, "flags.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lv = lambda l: sum(1 for f in flags if f["level"] == l)
    print(f"{case:20} steps={len(steps):3} flags: conflict={lv('conflict')} verify={lv('verify')} missing={lv('missing')}")


if __name__ == "__main__":
    cases = sys.argv[1:] or [c for c in CASES if not c.startswith("_")
                             and os.path.exists(os.path.join(OUT, c, "v2_stage1", "stage1.json"))]
    for c in cases:
        run_case(c)
