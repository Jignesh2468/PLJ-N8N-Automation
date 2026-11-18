"""IGR Maharashtra search data: parse → normalise → pre-filter → AI classify (A–F) → coverage."""
import io
import re
from html.parser import HTMLParser
from typing import Dict, List

from . import config, llm, prompts
from .common import ascii_digits, clean_num, docno_key, fmt_date, norm_text, parse_doc_no_raw, year_of

# canonical column names (header text lower-cased, non-alphanumerics removed)
_COLS = {
    "srocode": "srocode", "internaldocumentnumber": "internaldocumentnumber", "docno": "docno",
    "docname": "docname", "registrationdate": "registrationdate", "sroname": "sroname",
    "sellerparty": "sellerparty", "purchaserparty": "purchaserparty",
    "propertydescription": "propertydescription", "areaname": "areaname",
    "considerationamt": "consideration_amt", "consideration": "consideration_amt",
    "marketvalue": "marketvalue", "dateofexecution": "dateofexecution",
    "stampdutypaid": "stampdutypaid", "registrationfees": "registrationfees", "status": "status",
}


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell, self._in = [], [], "", False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in, self._cell = True, ""
        elif tag == "br" and self._in:
            self._cell += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in = False
            self._row.append(self._cell.strip())
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []

    def handle_data(self, data):
        if self._in:
            self._cell += data


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    if len(data) > 1 and data[1:2] == b"\x00":
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


# any other export layout (English / Marathi headings, other wording) is mapped by meaning
_COL_RULES = [
    (r"sro\s*code|office\s*code", "srocode"),
    (r"internal", "internaldocumentnumber"),
    (r"fee|फी|शुल्क\s*नोंदणी", "registrationfees"),
    (r"stamp|मुद्रांक", "stampdutypaid"),
    (r"market|बाजार", "marketvalue"),
    (r"consideration|मोबदला|amount", "consideration_amt"),
    (r"exec|करून\s*दिल्याचा|लिहून\s*दिल्याचा", "dateofexecution"),
    (r"(?:reg\w*|नोंदणी)\s*.*(?:date|दिनांक)|(?:date|दिनांक).*(?:reg|नोंदणी)|^date$|^दिनांक$", "registrationdate"),
    (r"doc\w*\s*(?:name|type)|deed\s*type|article|दस्त\w*\s*(?:प्रकार|नाव)|प्रकार", "docname"),
    (r"doc\w*\s*(?:no|number|क्र)|दस्त\s*(?:क्र|नं|क्रमांक)|अनुक्रमांक|serial|^no\.?$", "docno"),
    (r"sro|sub\s*-?\s*registrar|दुय्यम|निबंधक|office", "sroname"),
    (r"seller|executant|first\s*party|party\s*1|लिहून\s*देणार|देणार", "sellerparty"),
    (r"purchaser|buyer|claimant|second\s*party|party\s*2|लिहून\s*घेणार|घेणार", "purchaserparty"),
    (r"property|description|मिळकत|वर्णन", "propertydescription"),
    (r"area|village|गाव|विभाग", "areaname"),
    (r"status|स्थिती", "status"),
]


def _col_key(h: str) -> str:
    exact = _COLS.get(re.sub(r"[^a-z0-9]", "", (h or "").lower()))
    if exact:
        return exact
    t = re.sub(r"\s+", " ", (h or "").lower()).strip()
    for rx, key in _COL_RULES:
        if t and re.search(rx, t, re.I):
            return key
    return re.sub(r"[^a-z0-9_]", "", t)


def _header_index(rows: List[List[str]]) -> int:
    """Title rows may come before the heading row — pick the first row that names at least 3 known columns."""
    known = {"docno", "docname", "registrationdate", "sellerparty", "purchaserparty", "propertydescription", "sroname"}
    for i, r in enumerate(rows[:15]):
        if len({_col_key(c) for c in r} & known) >= 3:
            return i
    return 0


def _rows_from_table(header: List[str], body: List[List[str]], source: str) -> List[dict]:
    allrows = [header] + body
    hi = _header_index(allrows)
    header, body = allrows[hi], allrows[hi + 1:]
    keys = [_col_key(h) for h in header]
    # a table without the IGR index columns (an earlier report, a tracker, a bank list) is not search data
    if len(set(keys) & {"docno", "docname", "registrationdate", "sellerparty", "purchaserparty", "propertydescription", "sroname"}) < 3:
        return []
    out = []
    for r in body:
        if not any(c.strip() for c in r):
            continue
        row = {k: (r[i].strip() if i < len(r) else "") for i, k in enumerate(keys)}
        row["_source"] = source
        out.append(row)
    return out


def parse_igr_file(data: bytes, filename: str) -> List[dict]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = [[("" if v is None else str(v)) for v in r] for r in ws.iter_rows(values_only=True)]
        return _rows_from_table(rows[0], rows[1:], filename) if rows else []
    if data[:4] == b"\xd0\xcf\x11\xe0":  # genuine binary .xls
        import xlrd
        sh = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        rows = [[str(sh.cell_value(i, j)) for j in range(sh.ncols)] for i in range(sh.nrows)]
        return _rows_from_table(rows[0], rows[1:], filename) if rows else []
    # IGR "xls" is really an HTML table (usually UTF-16)
    p = _TableParser()
    p.feed(_decode(data))
    if not p.rows:
        return []
    return _rows_from_table(p.rows[0], p.rows[1:], filename)


_PLACES = {"वसई": "Vasai", "ठाणे": "Thane", "कल्याण": "Kalyan", "पनवेल": "Panvel", "पालघर": "Palghar",
           "भिवंडी": "Bhiwandi", "उल्हासनगर": "Ulhasnagar", "अंधेरी": "Andheri", "बोरीवली": "Borivali",
           "कुर्ला": "Kurla", "मुंबई": "Mumbai", "बांद्रा": "Bandra", "डहाणू": "Dahanu", "जव्हार": "Jawhar",
           "मोखाडा": "Mokhada", "अंबरनाथ": "Ambernath", "मुरबाड": "Murbad", "शहापूर": "Shahapur"}


def sro_english(raw: str) -> str:
    s = norm_text(raw)
    if not s:
        return ""
    joint = bool(re.search(r"सह|joint", s, re.I))
    num = re.search(r"(\d+)\s*$", s)
    place = next((en for mr, en in _PLACES.items() if mr in s), None)
    if not place:
        m = re.search(r"(?:S\.?\s*R\.?|Sub[- ]?Registrar)\s*\.?\s*([A-Za-z]+)", s, re.I)
        place = m.group(1).title() if m else None
    if not place:
        return s
    return f"{'Joint ' if joint else ''}Sub-Registrar {place}{'-' + num.group(1) if num else ''}"


def normalise_rows(rows: List[dict]) -> List[dict]:
    """Clean numbers/dates, dedupe across files, assign stable ids."""
    seen, out = set(), []
    for r in rows:
        raw_no = ascii_digits(str(r.get("docno", "")))
        pn, py = parse_doc_no_raw(raw_no) if re.search(r"[A-Za-z\u0900-\u097F/\-]", raw_no) else ("", "")
        docno = pn or clean_num(raw_no).split(".")[0]          # "TNN4-24821-2024" → 24821
        reg = fmt_date(r.get("registrationdate", ""))
        exe = fmt_date(r.get("dateofexecution", "")) or reg
        yr = year_of(reg) or year_of(exe) or (int(py) if py else None)
        sro = sro_english(r.get("sroname", ""))
        key = (sro, docno, yr, norm_text(r.get("propertydescription", ""))[:80])
        if docno and key in seen:
            continue
        seen.add(key)
        out.append({
            "id": f"R{len(out) + 1}",
            "docno": docno, "year": yr, "reg_date": reg, "exec_date": exe,
            "sro_raw": r.get("sroname", ""), "sro": sro,
            "docname_raw": r.get("docname", ""),
            "seller_raw": r.get("sellerparty", ""), "purchaser_raw": r.get("purchaserparty", ""),
            "property_raw": norm_text(r.get("propertydescription", "")),
            "area_raw": r.get("areaname", ""),
            "consideration": clean_num(r.get("consideration_amt", "")),
            "market_value": clean_num(r.get("marketvalue", "")),
            "stamp_duty": clean_num(r.get("stampdutypaid", "")),
            "registration_fee": clean_num(r.get("registrationfees", "")),
            "source_file": r.get("_source", ""),
        })
    return out


