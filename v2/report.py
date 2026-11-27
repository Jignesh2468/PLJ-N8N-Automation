"""Stage 4 — the Search Report and the Chain of Title, entirely in English, from the verified model only.

Nothing in a report is invented: each line comes from a document of the papers or a confirmed search entry, and
everything uncertain is printed as an observation for the advocate. English without any AI call:
  names    → the English spelling from the papers when the same party is known there; otherwise transliterated
  terms    → the Marathi / English legal glossary (deed types, land words), then transliteration
"""
import html
import re
from datetime import date
from typing import Dict, List, Optional

from app.common import parse_date
from app.english import DOC_TYPES, _dict_pass, has_dev, translit_mixed

from .names import compare

LETTERHEAD = ("<div class=lh><div class=lh-name>PROFICIENT LAW JUNCTION</div><div class=lh-tag>ADVOCATES &amp; LEGAL "
              "CONSULTANTS</div><div class=lh-addr>Office No: 4, Bhatt/Kadam Building, Old Nagardas Road, Behind Bhatia "
              "Medical, Andheri (East), Mumbai 400 069. Office: 022-28347177 | Mobile: 9869507557</div><div class=lh-addr>"
              "Email: plawjunction@gmail.com</div></div><hr class=divider>")
CSS = """
*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Times New Roman',Times,serif;font-size:12.5pt;color:#000;
background:#fff;padding:40px 56px;max-width:960px;margin:0 auto;line-height:1.6}.lh{text-align:center;margin-bottom:8px}
.lh-name{font-size:20pt;font-weight:bold;letter-spacing:2px}.lh-tag{font-size:12.5pt;font-weight:bold;margin-top:3px}
.lh-addr{font-style:italic;font-size:10.5pt;margin-top:4px}.divider{border:none;border-top:2.5px solid #000;margin:10px 0}
.date{text-align:right;font-weight:bold;margin-bottom:10px}.title{text-align:center;font-size:15pt;font-weight:bold;
text-decoration:underline;margin:10px 0 16px}.para{margin-bottom:10px;text-align:justify}h3{font-size:13pt;margin:20px 0 8px;
text-decoration:underline}table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:11.5pt}
th{background:#d0d0d0;border:1.5px solid #000;padding:6px 8px}td{border:1.5px solid #000;padding:6px 8px;vertical-align:top}
.yr{width:70px;text-align:center;white-space:nowrap}.small{font-size:10pt;color:#333}.sig{margin-top:36px;font-weight:bold}
.foot{margin-top:24px;border-top:1px solid #ccc;padding-top:6px;font-size:9pt;color:#777;text-align:center}
@media print{body{padding:0}.foot{display:none}tr{page-break-inside:avoid}}
"""
E = lambda s: html.escape(str(s if s is not None else ""))


class English:
    """English rendering of names and terms, preferring the spelling the papers use for the same party."""

    def __init__(self, stage1: dict):
        self.known = []
        for p in stage1["parties"]:
            eng = [s["name"] for s in p["spellings"] if not has_dev(s["name"])]
            if eng:
                self.known.append((p, eng[0], [s["name"] for s in p["spellings"]] + [f["name"] for f in p["former_names"]]))

    def name(self, n: str) -> str:
        if not n:
            return ""
        if has_dev(n):
            for p, eng, names in self.known:
                if any(compare(n, x, None, p["kind"]) == "same" for x in names):
                    return eng
        return self.text(n)

    @staticmethod
    def text(s: str) -> str:
        if not s:
            return ""
        if not has_dev(s):
            return " ".join(str(s).split())
        t = " ".join(str(s).split())
        if t in DOC_TYPES:
            return DOC_TYPES[t]
        from .glossary import apply
        return " ".join(translit_mixed(_dict_pass(apply(t))).split())

    @staticmethod
    def doc_no(raw: str) -> str:
        """Any registration number as 'number/year' ('KRL-2-24310-2023', 'मबई28-26098-2026' → '24310/2023', '26098/2026')."""
        from app.common import ascii_digits, parse_doc_no_raw
        n, y = parse_doc_no_raw(ascii_digits(raw or ""))
        return f"{int(n)}/{y}" if n and y else English.text(raw)

    def names(self, ns: List[str], limit: int = 6) -> str:
        # the members of a society listed one by one ('<Society> चे मेंबर <name>', '<Society> member <name>') are
        # written as the advocate writes them: 'the members of <Society>'
        groups: Dict[str, int] = {}
        for n in ns:
            m_ = re.match(r"^(.{6,}?)\s*(?:चे|ची|च्या|'s)?\s*(?:मेंबर|मेम्बर|सभासद|member)", n or "", re.I)
            if m_:
                groups[m_.group(1).strip()] = groups.get(m_.group(1).strip(), 0) + 1
        for soc, cnt in groups.items():
            if cnt >= 3:
                rest = [n for n in ns if not (n or "").startswith(soc)]
                return " & ".join([f"the members of {self.name(soc)}"] + [self.name(r) for r in rest][:limit])
        out = []
        for n in ns:
            e = self.name(n)
            if e and e not in out:
                out.append(e)
        if len(out) > limit:
            return " & ".join(out[:limit]) + f" & {len(out) - limit} others"
        return " & ".join(out) or "—"


