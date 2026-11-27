"""Evidence reader (v2): reads every page of the case papers and returns each fact WITH its page number and the
words exactly as printed. Nothing is inferred: a value that is not printed is left empty.

  python -m v2.extract_v2 "<case folder>" <case_name>        # reads PDFs under <case folder>/Property*/ (API COST)

The answer is cached in sample_out/<case_name>/v2_stage1/evidence_read.json; Stage 1 then runs on it at no cost:
  python -m v2.run_stage1 --evidence <case_name>
"""
import glob
import io
import json
import os
import re
import sys
import time
from typing import List

from pypdf import PdfReader

from app import config, llm
from app.extract import split_pdf

from . import llm_v2

EVIDENCE_SYSTEM = """You are a property-title paralegal in Maharashtra, India. You read scanned property papers
(English and Marathi) for an advocate who will rely on every fact you report. Accuracy and traceability matter more
than completeness.

ABSOLUTE RULES
1. Report only what is PRINTED on these pages. Never infer, complete, correct or guess a value. If a value is not
   printed, leave it "" (or []), never write "not mentioned", "N.A." or a note.
2. Every value carries "page" (the page number in the WHOLE FILE, as told in the instruction) and "quote" (the exact
   words on that page that state it, in the original script, at most 25 words). No page + quote = do not report it.
3. "value" is always in English, normalised: names transliterated (never translated), places in their English
   spelling, numbers in Western digits, areas with their unit ("40.69 sq m", "398 sq ft"), floor as a number or
   "ground". The original wording stays in "quote". Never merge two spellings, never "fix" a spelling.
   Put a value in its OWN field only: a final plot / CTS number is never a unit number; the officer who signed a
   property card is never the taluka; a valuer's description of the whole building is not the unit's floor.
4. A person who signs FOR a party (partner, director, constituted attorney / कुलमुखत्यार, authorised signatory,
   chairman / secretary of a society) is a SIGNATORY of that party, not a party.
5. When two pages disagree (unit number, area, name, date), report both as printed. Do not choose.

WHAT TO LIST
A. Every DOCUMENT physically in these pages (deed, agreement, Index-II, receipt, letter, certificate, approval,
   plan, 7/12 / property card, bill, ID card) — "furnished": true.
B. Every EARLIER DOCUMENT that is only RECITED (mentioned in a "WHEREAS" clause, title certificate, schedule,
   Index-II or letter) — "furnished": false, "recited_on_page" = the page where it is mentioned.
For each document:
  doc_type, category (title_transfer — any document that gives or passes rights in the unit or land: sale, conveyance,
  allotment letter, lease, gift, release, partition, inheritance / succession, court decree; development — development
  agreement / power of attorney for development; encumbrance — mortgage, charge, lien; registration_record — Index-II,
  registration receipt; approval; revenue_record — 7/12, property card, mutation; title_certificate; receipt;
  identity; plan; other), level (premises = the unit itself, project = the building / scheme, land = the land
  parcels), first_page, last_page,
  continues_previous: true when the first page here is clearly the middle of a document that began on earlier pages,
  exec_date, reg_date,
  registration: {number, year, office, as_printed}, execution_status (registered / executed_not_registered / draft),
  parties: [{name, role (as the document states it), page, quote, signatories: [{name, capacity, page, quote}],
             former_names: [{name, page, quote}]}],
  property: what THIS document describes —
    describes: "subject_unit" / "land_or_project" / "other_unit" / "unclear",
    unit_type, unit_no, floor, wing, building_no, building_name, society_name, project_name, carpet_area,
    builtup_area, village, taluka, district, pin   → each {value, page, quote}
    old_names: [{value, page, quote}]   (former names of the building / chawl / society, 'formerly known as')
    parcels: [{kind: survey / hissa / cts / gat / plot / final_plot, number (e.g. "134/1"), part (true if printed
               'part' / 'पैकी' / 'pt'), area, page, quote}]
  recitals: [{event (one line: who transferred what to whom), date, doc_no, from: [names], to: [names], page, quote}]
     — the title history this document narrates, each step separately.
  scheme: [{type, page, quote}] — what kind of property scheme or transaction this document SAYS it is, in your own
     short words (e.g. "CIDCO lottery allotment", "SRA rehabilitation scheme", "MHADA layout redevelopment",
     "society redevelopment", "sale of a new flat by the developer", "resale by an existing owner", "leasehold plot",
     "inheritance"), only when the document states it.

Return ONLY JSON: {"documents": [...]}"""


