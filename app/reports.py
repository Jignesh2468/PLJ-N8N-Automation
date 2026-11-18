"""Stage 5: render bank-format DOCX reports (python-docx) from analysis + drafted text."""
import io
import re
from typing import List

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Twips

from . import english, config
from .common import rs
from .drafting import QUESTIONS

FONT = "Times New Roman"


# ── low-level helpers ──────────────────────────────────────────────────────────
def _new_doc() -> Document:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(12)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.space_after = Pt(6)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Twips(11909), Twips(16834)
    sec.top_margin = sec.bottom_margin = Twips(1440)
    sec.left_margin, sec.right_margin = Twips(1800), Twips(1500)
    return doc


def _runs(p, text: str, size=None, bold_all=False, italic=False, underline=False):
    for part in re.split(r"(\*\*[^*]+\*\*)", text or ""):
        if not part:
            continue
        bold = bold_all or (part.startswith("**") and part.endswith("**"))
        r = p.add_run(part[2:-2] if part.startswith("**") and part.endswith("**") else part)
        r.bold, r.italic, r.underline = bold, italic, underline
        r.font.name = FONT
        if size:
            r.font.size = Pt(size)
    return p


def _para(doc, text="", align=None, size=None, bold=False, italic=False, underline=False, after=6, indent=None):
    p = doc.add_paragraph()
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "justify":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    elif align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_after = Pt(after)
    if indent:
        p.paragraph_format.left_indent = Twips(indent)
        p.paragraph_format.first_line_indent = Twips(-indent)
    _runs(p, text, size=size, bold_all=bold, italic=italic, underline=underline)
    return p


def _bottom_border(p):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "12"), ("w:space", "1"), ("w:color", "000000")):
        b.set(qn(k), v)
    bdr.append(b)
    pPr.append(bdr)


def _letterhead(doc, ref: str, date: str):
    _para(doc, config.FIRM_NAME, "center", 24, bold=True, after=0)
    _para(doc, config.FIRM_TAGLINE, "center", 14, bold=True, after=0)
    _para(doc, config.FIRM_ADDRESS, "center", 10, italic=True, after=0)
    p = _para(doc, f"Email :{config.FIRM_EMAIL}", "center", 10, italic=True, after=8)
    _bottom_border(p)
    p = doc.add_paragraph()
    from docx.enum.text import WD_TAB_ALIGNMENT
    p.paragraph_format.tab_stops.add_tab_stop(Twips(8600), WD_TAB_ALIGNMENT.RIGHT)
    _runs(p, f"**{ref}**\t**Date: {date}**" if ref else f"\t**Date: {date}**")


