"""Run Stage 1 on saved case readings (no API cost).

  python -m v2.run_stage1                 # every case in sample_out/
  python -m v2.run_stage1 <case>        # one case
Writes sample_out/<case>/v2_stage1/{stage1.json, review.html} and sample_out/v2_stage1_index.html.
Cases placed in sample_out/_holdout/ (lawyer-finalised, reserved for the final test) are never read here.
"""
import json
import os
import sys

from .from_saved import load_saved
from .review import E, render
from .stage1 import run_stage1

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "sample_out")


def run_case(case: str, evidence_mode: bool = False) -> dict:
    d = os.path.join(OUT, case, "v2_stage1")
    ev_path = os.path.join(d, "evidence_read.json")
    if evidence_mode or os.path.exists(ev_path):
        from .extract_v2 import to_v2           # exact page + quote (evidence reader already run for this case)
        docs = to_v2(json.load(open(ev_path, encoding="utf-8")))
    else:
        docs = load_saved(os.path.join(OUT, case, "legal_analysis_ai.json"))
    res = run_stage1(docs)
    os.makedirs(d, exist_ok=True)
    json.dump(res, open(os.path.join(d, "stage1.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    open(os.path.join(d, "review.html"), "w", encoding="utf-8").write(render(case, res))
    return res


def main(cases):
    rows = []
    for c in cases:
        r = run_case(c)
        lv = lambda l: sum(1 for f in r["flags"] if f["level"] == l)
        u = r["property"]["unit"]
        unit = u["unit_no"].get("value") or ("conflict" if u["unit_no"]["status"] == "conflict" else "—")
        rows.append(f"<tr><td><a href='{E(c)}/v2_stage1/review.html'>{E(c)}</a></td><td>{r['documents']}</td><td>{E(unit)}</td>"
                    f"<td>{len(r['property']['land']['parcels'])}</td><td>{len(r['parties'])}</td><td>{lv('conflict')}</td>"
                    f"<td>{lv('verify')}</td><td>{lv('missing')}</td></tr>")
        print(f"{c:22} docs={r['documents']:3} unit={unit!s:8} parcels={len(r['property']['land']['parcels']):2} "
              f"parties={len(r['parties']):3} conflict={lv('conflict')} verify={lv('verify')} missing={lv('missing')}")
    from .review import CSS
    open(os.path.join(OUT, "v2_stage1_index.html"), "w", encoding="utf-8").write(
        f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Stage 1 review</title><style>{CSS}</style></head><body><main><h1>Stage 1 — property model &amp; party table</h1>"
        f"<div class=sub>Development build, not production.</div><div class=wrap><table><tr><th>Case</th><th>Docs</th>"
        f"<th>Unit</th><th>Parcels</th><th>Parties</th><th>Conflicts</th><th>Verify</th><th>Missing</th></tr>"
        + "".join(rows) + "</table></div></main></body></html>")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--evidence"] or sorted(d for d in os.listdir(OUT) if not d.startswith("_")
                                  and os.path.exists(os.path.join(OUT, d, "legal_analysis_ai.json")))
    main(args)
