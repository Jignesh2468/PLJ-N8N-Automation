"""Reading one IGR search entry into facts — what a lawyer reads in it before judging it:
the kind of deed, who gives and who receives, the unit(s) it names, the building / society / project it names,
the land numbers (and whether 'part'), and earlier documents it cites.

Only LANGUAGE knowledge lives here (Marathi / English deed names, how units and land numbers are written).
Nothing here decides whether an entry belongs to a property — that is search.py, from these facts.
"""
import re
from typing import Dict, List

from app.common import ascii_digits, docno_key
from app.igr import _split_names, land_refs

from .names import party_kind, split_agent, split_alias

# ── what kind of deed (glossary: Marathi / English names used in the IGR index) ──
_KINDS = [  # (family, label, pattern) — first match wins; order: specific before general
    ("release_charge", "Reconveyance / release of mortgage",
     r"रिकन्व्ह|reconvey|पुनर्हस्तांतरण|release\s+of\s+(?:mortgage|charge)|satisfaction|discharge|प्रत्यार्पण|प्रत्यातरण"),
    ("mortgage", "Mortgage", r"गहाण|तारण|mortgage|deposit\s+of\s+title|hypothec|equitable|मॉर्गेज|मॉरगेज|चार्ज"),
    ("development", "Development Agreement", r"विकसन|development\s+agreement|developement"),
    ("poa", "Power of Attorney", r"मुखत्यार|power\s+of\s+attorney|\bpoa\b"),
    ("paaa", "Permanent Alternate Accommodation Agreement", r"पर्यायी\s*जागे|alternate\s+accommodation"),
    ("release_rights", "Release Deed", r"हक्क\s*सोड|हक्कसोड|release|relinquish|परिमोचन"),
    ("gift", "Gift Deed", r"बक्षी|gift"),
    ("partition", "Partition Deed", r"वाटणी|वाटप|partition"),
    ("exchange", "Exchange Deed", r"अदलाबदल|exchange|एक्सचें"),
    ("lease", "Lease / Licence", r"भाडेपट्टा|लीज|\blease|licen[cs]e|लिव्ह|भाडे"),
    ("rectification", "Rectification Deed", r"दुरुस्ती|rectif|correction"),
    ("cancellation", "Cancellation Deed", r"रद्द|cancel"),
    ("conveyance", "Deed of Conveyance", r"अभिहस्तां|अभिहस्तातं|अभिहस्तंा|conveyance|transfer\s+deed|deed\s+of\s+transfer|हस्तांतरण"),
    ("sale", "Sale Deed", r"खरेदी|विक्री|sale\s+deed|सेल\s*डीड|kharedi"),
    ("agreement", "Agreement (for Sale)", r"करारनामा|साठेखत|agreement|करार"),
    ("declaration", "Declaration / Affidavit / Undertaking",
     r"घोषणा|प्रतिज्ञा|declaration|affidavit|undertaking|हमीपत्र|बंधपत्र|bond|indemnity|क्षतिपूर्ती"),
]
_TDR = re.compile(r"टी\.?\s*डी\.?\s*आर|डी\.?\s*आर\.?\s*सी|\bT\.?\s*D\.?\s*R\b|\bD\.?\s*R\.?\s*C\b|transferable\s+development", re.I)
TITLE_FAMILIES = {"conveyance", "sale", "agreement", "development", "gift", "partition", "exchange", "release_rights",
                  "lease", "paaa"}


def deed_kind(docname: str) -> Dict[str, str]:
    t = docname or ""
    for fam, label, rx in _KINDS:
        if re.search(rx, t, re.I):
            return {"family": fam, "label": label}
    return {"family": "other", "label": (docname or "").strip()[:60] or "Unknown"}


# ── parties ─────────────────────────────────────────────────────────────────────
_CONFIRMING = re.compile(r"मान्यता\s*देणार|confirming|कन्फर्मिंग|consenting", re.I)
_BORROWER = re.compile(r"कर्ज\s*घेणार|borrower|mortgagor|मॉरगेजर(?!\s*ी)|मोर्गेजर", re.I)
_LENDER_MARK = re.compile(r"कर्ज\s*देणार|lender|mortgagee|मॉरगेजी|मोर्गेजी|trustee|ट्रस्टी", re.I)


