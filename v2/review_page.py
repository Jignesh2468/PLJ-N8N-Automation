"""The advocate's review page: the only human step.

It shows (1) the entries the Search Report will contain — each can be REMOVED, (2) the entries that MAY concern the
property — each can be INCLUDED, and (3) the observations for information. The advocate's choices are sent as
{"entries": {"9664/2010": "include" | "exclude"}, "note": "..."}; the final reports are regenerated from them, and the
choices are kept as test data, so the system is checked against every advocate correction from then on.
Without a server address (local review), the page lets the advocate download the choices as a file.
"""
import html
import json
from typing import List

E = lambda s: html.escape(str(s if s is not None else ""))

CSS = """
:root{--bg:#f7f6f2;--fg:#1d1c1a;--mute:#6b6860;--line:#dcd8cf;--card:#fff;--ok:#2f6b3a;--warn:#9a5b00;--bad:#a12a2a;
--accent:#27466e}@media (prefers-color-scheme:dark){:root{--bg:#171715;--fg:#ecebe6;--mute:#a19e95;--line:#3a3934;
--card:#22221f;--ok:#7cc48a;--warn:#e0a54a;--bad:#ef7b7b;--accent:#8fb4e6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:1000px;margin:0 auto;padding:24px 16px 96px}h1{font-size:21px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 8px}.sub{color:var(--mute);font-size:13px}.card{background:var(--card);border:1px solid
var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;display:flex;gap:12px;align-items:flex-start}
.card .txt{flex:1;min-width:0}.why{color:var(--mute);font-size:12.5px;margin-top:3px}.ch{display:flex;gap:6px;
flex-shrink:0}.ch label{border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:13px;
white-space:nowrap}.ch input{display:none}.ch input:checked+span{font-weight:600}.ch label:has(input:checked){
border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}.obs{border-left:4px solid
var(--warn)}.obs.conflict{border-left-color:var(--bad)}textarea{width:100%;min-height:70px;background:var(--card);
color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px}.bar{position:fixed;left:0;right:0;bottom:0;
background:var(--card);border-top:1px solid var(--line);padding:12px 16px;display:flex;gap:12px;justify-content:center;
align-items:center}button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:9px 18px;font-size:14px;
cursor:pointer}#msg{color:var(--mute);font-size:13px}@media (max-width:640px){.card{flex-direction:column}}
"""


def review_page(label: str, entries: List[dict], flags: List[dict], action: str = "") -> str:
    conf = [e for e in entries if e["tier"] == "confirmed"]
    poss = [e for e in entries if e["tier"] == "possible"]

    def card(e, default):
        names = f"{' & '.join(e['from'][:2]) or '—'} → {' & '.join(e['to'][:2]) or '—'}"
        k = E(e["entry"])
        return (f"<div class=card><div class=txt><b>{E(e['type'])}</b> No. {k} dated {E(e['date'])}<br>{E(names)}"
                f"<div class=why>{E('; '.join(e['why']))}</div></div><div class=ch>"
                f"<label><input type=radio name='{k}' value=include {'checked' if default == 'include' else ''}>"
                f"<span>Include</span></label><label><input type=radio name='{k}' value=exclude "
                f"{'checked' if default == 'exclude' else ''}><span>Remove</span></label></div></div>")

    obs = "".join(f"<div class='card obs {E(f['level'])}'><div class=txt>{E(f['text'])}"
                  + ("<ul>" + "".join(f"<li>{E(x)}</li>" for x in f.get('items') or []) + "</ul>" if f.get("items") else "")
                  + "</div></div>" for f in flags if f.get("topic") != "possible entry")
    js = """
const ACTION=%s;
function choices(){const out={entries:{},note:document.getElementById('note').value};
 document.querySelectorAll('input[type=radio]:checked').forEach(i=>{out.entries[i.name]=i.value});return out}
async function send(){const c=choices();const m=document.getElementById('msg');
 if(!ACTION){const b=new Blob([JSON.stringify(c,null,1)],{type:'application/json'});const a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download='review_decisions.json';a.click();m.textContent='Decisions saved as a file.';return}
 m.textContent='Sending…';try{const r=await fetch(ACTION,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(c)});m.textContent=r.ok?'Saved — final reports are being generated.':'Could not save ('+r.status+').'}
 catch(e){m.textContent='Could not reach the server.'}}
""" % json.dumps(action or "")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>Review — {E(label)}</title><style>{CSS}</style></head><body><main>"
            f"<h1>Review before the reports are final — {E(label)}</h1>"
            f"<div class=sub>{len(conf)} entries in the Search Report · {len(poss)} to decide · "
            f"{len([f for f in flags if f.get('topic') != 'possible entry'])} observations</div>"
            f"<h2>Entries that may concern the property — decide each ({len(poss)})</h2>"
            + ("".join(card(e, "exclude") for e in poss) or "<div class=sub>None.</div>") +
            f"<h2>Entries in the Search Report ({len(conf)}) — remove any that do not belong</h2>"
            + ("".join(card(e, "include") for e in conf) or "<div class=sub>None.</div>") +
            f"<h2>Observations (for information — they appear in the Chain of Title)</h2>{obs or '<div class=sub>None.</div>'}"
            "<h2>Note for the file (optional)</h2><textarea id=note placeholder='Anything the system should learn from this "
            "case'></textarea></main><div class=bar><button onclick='send()'>Save decisions &amp; finalise</button>"
            f"<span id=msg></span></div><script>{js}</script></body></html>")