def property_text(stage1: dict, en: English) -> str:
    m = stage1["property"]
    u = m["unit"]
    val = lambda r: r.get("value") or ""
    parts = []
    if val(u["unit_no"]):
        parts.append(f"{en.text(val(u['unit_type'])) or 'Unit'} No. {en.text(val(u['unit_no']))}")
    if val(u["wing"]):
        parts.append(f"Wing {en.text(val(u['wing']))}")
    if val(u["floor"]):
        f = en.text(val(u["floor"]))
        if re.fullmatch(r"\d+", f.strip()):
            n = int(f)
            f = f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
        parts.append(f if re.search(r"floor", f, re.I) else f"on the {f} floor")
    if val(u["carpet_area"]):
        parts.append(f"admeasuring {en.text(val(u['carpet_area']))} carpet area")
    # building names: the distinctive ones, not a sentence that merely contains a name ('… proposed to be named X')
    names = []
    for n in m["building"]["names"]:
        t = en.text(n["name"])
        if len(t.split()) <= 6 and not any(t.lower() in x.lower() or x.lower() in t.lower() for x in names):
            names.append(t)
    names = names[:2]
    if m["building"]["number"].get("value"):
        parts.append(f"Building No. {en.text(m['building']['number']['value'])}")
    if names:
        parts.append("known as " + " / ".join(f"'{n}'" for n in names))
    land = [f"{p['kind']} No. {p['number']}{' (part)' if p['extent'] == 'part' else ''}" for p in m["land"]["parcels"]
            if p["kind"] != "Old Survey" and p["in_unit_documents"]] or \
           [f"{p['kind']} No. {p['number']}{' (part)' if p['extent'] == 'part' else ''}" for p in m["land"]["parcels"]
            if p["kind"] != "Old Survey"]
    if land:
        parts.append("on the land bearing " + ", ".join(land))
    for k, label in (("village", "Village"), ("taluka", "Taluka"), ("district", "District")):
        if m["land"][k].get("value"):
            parts.append(f"{label} {en.text(m['land'][k]['value'])}")
    return ", ".join(parts) or "Property description not established from the documents (see observations)"


def _line(s: dict, en: English) -> str:
    """'Deed of Conveyance dated 23/12/2010 executed between A and B, registered with … under Sr. No. 9664/2010.'"""
    typ = en.text(s["type"])
    frm, to = en.names(s["from"]), en.names(s["to"])
    mort = re.search(r"mortgage|deposit of title", typ, re.I) and not re.search(r"release|reconvey", typ, re.I)
    who = (f"executed by <b>{E(frm)}</b> (mortgagor) in favour of <b>{E(to)}</b> (mortgagee)" if mort else
           f"executed between <b>{E(frm)}</b> and <b>{E(to)}</b>" if s["to"] else f"made by <b>{E(frm)}</b>")
    reg = []
    if s.get("office"):
        reg.append(f"registered with {E(en.text(s['office']))}")
    sd = s.get("search_date")
    if sd and s.get("date") and parse_date(sd) and parse_date(s["date"]) and parse_date(sd) != parse_date(s["date"]):
        reg.append(f"on {E(sd)}")
    if s.get("doc_no"):
        reg.append(f"under Sr. No. {E(en.doc_no(s['doc_no']))}")
    d = f" dated {E(s['date'])}" if s.get("date") else ""
    return f"{E(typ)}{d} {who}" + (", duly " + " ".join(reg) if reg else "") + "."