# role words and numbering written INTO the name column ('X मोर्गेजर २ / बॉरोअर - लिहून देणार'), and Marathi case
# endings after a name ('… डेव्हलपर्स च्या', '… एलएलपीचे') — grammar, not part of the name
_ROLE_WORDS = re.compile(r"\(?\s*(?:मोर्गेजर|मॉरगेजर|मॉर्गेजर|मॉरगेजी|मोर्गेजी|बॉरोअर|बोरोअर|लिहून\s*देणार|लिहून\s*घेणार|"
                         r"कर्ज\s*घेणार|कर्ज\s*देणार|मान्यता\s*देणार|ऑब्लाईजर|ऑब्लीजी|मालक|सिक्युरिटी\s*ट्रस्टी|"
                         r"borrower|mortgagor|mortgagee|confirming\s+party|vendor|purchaser|executant|claimant|obliger|"
                         r"obligee|owner|security\s+trustee)\s*[\d१२३४५६७८९०]*\s*\)?\s*[/\-–:]*", re.I)
_CASE_END = re.compile(r"\s*(?:च्या|चे|ची|चा|यांच्या|यांचे|यांची)\s*$")


def _clean_party(name: str) -> str:
    t = _ROLE_WORDS.sub(" ", name or "")
    t = re.sub(r"\s+", " ", t).strip(" -–/:,.\"'")
    t = re.sub(r"(?<=[ऀ-ॿ])(?:च्या|चे|ची)$", "", t)       # 'एलएलपीचे' → 'एलएलपी'
    return _CASE_END.sub("", t).strip(" -–/:,.")


def side_parties(raw: str) -> List[dict]:
    out = []
    for x in _split_names(raw):
        party, agent = split_agent(x)
        main, former = split_alias(_clean_party(party))
        if len(re.sub(r"\W", "", main)) < 2:
            continue
        out.append({"name": main, "former": former, "signatory": agent, "kind": party_kind(main),
                    "confirming": bool(_CONFIRMING.search(x)), "borrower": bool(_BORROWER.search(x)),
                    "lender": bool(_LENDER_MARK.search(x)) or party_kind(main) == "lender"})
    return out


# ── units, buildings, names, land, citations in the property description ───────
_UNIT_WORD = (r"(?:सदनिका|फ्लॅट|फ्लॅट|फ़्लॅट|फ़्लैट|फ्लैट|फ्लेट|फलॅट|flat|apartment|गाळा|गाला|gala|दुकान|दूकान|शॉप|shop|ऑफिस|office|युनिट|यूनिट|unit|"
              r"रूम|रुम|room|खोली|टेनामेंट|टेनमेंट|tenement|बंगला|bungalow|row\s*house|पार्किंग|parking|गॅरेज|garage)")
# a wing letter may be Latin ('A-1001') or Marathi ('ए/1001', 'बी-2505') before the number
_NUMTOK = (r"(?:(?:[A-Za-z]{1,2}|[ऀ-ॿ]{1,3})\s?[-/]\s?|[A-Za-z]{1,2}\s?)?\d{1,5}"
           r"(?:\s?[-/]?\s?[A-Za-z](?![A-Za-z]))?")
_UNIT_RX = re.compile(_UNIT_WORD + r"\s*\.?\s*(?:नंबर|क्रमांक|क्रं|क्र|नं|न|no|nos|number|numbers)?\s*\.?\s*[:\-]{0,2}\s*"
                      r"(" + _NUMTOK + r"(?:\s*(?:,|आणि|and|&|व|ते|to)\s*" + _NUMTOK +
                      r"(?!\d)(?!\s*(?:वा|ला|रा|था|वी|st|nd|rd|th|मजला|माळा|floor|flr)))*)", re.I)
_BLDG_NO_RX = re.compile(r"(?:बिल्डिंग|बिल्डींग|बिल्डिंग|इमारत|ईमारत|building|bldg|tower|टॉवर|wing|विंग|chawl|चाळ|बि)\s*\.?\s*"
                         r"(?:नंबर|नं|न|क्रं|क्र|क्रमांक|no|number)\s*\.?\s*[:\-]?\s*([A-Za-z]{0,3}\s?-?\d{1,4}[A-Za-z]?)", re.I)
