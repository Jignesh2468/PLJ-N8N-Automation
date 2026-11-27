"""Stage 1 review page: the Property Model and Party Table of one case, every value with its sources, and every
flag — for a lawyer to check against the papers. Local HTML file (client data: not published anywhere)."""
import html
from typing import List

E = lambda s: html.escape(str(s if s is not None else ""))

CSS = """
:root{--bg:#fbfaf7;--fg:#1c1b19;--mute:#6b6860;--line:#dedad2;--card:#fff;--ok:#2f6b3a;--warn:#9a5b00;--bad:#a12a2a;--accent:#27466e}
@media (prefers-color-scheme:dark){:root{--bg:#181816;--fg:#ecebe6;--mute:#a19e95;--line:#3a3934;--card:#22221f;--ok:#7cc48a;--warn:#e0a54a;--bad:#ef7b7b;--accent:#8fb4e6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;
border-bottom:1px solid var(--line);padding-bottom:6px}.sub{color:var(--mute);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);font-size:14px}
th,td{text-align:left;vertical-align:top;padding:7px 9px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--mute);font-weight:600}
.src{color:var(--mute);font-size:12px}.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid currentColor;margin-right:4px}
.confirmed{color:var(--ok)}.conflict{color:var(--bad)}.not_stated,.missing{color:var(--mute)}.verify{color:var(--warn)}
.flag{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);padding:8px 10px;margin:6px 0}
.flag.conflict{border-left-color:var(--bad)}.flag.missing{border-left-color:var(--mute)}
.note{background:var(--card);border:1px solid var(--line);padding:10px 12px;font-size:13px;color:var(--mute)}
.wrap{overflow-x:auto}
"""


def _srcs(lst: List[str], n: int = 4) -> str:
    if not lst:
        return ""
    more = f" (+{len(lst) - n} more)" if len(lst) > n else ""
    return "<div class=src>" + "; ".join(E(x) for x in lst[:n]) + more + "</div>"


def _field(label: str, r: dict) -> str:
    from .evidence import cite
    st = r.get("status", "not_stated")
    if st == "confirmed":
        val = E(r["value"]) + _srcs([cite(e) for e in r["evidence"]])
    elif st == "conflict":
        val = "<br>".join(f"<b>{E(c['value'])}</b>{_srcs([cite(e) for e in c['evidence']], 3)}" for c in r["candidates"])
    else:
        val = "<span class=not_stated>not stated in the documents</span>"
    return f"<tr><td>{E(label)}</td><td><span class='tag {st}'>{E(st.replace('_', ' '))}</span></td><td>{val}</td></tr>"