def _registered_no(doc_no: str) -> bool:
    """A registration number proper — number + year ('3174/2025', 'KRL-4/12655/2018') — not a reference such as a
    property-card entry number or an approval file number."""
    from app.common import ascii_digits, parse_doc_no_raw
    n, y = parse_doc_no_raw(ascii_digits(doc_no or ""))
    return bool(n and y and re.fullmatch(r"\d{1,6}", str(n).strip()) and 1900 < int(y) < 2100)


def _heads(stage1: dict) -> List[str]:
    """The people the Search Report traces the title INTO: the developer(s) and the present holder(s)."""
    m = stage1["property"]
    pid = {x.get("id") for x in (m.get("present_holder") or {}).get("holders", []) + m["project"]["developers"]}
    # everyone who has held the UNIT (a resale's previous owners, the builder who first sold it) — the parties to the
    # unit's own deeds — is a head of the chain the Search Report traces
    for t in m["title_history"]:
        if t.get("level") == "premises":
            pid |= {x.get("id") for x in (t.get("from") or []) + (t.get("to") or []) if x.get("id")}
    pid.discard(None)
    lenders = {p["id"] for p in stage1["parties"] if p["kind"] in ("lender", "authority")}
    pid -= lenders
    names = []
    for p in stage1["parties"]:
        if p["id"] in pid:
            names += [s["name"] for s in p["spellings"]] + [f["name"] for f in p["former_names"]]
    return names


def _is_head(name: str, heads: List[str]) -> bool:
    return any(compare(name, h) == "same" for h in heads)


def report_entry(s: dict, heads: List[str]) -> bool:
    """What the advocate puts in the Search Report: the deeds by which title reaches the developer / present holder
    (conveyance, development agreement, agreement for sale TO them), and the charges and declarations made BY them.
    Earlier dealings between previous owners stay in the Chain of Title."""
    to = s.get("search_to") or s["to"]
    frm = s.get("search_from") or s["from"]
    t = (s.get("family") or s["type"]).lower()
    if re.search(r"power\s+of\s+attorney|\bpoa\b|मुखत्यार", t + " " + s["type"].lower()):
        return False                       # a power of attorney gives authority, not title: Chain of Title only
    if s.get("level") == "premises" or any("names our unit" in w for w in s.get("why") or []):
        return True                        # every deed of the unit itself (a resale's earlier agreements included)
    if re.search(r"mortgage|deposit of title|declaration|affidavit|undertaking|release_charge|reconvey", t):
        return any(_is_head(n, heads) for n in frm + to)
    return any(_is_head(n, heads) for n in to)


