"""Make every report-facing value English.

IGR Maharashtra data is largely Marathi (Devanagari). Before drafting and before legalinfo_ai.json is written,
every string that still contains Devanagari is converted:
  1. known document types / legal terms  → fixed English dictionary (exact, no AI)
  2. everything else (names, property descriptions) → one batched AI call (ChatGPT, Claude as fallback):
     names are TRANSLITERATED (रुपनवल डेव्हलपर्स → Roopnaval Developers), descriptions TRANSLATED, numbers kept
  3. anything the AI could not convert → rule-based transliteration, so no Devanagari ever reaches a report.
Keys ending in "_raw" keep the original Marathi IGR text as evidence inside legal_analysis_ai.json only.
"""
import json
import logging
import re
from typing import Dict, List

from . import llm
from .common import ascii_digits

log = logging.getLogger("plj.english")
DEV = re.compile(r"[ऀ-ॿ]")

# ── 1. fixed dictionary: document types and common legal words ─────────────────
DOC_TYPES = {
    "करारनामा": "Agreement for Sale", "अभिहस्तांतरणपत्र": "Deed of Conveyance", "अभिहस्तातंरणपत्र": "Deed of Conveyance",
    "करारनामा किंवा त्याचे अभिलेख किंवा करार संक्षेपलेख": "Agreement (or Record / Memorandum of Agreement)",
    "घोषणा पत्र": "Declaration",
    "अभिहस्तांतरण पत्र": "Deed of Conveyance", "खरेदीखत": "Sale Deed", "खरेदी खत": "Sale Deed", "गहाणखत": "Mortgage Deed",
    "गहाण खत": "Mortgage Deed", "हक्कसोडपत्र": "Release Deed", "हक्क सोड पत्र": "Release Deed", "रिलीज डीड": "Release Deed",
    "बक्षीसपत्र": "Gift Deed", "बक्षिसपत्र": "Gift Deed", "रिकन्व्हेन्स": "Reconveyance Deed", "पुनर्हस्तांतरण": "Reconveyance Deed",
    "कुलमुखत्यारपत्र": "Power of Attorney", "कुलमुखत्यार पत्र": "Power of Attorney", "विकसनकरारनामा": "Development Agreement",
    "विकसन करारनामा": "Development Agreement", "भाडेपट्टा": "Lease Deed", "भाडेपट्टाकरार": "Lease Deed", "वाटणीपत्र": "Partition Deed",
    "मृत्युपत्र": "Will", "अदलाबदलपत्र": "Exchange Deed", "घोषणापत्र": "Declaration", "प्रतिज्ञापत्र": "Affidavit",
    "लिव्ह अॅड लायसन्सेस": "Leave and Licence", "लिव्ह अॅन्ड लायसन्सेस": "Leave and Licence",
    "36-अ-लिव्ह अॅड लायसन्सेस": "Leave and Licence", "(36-अ)लिव्ह अॅड लायसन्सेस": "Leave and Licence",
    "सूची क्र.2": "Index-II", "सूची क्र. 2": "Index-II", "दस्त गोषवारा": "Dast Goshwara", "पावती": "Registration Receipt",
    "ट्रान्सफर डीड": "Transfer Deed", "लीजडीड": "Lease Deed", "लीज डीड": "Lease Deed", "सेल डीड": "Sale Deed",
    "कन्व्हेन्स डीड": "Conveyance Deed", "रिकन्व्हेन्स": "Reconveyance Deed", "कन्फर्मेशन डीड": "Confirmation Deed",
    "डीड ऑफ एक्सेंज": "Deed of Exchange", "डीड ऑफ एक्सचेंज": "Deed of Exchange", "अदलाबदल पत्र": "Deed of Exchange",
    "डीड ऑफ मॉरगेज": "Mortgage Deed", "मॉरगेज डीड": "Mortgage Deed", "फर्दर चार्ज": "Further Charge",
    "चुक दुरुस्ती पत्र": "Rectification Deed", "65-चुक दुरुस्ती पत्र": "Rectification Deed",
    "पुरवणी करारनामा": "Supplementary Agreement", "पुरवणी करार": "Supplementary Agreement",
    "सेल डीड": "Sale Deed", "विकसनकरारनामा": "Development Agreement", "विकसन करारनामा": "Development Agreement",
    "क्षतिपूर्ती बंधपत्र": "Indemnity Bond", "35-क्षतिपूर्ती बंधपत्र": "Indemnity Bond",
    "अविभाज्य हिश्याची पूर्ण विक्री": "Sale Deed (undivided share)", "अविभाज्य हिश्याचे पूर्ण बक्षिस": "Gift Deed (undivided share)",
    "परिमोचनपत्र (रिलीज)": "Release Deed", "परिमोचनपत्र": "Release Deed", "रद्दलेख": "Cancellation Deed", "रद्द लेख": "Cancellation Deed",
    "करारनामा किंवा त्याचे अभिलेख किंवा ज्ञापन": "Agreement", "विक्री करार": "Agreement for Sale",
    "गाव नमुना सात": "7/12 Extract", "सात बारा": "7/12 Extract", "७/१२": "7/12 Extract",
}
WORDS = {  # applied inside longer text before the AI step (helps the AI and the fallback)
    "मौजे": "Village", "गाव": "Village", "तालुका": "Taluka", "जिल्हा": "District", "सर्वे नं": "Survey No.",
    "सर्व्हे नं": "Survey No.", "स.न.": "S.No.", "हि.न.": "H.No.", "हिस्सा नं": "Hissa No.", "युनिट नं": "Unit No.",
    "गाळा नं": "Gala No.", "सदनिका नं": "Flat No.", "माळा नं": "Floor", "मजला": "Floor", "तळ मजला": "Ground Floor",
    "पहिला मजला": "First Floor", "दुसरा मजला": "Second Floor", "तिसरा मजला": "Third Floor", "इमारतीचे नाव": "Building",
    "क्षेत्र": "Area", "चौ.मी.": "sq.m.", "चौ फुट": "sq.ft.", "बिल्ट अप": "built-up", "तर्फे": "through",
    "भागीदार": "Partner", "डायरेक्टर": "Director", "मेसर्स": "M/s.", "मे.": "M/s.", "ग्रुप": "Group", "प्रा.लि.": "Pvt. Ltd.", "बँक": "Bank", "बॅक": "Bank",
    "सहकारी": "Co-operative", "शाखा": "Branch", "वसई": "Vasai", "सातिवली": "Sativali", "सातीवली": "Sativali",
    "पालघर": "Palghar", "ठाणे": "Thane", "पूर्व": "East", "पश्चिम": "West", "दु.नि.": "Sub-Registrar", "सह दु.नि.": "Joint Sub-Registrar",
    "इतर माहिती": "Other details", "लिहून देणार": "Executant", "लिहून घेणार": "Claimant", "मान्यता देणार": "Confirming Party",
    "कर्ज घेणार": "Borrower", "कर्ज देणार": "Lender", "उपलब्ध नाही": "not available", "उपलब्द नाही": "not available",
    "हौसिंग": "Housing", "हाउसिंग": "Housing", "फायनान्स": "Finance", "फायनांस": "Finance",
    "प्रायवेट लिमिटेड": "Private Limited", "प्राइवेट लिमिटेड": "Private Limited", "प्रा.लि.": "Pvt. Ltd.", "लिमिटेड": "Limited",
    "अधिकृत सही करणार": "Authorised Signatory", "अधिकृत हस्ताक्षरी": "Authorised Signatory", "पूर्वीचे नाव": "formerly",
    "लिहून घेणार -": "", "लिहून देणार -": "",
}


