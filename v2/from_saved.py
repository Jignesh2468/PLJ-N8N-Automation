"""Adapter: the documents already read by the production reader (saved legal_analysis_ai.json) → v2 documents.

The saved readings give, for each document, its file and PAGE RANGE, its parties with roles and its property
description as one text. Fields are parsed from that text here, so a fact can be traced to the document and its
page range, but not to the exact page (page_exact = False). The evidence reader (extract_v2.py) gives the exact page
and a quote for every field.

The production 'subject_property' (merged by the AI across documents, with no source) is deliberately NOT used.
"""
import json
import re
from typing import List

from app.common import ascii_digits

# letters only in CAPITALS and joined to the number ('B-1801', 'A2601', '1801B'); never words ('on 2nd floor')
_NUM = r"(?-i:(?:[A-Z]{1,2}\s?[-/]?\s?)?\d+(?:\s?[-/]?\s?[A-Z](?![A-Za-z]))?)(?![\d/])"
_RX = {
    "unit": re.compile(r"\b(flat|apartment|residential\s+unit|unit|shop|office|gala|industrial\s+unit|room|tenement|"
                       r"row\s*house|bungalow|plot)\s*(?:no\.?|number|nos?\.?|bearing\s+(?:no\.?|number))?\s*[:.\-]?\s*"
                       r"(" + _NUM + r")", re.I),
    "unit_mr": re.compile(r"(सदनिका|फ्लॅट|गाळा|दुकान|रूम|खोली)\s*(?:क्र\.?|नं\.?|क्रमांक)?\s*[:.\-]?\s*(" + _NUM + r")"),
    "floor": re.compile(r"\b(\d+)\s*(?:st|nd|rd|th)\s+(?:habitable\s+)?floor|\bfloor\s*no\.?\s*(\d+)|\b(ground|upper\s+ground|"
                        r"lower\s+ground|stilt|podium|first|second|third|fourth|fifth)\s+floor|(\d+)\s*(?:वा|ला|रा|था|वी)\s*मजला|"
                        r"(तळ)\s*मजला", re.I),
    "wing": re.compile(r"\bwing\s*[-:]?\s*['\"‘“]?([A-Z]\d?)\b(?!['’]s)|\b['\"‘“]?([A-Z]\d?)['\"’”]?\s*[-]?\s*wing\b", re.I),
    "bno": re.compile(r"(?:\bbuilding|\bbldg\.?|\btower|\bchawl|बिल्डींग|बिल्डिंग|इमारत|टॉवर|चाळ)\s*(?:no\.?|number|नं\.?|क्र\.?|"
                      r"क्रमांक)\s*[:.\-]?\s*([A-Z]{0,2}\s*-?\d+[A-Z]?)", re.I),
    "bname": re.compile(r"\b(?:building|bldg\.?|tower|complex|wing|premises)\s+(?:known\s+as|named|called|styled\s+as)\s+"
                        r"['\"‘“]?([^,;'\"’”()]{3,60})", re.I),
    "bname2": re.compile(r"(?:building\s*name|name\s+of\s+(?:the\s+)?building|इमारतीचे\s*नाव|in\s+the\s+building)\s*[:\-]?\s*"
                         r"['\"‘“]?([^,;'\"’”()]{3,60})", re.I),
    "proj": re.compile(r"\b(?:project|scheme|layout|complex)\s+(?:known\s+as|named|called|styled\s+as)\s+['\"‘“]?"
                       r"([^,;'\"’”()]{3,60})", re.I),
    "soc": re.compile(r"([A-Z][A-Za-z0-9 .&'\-]{2,60}?)\s+(?:Co\s*-?\s*op(?:erative)?\.?\s*(?:Hsg|Housing)\.?\s*Soc(?:iety)?"
                      r"\.?(?:\s*Ltd\.?|\s*Limited)?|C\.?H\.?S\.?(?:L\.?)?\b|Housing\s+Society)", re.I),
    "old": re.compile(r"(?:formerly|earlier|previously)\s+(?:known\s+as|called)\s+['\"‘“]?([^,;'\"’”()]{3,60})", re.I),
    "carpet": re.compile(r"(\d[\d,]*(?:\.\d+)?\s*(?:sq\.?\s*(?:ft|feet|fts|mtrs?|meters?|metres?|m\b)|square\s+(?:feet|met\w*)))"
                         r"[^,;\d]{0,30}?carpet|carpet[^,;\d]{0,30}?(\d[\d,]*(?:\.\d+)?\s*(?:sq\.?\s*(?:ft|feet|fts|mtrs?|"
                         r"meters?|metres?|m\b)|square\s+(?:feet|met\w*)))", re.I),
    "builtup": re.compile(r"(\d[\d,]*(?:\.\d+)?\s*(?:sq\.?\s*(?:ft|feet|fts|mtrs?|meters?|metres?|m\b)|square\s+(?:feet|met\w*)))"
                          r"[^,;\d]{0,30}?built\s*-?\s*up|built\s*-?\s*up[^,;\d]{0,30}?(\d[\d,]*(?:\.\d+)?\s*(?:sq\.?\s*"
                          r"(?:ft|feet|fts|mtrs?|meters?|metres?|m\b)|square\s+(?:feet|met\w*)))", re.I),
    "village": re.compile(r"(?:\bvillage|\bmouje|\bmauje|\bmoje|मौजे|मौजा|गाव)\s*[:\-]?\s*([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ\- ]"
                          r"{1,30}?)(?=\s*(?:,|\.|;|\)|$|\s+tal|\s+taluka|\s+in\s|\s+within|\s+dist|\s+bearing|\s+district|"
                          r"\s+ता\.|\s+तालुका))", re.I),
    "taluka": re.compile(r"(?:\btaluka|\btal\.|तालुका|ता\.)\s*[:\-]?\s*(?!(?:and|&|district|dist|व|जिल्हा)\b)"
                         r"([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ\-]{1,25})", re.I),
    "district": re.compile(r"(?:\bdistrict|\bdist\.|जिल्हा|जि\.)\s*[:\-]?\s*([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ\- ]{2,30}?)"
                           r"(?=\s*(?:,|\.|;|\)|$|\s+and|\s+within|\s+pin|\s+state))", re.I),
    "pin": re.compile(r"(?<!\d)(4\d{2}\s?\d{3})(?!\d)"),
}