def _read_chunk(chunk: bytes, p1: int, p2: int, filename: str) -> List[dict]:
    hint = (f"File: {filename}. These are pages {p1}-{p2} of the file: report page numbers in the WHOLE file "
            f"(the first page here is page {p1}). Other pages are read separately — leave a value empty if it is not "
            f"on these pages.")
    try:
        out = llm_v2.call_json("v2_evidence_extract", EVIDENCE_SYSTEM, [llm.pdf_block(chunk), llm.text_block(hint)],
                               max_tokens=32000)
    except llm.TruncatedError:
        if p2 <= p1:
            raise
        mid = (p1 + p2) // 2
        reader = PdfReader(io.BytesIO(chunk))
        from pypdf import PdfWriter

        def part(a, b):
            w = PdfWriter()
            for i in range(a - p1, b - p1 + 1):
                w.add_page(reader.pages[i])
            buf = io.BytesIO()
            w.write(buf)
            return buf.getvalue()
        return _read_chunk(part(p1, mid), p1, mid, filename) + _read_chunk(part(mid + 1, p2), mid + 1, p2, filename)
    docs = (out or {}).get("documents") if isinstance(out, dict) else out
    for d in docs or []:
        d["file"] = filename
    return docs or []


def read_case(folder: str) -> List[dict]:
    """Every PDF of the case, wherever it was uploaded, except the government search-fee receipts (recognised by
    their content: GRN / receipt number), which say nothing about the property's title."""
    from app.extract import parse_search_receipt
    docs = []
    for f in sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True)):
        data = open(f, "rb").read()
        r = parse_search_receipt(data, os.path.basename(f))
        if r.get("grn") or r.get("receipt_no"):
            print(f"  skipping fee receipt {os.path.basename(f)}")
            continue
        print(f"  reading {os.path.basename(f)}")
        for chunk, p1, p2 in split_pdf(data):
            docs += _read_chunk(chunk, p1, p2, os.path.basename(f))
    return docs


def _join_continuations(raw: List[dict]) -> List[dict]:
    """The file is read in page chunks; a document running across a chunk boundary comes back as two pieces. A piece
    is joined to the previous one when the reader says it continues it, or when it starts on the very next page of
    the same file with the same kind of document and no registration number of its own that differs."""
    out: List[dict] = []
    for d in raw:
        prev = out[-1] if out else None
        if prev and prev.get("file") == d.get("file") and d.get("furnished", True) and prev.get("furnished", True):
            try:
                adjacent = int(d.get("first_page") or 0) == int(prev.get("last_page") or -9) + 1
            except (TypeError, ValueError):
                adjacent = False
            kind = lambda x: re.sub(r"\(.*?\)|[^a-z ]", " ", (x.get("doc_type") or "").lower()).split()[:2]
            reg = lambda x: str((x.get("registration") or {}).get("number") or "")
            if (d.get("continues_previous") and adjacent) or \
                    (adjacent and kind(d) == kind(prev) and (not reg(d) or not reg(prev) or reg(d) == reg(prev))):
                prev["last_page"] = d.get("last_page") or prev.get("last_page")
                for k in ("parties", "recitals", "scheme"):
                    prev[k] = (prev.get(k) or []) + (d.get(k) or [])
                pp, dp = prev.setdefault("property", {}) or {}, d.get("property") or {}
                for k, v in dp.items():
                    if k == "parcels" or k == "old_names":
                        pp[k] = (pp.get(k) or []) + (v or [])
                    elif isinstance(v, dict) and str(v.get("value") or "").strip() and \
                            not str((pp.get(k) or {}).get("value") or "").strip():
                        pp[k] = v
                for k in ("exec_date", "reg_date", "execution_status"):
                    if not prev.get(k) and d.get(k):
                        prev[k] = d[k]
                if not reg(prev) and reg(d):
                    prev["registration"] = d["registration"]
                continue
        out.append(d)
    return out


