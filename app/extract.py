"""Stage 1: read EVERY property document (PDF / DOCX / image) with Claude, chunked by pages.
Also reads text-based e-Search receipts / GRAS challans without AI."""
import hashlib
import io
import re
from typing import List, Tuple

from pypdf import PdfReader, PdfWriter

import logging

from . import config, llm, prompts

log = logging.getLogger("plj.extract")
from .common import clean_num, clean_value, fmt_date

_SUBJECT_FIELDS = ["unit_type", "unit_no", "floor", "wing", "building_name", "project_name", "builtup_area",
                   "carpet_area", "survey_no", "hissa_no", "cts_no", "plot_no", "land_area", "village",
                   "taluka", "district", "pin", "road_locality", "full_description"]


def split_pdf(data: bytes) -> List[Tuple[bytes, int, int]]:
    """Return [(chunk_bytes, first_page, last_page)] each within page and size limits."""
    reader = PdfReader(io.BytesIO(data))
    n = len(reader.pages)
    if n == 0:
        return []
    avg_mb = (len(data) / (1024 * 1024)) / n
    per_chunk = max(1, min(config.PDF_CHUNK_MAX_PAGES, int(config.PDF_CHUNK_MAX_MB / max(avg_mb, 0.01))))
    if per_chunk >= n:
        return [(data, 1, n)]
    chunks = []
    for start in range(0, n, per_chunk):
        w = PdfWriter()
        end = min(n, start + per_chunk)
        for i in range(start, end):
            w.add_page(reader.pages[i])
        buf = io.BytesIO()
        w.write(buf)
        chunks.append((buf.getvalue(), start + 1, end))
    return chunks


def _docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(p for p in parts if p.strip())


def _run(blocks, label: str) -> dict:
    out = llm.call_json("extract_documents", prompts.EXTRACT_SYSTEM, blocks, max_tokens=config.EXTRACT_MAX_TOKENS)
    if not isinstance(out, dict):
        out = {"documents": out if isinstance(out, list) else []}
    out.setdefault("documents", [])
    out.setdefault("subject_property", {})
    out.setdefault("discrepancies", [])
    return out


def _pages(reader, p1: int, p2: int) -> bytes:
    w = PdfWriter()
    for i in range(p1 - 1, p2):
        w.add_page(reader.pages[i])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _read_pdf_part(reader, chunk: bytes, p1: int, p2: int, filename: str) -> List[dict]:
    """Read pages p1..p2; if the answer is too long to fit, read each half separately (down to single pages)."""
    hint = (f"File: {filename}. These are pages {p1}-{p2} of the file. "
            f"Report page numbers relative to the whole file (add {p1 - 1}). Use refs prefixed 'P{p1}-'. "
            f"Other pages of this file are read separately: never write notes such as 'not visible in these "
            f"pages' into any field — leave a value empty if it is not on these pages.")
    try:
        return [_run([llm.pdf_block(chunk), llm.text_block(hint)], f"{filename}:{p1}-{p2}")]
    except llm.TruncatedError:
        if p2 <= p1:
            raise
        mid = (p1 + p2) // 2
        log.warning("extract: answer for %s pages %d-%d too long — reading %d-%d and %d-%d separately",
                    filename, p1, p2, p1, mid, mid + 1, p2)
        return (_read_pdf_part(reader, _pages(reader, p1, mid), p1, mid, filename) +
                _read_pdf_part(reader, _pages(reader, mid + 1, p2), mid + 1, p2, filename))