def has_dev(s) -> bool:
    return isinstance(s, str) and bool(DEV.search(s))


def _dict_pass(s: str) -> str:
    t = ascii_digits(s).strip()
    key = re.sub(r"\s+", " ", t)
    if key in DOC_TYPES:
        return DOC_TYPES[key]
    for k in sorted(WORDS, key=len, reverse=True):
        t = t.replace(k, WORDS[k])
    # 'मे युनिवर्स इंटरप्राईजेस' (no dot) = M/s. — only as a whole word before a name, never inside 'मेहता'
    t = re.sub(r"(?:(?<=^)|(?<=[\s,{\"]))मे(?=\s+[ऀ-ॿ])", "M/s.", t)
    return t


# ── 3. rule-based transliteration (last resort; never fails) ──────────────────
_V = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ru", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
      "ऑ": "o", "ॲ": "a", "अॅ": "a", "ऍ": "e", "ऎ": "e", "ऒ": "o", "ॠ": "ru", "ऌ": "l"}
_M = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ru", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o",
      "ॅ": "a", "ं": "n", "ँ": "n", "ः": "h", "ॆ": "e", "ॊ": "o", "ॄ": "ru"}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "z", "ञ": "n",
      "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
      "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
      "श": "sh", "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f"}


def transliterate(s: str) -> str:
    out, i, s = [], 0, ascii_digits(s or "")
    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if ch in _C:
            out.append(_C[ch])
            if nxt == "्":
                i += 2
                continue
            if nxt in ("ं", "ँ"):
                out.append("an")
                i += 2
                continue
            if nxt in _M:
                out.append(_M[nxt])
                i += 2
                continue
            # inherent 'a' — dropped at the end of a word (Marathi/Hindi schwa deletion)
            if nxt and (nxt in _C or nxt in _V):
                out.append("a")
            i += 1
            continue
        if ch in _V:
            out.append(_V[ch])
        elif ch in _M:
            out.append(_M[ch])
        elif ch in "़्":
            pass
        elif ch in "।॥":
            out.append(".")
        elif "ऀ" <= ch <= "ॿ":
            pass                            # any other Devanagari sign: never left in the English output
        else:
            out.append(ch)
        i += 1
    t = "".join(out)
    return re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), t)        # Title Case each word