def _first(rx, text, groups=None):
    m = rx.search(text)
    if not m:
        return ""
    for g in (groups or range(1, (m.lastindex or 0) + 1)):
        if m.group(g):
            return m.group(g).strip()
    return ""


def parse_property(text: str) -> dict:
    t = ascii_digits(text or "")
    p = {"text": text or ""}
    # a Final Plot / City Survey plot / TPS plot is a LAND number, never the unit
    tu = re.sub(r"(?:final|f\.?\s*p\.?|t\.?\s*p\.?\s*s\.?|city\s*survey|cts|c\.t\.s\.?|survey|s\.\s*no\.?)\s*plot\s*"
                r"(?:no\.?|number)?\s*[:.\-]?\s*\d+", " ", t, flags=re.I)
    m = next((x for x in _RX["unit"].finditer(tu) if x.group(1).lower() != "plot"), None) or _RX["unit"].search(tu)
    if m:
        p["unit_type"], p["unit_no"] = m.group(1).title(), re.sub(r"\s+", "", m.group(2))
    else:
        m = _RX["unit_mr"].search(t)
        if m:
            p["unit_type"], p["unit_no"] = {"सदनिका": "Flat", "फ्लॅट": "Flat", "गाळा": "Gala", "दुकान": "Shop"}.get(
                m.group(1), "Unit"), re.sub(r"\s+", "", m.group(2))
    p["floor"] = _first(_RX["floor"], t)
    if p["floor"] == "तळ":
        p["floor"] = "ground"
    p["wing"] = _first(_RX["wing"], t)
    p["building_no"] = _first(_RX["bno"], t)
    p["building_name"] = _first(_RX["bname"], t) or _first(_RX["bname2"], t)
    p["project_name"] = _first(_RX["proj"], t)
    soc = _RX["soc"].search(t)
    if soc:
        name = re.sub(r"^(?:the|in|of|known\s+as|named|in\s+the)\s+", "", soc.group(1).strip(), flags=re.I)
        p["society_name"] = name.strip(" '\"‘’“”")
    p["old_name"] = _first(_RX["old"], t)
    p["carpet_area"] = _first(_RX["carpet"], t)
    p["builtup_area"] = _first(_RX["builtup"], t)
    p["village"] = _first(_RX["village"], t)
    p["taluka"] = _first(_RX["taluka"], t)
    p["district"] = _first(_RX["district"], t)
    p["pin"] = re.sub(r"\s", "", _first(_RX["pin"], t))
    return {k: v for k, v in p.items() if v}


def load_saved(path: str) -> List[dict]:
    return docs_from_analysis(json.load(open(path, encoding="utf-8")))


def docs_from_analysis(an: dict) -> List[dict]:
    """The documents of a saved analysis (the live system's legal_analysis_ai.json) as v2 documents."""
    out = []
    for d in an.get("documents", []):
        if d.get("source") == "igr":
            continue                                       # search entries belong to Stage 2, not to the papers
        prop = parse_property(d.get("property_text") or "")
        out.append({
            "ref": d.get("ref"), "doc_type": d.get("doc_type") or "", "category": d.get("category") or "",
            "level": d.get("level") or "", "file": d.get("file") or "", "pages": d.get("pages") or "",
            "exec_date": d.get("exec_date") or "", "reg_date": d.get("reg_date") or "",
            "doc_no": d.get("doc_no_raw") or (f"{d['doc_number']}/{d['doc_year']}" if d.get("doc_number") and d.get("doc_year") else ""),
            "sro": d.get("sro") or "", "furnished": bool(d.get("furnished")),
            "execution_status": "draft" if d.get("draft") else (d.get("execution_status") or ""),
            "parties": [{"name": p.get("name") or "", "role": p.get("role") or ""} for p in d.get("parties") or []
                        if isinstance(p, dict)],
            "property": prop if len(prop) > 1 else {},
            "page_exact": False,
        })
    return out