def extract_file(data: bytes, filename: str) -> dict:
    """Returns {"subject_property":{}, "documents":[...], "discrepancies":[...]} for one file."""
    name = filename.lower()
    results = []
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        for chunk, p1, p2 in split_pdf(data):
            results += _read_pdf_part(reader, chunk, p1, p2, filename)
    elif name.endswith(".docx"):
        text = _docx_text(data)
        results.append(_run([llm.text_block(f"File: {filename}\n\n{text[:180000]}")], filename))
    elif name.endswith((".jpg", ".jpeg", ".png")):
        mt = "image/png" if name.endswith(".png") else "image/jpeg"
        results.append(_run([llm.image_block(data, mt), llm.text_block(f"File: {filename}")], filename))
    else:
        return {"subject_property": {}, "documents": [], "discrepancies": []}

    merged = {"subject_property": {}, "documents": [], "discrepancies": []}
    # refs must be unique across files: 'X_rec_p1.pdf' and 'X_rec_p2.pdf' share their first 12 letters
    stem = (re.sub(r"[^A-Za-z0-9]+", "", filename.rsplit(".", 1)[0])[:12] or "F") + \
        hashlib.md5(filename.encode("utf-8")).hexdigest()[:4]
    merged["subject_property"] = merge_part_subjects([res.get("subject_property") or {} for res in results])
    for res in results:
        refmap = {}
        for d in res.get("documents") or []:
            if not isinstance(d, dict):
                continue
            new_ref = f"{stem}-{len(merged['documents']) + 1}"
            refmap[str(d.get("ref", ""))] = new_ref
            d["ref"] = new_ref
            d["file"] = filename
            merged["documents"].append(d)
        for d in merged["documents"]:
            if d.get("parent_ref") in refmap:
                d["parent_ref"] = refmap[d["parent_ref"]]
        merged["discrepancies"] += [x for x in res.get("discrepancies") or [] if x]
    return merged


def _unit_digits(subject: dict) -> str:
    return (re.findall(r"\d+", subject.get("unit_no") or "") or [""])[0].lstrip("0")


def merge_part_subjects(parts: List[dict]) -> dict:
    """One file read in several page parts: each field from the part that actually prints it. A long deed's first part
    describes the land; the unit (flat no., floor, wing, carpet area) is in the schedule at the end — so a value is never
    taken from a reader's note, and the description that names the unit wins over one that does not."""
    out: dict = {}
    for sp in parts:
        for k, v in sp.items():
            v = clean_value(v)
            if v and not out.get(k):
                out[k] = v
    unit = _unit_digits(out)
    descs = [clean_value(sp.get("full_description") or "") for sp in parts]
    named = [d for d in descs if unit and re.search(rf"(?<!\d){unit}(?!\d)", d)]
    if named:
        out["full_description"] = max(named, key=len)
    return out


def complete_description(subject: dict) -> dict:
    """The schedule of the unit and the land description sit on different pages of a long agreement: when the chosen
    description does not name the unit, the unit clause is put in front of it (from the fields read)."""
    unit = _unit_digits(subject)
    desc = clean_value(subject.get("full_description") or "")
    if not unit or re.search(rf"(?<!\d){unit}(?!\d)", desc):
        return dict(subject, full_description=desc) if desc != subject.get("full_description") else subject
    parts = [f"{subject.get('unit_type') or 'Unit'} No. {subject['unit_no']}"]
    if subject.get("carpet_area") or subject.get("builtup_area"):
        parts.append(f"admeasuring {subject.get('carpet_area') or subject.get('builtup_area')}"
                     f"{' carpet area' if subject.get('carpet_area') and 'carpet' not in subject['carpet_area'].lower() else ''}")
    if subject.get("floor"):
        parts.append(f"on the {subject['floor']}{'' if 'floor' in subject['floor'].lower() else ' floor'}")
    wing = re.sub(r"(?i)^\s*wing\s*", "", subject.get("wing") or "")
    if wing:
        parts.append(f"of Wing {wing}")
    bldg = subject.get("building_name") or subject.get("project_name")
    if bldg:
        parts.append(f"of {bldg}")
    lead = " ".join(parts)
    return dict(subject, full_description=f"{lead}, {desc}" if desc else lead)


def merge_subjects(subjects: List[dict]) -> dict:
    """Latest document wins for each field (subjects passed oldest→newest)."""
    out = {}
    for sp in subjects:
        for k in _SUBJECT_FIELDS:
            if clean_value(sp.get(k)):
                out[k] = clean_value(sp[k])
        for k in ("search_terms_marathi", "search_terms_english"):
            out.setdefault(k, [])
            for t in sp.get(k) or []:
                if t and t not in out[k]:
                    out[k].append(t)
    return out