def file_year_range(filename: str):
    """'2002-07.xls' → (2002, 2007); '2002-2007.xls' → (2002, 2007); 'IGR 2016.xls' → (2016, 2016)."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "")
    m = re.search(r"(?<!\d)((?:19|20)\d{2})\s*(?:-|–|to|_)\s*((?:19|20)?\d{2})(?!\d)", stem, re.I)
    if m:
        a, b = int(m.group(1)), m.group(2)
        b = int(b) if len(b) == 4 else (a // 100) * 100 + int(b)
        if b < a:
            b += 100
        if 0 <= b - a <= 40:
            return a, b
    ys = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", stem)]
    return (min(ys), max(ys)) if ys else None


def coverage_years(rows: List[dict], files: List[str] = None) -> List[int]:
    """Years actually searched. A file name with a period ('2002-07.xls') covers every year in it; otherwise a
    file covers the span between its first and last registration year (IGR exports are one contiguous search)."""
    years = {r["year"] for r in rows if r.get("year")}
    by_file: Dict[str, List[int]] = {}
    for r in rows:
        if r.get("year"):
            by_file.setdefault(r.get("source_file", ""), []).append(r["year"])
    for f in set(list(by_file) + list(files or [])):
        rng = file_year_range(f)
        if by_file.get(f):
            lo, hi = min(by_file[f]), max(by_file[f])
            if not rng:
                rng = (lo, hi)
            elif rng[0] == rng[1] and hi > rng[1]:          # '2002_1.xls' holding 2004-2006 rows → searched 2002-2006
                rng = (min(rng[0], lo), hi)
        if rng:
            years.update(range(rng[0], rng[1] + 1))
    return sorted(years)


# ── pre-filter (cheap, deterministic) ──────────────────────────────────────────
_STOP = {"the", "and", "through", "partner", "director", "pvt", "ltd", "private", "limited", "m/s", "shri",
         "smt", "mrs", "mr", "others", "floor", "first", "second", "unit", "premises", "building", "village",
         "taluka", "district", "road", "east", "west", "vasai", "thane", "palghar", "national", "highway"}


def build_keys(subject: dict, parties: List[str]) -> dict:
    unit = [n for n in unit_nums(subject) if n]
    terms = [t for t in (subject.get("search_terms_marathi") or []) if t]
    terms += [t for t in (subject.get("search_terms_english") or []) if t]
    for k in ("building_name", "project_name"):
        if subject.get(k):
            terms.append(subject[k])
    party_tokens = set()
    for p in parties:
        for w in re.split(r"[\s,./&()]+", p.lower()):
            if len(w) > 3 and w not in _STOP:
                party_tokens.add(w)
    return {
        "unit": unit,
        "terms": sorted({t.lower().strip() for t in terms if len(t.strip()) >= 3}),
        "survey": re.sub(r"[^\dA-Za-z/]", "", ascii_digits(subject.get("survey_no") or subject.get("cts_no") or "")),
        "skels": subject.get("_bldg_skels") or _plain(name_skels(subject)),
        "land_refs": subject.get("_land_refs") or subject_land_refs(subject),
        "party_skels": sorted({skeleton(core_name(p)) for p in parties if len(skeleton(core_name(p))) >= 6}),
        "hissa": re.sub(r"[^\dA-Za-z/]", "", ascii_digits(subject.get("hissa_no", ""))),
        "party_tokens": sorted(party_tokens),
    }


_SURVEY_RX = r"(?:स\.?\s*न|सर्वे\s*(?:नं|न|क्र)|सव्हे\s*(?:नं|क्र)|s\.?\s*no|survey\s*no|sr\.?\s*no)\.?\s*[:\-]?\s*"
_HISSA_RX = r"(?:हि\.?\s*न|हिस्सा\s*(?:नं|न|क्र)|h\.?\s*no|hissa\s*no)\.?\s*[:\-]?\s*"


_UNIT_RX = re.compile(r"(?:unit|युनिट|गाळा|shop|दुकान|flat|फ्लॅट|सदनिका|office)\s*(?:no|नं|क्र)?\.?\s*\d", re.I)


def score_row(row: dict, keys: dict) -> int:
    text = ascii_digits(" ".join([row["property_raw"], row["seller_raw"], row["purchaser_raw"]])).lower()
    score = 0
    if any(re.search(rf"(?<!\d)0*{u}(?!\d)", text) for u in keys["unit"] or []):
        score += 3
    if any(t in text for t in keys["terms"]):
        score += 4
    lm = land_match(land_refs(row["property_raw"]), keys.get("land_refs") or [])
    if lm["strict"]:
        score += 4 if not _UNIT_RX.search(row["property_raw"]) else 2      # land-level deeds first
    elif lm["partial"]:
        score += 1
    sk = skeleton(row["property_raw"])
    if any(x in sk for x in keys.get("skels") or []):
        score += 4
    psk = skeleton(row["seller_raw"] + " " + row["purchaser_raw"])
    score += min(3, sum(1 for t in keys["party_tokens"] if t in text) +
                 2 * sum(1 for x in keys.get("party_skels") or [] if x in psk))
    return score


def prefilter(rows: List[dict], keys: dict) -> List[dict]:
    # a term present in most rows (e.g. the village name) cannot identify the property — drop it
    if rows:
        texts = [ascii_digits(" ".join([r["property_raw"], r["seller_raw"], r["purchaser_raw"]])).lower() for r in rows]
        keys = dict(keys, terms=[t for t in keys["terms"] if sum(t in x for x in texts) <= 0.3 * len(texts)],
                    party_tokens=[t for t in keys["party_tokens"] if sum(t in x for x in texts) <= 0.3 * len(texts)])
    scored = [(score_row(r, keys), r) for r in rows]
    cand = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s >= 3]
    return cand, len(cand)          # the AI limit (IGR_MAX_CANDIDATES) is applied in classify(), after the code pre-check


# ── AI classification ──────────────────────────────────────────────────────────
def classify(candidates: List[dict], subject: dict, chain_parties: List[str]) -> Dict[str, dict]:
    import json
    results: Dict[str, dict] = {}
    # rows the code can decide alone (other flats of the same project, another tower) never go to the AI
    for r in candidates:
        pc = precheck(r, subject)
        if pc:
            results[r["id"]] = {"id": r["id"], "class": pc[0], "reason": pc[1], "parties": default_parties(r, r["docname_raw"])}
            if pc[0] == "B":
                verify_class(r, results[r["id"]], subject)       # TDR / deposit of title deeds / declarations → C
    ai_rows = [r for r in candidates if r["id"] not in results]
    classify.ai_eligible = len(ai_rows)
    candidates_all, candidates = candidates, ai_rows[: config.IGR_MAX_CANDIDATES]
    classify.ai_sent = len(candidates)
    master = {k: v for k, v in subject.items() if v and not k.startswith("_")}
    for i in range(0, len(candidates), config.IGR_BATCH_SIZE):
        batch = candidates[i:i + config.IGR_BATCH_SIZE]
        payload = [{
            "id": r["id"], "document_type": r["docname_raw"], "registration_date": r["reg_date"],
            "execution_date": r["exec_date"], "sro": r["sro_raw"], "doc_no": r["docno"],
            "party_1_seller_column": r["seller_raw"], "party_2_purchaser_column": r["purchaser_raw"],
            "property_description": r["property_raw"], "area": r["area_raw"],
            "consideration": r["consideration"], "market_value": r["market_value"],
        } for r in batch]
        text = ("SUBJECT PROPERTY MASTER:\n" + json.dumps(master, ensure_ascii=False) +
                "\n\nKNOWN TITLE-CHAIN PARTIES:\n" + json.dumps(chain_parties, ensure_ascii=False) +
                "\n\nIGR ROWS:\n" + json.dumps(payload, ensure_ascii=False))
        out = llm.call_json("igr_classify", prompts.IGR_SYSTEM, [llm.text_block(text)], max_tokens=12000)
        for item in (out.get("rows") if isinstance(out, dict) else out) or []:
            if isinstance(item, dict) and item.get("id"):
                results[item["id"]] = item
    # deterministic check of every AI class against the raw IGR text (never trust the model alone)
    by_id = {r["id"]: r for r in candidates}
    for r in candidates_all:
        if r["id"] in by_id:
            continue
        if r["id"] not in results:
            # beyond the AI limit: decided by the printed identifiers alone, never silently left out
            results[r["id"]] = {"id": r["id"], "class": "F", "reason": "not sent to AI (IGR_MAX_CANDIDATES) — code check only",
                                "parties": default_parties(r, r["docname_raw"])}
            verify_class(r, results[r["id"]], subject)
        assign_mortgage_roles(r, results[r["id"]])
    for rid, r in by_id.items():
        c = results.setdefault(rid, {"id": rid, "class": "F", "reason": "model returned no class"})
        verify_class(r, c, subject)
        assign_mortgage_roles(r, c)
    return results


# ── deterministic verification ─────────────────────────────────────────────────
_DEV_SKEL = {**{ch: "k" for ch in "कखक़ख़"}, **{ch: "g" for ch in "गघग़"}, **{ch: "c" for ch in "चछ"},
             **{ch: "j" for ch in "जझज़"}, **{ch: "t" for ch in "टठतथ"}, **{ch: "d" for ch in "डढदधड़ढ़"},
             **{ch: "n" for ch in "णनञङ"}, **{ch: "p" for ch in "प"}, **{ch: "f" for ch in "फफ़"},
             **{ch: "b" for ch in "बभ"}, "म": "m", "र": "r", "ल": "l", "ळ": "l", "व": "v",
             **{ch: "s" for ch in "शषस"}}


def skeleton(text: str) -> str:
    """Script-independent consonant skeleton: 'AAREN PLAZA', 'आरएन प्लाझा', 'आरेन प्लाजा' → 'rnplj'."""
    t = (text or "").lower().replace("ght", "t").replace("igh", "i")      # heights / height → हाईट्स
    t = re.sub(r"c(?=[iey])", "s", t)                                     # ecocity → इकोसिटी (soft c)
    for a, b in (("ch", "c"), ("sh", "s"), ("ph", "f"), ("th", "t"), ("dh", "d"), ("bh", "b"),
                 ("gh", "g"), ("kh", "k"), ("jh", "j"), ("ck", "k"), ("q", "k"), ("x", "ks"), ("z", "j"),
                 ("w", "v")):
        t = t.replace(a, b)
    out = []
    for ch in t:
        if ch == "ृ":                      # कृष्ण = krishna
            out.append("r")
        elif ch in "ंँ":                    # anusvara: संघवी = sanghvi
            out.append("n")
        elif ch in _DEV_SKEL:
            out.append(_DEV_SKEL[ch])
        elif "a" <= ch <= "z" and ch not in "aeiouyh":
            out.append("k" if ch == "c" else ch)
    s = "".join(out)
    return re.sub(r"(.)\1+", r"\1", s)


_GENERIC = re.compile(
    r"\b(?:co\s*-?\s*op(?:erative)?|housing|society|soc|ltd|limited|chs|chsl|llp|pvt|private|the|premises|"
    r"apartments?|building|bldg|wing|no|shri|shree|sri|m/s)\b\.?|को\s*-?\s*ऑप(?:रेटिव्ह)?|को\s*-?\s*ऑपरेटीव्ह|"
    r"हौसिंग|हाउसिंग|हौ\.?|सोसायटी|सोसा\.?|सोसायटि|लिमिटेड|लि\.?|मर्यादित|सहकारी|गृहनिर्माण|संस्था|श्री|मे\.|प्रा\.", re.I)
# a search term that is only an area / road name ('Tagore Nagar', 'S.V. Road', 'Vikhroli East') — not a building
_LOCALITY = re.compile(r"^\s*(?:\S+\s+){0,2}(?:nagar|नगर|colony|कॉलनी|road|rd\.?|marg|मार्ग|chowk|चौक|wadi|वाडी|pada|पाडा|"
                       r"gaon|गाव|east|west|पूर्व|पश्चिम|village)\s*$", re.I)
_BLDG_WORD = (r"plaza|tower|park|complex|apartment|residency|heights|estate|building|society|chs|co-?op|enclave|"
              r"towers|vihar|niwas|sadan|bhavan|प्लाझा|प्लाजा|टॉवर|पार्क|इमारत|अपार्ट|सोसायटी|हाईट्स|हाइट्स")


def core_name(text: str) -> str:
    """'Pant Nagar Shri Krishnangan Co-op. Hsg. Soc. Ltd.' → 'Pant Nagar Krishnangan' (generic words removed)."""
    return re.sub(r"\s+", " ", _GENERIC.sub(" ", text or "")).strip(" .,-")


def name_skels(subject: dict) -> List[str]:
    """Skeletons identifying the subject building / society / project: the full core name and each distinctive word."""
    fields = [subject.get("building_name", ""), subject.get("project_name", ""), subject.get("society_name", "")]
    terms = [t for t in (subject.get("search_terms_english") or []) + (subject.get("search_terms_marathi") or [])
             if re.search(_BLDG_WORD, t, re.I)]
    out = set()
    for i, n in enumerate(fields + terms):
        for seg in re.split(r"\(|\)|–|—|\s-\s|\bformerly\b|\bearlier\b|\bpreviously\b|known as|पूर्वीचे|उर्फ|alias", n or "", flags=re.I):
            seg = re.sub(r"(?i)\b(?:tower|wing|bldg|building|block|chawl)\s*(?:no\.?|nos\.?)?\s*[-–]?\s*"
                         r"(?:\d+(?:\s*(?:[-–&,/]|and)\s*\d+)*|[A-Za-z]\b)?|टॉवर\s*[-–]?\s*\d*|विंग", " ", seg)
            core = core_name(seg)
            if i >= len(fields) and _LOCALITY.search(core):
                continue          # 'Building No. 180-181 Tagore Nagar' → 'Tagore Nagar' is the area, not the building
            # a whole name of two or more words may be short: 'Cini Homes' / 'सिनी होम्स' → 'snms'
            # 'VIVANT-D' is two words as much as 'Vivant D'
            if len(skeleton(core)) >= 5 or (len(skeleton(core)) == 4 and len(re.split(r"[\s\-–]+", core.strip())) >= 2):
                out.add(skeleton(core))
            for w in re.split(r"[\s,./()\-]+", core):
                if len(skeleton(w)) >= 5:
                    out.add("w:" + skeleton(w))
    # an English plural 's' is written 'ज' in Marathi: 'Om Shantivan Skies' = 'ओम शांतीवन स्काईज'
    out |= {x[:-1] + "j" for x in out if x.endswith("s") and len(x) >= 6}
    return sorted(out)


def _short_name_words(subject: dict) -> List[str]:
    """Distinctive words of a short building name (only used when no long name skeleton exists)."""
    if any(not x.startswith("w:") for x in name_skels(subject)):
        return []
    n = re.sub(r"(?i)\b(?:tower|wing|bldg|building|block|no|plot)\b\.?\s*[-–]?\s*[\dA-Za-z]?\b", " ", subject.get("building_name", "") or "")
    words = [skeleton(w) for w in re.split(r"[\s,./()\-]+", core_name(n)) if w]
    return [w for w in words if 3 <= len(w) < 5][:2]


def _plain(skels) -> List[str]:
    return [x[2:] if x.startswith("w:") else x for x in skels]


def set_row_stats(subject: dict, rows: List[dict]) -> dict:
    """Mark identifiers that are too common in this search to identify the property (layout-wide CTS, area names)."""
    if not rows:
        return subject
    skel_rows = [skeleton(r.get("property_raw", "")) for r in rows]
    n = len(rows)
    sk = name_skels(subject)
    # whole names are always kept; a single word is dropped when it is common in this search (e.g. an area name)
    subject["_bldg_skels"] = [x for x in sk if not x.startswith("w:")] + \
        [x[2:] for x in sk if x.startswith("w:") and sum(x[2:] in t for t in skel_rows) <= max(3, 0.10 * n)]
    subject["_land_refs"] = subject_land_refs(subject)
    if subject["_land_refs"]:
        # "common" = the land id is shared by many OTHER buildings (a layout-wide CTS), not just by the units of ours
        hit_rows = [i for i, r in enumerate(rows)
                    if land_match(land_refs(r.get("property_raw", "")), subject["_land_refs"])["strict"]]
        ours = sum(1 for i in hit_rows if any(x in skel_rows[i] for x in subject["_bldg_skels"]))
        common = lambda hits, mine: len(hits) > max(20, 0.10 * n) and mine < 0.5 * len(hits)
        subject["_land_common"] = common(hit_rows, ours)
        # the sub-division itself can be layout-wide too ('CTS 194A' of a whole MHADA colony): then naming it proves nothing
        sub_refs = [x for x in subject["_land_refs"] if x[2]]
        if subject["_land_common"] and sub_refs:
            sub_hits = [i for i in hit_rows if land_match(land_refs(rows[i].get("property_raw", "")), sub_refs)["strict"]]
            subject["_hissa_common"] = common(sub_hits, sum(1 for i in sub_hits
                                                             if any(x in skel_rows[i] for x in subject["_bldg_skels"])))
    return subject


# ── land identifiers (survey / hissa / gat / CTS), lists included ───────────────
_CTS_P = r"c\.?\s*t\.?\s*s\.?|स[ीि]\.?\s*ट[ीि]\.?\s*एस\.?|न\.?\s*भू\.?\s*(?:क्र)?|नगर\s*भूमापन(?:\s*क्रमांक)?|सि\.?\s*स\.?\s*(?:नं|न)|city\s*survey"
_SVY_P = r"स\.?\s*न\.?|स\.?\s*नं\.?|सर्व्हे|सर्वे|सव्हे|सर्व्ह|र्सव्हे|(?<![A-Za-z])s\.?\s*no\b|survey|(?<![A-Za-z])sr\.?\s*no\b|गट|(?<![A-Za-z])gat\b|(?<![A-Za-z])g\.?\s*no\b"
_SV = r"(?:" + _CTS_P + "|" + _SVY_P + r")\s*\.?\s*(?:नंबर|नं|न|क्र|क्रं|no|nos|number|क्रमांक)?\s*\.?\s*[:\-]?\s*"
_HS = r"(?:हि\.?\s*न\.?|हि\.?\s*नं\.?|हिस्सा|हीस्सा|हिसा|h\.?\s*no\b|hissa)\s*\.?\s*(?:नंबर|नं|न|क्र|nos|no|numbers|number)?\s*\.?\s*[:\-]?\s*"
_DEV_LETTER = {"अ": "A", "ए": "A", "ब": "B", "बी": "B", "क": "C", "सी": "C", "ड": "D", "डी": "D", "इ": "E", "ई": "E",
               "एफ": "F", "फ": "F", "ई.": "E"}
_SUBTOK = r"(?:\d+|[A-Za-z](?![A-Za-z])|एफ|बी|सी|डी|ए|अ|ब|क|ड|इ|ई)"
_ID = r"(\d+)\s*([A-Za-z](?![A-Za-z])|(?:बी|सी|डी|ए|अ|ब|क|ड)(?![\u0900-\u097F]|\s*\d))?((?:\s*/\s*" + _SUBTOK + r")*)"
_PART = r"(?:\s*\(?\s*(?:पार्ट|part|pt|पीटी|पै\.?|पैकी|p(?=\s*\)))\.?[^,)\d]{0,3}(?:\s*\d+\s*(?:&|,|and)\s*\d+)?\s*\)?)?"
_AREA_AFTER = re.compile(r"^\s*(?:\.\d|चौ|sq|square|हे\b|हेक्टर|आर\b|गुंठे|%|टक्के|फु)", re.I)


def _sub_norm(main_letter: str, rest: str) -> str:
    parts = [x.strip() for x in re.split(r"/", rest or "") if x.strip()]
    if main_letter:
        parts = [main_letter.upper()] + parts
    return "/".join(_DEV_LETTER.get(x, x).upper() for x in parts)


def land_refs(text: str) -> List[tuple]:
    """[(kind, main, sub), ...] — kind 'cts' or 'sv'; reads lists such as 'सीटीएस नं. 538/1 पार्ट,464/1पार्ट,463'."""
    t = ascii_digits(text or "")
    out = []
    for m in re.finditer(r"(" + _CTS_P + "|" + _SVY_P + r")\s*\.?\s*(?:नंबर|नं|न|क्र|क्रं|no|nos|number|क्रमांक)?\s*\.?\s*[:\-]?\s*", t, re.I):
        kind = "cts" if re.match(_CTS_P, m.group(1), re.I) else "sv"
        if re.search(r"(?:old|जुना|जुने|जुन्या|जु\.|ओल्ड|former|पूर्वीचा|पूर्वीचे)\s*[.:\-]?\s*$", t[max(0, m.start() - 14):m.start()], re.I):
            kind = "old"                     # old survey no. — replaced by the new number, never used to match
        pos = m.end()
        # 'सर्वे नं.,प्लॉट नं. बी 111/1/2(पार्ट),118/4(पार्ट)…' — a plot label between the keyword and the list
        filler = re.match(r"[\s,]*(?:प्लॉट|plot)\s*(?:नं|no|क्र)?\s*\.?\s*(?:[A-Za-z]|बी|सी|ए|डी)?\s*(?=\d+\s*/)", t[pos:], re.I)
        if filler:
            pos += filler.end()
        first = True
        group = []
        while True:
            mm = re.match(_ID, t[pos:], re.I)
            if not mm or _AREA_AFTER.match(t[pos + mm.end():]):
                break
            main, sub = mm.group(1), _sub_norm(mm.group(2), mm.group(3))
            pos += mm.end()
            if not sub and first:                                  # "स.न. 16 हि.न. 4"
                h = re.match(r"[\s,]*" + _HS + r"(\d+[A-Za-z]?(?:\s*/\s*" + _SUBTOK + r")*)", t[pos:pos + 50], re.I)
                if h:
                    sub = _sub_norm("", h.group(1).replace(" ", ""))
                    if re.match(r"\d+[A-Za-z]$", sub):
                        sub = sub[:-1] + "/" + sub[-1]
                    # 'Hissa Nos. 4 (pt) and 7/2 (pt)' — more hissas of the same survey number (stops before a number
                    # that has its own 'Hissa' after it, i.e. the next survey number)
                    hp = pos + h.end()
                    while True:
                        more = re.match(_PART + r"\s*(?:,|&|and|आणि|व)\s*(\d+[A-Za-z]?(?:\s*/\s*" + _SUBTOK + r")*)" + _PART,
                                        t[hp:hp + 40], re.I)
                        if not more or re.match(r"\s*,?\s*" + _HS, t[hp + more.end():hp + more.end() + 20], re.I) or \
                                _AREA_AFTER.match(t[hp + more.end():]):
                            break
                        out.append((kind, main, _sub_norm("", more.group(1).replace(" ", ""))))
                        hp += more.end()
            out.append((kind, main, sub or None))
            group.append(len(out) - 1)
            first = False
            sep = re.match(_PART + r"\s*(?:,|&|and|आणि|व)?\s*(?=\d)", t[pos:], re.I)
            if not sep or not sep.group(0).strip():          # needs a comma / 'and' / 'part' between two ids
                break
            pos += sep.end()
        # parallel lists: survey list followed by a hissa list of the same length → pair them
        if len(group) > 1 and all(out[i][2] is None or i == group[0] for i in group):
            hm = re.match(r"[\s,;]*" + _HS + r"((?:\d+[A-Za-z]?(?:/[\dA-Za-z]+)*)(?:\s*(?:,|&|and|आणि|व)\s*\d+[A-Za-z]?(?:/[\dA-Za-z]+)*)+)",
                          t[pos:pos + 80], re.I)
            if hm:
                hs = re.findall(r"\d+[A-Za-z]?(?:/[\dA-Za-z]+)*", hm.group(1))
                if len(hs) == len(group):
                    for i, h in zip(group, hs):
                        out[i] = (out[i][0], out[i][1], _sub_norm("", h))
    if not out:
        # old land deeds list the parcels without the word 'survey': 'येथिल 114 / 6 क्षेत्र 1390 चौ.मी., 118 / 8 क्षेत्र 810'
        for m in re.finditer(r"(?<![\d/.])(\d{1,4})\s*/\s*(\d{1,3}[A-Za-z]?(?:\s*/\s*\d{1,3}[A-Za-z]?)?)" + _PART +
                             r"\s*,?\s*(?=क्षेत्र|area\b|admeasuring|एरिया)", t, re.I):
            out.append(("sv", m.group(1), _sub_norm("", m.group(2).replace(" ", ""))))
    return out


def land_ids(text: str) -> List[tuple]:
    """[(survey_or_cts, hissa_or_None), ...] (kind dropped; kept for older callers)."""
    return [(m, s) for k, m, s in land_refs(text)]


def subject_land_refs(subject: dict) -> List[tuple]:
    """Every land id of the subject: from its description (prefixed) and from the survey / CTS / hissa fields."""
    refs = set(land_refs(subject.get("full_description", "")))
    desc = (subject.get("full_description", "") + " " + subject.get("survey_no", "")).lower()
    for field, kind in (("cts_no", "cts"), ("survey_no", "sv")):
        v = ascii_digits(subject.get(field) or "")
        if not v.strip():
            continue
        if kind == "sv" and re.search(r"c\.?\s*t\.?\s*s", desc) and not re.search(r"survey|s\.\s*no|गट|gat|hissa|हिस्सा", desc):
            kind = "cts"                                          # the "survey" field holds CTS numbers
        found = land_refs(("CTS No. " if kind == "cts" else "S.No. ") + v)
        hissa_txt = ascii_digits(subject.get("hissa_no", ""))
        # grouped hissa list: '118: 4(part), 7(part); 125: 1(part), 2, 5A, 6/3' → hissas of each survey number
        grouped = {g.group(1): re.findall(r"\d+[A-Za-z]?(?:/[\dA-Za-z]+)*", g.group(2))
                   for g in re.finditer(r"(\d+)\s*[:\-–]\s*([^;:]+?)(?=;|$|\s\d+\s*[:\-–])", hissa_txt)}
        if kind == "sv" and grouped and set(grouped) & {m for k, m, sb in found}:
            for k, m, sb in found:
                for h in grouped.get(m) or [None]:
                    refs.add((kind, m, sb or (_sub_norm("", h) if h else None)))
            continue
        hss = re.findall(r"\d+[A-Za-z]?(?:/[\dA-Za-z]+)*", hissa_txt)
        for i, (k, m, sb) in enumerate(found):
            h = hss[i] if kind == "sv" and len(hss) == len(found) else ""
            refs.add((kind, m, sb or (_sub_norm("", h) if h else None)))
    # 'Survey No. 118' alone next to 'Survey No. 118 Hissa No. 4 (part)': the subject is the listed hissas, not all of 118
    with_sub = {(k, m) for k, m, s in refs if s}
    refs = {x for x in refs if x[2] or (x[0], x[1]) not in with_sub}
    return sorted(refs, key=lambda x: (x[0], int(x[1]), x[2] or ""))


def land_match(row_refs: List[tuple], subj_refs: List[tuple]) -> dict:
    strict = partial = conflict = False
    if any(k != "old" for k, m, s in subj_refs):          # match on current numbers; old numbers only as a last resort
        subj_refs = [x for x in subj_refs if x[0] != "old"]
        row_refs = [x for x in row_refs if x[0] != "old"] or row_refs
    for rk, rm, rs in row_refs:
        for sk, sm, ss in subj_refs:
            if rm != sm or (rk != sk):
                continue
            if not ss or (rs and (rs == ss or rs.startswith(ss + "/") or ss.startswith(rs + "/"))):
                strict = True
            elif not rs:
                partial = True
            else:
                conflict = True
    other = bool(row_refs) and not (strict or partial or conflict)
    return {"strict": strict, "partial": partial and not strict, "conflict": conflict and not strict, "other": other}


# ── unit numbers with a letter (1801B, B-1801, सदनिका क्र.1801बी) and tower numbers ──
_LET = r"(?:[A-Za-z](?![A-Za-z])|एफ|बी|सी|डी|ए|ई)(?![ऀ-ॿ])"


def unit_nums(subject: dict) -> List[str]:
    """All unit numbers of the subject ('1704 & 1804', '101, 102' → two units)."""
    raw = ascii_digits(subject.get("unit_no", ""))
    nums = [n for n in re.findall(r"\d+", raw) if not re.fullmatch(r"(?:19|20)\d\d", n) or len(re.findall(r"\d+", raw)) == 1]
    return [n.lstrip("0") or "0" for n in nums] or [""]           # 'Flat No. 04' in the deed = 'Flat No:4' in IGR


def unit_spec(subject: dict):
    raw = ascii_digits(subject.get("unit_no", ""))
    num = (re.findall(r"\d+", raw) or [""])[0]
    letter = ""
    m = re.search(r"\d+\s*[-/]?\s*([A-Za-z])(?![A-Za-z])", raw) or re.search(r"(?<![A-Za-z])([A-Za-z])\s*[-/]?\s*\d", raw)
    if m:
        letter = m.group(1).upper()
    else:
        w = re.sub(r"(?i)\bwing\b|विंग|[\s\-]", "", subject.get("wing", "") or "")
        if len(w) == 1 and w.isalpha():
            letter = w.upper()
    return num, letter


def unit_occurrences(prop: str, num: str) -> List[str]:
    """Letters attached to each occurrence of the unit number ('' when none)."""
    out = []
    for m in re.finditer(r"(?<![\d.,])(?:(" + _LET + r")\s*[-/]\s*)?0*" + num + r"(?:\s*[-/]?\s*(" + _LET + r"))?(?!\d)(?!\.\d)"
                         r"(?!\s*(?:चौ|sq|स्क्वे|%|टक्के|फु|ft))", prop, re.I):
        let = m.group(1) or m.group(2) or ""
        out.append(_DEV_LETTER.get(let, let).upper())
    return out


# 'Building No. 2', 'बिल्डिंग न. 1', 'बिल्डींग नं 2', 'इमारत क्रमांक 3', 'टॉवर क्रं. 4'
# also alphanumeric building codes of a layout: CIDCO 'Building No. EB10' / 'BLDG NO EB05', 'Tower T-3', 'Bldg A2'
_TOWER = re.compile(r"(?:tower|टॉवर|टाॅवर|टावर|टॉव्हर|bldg\.?|building|बिल्डींग|बिल्डिंग|बिल्डिंग|इमारत|chawl|चाळ)\s*"
                    r"(?:no\.?|नं\.?|नंबर|न\.?|क्रं\.?|क्र\.?|क्रमांक|number)?\s*[-–:]?\s*"
                    r"((?:[A-Za-z]{1,3}[-–]?)?\d+(?![\d/]))", re.I)      # letters touch the digits: 'of 3' is not 'OF3'


def _tower_key(t: str) -> str:
    """'EB 05' / 'eb-5' / 'EB05' → 'EB5'; '02' → '2' — one building however it is written."""
    m = re.fullmatch(r"\s*([A-Za-z]*)\s*[-–]?\s*0*(\d+)\s*", t or "")
    return (m.group(1).upper() + m.group(2)) if m else (t or "").upper()


def tower_of(text: str) -> str:
    m = _TOWER.search(ascii_digits(text or ""))
    return _tower_key(m.group(1)) if m else ""


def _facts(row: dict, subject: dict) -> dict:
    prop = ascii_digits(row.get("property_raw", ""))
    num, letter = unit_spec(subject)
    occ = [o for n in unit_nums(subject) if n for o in unit_occurrences(prop, n)]
    wing_in_row = re.search(r"(?:wing|विंग)\s*[-:]?\s*(" + _LET + ")|(" + _LET + r")\s*[-]?\s*(?:wing|विंग)", prop, re.I)
    row_wing = _DEV_LETTER.get((wing_in_row.group(1) or wing_in_row.group(2)) if wing_in_row else "", "")
    row_wing = (row_wing or ((wing_in_row.group(1) or wing_in_row.group(2)) if wing_in_row else "")).upper()
    if letter:
        unit_hit = any(o == letter or (o == "" and row_wing in ("", letter)) for o in occ)
    else:
        unit_hit = any(o == "" for o in occ) or bool(occ)
    skels = set(subject.get("_bldg_skels") or _plain(name_skels(subject)))
    row_skel = skeleton(prop)
    bldg_hit = any(sk in row_skel for sk in skels)
    if not bldg_hit:
        # short names ("Woods", "Om Sai") cannot be found inside other words — compare whole words instead
        short = subject.get("_short_words")
        if short is None:
            short = _short_name_words(subject)
        if short:
            row_words = {skeleton(w) for w in re.split(r"[\s,./()\-:;\"']+", prop) if w}
            bldg_hit = all(w in row_words for w in short)
    st = tower_of(" ".join([subject.get("building_name", ""), subject.get("wing", ""), subject.get("project_name", ""),
                            subject.get("full_description", "")]))
    rts = {_tower_key(t) for t in _TOWER.findall(prop)}   # every building the row names ('buildings No. 1 and 2' → both)
    tower_conflict = bool(st and rts and st not in rts)
    if tower_conflict:
        bldg_hit = False
    lm = land_match(land_refs(prop), subject.get("_land_refs") or subject_land_refs(subject))
    land_strict, land_partial, land_conflict, other_survey = lm["strict"], lm["partial"], lm["conflict"], lm["other"]
    if st and st in rts and (land_strict or land_partial) and not land_conflict:
        bldg_hit = True           # same land and the subject's building / chawl number ('चाळ नं. 180 अँड 181')
    plot = re.sub(r"\D", "", ascii_digits(subject.get("plot_no", "")))
    other_unit = False
    for m in re.finditer(r"(" + _UNIT_WORD + r")\s*\.?\s*(?:नंबर|नं|न|क्रं|क्र|क्रमांक|no|number)?\s*\.?\s*[:\-]?\s*"
                         r"(?:नंबर|नं|क्र|no)?\s*\.?\s*(\d+)", prop, re.I):
        if re.match(r"plot|प्लॉट", m.group(1), re.I) and (
                m.group(2) == plot or re.search(r"(?:final|फायनल|अंतिम|f\.?\s*p)\W*$", prop[max(0, m.start() - 12):m.start()], re.I)):
            continue                         # Final Plot No. of a TP scheme is a land id, not another unit
        other_unit = True
        break
    # unit-level rows without a unit word: "1905A NORTH IMPERIAL GREENS", "B-0607 CODENAME LOTTERY", "11TH FLOOR …"
    lead = re.match(r"^\W*(?:[A-Za-z]\s*[-/.]?\s*)?\d{2,5}(?:\s*[-/.]?\s*[A-Za-z](?![A-Za-z]))?(?=[\s,]+\S)(?!\s*(?:sq|चौ|square|हे|आर))",
                    prop, re.I)
    floor_sig = re.search(r"\d+\s*(?:st|nd|rd|th)\s*(?:habitable\s*)?floor|मजला|माळा\s*नं|\d+\s*(?:ला|रा|था|वा|वी)\s*माळा|floor\s*no", prop, re.I)
    units = [n for n in unit_nums(subject) if n]
    listed = bool(units) and any(n in [x.lstrip("0") for x in re.findall(r"\d+", m.group(1))]
                                 for m in _UNIT_LIST.finditer(prop) for n in units)
    unit_level = other_unit or bool(lead) or bool(floor_sig) or bool(occ)
    other_unit = unit_level and not unit_hit and not listed
    if subject.get("_land_common") and not bldg_hit:
        # the CTS / survey covers a whole layout: a land match alone proves nothing (another building's flat or lease
        # on the same CTS). Except a land-level deed that names a subject HISSA exactly — the conveyances, project
        # mortgages and releases of the subject's own sub-division.
        hissa_refs = [] if subject.get("_hissa_common") else [x for x in (subject.get("_land_refs") or []) if x[2]]
        if unit_level or listed or not (hissa_refs and land_match(land_refs(prop), hissa_refs)["strict"]):
            land_strict = land_partial = False
    named_other = (not bldg_hit) and (tower_conflict or bool(re.search(
        r"estate|इस्टेट|इंडस्ट्रियल|industrial park|park|पार्क|nagar|नगर|building name|इमारतीचे नाव|plaza|प्लाझा|tower|टॉवर|"
        r"complex|कॉम्प्लेक्स|paradise|पॅराडाइज|पॅराडाईज|residency|apartment|अपार्टमेंट|society|सोसायटी|bldg|बिल्डींग|इमारत", prop, re.I)))
    return {"unit_hit": unit_hit, "bldg_hit": bldg_hit, "land_strict": land_strict, "land_partial": land_partial,
            "land_conflict": land_conflict, "other_unit": other_unit, "other_survey": other_survey,
            "named_other": named_other, "tower_conflict": tower_conflict, "land_doc": bool(_LAND_DOC.search(row.get("docname_raw", ""))),
            "listed": listed}


def precheck(row: dict, subject: dict):
    """Rows the code can decide without AI: another flat of the same project (C) / another tower (E)."""
    f = _facts(row, subject)
    if f["tower_conflict"] and (f["other_unit"] or f["unit_hit"] or not f["land_strict"] or
                                not is_mortgage_type(row.get("docname_raw", ""))):
        # names another building number of the layout: its flats / parking / supplements are not ours (a mortgage
        # of the common land is still looked at by the AI)
        return "E", "code: different tower / building number"
    if f["other_unit"] and not f["unit_hit"]:
        if f["bldg_hit"]:
            return "C", "code: another unit of the subject building / project"
        if not f["land_strict"]:
            return "E", "code: a unit-level entry that does not carry the subject unit number"
    if f["land_strict"] and not f["other_unit"] and not f["unit_hit"] and f["land_doc"] and not f["land_conflict"]:
        # a land-level deed (conveyance, development, project mortgage, release) of the subject survey / hissa —
        # decided here so that the AI limit (IGR_MAX_CANDIDATES) can never leave it out of the chain
        return "B", "code: land-level deed of the subject survey / hissa"
    return None


_TDR = re.compile(r"टी\.?\s*डी\.?\s*आर|डी\.?\s*आर\.?\s*सी|\bT\.?\s*D\.?\s*R\b|\bD\.?\s*R\.?\s*C\b|transferable development|एफ\.?\s*एस\.?\s*आय\.?\s*/\s*टी", re.I)
_LAND_DOC = re.compile(
    r"convey|sale|lease|develop|attorney|poa|mortgage|release|gift|partition|exchange|will|reconvey|transfer|deed|"
    r"विकसन|कुलमुखत्यार|अभिहस्तां|अभिहस्तातं|खरेदी|भाडेपट्टा|गहाण|हक्क\s*सोड|हक्कसोड|बक्षी|बक्षि|वाटणी|"
    r"अदलाबदल|मृत्युपत्र|हस्तांतरण|ठेव|डिपॉझिट|करारनामा|डीड|रिकन्व्हेन्स|लीज|ट्रान्सफर|कन्फर्मेशन|सेल|परिमोचन|"
    r"विक्री|वाटप", re.I)
_UNIT_WORD = (r"(?:unit|युनिट|यूनिट|गाळा|गाला|gala|galla|shop|दुकान|शॉप|flat|फ्लॅट|फ्लॅट|फ्लेट|फलॅट|फ्लाट|फ्लैट|रूम|रुम|room|सदनिका|premises|office|"
              r"godown|plot|प्लॉट|tenement|टेनामेंट|टेनमेंट|टेनमॅंट|टेनॅमेंट|parking|पार्किंग|पार्कींग|garage|गॅरेज|गॅरेज)")
# a list of units in one deed: 'सदनिका नं. 102,103,…,2806,2808 आणि 3808' (project mortgage of unsold flats)
_UNIT_LIST = re.compile(_UNIT_WORD + r"\s*\.?\s*(?:नंबर|नं|न|क्रं|क्र|क्रमांक|nos?|numbers?)?\s*\.?\s*[:\-]?\s*"
                        r"((?:\d+\s*,\s*){2,}\d+(?:\s*(?:आणि|and|&)\s*\d+)?)", re.I)


def verify_class(row: dict, c: dict, subject: dict) -> None:
    """Correct the AI class using identifiers that are actually printed in the row."""
    f = _facts(row, subject)
    unit_hit, bldg_hit = f["unit_hit"], f["bldg_hit"]
    land_strict, land_partial, land_conflict = f["land_strict"], f["land_partial"], f["land_conflict"]
    other_unit, other_survey, named_other = f["other_unit"], f["other_survey"], f["named_other"]
    old = c.get("class") or "F"
    new = old
    if old == "A":
        if not unit_hit:
            new = "C" if (bldg_hit or land_strict) else "E"
        elif not (bldg_hit or land_strict):
            new = "D"
    elif old == "B":
        if land_conflict or not (land_strict or land_partial or bldg_hit):
            new = "E"
        elif other_unit or unit_hit:
            new = "A" if (unit_hit and (bldg_hit or land_strict)) else ("C" if bldg_hit or land_strict else "E")
    elif old == "C":
        if not (bldg_hit or land_strict):
            new = "E"
    elif old == "D":
        if land_conflict and not bldg_hit:
            new = "E"
        elif not (unit_hit or bldg_hit or land_strict or land_partial):
            new = "E"
    if new in ("A", "D") and not (bldg_hit or land_strict) and (other_survey or named_other):
        new = "E"          # same unit number but a different survey / a differently named building
    elif old in ("E", "F"):
        # the model missed it — two printed identifiers say otherwise
        if unit_hit and (bldg_hit or land_strict):
            new = "A"
        elif land_strict and not other_unit and not unit_hit and f["land_doc"]:
            new = "B"
        elif unit_hit and land_partial:
            new = "D"
        elif (land_strict or bldg_hit) and is_mortgage_type(row.get("docname_raw", "")) and re.search(
                r"unsold|विक्री\s*न\s*झालेल्या|न\s*विकलेल्या|न\s*विकलेले|project|प्रोजेक्ट|प्रकल्प", row.get("property_raw", ""), re.I):
            new = "B"      # project / land mortgage by the developer (land + unsold units)
    if f["listed"] and (bldg_hit or land_strict) and is_mortgage_type(row.get("docname_raw", "")):
        new = "B"          # project mortgage that names the subject unit among the units mortgaged
    if new == "B" and not land_strict and re.search(r"deposit of title|pawn|ठेव", row.get("docname_raw", ""), re.I):
        new = "C"          # a home-loan deposit of title deeds of some unit in the project — not a land deed
    if new == "B" and named_other and not bldg_hit and not is_mortgage_type(row.get("docname_raw", "")) and             _SOCIETY.search(row.get("purchaser_raw", "")):
        new = "C"          # conveyance of a portion of the layout to ANOTHER building's society — not our land chain
    if new == "B" and not f["land_doc"]:
        new = "C" if (land_strict or bldg_hit) else "E"     # an indemnity bond / declaration is not a land-title deed
    if new == "B" and _TDR.search(row.get("property_raw", "")) and not is_mortgage_type(row.get("docname_raw", "")):
        # sale of TDR / DRC rights generated from the land — not a transfer of the land itself
        # (a mortgage of the land that also charges its TDR is still a mortgage of the land)
        new = "C"
        c["tdr"] = True
    if new == "D" and other_unit and not unit_hit and (bldg_hit or land_strict):
        new = "C"          # another unit of the same building / land — project-level, not the subject
    if land_conflict and new in ("A", "B", "C", "D") and not bldg_hit:
        new = "E"          # same survey, different hissa → different land
    if new == "D" and named_other and not bldg_hit and not land_strict:
        new = "E"          # names another building and nothing ties it to ours
    if f["tower_conflict"] and new in ("A", "D"):
        new = "E"          # same unit number in another building / tower of the layout
    if new != old:
        c["class"] = new
        why = []
        if other_survey or named_other:
            why.append("row names a different survey / building")
        if land_conflict:
            why.append("same survey / CTS but a different hissa / sub-division")
        if f["tower_conflict"]:
            why.append("different tower")
        why.append(f"unit {'found' if unit_hit else 'not found'}, building {'found' if bldg_hit else 'not found'}, "
                   f"land {'match' if land_strict else ('survey only' if land_partial else 'no match')}")
        c["reason"] = (c.get("reason", "") + f" | code check: {old}→{new} ({'; '.join(why)})").strip(" |")


_SOCIETY = re.compile(r"society|सोसायटी|सोसायटि|सोसाइटी|सोसा\.?|को\s*-?\s*ऑप|को\s*-?\s*ओप|co\s*-?\s*op", re.I)
_BORROW = re.compile(r"कर्ज\s*घेणार|borrower|mortgagor", re.I)
_LENDER = re.compile(r"कर्ज\s*देणार|lender|mortgagee", re.I)


def assign_mortgage_roles(row: dict, c: dict) -> None:
    """Mortgage rows: decide Mortgagor/Mortgagee from the names, not from the IGR column order."""
    if not is_mortgage_type((c.get("doc_type_en") or "") + " " + (row.get("docname_raw") or "")):
        return
    sides = {"seller": row.get("seller_raw", ""), "purchaser": row.get("purchaser_raw", "")}

    def lender_score(t):
        return (3 if _LENDER.search(t) else 0) - (3 if _BORROW.search(t) else 0) + \
               (1 if re.search(r"bank|बँक|बॅक|बैंक|बॅंक", t, re.I) else 0)
    sc = {k: lender_score(v) for k, v in sides.items()}
    lender_side = max(sc, key=sc.get) if sc["seller"] != sc["purchaser"] else None
    parties = c.get("parties") or []
    if lender_side:
        borrower_side = "purchaser" if lender_side == "seller" else "seller"
        lend_txt, borr_txt = skeleton(sides[lender_side]), skeleton(sides[borrower_side])
        for p in parties:
            sk = skeleton(p.get("name", ""))
            if sk and sk[:6] in lend_txt and sk[:6] not in borr_txt:
                p["role"] = "Mortgagee"
            elif sk and sk[:6] in borr_txt:
                p["role"] = "Mortgagor"
    else:
        blank = [k for k, v in sides.items() if not re.sub(r"[\s\-{}\",]", "", v)]
        if len(blank) == 1:                       # only the borrower is printed
            for p in parties:
                p["role"] = "Mortgagor"
            c["discrepancy"] = ((c.get("discrepancy") or "") +
                                " Mortgagee (bank) name is not shown in the IGR index — obtain the registered copy.").strip()
    c["parties"] = parties


def _split_names(raw: str) -> List[str]:
    t = re.sub(r'[{}"]', " ", raw or "")
    parts = re.split(r"\s*(?:,|\s-\s-|--)\s*", t)
    out = []
    for x in parts:
        x = re.sub(r"^[.\s]+", "", re.sub(r"\s+", " ", x)).strip(" -")      # '..कॅटॅलीस्ट ट्रस्टीशिप' → no leading dots
        x = re.sub(r"^(.{12,}?)\s+(?:through|throug|thr|तर्फे)\s+\1.*$", r"\1", x, flags=re.I)   # "A through A …" → "A"
        firm = re.match(r"^(.*?(?:प्रा\.?\s*लि\.?|प्रायवेट\s*लिमिटेड|प्राइवेट\s*लिमिटेड|लिमिटेड|लि\.|एलएलपी|डेव्हलपर्स|बिल्डर्स|"
                        r"इन्फ्रास्ट्रक्चर|कॉर्पोरेशन|बँक|बॅक|असोसिएट्स|एंटरप्रायजेस|ग्रुप|pvt\.?\s*ltd\.?|private limited|limited|ltd\.?|"
                        r"llp|developers|builders|corporation|bank|associates|enterprises|group)(?:\s*\([^)]*\))?)\s*"
                        r"(?:चे|यांच्या|यांचे)?\s*(?:(?:तर्फे|through|thr\.?|चे\s*अधिकृत|अधिकृत)(?=\s|$|[-,:])|"
                        r"पार्टनर|भागीदार|partner)", x, re.I)
        if firm:
            x = firm.group(1).strip()                   # "M/s X Developers through partner …" → "M/s X Developers"
        else:
            poa = re.match(r"^(.{5,}?)\s+(?:तर्फे|यांच्या\s*तर्फे|through|thr\.?)\s+(?:कु\.?\s*मु\.?|कुलमुखत्यार|"
                           r"constituted\s*attorney|c\.?\s*a\.?|poa|power\s*of\s*attorney|attorney|मुखत्यार)", x, re.I)
            if poa:
                x = poa.group(1).strip()                # "रमेश पाटील तर्फे कु.मु. सुरेश" → "रमेश पाटील"
        if len(re.sub(r"[\s\-]", "", x)) > 2 and x not in out:
            out.append(x)
    return out


_FINANCIER = re.compile(r"bank|बँक|बॅक|बैंक|बॅंक|financ|फायनान्स|फायनान्स|capital|hdfc|housing\s+development", re.I)
_LENDER_NAME = re.compile(r"^(.*?(?:bank\s+of\s+(?:india|baroda|maharashtra)|bank(?:\s+(?:ltd\.?|limited))?|"
                          r"(?:limited|ltd\.?)(?!\s*(?:limited|ltd))|बँक|लिमिटेड))", re.I)


def default_parties(row: dict, doc_type: str) -> List[dict]:
    """Parties straight from the IGR columns when the model returned none."""
    sel, pur = _split_names(row.get("seller_raw", "")), _split_names(row.get("purchaser_raw", ""))
    if is_mortgage_type(doc_type + " " + (row.get("docname_raw") or "")):
        bank = re.compile(r"bank|बँक|बॅक|बैंक|बॅंक|कर्ज\s*देणार", re.I)
        s_bank, p_bank = any(bank.search(x) for x in sel), any(bank.search(x) for x in pur)
        if s_bank != p_bank:
            lender_is_seller = s_bank
        elif not sel or not pur:                      # one column blank → the printed side is the borrower
            lender_is_seller = not sel
        else:                                          # IGR convention by document type
            lender_is_seller = bool(re.search(r"deposit", row.get("docname_raw") or "", re.I))
        mortgagors, mortgagees = (pur, sel) if lender_is_seller else (sel, pur)
        # '(कर्ज देणार)- Catalyst …' / '(लिहून घेणार) - Kotak …' — the role label is not part of the name
        unlabel = lambda names: [re.sub(r"^\(?\s*(?:कर्ज|लिहून|लिहुन)\s*(?:देणार|घेणार)\s*\)?\s*[-:]?\s*", "", n).strip() or n
                                 for n in names]
        mortgagors, mortgagees = unlabel(mortgagors), unlabel(mortgagees)
        # the lender cell of a deposit-of-title-deeds row is ONE institution followed by its branch address
        # ('BAJAJ HOUSING FINANCE LTD PLOT NO 12, KOHINOOR ESTATE …') — keep the institution only
        lender_raw = re.sub(r'[{}"]', " ", row.get("seller_raw" if lender_is_seller else "purchaser_raw", "") or "")
        inst = _LENDER_NAME.match(re.sub(r"\s+", " ", lender_raw).strip())
        if inst and _FINANCIER.search(inst.group(1)):
            mortgagees = [inst.group(1).strip(" ,-")]
        if not mortgagees:
            mortgagees = ["(Mortgagee not shown in IGR index)"]
        return [{"name": n, "role": "Mortgagor"} for n in mortgagors] + [{"name": n, "role": "Mortgagee"} for n in mortgagees]
    # "लिहून देणार - X" / "लिहून घेणार - Y" labels inside the cells override the column order
    ex, cl = [], []
    for n in sel + pur:
        lab = re.match(r"^\(?\s*(लिहून\s*देणार|लिहुन\s*देणार|executant|लिहून\s*घेणार|लिहुन\s*घेणार|claimant|मान्यता\s*देणार|confirming party)\s*\)?\s*[-:]?\s*", n, re.I)
        name = n[lab.end():].strip() if lab else n
        if lab and re.search(r"घेणार|claimant", lab.group(1), re.I):
            cl.append(name)
        elif lab and re.search(r"मान्यता|confirming", lab.group(1), re.I):
            ex.append(name + " (Confirming Party)")
        elif lab or n in sel:
            ex.append(name)
        else:
            cl.append(name)
    return [{"name": n, "role": "Executant"} for n in ex] + [{"name": n, "role": "Claimant"} for n in cl]


def is_mortgage_type(text: str) -> bool:
    if is_release_type(text) and not re.search(r"deposit of title|गहाण", text or "", re.I):
        return False
    return bool(re.search(r"mortgage|deposit of title|pawn|equitable|hypothec|charge|गहाण|तारण|चार्ज|मॉरगेज|मॉर्गेज", text or "", re.I))


def is_release_type(text: str) -> bool:
    return bool(re.search(r"release|reconvey|satisfaction|discharge|हक्कसोड|परिमोचन|रिकन्व्हेन्स|पुनर्हस्तांतरण", text or "", re.I))


def entry_key(row: dict):
    return docno_key(row.get("docno"), row.get("year"))