def translit_mixed(s: str) -> str:
    """Transliterate only the Devanagari runs of a mixed string (English words keep their case)."""
    return re.sub(r"[\u0900-\u097F][\u0900-\u097F\s.]*[\u0900-\u097F]|[\u0900-\u097F]",
                  lambda m: transliterate(m.group(0)), s or "")


# ── 2. AI pass ────────────────────────────────────────────────────────────────
SYSTEM = """You convert Marathi/Hindi text from Maharashtra property records (IGR index rows, deeds) into English
for a bank legal report. Return ONLY JSON: {"items":[{"id":"...","en":"..."}]} with one item per input id.
Rules:
- Names of persons, firms, banks, buildings and villages: TRANSLITERATE into the usual English spelling
  (रुपनवल डेव्हलपर्स → Roopnaval Developers; रमेशकुमार पुखराजजी भाटी → Rameshkumar Pukhrajji Bhati;
  आरेन प्लाझा → Aaren Plaza; सातिवली → Sativali; वसई जनता सहकारी बँक → Vasai Janata Sahakari Bank). Never translate a name's meaning.
- Descriptions: TRANSLATE into plain legal English, keeping every number, unit, area and survey/hissa/CTS exactly
  (स.न.16 हि.न.4 → S.No.16 H.No.4; युनिट नं 202, दुसरा मजला → Unit No. 202, Second Floor).
- Document types: standard English names (करारनामा → Agreement for Sale, गहाणखत → Mortgage Deed).
- Keep English parts unchanged. Do not add, drop or guess information. Output must contain no Devanagari."""


def _ai_map(strings: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i in range(0, len(strings), 60):
        batch = strings[i:i + 60]
        payload = [{"id": f"t{i + j}", "text": s} for j, s in enumerate(batch)]
        try:
            res = llm.call_json("translate_english", SYSTEM,
                                [llm.text_block(json.dumps({"items": payload}, ensure_ascii=False))], max_tokens=8000)
            got = {x.get("id"): x.get("en", "") for x in (res.get("items") if isinstance(res, dict) else res) or []
                   if isinstance(x, dict)}
            for j, s in enumerate(batch):
                en = (got.get(f"t{i + j}") or "").strip()
                if en and not has_dev(en):
                    out[s] = en
        except Exception as e:  # translation must never stop the job — fallback below
            log.warning("english: AI conversion failed for a batch (%s); using transliteration", e)
    return out


def _collect(obj, acc: set, key: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not str(k).endswith("_raw"):
                _collect(v, acc, k)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, acc, key)
    elif has_dev(obj):
        acc.add(obj)


def _apply(obj, mp: Dict[str, str]):
    if isinstance(obj, dict):
        return {k: (v if str(k).endswith("_raw") else _apply(v, mp)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_apply(v, mp) for v in obj]
    if has_dev(obj):
        return mp.get(obj) or translit_mixed(_dict_pass(obj))
    return obj


def to_english(*objs):
    """Convert every Devanagari string in the given structures (one shared map, one AI pass). Returns new objects."""
    acc: set = set()
    for o in objs:
        _collect(o, acc)
    if not acc:
        return objs if len(objs) > 1 else objs[0]
    mp: Dict[str, str] = {}
    pending = []
    for s in acc:
        d = _dict_pass(s)
        if not has_dev(d):
            mp[s] = d
        else:
            pending.append(s)
    if pending:
        ai = _ai_map([_dict_pass(s) for s in pending])
        for s in pending:
            mp[s] = ai.get(_dict_pass(s)) or translit_mixed(_dict_pass(s))
    res = tuple(_apply(o, mp) for o in objs)
    return res if len(res) > 1 else res[0]