def render(case: str, res: dict) -> str:
    m, parties, flags = res["property"], res["parties"], res["flags"]
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Stage 1 — {E(case)}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Property model &amp; party table — {E(case)}</h1>",
           f"<div class=sub>{res['documents']} documents read · {len(parties)} parties · {len(flags)} flags · "
           f"PLJ v2 Stage 1 (development build — not production)</div>"]
    if not res.get("page_exact"):
        out.append("<p class=note>Sources show the document and its <b>page range</b> (from the saved readings). "
                   "Exact page and quote for each fact need the evidence reader (not yet run for this case).</p>")
    out.append("<h2>Flags for the lawyer</h2>")
    if not flags:
        out.append("<p class=sub>None.</p>")
    for f in flags:
        out.append(f"<div class='flag {E(f['level'])}'><span class='tag {E(f['level'])}'>{E(f['level'])}</span>"
                   f"<b>{E(f['topic'])}</b> — {E(f['text'])}{_srcs(f.get('sources') or [], 3)}</div>")

    out.append("<h2>Unit</h2><div class=wrap><table><tr><th>Field</th><th>Status</th><th>Value and sources</th></tr>")
    for k, r in m["unit"].items():
        out.append(_field(k.replace("_", " ").title(), r))
    out.append("</table></div>")

    b = m["building"]
    out.append("<h2>Building / society / project</h2><div class=wrap><table><tr><th>Field</th><th>Status</th><th>Value and sources</th></tr>")
    out.append(_field("Building number", b["number"]))
    out.append("</table></div><div class=wrap><table style='margin-top:8px'><tr><th>Name</th><th>Used as</th><th>Documents</th></tr>")
    for n in b["names"] or [{"name": "—", "used_as": [], "sources": [], "documents": 0}]:
        out.append(f"<tr><td>{E(n['name'])}</td><td>{E(', '.join(n['used_as']))}</td><td>{n['documents']}{_srcs(n['sources'], 3)}</td></tr>")
    out.append("</table></div>")

    land = m["land"]
    out.append("<h2>Land</h2><div class=wrap><table><tr><th>Field</th><th>Status</th><th>Value and sources</th></tr>")
    for k in ("village", "taluka", "district", "pin"):
        out.append(_field(k.title(), land[k]))
    out.append("</table></div><div class=wrap><table style='margin-top:8px'><tr><th>Parcel</th><th>Extent</th>"
               "<th>In unit's description</th><th>Documents</th></tr>")
    for p in land["parcels"]:
        out.append(f"<tr><td>{E(p['kind'])} {E(p['number'])}</td><td>{E(p['extent'])}</td>"
                   f"<td>{'yes' if p['in_unit_documents'] else '<span class=verify>no — land papers only</span>'}</td>"
                   f"<td>{p['documents']}{_srcs(p['sources'], 3)}</td></tr>")
    if not land["parcels"]:
        out.append("<tr><td colspan=4 class=not_stated>No parcel numbers stated.</td></tr>")
    out.append("</table></div>")

    pr = m["project"]
    out.append("<h2>Project</h2><div class=wrap><table><tr><th>Item</th><th>Value and sources</th></tr>")
    devs = "<br>".join(f"{E(d['name'])} <span class=src>({E(d['id'] or 'not in party table')})</span>{_srcs(d['sources'], 2)}"
                       for d in pr["developers"]) or "<span class=not_stated>not stated</span>"
    out.append(f"<tr><td>Developer / promoter</td><td>{devs}</td></tr>")
    sch = "<br>".join(f"{E(s['scheme'])}{_srcs(s['documents'], 2)}" for s in pr["scheme_signals"]) or "<span class=not_stated>no signal</span>"
    out.append(f"<tr><td>Scheme signals</td><td>{sch}<div class=src>Signals only: words found in the papers, for the "
               f"lawyer to confirm.</div></td></tr></table></div>")

    h = m.get("present_holder")
    out.append("<h2>Present holder (per the latest document of the unit)</h2>")
    if h:
        out.append(f"<div class=wrap><table><tr><th>Holder(s)</th><th>Document</th><th>Status</th></tr><tr><td>"
                   f"{'<br>'.join(E(x['name']) + f' <span class=src>({E(x[chr(105)+chr(100)])})</span>' for x in h['holders']) or '—'}</td>"
                   f"<td>{E(h['document'])}<div class=src>{E(h['date'])} {E(h['doc_no'])}</div></td><td>{E(h['status'] or 'not stated')}</td></tr></table></div>")
    else:
        out.append("<p class=not_stated>Not established from the documents.</p>")

    out.append("<h2>Title history recited in the papers</h2><div class=wrap><table><tr><th>Date</th><th>Document</th>"
               "<th>From</th><th>To</th><th>Copy</th></tr>")
    nm = lambda xs: "<br>".join(E(x["name"]) + (f" <span class=src>({E(x['id'])})</span>" if x.get("id") else "") for x in xs) or "—"
    for t in m["title_history"]:
        out.append(f"<tr><td>{E(t['date'] or '—')}</td><td>{E(t['type'])}<div class=src>{E(t['doc_no'])} {E(t['office'])} · "
                   f"{E(t['level'])}</div>{_srcs([t['source']], 1)}</td><td>{nm(t['from'])}</td><td>{nm(t['to'])}</td>"
                   f"<td>{'furnished' if t['copy_furnished'] else 'recited only'}</td></tr>")
    out.append("</table></div>")

    out.append("<h2>Party table</h2><div class=wrap><table><tr><th>ID</th><th>Party</th><th>Kind</th><th>Roles</th>"
               "<th>Signatories</th><th>Other spellings / former names</th></tr>")
    for p in parties:
        roles = sorted({r["family"] for r in p["roles"]})
        spell = [s["name"] for s in p["spellings"] if s["name"] != p["name"]]
        former = [f"formerly: {f['name']}" for f in p["former_names"]]
        out.append(f"<tr><td>{E(p['id'])}</td><td><b>{E(p['name'])}</b>{_srcs([s for x in p['spellings'] for s in x['sources']], 2)}</td>"
                   f"<td>{E(p['kind'])}</td><td>{E(', '.join(roles))}</td>"
                   f"<td>{'<br>'.join(E(s['name']) for s in p['signatories']) or '—'}</td>"
                   f"<td>{'<br>'.join(E(x) for x in spell + former) or '—'}</td></tr>")
    out.append("</table></div></main></body></html>")
    return "\n".join(out)
