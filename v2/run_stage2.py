"""Stage 2 runner: Stage-1 model + the case's IGR search files → reconciled search (no API cost).

  python -m v2.run_stage2                # every case that has a Stage-1 result
  python -m v2.run_stage2 <case>
Writes sample_out/<case>/v2_stage2/{stage2.json, search_review.html}.
"""
import glob
import json
import os
import sys

from app.extract import parse_search_receipt
from app.igr import coverage_years, normalise_rows, parse_igr_file

from .review import CSS, E
from .search import reconcile, year_table

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "sample_out")
# where each development case's files are: a LOCAL file (client names — never committed)
CASES_FILE = os.environ.get("V2_CASES_FILE") or os.path.join(OUT, "v2_cases.json")
CASES = json.load(open(CASES_FILE, encoding="utf-8")) if os.path.exists(CASES_FILE) else {}


def load_search(folder: str):
    rows, files, period = [], [], None
    for f in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        low = f.lower()
        if low.endswith((".xls", ".xlsx", ".htm", ".html")) and "search" in os.path.dirname(low).replace("serach", "search"):
            found = parse_igr_file(open(f, "rb").read(), os.path.basename(f))
            if found:
                rows += found
                files.append(os.path.basename(f))
        elif low.endswith(".pdf"):
            r = parse_search_receipt(open(f, "rb").read(), os.path.basename(f))
            if r.get("period_from") and r.get("period_to"):
                period = (r["period_from"], r["period_to"])
    return normalise_rows(rows), files, period


def _entry_line(d: dict) -> str:
    r = d["row"]
    nm = lambda ps: " & ".join(p["name"] for p in ps if not p["confirming"]) or "—"
    return (f"<b>{E(r['kind']['label'])}</b> — {E(nm(r['sellers']))} → {E(nm(r['buyers']))}, "
            f"Doc {E(r['docno'])}/{E(r['year'])}, {E(r['office'])}, registered {E(r['reg_date'])}"
            f"<div class=src>{E(r['docname'])} · {E('; '.join(d['why']))}</div>")


def render(case: str, res: dict, table: list, period, files) -> str:
    dec = list(res["decisions"].values())
    poss = sorted([d for d in dec if d["tier"] == "possible"], key=lambda d: (d["row"]["year"] or 0))
    nots = [d for d in dec if d["tier"] == "not"]
    s = res["subject"]
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Stage 2 — {E(case)}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Search reconciliation — {E(case)}</h1>",
           f"<div class=sub>{res['rows_total']} search entries in {len(files)} files · {res['candidates']} linked to the "
           f"property in some way · judged one by one · PLJ v2 Stage 2 (development build)</div>",
           "<h2>What the search was checked against (from the papers)</h2><div class=wrap><table>",
           f"<tr><td>Unit</td><td>{E(', '.join(s['units']) or 'not established')}</td></tr>",
           f"<tr><td>Building / society / project names</td><td>{E('; '.join(s['names']) or '—')}</td></tr>",
           f"<tr><td>Building no.</td><td>{E(s['building_no'] or '—')}</td></tr>",
           "<tr><td>Land parcels</td><td>" + "<br>".join(
               E(p['label']) + (f" <span class=verify>shared: {E(p['shared_reason'])}</span>" if p.get('shared_reason') else "")
               for p in s["parcels"]) + "</td></tr>",
           "<tr><td>Chain of owners (start)</td><td>" + "<br>".join(E(c["names"][0]) + (
               f" <span class=src>(added from {E(c['via'])})</span>" if c.get("via") else "") for c in res["chain"]) +
           "</td></tr></table></div>",
           f"<h2>Search Report — year by year {E(f'({period[0]}–{period[1]})' if period else '')}</h2>"
           "<div class=wrap><table><tr><th>Year</th><th>Registration entries</th></tr>"]
    for row in table:
        if row["status"] == "not searched":
            cell = "<span class=not_stated>Search not taken (no search data supplied for this year)</span>"
        elif row["status"] == "NIL":
            cell = "NIL"
        else:
            cell = "<br><br>".join(_entry_line(d) for d in row["entries"])
        out.append(f"<tr><td>{row['year']}</td><td>{cell}</td></tr>")
    out.append("</table></div>")
    out.append(f"<h2>Possible entries — for the lawyer to verify ({len(poss)})</h2>")
    out.append("<div class=wrap><table>" + ("".join(f"<tr><td>{E(d['row']['year'])}</td><td>{_entry_line(d)}</td></tr>"
                                                    for d in poss) or "<tr><td>None.</td></tr>") + "</table></div>")
    out.append(f"<h2>Not related — {len(nots)} linked entries rejected, with the reason (audit)</h2><div class=wrap><table>")
    for d in sorted(nots, key=lambda d: (d["row"]["year"] or 0))[:300]:
        out.append(f"<tr><td>{E(d['row']['year'])}</td><td>{_entry_line(d)}</td></tr>")
    out.append("</table></div></main></body></html>")
    return "\n".join(out)


def run_case(case: str) -> dict:
    stage1 = json.load(open(os.path.join(OUT, case, "v2_stage1", "stage1.json"), encoding="utf-8"))
    rows, files, period = load_search(os.path.join(ROOT, "sample cases", CASES[case]))
    years = coverage_years(rows, files)
    period = period or ((min(years), max(years)) if years else (0, -1))
    res = reconcile(rows, stage1)
    table = year_table(res, years, period)
    d = os.path.join(OUT, case, "v2_stage2")
    os.makedirs(d, exist_ok=True)
    slim = {"subject": {k: v for k, v in res["subject"].items() if k != "paper_docs"}, "chain": res["chain"],
            "period": period, "years_searched": years,
            "decisions": [{"tier": x["tier"], "why": x["why"], "doc": f"{x['row']['docno']}/{x['row']['year']}",
                           "kind": x["row"]["kind"]["label"], "date": x["row"]["reg_date"],
                           "from": [p["name"] for p in x["row"]["sellers"]], "to": [p["name"] for p in x["row"]["buyers"]]}
                          for x in res["decisions"].values()]}
    json.dump(slim, open(os.path.join(d, "stage2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    open(os.path.join(d, "search_review.html"), "w", encoding="utf-8").write(render(case, res, table, period, files))
    conf = sorted(f"{x['row']['docno']}/{x['row']['year']}" for x in res["decisions"].values() if x["tier"] == "confirmed")
    poss = sorted(f"{x['row']['docno']}/{x['row']['year']}" for x in res["decisions"].values() if x["tier"] == "possible")
    print(f"{case:20} rows={res['rows_total']:5} linked={res['candidates']:4} confirmed={conf} possible={poss}")
    return res


if __name__ == "__main__":
    cases = sys.argv[1:] or [c for c in CASES if not c.startswith("_")
                             and os.path.exists(os.path.join(OUT, c, "v2_stage1", "stage1.json"))]
    for c in cases:
        run_case(c)
