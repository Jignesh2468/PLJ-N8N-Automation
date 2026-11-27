"""Run the v2 engine on a development case on this PC (no API, nothing uploaded).

  python -m v2.run_local <case> [<case> …]
Reads   sample_out/<case>/legal_analysis_ai.json (or v2_stage1/evidence_read.json when the evidence reader was run),
        the case's search files (folder from sample_out/v2_cases.json), and — if present —
        sample_out/<case>/v2_engine/review_decisions.json (the advocate's choices, downloaded from the review page).
Writes  sample_out/<case>/v2_engine/{search_report.html, chain_of_title.html, review.html, result.json}.
"""
import glob
import json
import os
import sys

from .engine import SEARCH_EXT, analyse
from .run_stage2 import CASES, OUT, ROOT


def search_files(case: str):
    folder = os.path.join(ROOT, "sample cases", CASES[case])
    out = []
    for f in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        low = f.lower()
        in_search = "search" in os.path.dirname(low).replace("serach", "search")
        if (low.endswith(SEARCH_EXT) and in_search) or (low.endswith(".pdf") and in_search):
            out.append((os.path.basename(f), open(f, "rb").read()))
    return out


def run(case: str) -> dict:
    ev = os.path.join(OUT, case, "v2_stage1", "evidence_read.json")
    if os.path.exists(ev):
        from .extract_v2 import to_v2
        analysis = to_v2(json.load(open(ev, encoding="utf-8")))
    else:
        analysis = json.load(open(os.path.join(OUT, case, "legal_analysis_ai.json"), encoding="utf-8"))
    d = os.path.join(OUT, case, "v2_engine")
    os.makedirs(d, exist_ok=True)
    dec_path = os.path.join(d, "review_decisions.json")
    decisions = json.load(open(dec_path, encoding="utf-8")) if os.path.exists(dec_path) else None
    res = analyse(analysis, search_files(case), case, decisions)
    for k in ("search_report", "chain_of_title", "review"):
        open(os.path.join(d, f"{k}.html"), "w", encoding="utf-8").write(res[k])
    json.dump({k: res[k] for k in ("summary", "entries", "flags", "decisions_applied")},
              open(os.path.join(d, "result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    s = res["summary"]
    print(f"{case:20} period {s['period'][0]}–{s['period'][1]}  in report {s['confirmed']:3}  to decide {s['to_verify']:3}  "
          f"observations {s['observations']:3}  decisions applied {len(res['decisions_applied'])}")
    return res


if __name__ == "__main__":
    for c in sys.argv[1:] or [c for c in CASES if os.path.exists(os.path.join(OUT, c, "legal_analysis_ai.json"))]:
        run(c)