def _subject_weight(ext: dict) -> int:
    """How much a file's description of the property can be trusted: a registered title deed / Index-II first,
    other deeds next, certificates after that, bank sanction letters, KYC and payment slips last."""
    docs = ext.get("documents") or []
    w = 0
    for d in docs:
        cat, typ = (d.get("category") or "").lower(), (d.get("doc_type") or "").lower()
        if cat in ("title_transfer", "development") and (d.get("doc_number") or d.get("doc_no_raw")) and d.get("furnished", True):
            w = max(w, 4)
        elif cat == "registration_record" or "index" in typ:
            w = max(w, 4)
        elif cat in ("title_transfer", "development") and d.get("furnished", True):
            w = max(w, 3)
        elif cat in ("title_certificate", "approval", "revenue_record", "society"):
            w = max(w, 2)
        else:
            w = max(w, 1 if not re.search(r"sanction|offer|loan|kyc|pan|aadhaar|payment|receipt|tds", typ) else 0)
    return w


_ID_NOISE = {"the", "and", "of", "no", "building", "bldg", "tower", "wing", "co", "op", "operative", "cooperative",
             "housing", "society", "hsg", "soc", "ltd", "limited", "chs", "chsl", "apartment", "apartments", "complex",
             "project", "phase", "plot", "sector", "known", "as", "residency", "heights", "park", "nagar", "road"}


def _identity(ext: dict) -> Tuple[str, set, str]:
    """(unit key, building / project words, village) a file gives for its property; '' / empty when not stated."""
    sp = ext.get("subject_property") or {}
    unit = _unit_key(sp.get("unit_no") or "")
    words = set()
    for k in ("building_name", "project_name", "society_name"):
        words |= {w for w in re.findall(r"[a-z]{3,}", str(sp.get(k) or "").lower()) if w not in _ID_NOISE}
    village = re.sub(r"[^a-z]", "", str(sp.get("village") or "").lower())
    return unit, words, village


def _same_property(a: Tuple[str, set, str], b: Tuple[str, set, str]) -> bool:
    """Two files describe different properties only when the unit numbers differ AND neither the building nor the
    village is shared. A file without a unit (land / project papers) never conflicts."""
    if not a[0] or not b[0] or a[0] == b[0]:
        return True
    return bool(a[1] & b[1]) or bool(a[2] and a[2] == b[2])


def split_foreign(extractions: List[dict], borrower: str = "") -> Tuple[List[dict], List[dict]]:
    """Set aside files that describe ANOTHER property (e.g. a stray agreement of a different flat in the bundle).
    The property of the case is the group with the most reliable documents; the borrower's name breaks a tie.
    Returns (kept extractions, [{file, description}] of the files set aside)."""
    ids = [_identity(ex) for ex in extractions]
    named = [i for i, x in enumerate(ids) if x[0] and (x[1] or x[2])]
    if len({ids[i][0] for i in named}) < 2:
        return extractions, []
    bwords = {w for w in re.findall(r"[a-z]{3,}", (borrower or "").lower())}

    def score(i):
        group = [j for j in named if _same_property(ids[i], ids[j])]
        parties = " ".join(p.get("name", "") for j in group for d in extractions[j].get("documents") or []
                           for p in d.get("parties") or [] if isinstance(p, dict)).lower()
        return (sum(_subject_weight(extractions[j]) + 1 for j in group) + (3 if bwords and bwords & set(re.findall(r"[a-z]{3,}", parties)) else 0), len(group))

    main = max(named, key=score)
    kept, foreign = [], []
    for i, ex in enumerate(extractions):
        if _same_property(ids[main], ids[i]):
            kept.append(ex)
        else:
            sp = ex.get("subject_property") or {}
            files = sorted({d.get("file") for d in ex.get("documents") or [] if d.get("file")})
            foreign.append({"file": ", ".join(files) or "",
                            "description": sp.get("full_description") or " ".join(
                                str(sp.get(k)) for k in ("unit_no", "building_name", "village") if sp.get(k))})
    return kept, foreign


def merge_subjects_ranked(extractions: List[dict]) -> dict:
    """Property details from the most reliable file win (registered deed > other deeds > certificates > letters)."""
    order = sorted(range(len(extractions)), key=lambda i: (_subject_weight(extractions[i]), i))
    subject = reconcile_unit(merge_subjects([extractions[i].get("subject_property") or {} for i in order]), extractions)
    return complete_description(subject)


_UNIT_IN_TEXT = re.compile(r"\b(?:flat|unit|shop|gala|office|apartment|premises|room)\s*(?:no\.?|number)?\s*[:\-]?\s*"
                           r"([A-Za-z]?\s*-?\s*\d{1,5}(?:\s*-?[A-Za-z](?![A-Za-z]))?)(?![\d/])", re.I)