def to_v2(raw: List[dict]) -> List[dict]:
    """Evidence-reader output → the v2 document format used by Stage 1 (exact page + quote on every fact)."""
    out = []
    for i, d in enumerate(_join_continuations(raw), 1):
        pr = d.get("property") or {}
        prop, pages, quotes = {}, {}, {}
        for k in ("unit_type", "unit_no", "floor", "wing", "building_no", "building_name", "society_name", "project_name",
                  "carpet_area", "builtup_area", "village", "taluka", "district", "pin"):
            v = pr.get(k)
            if isinstance(v, dict) and str(v.get("value") or "").strip():
                prop[k], pages[k], quotes[k] = v["value"], str(v.get("page") or ""), v.get("quote") or ""
        olds = [o for o in pr.get("old_names") or [] if isinstance(o, dict) and o.get("value")]
        if olds:
            prop["old_name"], pages["old_name"], quotes["old_name"] = olds[0]["value"], str(olds[0].get("page") or ""), \
                olds[0].get("quote") or ""
        parcels = [p for p in pr.get("parcels") or [] if isinstance(p, dict) and p.get("number")]
        if parcels:                                      # used as the reader gave them — never re-parsed from text
            prop["parcels"] = [{"kind": str(p.get("kind") or "").strip(), "number": str(p["number"]).strip(),
                                "part": bool(p.get("part")), "area": p.get("area") or "",
                                "page": str(p.get("page") or ""), "quote": p.get("quote") or ""} for p in parcels]
        if prop:
            prop["_pages"], prop["_quotes"] = pages, quotes
            if pr.get("describes") == "other_unit":
                prop = {}                                # another unit's description never feeds the subject's
        reg = d.get("registration") or {}
        parties = []
        for p in d.get("parties") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            sig = "; ".join(s.get("name") for s in p.get("signatories") or [] if isinstance(s, dict) and s.get("name"))
            former = " ".join(f"formerly known as {f['name']}" for f in p.get("former_names") or []
                              if isinstance(f, dict) and f.get("name"))
            name = p["name"] + (f" ({former})" if former else "") + (f" through {sig}" if sig else "")
            parties.append({"name": name, "role": p.get("role") or "", "page": str(p.get("page") or ""),
                            "quote": p.get("quote") or ""})
        out.append({"ref": d.get("ref") or f"E{i}", "doc_type": d.get("doc_type") or "", "category": d.get("category") or "",
                    "level": d.get("level") or "", "file": d.get("file") or "",
                    "pages": f"{d.get('first_page')}-{d.get('last_page')}" if d.get("first_page") else
                             str(d.get("recited_on_page") or ""),
                    "exec_date": d.get("exec_date") or "", "reg_date": d.get("reg_date") or "",
                    "doc_no": reg.get("as_printed") or (f"{reg.get('number')}/{reg.get('year')}" if reg.get("number") else ""),
                    "sro": reg.get("office") or "", "furnished": bool(d.get("furnished", True)),
                    "execution_status": d.get("execution_status") or "", "parties": parties, "property": prop,
                    "recitals": d.get("recitals") or [],
                    "scheme": [s for s in d.get("scheme") or [] if isinstance(s, dict) and s.get("type")],
                    "page_exact": True})
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    folder, case = sys.argv[1], sys.argv[2]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dst = os.path.join(root, "sample_out", case, "v2_stage1")
    os.makedirs(dst, exist_ok=True)
    t0 = time.time()
    raw = read_case(folder)
    json.dump(raw, open(os.path.join(dst, "evidence_read.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    u = dict(llm_v2.USAGE, model=config.CLAUDE_MODEL, seconds=round(time.time() - t0))
    json.dump(u, open(os.path.join(dst, "evidence_usage.json"), "w"), indent=1)
    print(f"{case}: {len(raw)} documents read with page-level evidence; usage {u}")