def search_report(case: str, stage1: dict, stage2: dict, steps: List[dict], years: List[int], period, receipts: List[dict],
                  offices: List[str]) -> str:
    en = English(stage1)
    m = stage1["property"]
    holders = [en.name(h["name"]) for h in (m.get("present_holder") or {}).get("holders", [])]
    heads = _heads(stage1)
    by_year: Dict[int, List[str]] = {}
    for s in steps:
        if not report_entry(s, heads):
            continue                                   # an earlier dealing between previous owners: Chain of Title
        y = (parse_date(s.get("search_date") or "") or parse_date(s["date"]) or date(1900, 1, 1)).year if s["in_search"] \
            else (parse_date(s["date"]) or date(1900, 1, 1)).year
        if s["in_search"]:
            # a deed found in the search is reported with its parties AS REGISTERED
            shown = dict(s, **{k: s[f"search_{k}"] for k in ("from", "to") if s.get(f"search_{k}")})
            by_year.setdefault(y, []).append(_line(shown, en))
        elif s["in_papers"] and _registered_no(s.get("doc_no")) and period and period[0] <= y <= period[1]:
            by_year.setdefault(y, []).append(_line(s, en) + " <span class=small>(as per the documents furnished; not "
                                                             "found in the search data supplied — certified copy / Index-II "
                                                             "to be obtained)</span>")
    # declarations / affidavits the developer or holder made on the land (listed for verification in Stage 2) are
    # reported like the advocate does — they are their own acts on the property
    for d in stage2["decisions"].values():
        r = d["row"]
        if d["tier"] == "possible" and r["kind"]["family"] == "declaration" and \
                any(_is_head(p["name"], heads) for p in r["sellers"] + r["buyers"]):
            s = {"type": r["kind"]["label"], "date": r["exec_date"] or r["reg_date"], "doc_no": f"{r['docno']}/{r['year']}",
                 "office": r["office"], "from": [p["name"] for p in r["sellers"]], "to": [p["name"] for p in r["buyers"]],
                 "search_date": r["reg_date"]}
            by_year.setdefault(int(r["year"]), []).append(_line(s, en))
    rows = []
    for y in range(period[0], period[1] + 1) if period else []:
        # IGR exports no file for a year without entries: a year of the searched period with no entries is NIL
        cell = "<br><br>".join(by_year.get(y, [])) or "NIL"
        rows.append(f"<tr><td class=yr>{y}</td><td>{cell}</td></tr>")
    before = [s for s in steps if s["in_papers"] and not s["in_search"] and period and _registered_no(s.get("doc_no"))
              and report_entry(s, heads) and (parse_date(s["date"]) or date(2999, 1, 1)).year < period[0]]
    poss = sorted([d for d in stage2["decisions"].values() if d["tier"] == "possible"], key=lambda d: d["row"]["year"] or 0)
    off = ", ".join(sorted({en.text(o) for o in offices if o})[:6]) or "the Sub-Registrar offices searched"
    seen_grn, grns = set(), []
    for r in receipts:
        if r.get("grn") and r["grn"] not in seen_grn:          # the same challan saved twice is one payment
            seen_grn.add(r["grn"])
            grns.append(f"GRN {E(r['grn'])}" + (f" dated {E(r['date'])}" if r.get("date") else ""))
    grn = "; ".join(grns)
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
           f"initial-scale=1'><title>Search Report — {E(case)}</title><style>{CSS}</style></head><body>", LETTERHEAD,
           f"<div class=date>Date: {date.today():%d/%m/%Y}</div><div class=title>SEARCH REPORT</div>",
           "<p class=para><b>To,<br>The Manager,<br>[Bank Name &amp; Branch].</b></p>",
           f"<p class=para><b>Property:</b> {E(property_text(stage1, en))} (hereinafter referred to as the said Property).</p>",
           f"<p class=para><b>A/C: {E(' & '.join(holders) or '[holder not established — see observations]')}.</b></p>",
           f"<p class=para>We have caused to take online search of the above-mentioned property on igrmaharashtra.gov.in, "
           f"the official website of the Government of Maharashtra, at {E(off)}"
           + (f", for the period from {period[0]} to {period[1]}." if period else ".") + "</p>",
           "<table><tr><th class=yr>Year</th><th>Registration entries (transactions)</th></tr>" + "".join(rows) + "</table>"]
    if before:
        out.append("<p class=para><b>Documents disclosed in the papers, registered before the search period:</b><br>" +
                   "<br>".join(_line(s, en) for s in before) + "</p>")
    # entries that MAY concern the property are not printed in the Search Report; they are listed for the advocate
    # in the Chain of Title observations (see checks.run_checks)
    if grn:
        out.append(f"<p class=para>Government fees paid vide {grn}.</p>")
    out.append("<p class=para><i>This report is limited to the search data supplied for the period and offices stated "
               "above. It does not cover unregistered transactions or records not indexed on the IGR website.</i></p>"
               "<div class=sig><p>For Proficient Law Junction LLP</p><br><br><p>Authorised Signatory</p></div>"
               f"<div class=foot>Generated by PLJ v2 (development build) · {E(case)}</div></body></html>")
    return "\n".join(out)