def _unit_key(u: str) -> str:
    """'04' / '4' / 'No. 4' → '4';  'B-1801' → 'B1801'."""
    t = re.sub(r"[\s\-]", "", str(u or "")).upper()
    m = re.match(r"^([A-Z]?)0*(\d+)([A-Z]?)$", t)
    return (m.group(1) + m.group(2) + m.group(3)) if m else t


def reconcile_unit(subject: dict, extractions: List[dict]) -> dict:
    """The unit number printed in most documents wins (registered deeds / Index-II count double). One misread letter
    (e.g. a possession letter read as 'Flat No. 01, Floor No. 04' for Flat No. 04 on the 1st floor) must not decide
    the subject property — and with a wrong unit number no IGR entry of the unit is ever matched."""
    if not subject.get("unit_no"):
        return subject
    votes, texts = {}, {}
    for ex in extractions:
        for d in ex.get("documents") or []:
            if not isinstance(d, dict) or (d.get("level") or "").lower() == "land":
                continue
            reg = (d.get("category") in ("title_transfer", "registration_record") and
                   bool(d.get("doc_number") or d.get("doc_no_raw") or d.get("reg_date")))
            for u in {_unit_key(m.group(1)) for m in _UNIT_IN_TEXT.finditer(d.get("property_text") or "")}:
                if re.search(r"\d", u):
                    votes[u] = votes.get(u, 0) + (2 if reg else 1)
                    if reg and d.get("category") == "title_transfer" and d.get("property_text"):
                        texts.setdefault(u, []).append((d.get("reg_date") or d.get("exec_date") or "", d["property_text"]))
    cur = _unit_key(subject["unit_no"])
    if not votes:
        return subject
    best = max(votes, key=votes.get)
    if best == cur or votes[best] < 3 or votes[best] < 2 * votes.get(cur, 0):
        return subject
    log.warning("subject unit %s replaced by %s (%s)", subject["unit_no"], best, votes)
    from .common import parse_date
    subject = dict(subject, unit_no=best if not best.isdigit() else
                   next((m.group(1).strip() for ex in extractions for d in ex.get("documents") or [] if isinstance(d, dict)
                         for m in _UNIT_IN_TEXT.finditer(d.get("property_text") or "") if _unit_key(m.group(1)) == best), best))
    if _unit_key(subject.get("floor", "")) == cur:
        subject["floor"] = ""                      # unit and floor were swapped in the misread description
    deeds = sorted(texts.get(best, []), key=lambda x: parse_date(x[0]) or parse_date("01/01/1900"))
    if deeds:
        subject["full_description"] = deeds[-1][1]  # latest registered deed of the right unit
    return subject


# ── e-Search receipt / GRAS challan (text PDFs in search_docs) ────────────────
def parse_search_receipt(data: bytes, filename: str) -> dict:
    try:
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return {}
    if len(text.strip()) < 30:
        return {}
    t = re.sub(r"[ \t]+", " ", text)
    out = {"file": filename}
    m = re.search(r"Receipt\s*no\.?\s*:?\s*(\d{6,})", t, re.I)
    if m:
        out["receipt_no"] = m.group(1)
    m = re.search(r"\b(MH\d{9,}[A-Z0-9]*)\b", t)
    if m:
        out["grn"] = m.group(1)
    m = re.search(r"From\s*:?\s*((?:19|20)\d{2})\s*To\s*:?\s*((?:19|20)\d{2})", t, re.I)
    if m:
        out["period_from"], out["period_to"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Received\s*Fee\s*:?\s*(\d+)", t, re.I)
    if m:
        out["fee"] = clean_num(m.group(1))
    m = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+(?:19|20)\d{2})", t) or re.search(r"(\d{2}/\d{2}/(?:19|20)\d{2})", t)
    if m:
        out["date"] = fmt_date(m.group(1))
    m = re.search(r"S\.?No/CTS\s*No/G\.?No\.?\s*:?\s*([\w/]+)", t, re.I)
    if m:
        out["survey_searched"] = m.group(1)
    m = re.search(r"Village\s*:?\s*([A-Za-z]+)", t)
    if m:
        out["village"] = m.group(1)
    return out if len(out) > 1 else {}