# a name followed by the word that makes it a society / building / project ('शांतीनगर गोकुळ दर्शन को ऑ हौ सो लि')
_NAMED_RX = re.compile(
    r"((?:[^\s,;:(){}\"\d][^\s,;:(){}\"]*\s+){1,5}?)(?:को\s*-?\s*ऑ(?:प|परेटिव्ह|परेटीव्ह)?\.?\s*(?:हौ|हा|हाउ)|co\s*-?\s*op|"
    r"सहकारी\s*गृहनिर्माण|सी\s*\.?\s*एच\s*\.?\s*एस|\bc\.?h\.?s\b|हौसिंग\s*सोसायटी|सोसायटी|society|"
    r"अपार्टमेंट|apartment|हाईट्स|हाइट्स|heights|टॉवर्स|towers|रेसिडेन्सी|residency|प्रोजेक्ट|project|"
    r"कॉम्प्लेक्स|complex|पार्क\b|park\b|प्लाझा|plaza)", re.I)
_BLDG_NAME_RX = re.compile(r"(?:इमारतीचे\s*नाव|building\s*name|name\s*of\s*(?:the\s*)?building)\s*[:\-]?\s*([^,;]{2,60})", re.I)
_CITE_RX = re.compile(r"(?:दस्त\s*(?:क्र|क्रमांक|नं)\.?|doc(?:ument)?\.?\s*no\.?|sr\.?\s*no\.?)\s*[:\-]?\s*"
                      r"(?:[^\d\s]{1,8}[\s\-]?\d?\s*[-/ ]\s*)?(\d{1,6})\s*[-/ ]\s*((?:19|20)\d{2})", re.I)
_PART_RX = r"\s*\(?\s*(?:part|pt\.?|p\.(?=\s|\))|पार्ट|पैकी|पै\.|पै\b)"


# home-loan index format: the unit first — '904 SHREE …', '1B/1207 J P NORTH ALEXA', '1402/1B JP NORTH', 'A-1801 …'
_LEAD_UNIT = re.compile(r"^\W*((?:\d?[A-Za-z]{1,2}\s?[-/]\s?)?\d{1,5}(?:\s?[-/]\s?\d?[A-Za-z]{1,2}(?![A-Za-z]))?"
                        r"(?:\s?[A-Za-z](?![A-Za-z]))?)(?:\s*[,\-.]\s*|\s+)"
                        r"(?!(?:sq|square|acre|ft|feet|mtr|meter|metre)(?![A-Za-z])|चौ|हे\.|हेक्टर|आर\s|गुंठे|एकर|रोजी|"
                        r"साली|सालचा|पासून|दिनांक|वर्ष)"
                        r"(?=\S)", re.I)
# Marathi / English function words that can sit before 'सोसायटी' without being part of a name
_STOP = re.compile(r"^(?:सोबत|व|आणि|चे|ची|च्या|चा|या|सदर|त्या|मध्ये|येथील|येथिल|हे|ही|तर्फे|आहे|असलेल्या|"
                   r"the|of|with|and|said|in|at|for|from|to|by|its|their)$", re.I)


# a unit CODE at the start: '1-B/2108 …', '409/बी/4751, …', '409/C/4776 SHANTIVAN', '1/B-2505 JP …'
_LEAD_CODE = re.compile(r"^\W*((?:[0-9A-Za-zऀ-ॿ]{1,6}\s?[-/]\s?){1,3}[A-Za-zऀ-ॿ]{0,2}\d{2,5}[A-Za-z]?)"
                        r"(?=[\s,]|$)")
# building / block / tenement anywhere in the text: '409/बी/4751', '409बी/4757', '409/C/4776'
_TENEMENT_CODE = re.compile(r"(?<![\d/])\d{2,4}\s*/?\s*(?:[A-Za-z]{1,2}|[ऀ-ॿ]{1,3})\s*/\s*(\d{3,5})(?![\d/])")
# floor first, then the unit: '24TH FLR 2408, 1A WING …'
_FLOOR_UNIT = re.compile(r"^\W*\d{1,3}\s*(?:st|nd|rd|th)?\s*(?:flr|floor|मजला)\.?\s*[,\-]?\s*(\d{2,5}[A-Za-z]?)\b", re.I)