def chain_of_title(case: str, stage1: dict, steps: List[dict], flags: List[dict], period) -> str:
    en = English(stage1)
    m = stage1["property"]
    h = m.get("present_holder") or {}
    holders = [en.name(x["name"]) for x in h.get("holders", [])]
    docs = [d for d in stage1.get("doc_list", []) if d.get("furnished")]
    docs.sort(key=lambda d: parse_date(d.get("date") or "") or date(1900, 1, 1))
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
           f"initial-scale=1'><title>Chain of Title — {E(case)}</title><style>{CSS}</style></head><body>", LETTERHEAD,
           f"<div class=date>Date: {date.today():%d/%m/%Y}</div><div class=title>CHAIN OF TITLE</div>",
           "<p class=para><b>To,<br>The Branch Manager,<br>[Bank Name &amp; Branch].</b></p>",
           f"<p class=para><b>Reg: {E(property_text(stage1, en))}.</b></p>",
           f"<p class=para><b>Belonging to: {E(' & '.join(holders) or '[not established — see observations]')}.</b></p>",
           f"<h3>Documents perused ({len(docs)})</h3><table><tr><th>Sr.</th><th>Document</th><th>Date</th><th>Source</th></tr>"]
    for i, d in enumerate(docs, 1):
        out.append(f"<tr><td>{i:02d}.</td><td>{E(en.text(d['doc_type']))}"
                   + (f" — No. {E(en.text(d['doc_no']))}" if d.get("doc_no") else "") +
                   f"</td><td>{E(d.get('date') or '—')}</td><td class=small>{E(d.get('file'))} pp. {E(d.get('pages'))}</td></tr>")
    out.append("</table><h3>Chain of title — transaction-wise</h3>")
    for i, s in enumerate(steps, 1):
        src = ("Copy furnished and verified in the search" if s["in_papers"] and s["in_search"] and s["copy"] == "furnished" else
               "Copy furnished; not found in the search data" if s["in_papers"] and s["copy"] == "furnished" else
               "Recited in the documents and verified in the search" if s["in_papers"] and s["in_search"] else
               "Recited in the documents only" if s["in_papers"] else "Found in the search; copy not furnished")
        out.append("<table><tr><th style='width:210px'>Transaction</th><td>" + f"{i:02d}" + "</td></tr>"
                   f"<tr><th>Date</th><td>{E(s['date'] or 'Not stated in the documents')}</td></tr>"
                   f"<tr><th>Document</th><td>{E(en.text(s['type']))}</td></tr>"
                   f"<tr><th>Transferor / previous holder</th><td>{E(en.names(s['from']))}</td></tr>"
                   f"<tr><th>Transferee / new holder</th><td>{E(en.names(s['to']))}</td></tr>"
                   f"<tr><th>Registration</th><td>{E(('Sr. No. ' + en.doc_no(s['doc_no'])) if s['doc_no'] else 'Not registered / not stated')}"
                   f"{(', ' + E(en.text(s['office']))) if s.get('office') else ''}</td></tr>"
                   f"<tr><th>Source</th><td>{E(src)}</td></tr></table>")
    status = (h.get("status") or "").replace("_", " ")
    out.append("<h3>Present title position</h3><p class=para>" +
               (f"As per the documents provided, the said property stands in the name of <b>{E(' & '.join(holders))}</b> "
                f"under the {E(en.text(h.get('document', '').split(' (')[0]))}"
                + (f" ({E(status)})" if status and status != "registered" else "") + "." if holders else
                "The present holder could not be established from the documents provided.") + "</p>")
    charges = [f for f in flags if f["topic"] == "charge"]
    out.append("<h3>Encumbrances</h3><p class=para>" + ("<br>".join(E(en.text(f["text"])) for f in charges) or
                                                         "No subsisting charge found in the documents and search data supplied.") + "</p>")
    obs = [f for f in flags if f["topic"] != "charge"]
    out.append("<h3>Observations / documents and information required</h3><table><tr><th>Sr.</th><th>Observation</th></tr>")
    for i, f in enumerate(obs, 1):
        out.append(f"<tr><td>{i:02d}.</td><td>{E(en.text(f['text']))}</td></tr>")
    if not obs:
        out.append("<tr><td>—</td><td>None.</td></tr>")
    out.append("</table><div class=sig><p>Place: Mumbai.</p><p>Date: " + f"{date.today():%d/%m/%Y}" +
               "</p><br><p>PROFICIENT LAW JUNCTION</p><p>Advocates &amp; Legal Consultants</p></div>"
               f"<div class=foot>Generated by PLJ v2 (development build) · {E(case)}</div></body></html>")
    return "\n".join(out)