def _table(doc, widths: List[int], header: List[str], rows: List[List[str]], intro: str = "", center=(0,)):
    ncols = len(widths)
    t = doc.add_table(rows=0, cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._tbl.tblPr
    lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed"); tblPr.append(lay)
    tw = OxmlElement("w:tblW"); tw.set(qn("w:w"), str(sum(widths))); tw.set(qn("w:type"), "dxa")
    for old in tblPr.findall(qn("w:tblW")):
        tblPr.remove(old)
    tblPr.append(tw)
    for i, gc in enumerate(t._tbl.tblGrid.findall(qn("w:gridCol"))):
        gc.set(qn("w:w"), str(widths[i]))
    def add_row(cells, bold=False, shade=False, center_cols=()):
        r = t.add_row()
        for i, txt in enumerate(cells):
            c = r.cells[i]
            c.width = Twips(widths[i])
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            if i in center_cols:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _runs(p, txt, bold_all=bold)
            if shade:
                tcPr = c._tc.get_or_add_tcPr()
                s = OxmlElement("w:shd")
                s.set(qn("w:val"), "clear"); s.set(qn("w:color"), "auto"); s.set(qn("w:fill"), "D9D9D9")
                tcPr.append(s)
        return r
    if intro:
        r = add_row([intro] + [""] * (ncols - 1), bold=True, shade=True)
        a = r.cells[0].merge(r.cells[ncols - 1])
        for extra in a.paragraphs[1:]:
            extra._p.getparent().remove(extra._p)
    if header:
        add_row(header, bold=True, shade=True, center_cols=range(ncols))
    for row in rows:
        add_row(row, center_cols=center)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def _page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _signature(doc, date: str):
    _para(doc, f"**Place: {config.FIRM_PLACE}.**", after=0)
    _para(doc, f"**Date: {date}.**", after=24)
    _para(doc, config.FIRM_NAME, bold=True, after=0)
    _para(doc, config.FIRM_TAGLINE, bold=True)


def _to_block(doc, bank: dict, manager="The Branch Manager / Chief Manager,"):
    _para(doc, "**To,**", after=0)
    _para(doc, f"**{manager}**", after=0)
    _para(doc, f"**{bank.get('name') or '[Bank Name]'},**", after=0)
    _para(doc, f"**{bank.get('branch') or '[Branch]'}.**", after=10)


def _footer_note(doc):
    sec = doc.sections[0]
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(p, "Draft generated by PLJ AI — to be verified and signed by the Advocate before issue.", size=8, italic=True)


def _save(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── document descriptions (deterministic, from verified data) ─────────────────
_PART = ["One Part", "Other Part"]
_PART3 = ["First Part", "Second Part", "Third Part", "Fourth Part"]


def _group_parties(d):
    groups = []
    for p in d.get("parties", []):
        role = (p.get("role") or "").strip()
        if groups and groups[-1][0].lower() == role.lower():
            groups[-1][1].append(p["name"])
        else:
            groups.append([role, [p["name"]]])
    return groups


def describe(d: dict, with_registration=True) -> str:
    date = d.get("exec_date") or d.get("reg_date")
    groups = _group_parties(d)
    if d["category"] in ("receipt", "registration_record", "identity", "plan", "revenue_record") or \
            (not groups and d.get("short_description")):
        if d.get("short_description"):
            return d["short_description"]
    if len(groups) >= 2 and d["category"] in ("title_transfer", "development", "encumbrance", "society"):
        parts = _PART if len(groups) == 2 else _PART3
        segs = []
        for i, (role, names) in enumerate(groups[:len(parts)]):
            segs.append(f"**{' & '.join(names)}** as ‘the {role}’ of the {parts[i]}")
        s = f"{d['doc_type']} dated {date} entered between " + " AND ".join(segs) + "."
        if with_registration and d.get("doc_no_display"):
            s += f" [Registered with {d.get('sro_display') or 'the Sub-Registrar of Assurances'}, under Document No.{d['doc_no_display']}" \
                 f"{' on ' + d['reg_date'] if d.get('reg_date') and d.get('reg_date') != date else ''}]."
        return s
    s = f"{d['doc_type']}{' dated ' + date if date else ''}"
    if d.get("reference_no"):
        s += f" bearing No.{d['reference_no']}"
    if d.get("issued_by"):
        s += f" issued by {d['issued_by']}"
    elif groups:
        s += " in favour of " + " & ".join(n for _, ns in groups for n in ns)
    return s + "."


# ── SEARCH REPORT ──────────────────────────────────────────────────────────────
def _igr_entry_text(e: dict) -> str:
    parties = e.get("parties_en") or []
    groups = []
    for p in parties:
        if groups and groups[-1][0] == p.get("role"):
            groups[-1][1].append(p.get("name", ""))
        else:
            groups.append([p.get("role", ""), [p.get("name", "")]])
    date = e.get("exec_date") or e.get("reg_date")
    s = f"{e.get('doc_type_en') or english.to_english(e.get('docname_raw') or '')} dated {date}"
    if groups:
        s += " executed between " + " and ".join(f"{' & '.join(n)} as ‘{r}’" for r, n in groups)
    s += (f", duly registered with the Office of the {e.get('sro')}"
          f"{' on ' + e['reg_date'] if e.get('reg_date') and e.get('reg_date') != date else ''}"
          f" under Sr. No.{e['docno']}/{e['year']}.")
    if e.get("property_en"):
        s += f" The said document relates to {e['property_en'].rstrip('.')}."
    if e.get("consideration") and e["consideration"] != "0":
        s += f" Consideration {rs(e['consideration'])}."
    if e.get("class") == "B":
        s += " (Transaction relating to the underlying land.)"
    if e.get("class") == "D":
        s += " [Possible match — the available IGR extract does not conclusively identify the subject premises; original document to be verified.]"
    if e.get("discrepancy"):
        s += f" [Note: {e['discrepancy']}]"
    return s


def search_report(analysis: dict, drafted: dict) -> bytes:
    doc = _new_doc()
    _footer_note(doc)
    date = analysis["report_date"]
    ig = analysis["igr"]
    sp = analysis["subject_property"]
    _letterhead(doc, "", date)
    _para(doc, "SEARCH REPORT", "center", 14, bold=True, underline=True, after=10)
    _to_block(doc, analysis["bank"], "The Manager,")
    _para(doc, f"**Property:** {sp.get('full_description', '')} (Hereinafter referred as the said Premises).", "justify")
    acct = " & ".join(analysis["current_owner"]["names"]) or analysis.get("applicant", "")
    _para(doc, f"**A/C: {acct.rstrip('.')}.**")
    sro_loc = next((e["sro"] for e in ig["entries"] if e.get("sro")), "") or "the concerned Sub-Registrar"
    years = ig["period_to"] - ig["period_from"]
    _para(doc, (f"We have caused to take online search of the above-mentioned property for the period of {years} years "
                f"in the igrmaharashtra.gov.in i.e. Official website of State Government of Maharashtra i.e. for the "
                f"period from {ig['period_from']} to {ig['period_to']} at the Office of {sro_loc}."), "justify")
    by_year = {}
    for e in ig["entries"]:
        by_year.setdefault(e.get("year"), []).append(e)
    rows = []
    for y in range(ig["period_from"], ig["period_to"] + 1):
        if y not in ig["coverage_years"]:
            rows.append([str(y), "IGR data for the said year was not available in the data supplied for search."])
        elif by_year.get(y):
            ents = sorted(by_year[y], key=lambda e: (e.get("exec_date") or "")[-4:] + (e.get("exec_date") or ""))
            rows.append([str(y), "\n\n".join(_igr_entry_text(e) for e in ents)])
        else:
            rows.append([str(y), "NIL"])
    _table(doc, [1000, 7600], ["Years", "Registration Entries (Transactions)"], rows, center=(0,))
    for r in ig.get("receipts", []):
        bits = []
        if r.get("grn"):
            bits.append(f"GRN No. {r['grn']}")
        if r.get("receipt_no"):
            bits.append(f"Receipt No. {r['receipt_no']}")
        line = "Govt. fees paid vide " + " & ".join(bits)
        if r.get("date"):
            line += f" dated {r['date']}"
        if r.get("fee"):
            line += f" ({rs(r['fee'])})"
        if r.get("period_from"):
            line += f" for search period {r['period_from']} to {r.get('period_to', '')}"
        _para(doc, line + ".")
    for title, items in (("SEARCH OBSERVATIONS", drafted.get("search_observations")),
                         ("DOCUMENTS REQUIRED / TO BE VERIFIED", [x["description"] if isinstance(x, dict) else x
                                                                 for x in drafted.get("documents_to_obtain", [])]),
                         ("ORAL CONFIRMATION REQUIRED FROM BANK / APPLICANT", drafted.get("oral_confirmations"))):
        if items:
            _para(doc, title, bold=True, underline=True, after=4)
            for i, it in enumerate(items, 1):
                _para(doc, f"{i}.  {it}", "justify", indent=400)
    _para(doc, "")
    _para(doc, config.FIRM_SIGNATORY, bold=True, after=30)
    _para(doc, "Authorised Signatory", bold=True)
    return _save(doc)


# ── TITLE PACKAGE: Special Report + Certificate + Chain of Title ──────────────
def title_report(analysis: dict, drafted: dict, ref: str) -> bytes:
    doc = _new_doc()
    _footer_note(doc)
    date = analysis["report_date"]
    sp = analysis["subject_property"]
    ig = analysis["igr"]
    prop = sp.get("full_description", "")
    owner = drafted.get("belonging_to") or " & ".join(analysis["current_owner"]["names"])
    docs = analysis["documents"]

    # 1. SPECIAL REPORT
    _letterhead(doc, ref, date)
    _para(doc, "SPECIAL REPORT ON TITLE", "center", 14, bold=True, underline=True, after=10)
    _para(doc, f"**Reg: {prop}**", "justify")
    _para(doc, f"**Belonging to: {owner.upper().rstrip('.')}.**", after=10)
    sr = drafted["special_report"]
    rows = []
    for k, q in QUESTIONS.items():
        if k == "B1":
            rows.append(["B", "", "**INVESTIGATIONS:**", ""])
        rows.append(["A" if k == "A1" else "", f"{int(k[1:])}.", q, sr.get(k, "")])
    _table(doc, [450, 900, 3650, 3600], ["", "Sr. No.", "Aspect to be considered", "Counsel’s Statement"], rows,
           center=(0, 1))
    _signature(doc, date)

    # 2. CERTIFICATE
    _page_break(doc)
    _letterhead(doc, ref, date)
    _para(doc, "CERTIFICATE:", "center", 14, bold=True, underline=True, after=10)
    _to_block(doc, analysis["bank"])
    _para(doc, f"**Reg: {prop}**", "justify")
    _para(doc, f"**Belonging to: {owner.upper().rstrip('.')}.**", after=10)
    enc = analysis["encumbrances"]
    years = ig["period_to"] - ig["period_from"]
    cert = [
        "As requested, I have conducted the legal investigation of the title and made a search of records in the registration office and other offices as required in the matter.",
        "I have answered all the queries in the Special Report which is enclosed.",
        "I hereby certify that, the registration particulars, numbers, date and page particulars etc. as shown in the original title deed and contents thereof tally with the information as stated in the record of office of Sub Registrar of Assurances, as well as with certified copy of the title deeds, which was obtained by me is enclosed with this Certificate.",
        "I further, certify that, the photograph of previous owner and of intending mortgagor affixed (seen) in the title deed tally with record of registration office as well as certified copy of the title deed.",
        "Chain of Title relating to the property is complete as given in the Annexure hereto." if not any(
            not d["furnished"] for d in docs if d["category"] == "title_transfer")
        else "Chain of Title relating to the property is as given in the Annexure hereto, subject to the documents to be obtained as stated therein.",
        "I have verified, tallied and compared the documents from the record of the office of Sub Registrar of Assurances and also from the records of other appropriate authorities.",
        "We shall be liable /responsible, if any loss is caused to the bank due to negligence on my part in making the search and bank has the unqualified right to publish my name for including in the caution list being maintained by the INDIAN BANK’S ASSOCIATION OR RESERVE BANK OF INDIA OR ANY OTHER SUCH BODY for circulation amongst Banks/Financial Institution.",
        f"The Search Report of which is annexed hereto, conducted by me, for the period from {ig['period_from']} to {ig['period_to']} ({years} years) "
        + ("does not disclose any encumbrances as stated therein." if not enc else "discloses the entries stated therein.")
        + (f" IGR data for the year(s) {', '.join(map(str, ig['missing_years']))} was not available in the data supplied." if ig["missing_years"] else ""),
        "I have not given opinion earlier on investigation of title relating to the same property.",
        "I find following defects/No defects in the title of the person offering mortgage:",
    ]
    for c in cert:
        _para(doc, c, "justify")
    for i, dft in enumerate(drafted.get("defects") or ["No defects."], 1):
        _para(doc, f"{i}.  {dft}", "justify", indent=400)
    _para(doc, drafted.get("certificate_opinion", ""), "justify")
    dep_rows = _deposit_rows(docs, drafted)
    _table(doc, [650, 1450, 5250, 1250], ["Sr. No.", "Date of Document", "Name of the Document", "Original/ Xerox"],
           dep_rows, center=(0, 1, 3), intro="The valid mortgage can be created by deposit of the following original title deeds. "
                           "The said title deeds are original and genuine and not duplicate or fake as observed by me.")
    _precautions(doc, drafted)
    _para(doc, "Encl: 1. Special Report.  2. Chain of Title.  3. Search Report.")
    _signature(doc, date)

    # 3. CHAIN OF TITLE
    _page_break(doc)
    _letterhead(doc, ref, date)
    _para(doc, "CHAIN OF TITLE:", "center", 14, bold=True, underline=True, after=10)
    _to_block(doc, analysis["bank"])
    _para(doc, f"**Reg: {prop}**", "justify")
    _para(doc, f"**Belonging to: {owner.upper().rstrip('.')}.**", after=10)
    per_rows = [[f"{i:02d}.", d.get("exec_date") or d.get("reg_date") or "", describe(d), d["original_or_copy"]]
                for i, d in enumerate(docs, 1)]
    _table(doc, [650, 1450, 5250, 1250], ["Sr. No.", "Date of Document", "Name of the Document", "Original/ Xerox"],
           per_rows, center=(0, 1, 3), intro="As instructed by your good selves, I have conducted legal investigation of the title and took "
                           "the search of records pertaining to the said Premises. I have perused the documents/copies of documents viz.")
    sro_loc = next((d.get("sro_display") for d in reversed(docs) if d.get("sro_display")), "the concerned Sub-Registrar")
    status = drafted.get("title_status", "clear_subject_to")
    qual = {"clear": "Title of the said Premises is clear and marketable as per law.",
            "clear_subject_to": "Title of the said Premises is clear and marketable as per law, subject to compliance of the points stated hereinbelow.",
            "defective": "Title of the said Premises is NOT clear and marketable for the reasons stated hereinbelow.",
            "not_established": "Title of the said Premises is not established from the documents provided, for the reasons stated hereinbelow."}
    _para(doc, f"I have completed the said investigation on **{date}** in the office of **{sro_loc}** in relation to the said Premises. "
               + qual.get(status, qual["clear_subject_to"]), "justify")
    _para(doc, "Now, on the basis of the aforesaid, I am giving report of investigation of the said Premises, which also includes Chain of Title as under;", "justify")
    _para(doc, f"**From {ig['period_from']} to {ig['period_to']} ({years} Years):**", underline=False)
    for i, ptxt in enumerate(drafted.get("chain_paragraphs", [])):
        _para(doc, f"**{_alpha(i)})**  {ptxt}", "justify", indent=500)
    _para(doc, "On the basis of the same as well as documents/copies of documents perused by me as aforesaid I have to report as follows:", "justify")
    _para(doc, f"1.  {drafted.get('opinion_paragraph', '')}", "justify", indent=400)
    _para(doc, "2.  The valid Mortgage can be created by deposit of the original title deeds and copies of documents.", "justify", indent=400)
    _table(doc, [650, 1450, 5250, 1250], ["Sr. No.", "Date of Document", "Name of the Document", "Original/ Xerox"],
           dep_rows, center=(0, 1, 3))
    _para(doc, "SCHEDULE OF PROPERTY:", bold=True, underline=True)
    _para(doc, prop, "justify")
    if drafted.get("unverified_numbers"):
        _para(doc, "[Reviewer note — numbers in the drafted text not found in source data, verify before issue: "
              + ", ".join(drafted["unverified_numbers"]) + "]", italic=True, size=9)
    _signature(doc, date)
    return _save(doc)


def _deposit_rows(docs, drafted):
    rows = []
    for d in docs:
        if d["original_or_copy"] in ("Original", "Xerox copy"):
            rows.append([d.get("exec_date") or d.get("reg_date") or "", describe(d), d["original_or_copy"]])
    for x in drafted.get("documents_to_obtain", []):
        if isinstance(x, dict):
            rows.append(["", f"{x.get('description', '')} (to be obtained)", x.get("original_or_copy", "Original")])
        elif x:
            rows.append(["", f"{x} (to be obtained)", "Original"])
    return [[f"{i:02d}."] + r for i, r in enumerate(rows, 1)]


def _precautions(doc, drafted):
    items = drafted.get("precautions") or []
    if not items:
        return
    _table(doc, [900, 7700], ["Sr. No.", "Precaution to be taken by Bank before disbursement of the loan amount:"],
           [[f"{i:02d}.", p] for i, p in enumerate(items, 1)], center=(0,))


def _alpha(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s