def _units(text: str) -> List[str]:
    out = []
    code, fl = _LEAD_CODE.match(text or ""), _FLOOR_UNIT.match(text or "")
    lead = _LEAD_UNIT.match(text or "")
    if fl:
        out.append(fl.group(1))
    elif code and re.search(r"[/\-]", code.group(1)) and not re.search(r"(?:19|20)\d{2}\s*$", code.group(1)) and \
            not re.match(r"^\W*\d{1,4}\s*/\s*\d{1,3}\s*(?:,|$|\s+(?:क्षेत्र|area))", text):
        out.append(re.sub(r"\s", "", code.group(1)))     # (a survey '114/6 क्षेत्र' is land, not a unit code)
    elif lead:
        out.append(re.sub(r"\s", "", lead.group(1)))    # '904 SHREE KRISHNA HEIGHTS …' — home-loan index format
    for m in _TENEMENT_CODE.finditer(text):
        out.append(m.group(1))                        # MHADA 'building/block/tenement' — '409/बी/4751'
    for m in _UNIT_RX.finditer(text):
        for n in re.split(r"\s*(?:,|आणि|and|&|व)\s*", m.group(1)):
            rng = re.match(r"\s*(\d+)\s*(?:ते|to)\s*(\d+)\s*$", n)
            if rng and int(rng.group(2)) - int(rng.group(1)) <= 200:
                out += [str(i) for i in range(int(rng.group(1)), int(rng.group(2)) + 1)]
            elif n.strip():
                out.append(re.sub(r"\s", "", n))
    # 'wing/unit' composites ('1B/1207', '1402/1B', '409/C/4776'): the unit number is the part with the most digits
    res = []
    for u in out:
        parts = [p for p in re.split(r"[/]", u) if p]
        if len(parts) == 1 and re.fullmatch(r"\d{1,2}-[A-Za-z]{1,2}-?\d{2,5}", u):
            parts = [u.split("-")[0], u.split("-", 1)[1]]          # '1-B-2108'
        res.append(max(parts, key=lambda p: (len(re.sub(r"\D", "", p)), len(p))) if len(parts) > 1 else u)
    return res


def _part(text: str, main: str, sub: str) -> bool:
    pat = re.escape(main) + (r"\s*[/\-]\s*" + r"\s*[/\-]\s*".join(re.escape(x) for x in sub.split("/")) if sub else "")
    return bool(re.search(r"(?<![\d/])" + pat + r"(?![\d/])" + _PART_RX, text, re.I))


def read_row(row: dict) -> dict:
    prop = ascii_digits(row.get("property_raw") or "")
    kind = deed_kind(row.get("docname_raw") or "")
    if _TDR.search(prop) or _TDR.search(row.get("docname_raw") or ""):
        # a sale of TDR / a DRC certificate generated from land — development rights, not the land itself
        kind = {"family": "tdr", "label": "Transfer of TDR / DRC (development rights certificate)"}
    sellers, buyers = side_parties(row.get("seller_raw") or ""), side_parties(row.get("purchaser_raw") or "")
    lands = []
    for k, m, s in land_refs(prop):
        lands.append({"kind": k, "main": m, "sub": s or "", "part": _part(prop, m, s or "")})
    named = []
    for m in list(_NAMED_RX.finditer(prop)) + list(_BLDG_NAME_RX.finditer(prop)):
        nm = re.sub(r"^(?:मौजे|मौज्े|मु\.?|ता\.?|गाव|village|in|at|of|the|known\s+as)\s+", "", m.group(1).strip(), flags=re.I)
        words = [w for w in re.split(r"\s+", nm.strip(" .-:")) if w and not _STOP.match(w)]
        nm = " ".join(words)
        if len(re.sub(r"\W", "", nm)) >= 3 and nm not in named:
            named.append(nm)
    cites = []
    for m in _CITE_RX.finditer(prop):
        k = docno_key(m.group(1), m.group(2))
        if k and k not in cites:
            cites.append(k)
    return {"id": row["id"], "docno": row.get("docno"), "year": row.get("year"), "reg_date": row.get("reg_date"),
            "exec_date": row.get("exec_date"), "office": row.get("sro") or row.get("sro_raw"),
            "docname": (row.get("docname_raw") or "").strip(), "kind": kind,
            "sellers": sellers, "buyers": buyers, "units": _units(prop),
            "building_nos": [re.sub(r"\s", "", m.group(1)) for m in _BLDG_NO_RX.finditer(prop)],
            "named": named, "land": lands, "cites": cites, "text": prop,
            "consideration": row.get("consideration"), "source_file": row.get("source_file")}
