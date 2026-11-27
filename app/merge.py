"""Stage 3 (pure code, no AI): merge PDF documents + IGR matches → one verified, sorted analysis."""
import re
from datetime import date, datetime
from collections import Counter
from typing import Dict, List, Optional

from . import config
from .common import (ascii_digits, clean_num, clean_value, docno_key, fmt_date, parse_date, parse_doc_no_raw, today_str, year_of)
from .igr import _split_names, default_parties, is_mortgage_type, is_release_type, skeleton

TRANSFEREE_ROLES = ("purchaser", "transferee", "allottee", "buyer", "donee", "assignee", "vendee", "lessee",
                    "releasee", "legatee", "beneficiary", "heir", "successor", "claimant")
TRANSFEROR_ROLES = ("vendor", "seller", "transferor", "builder", "promoter", "developer", "owner", "donor",
                    "assignor", "lessor", "executant", "releasor", "testator", "deceased")
MAIN_CATEGORIES = ("title_transfer", "development", "encumbrance")


# ── normalisation ──────────────────────────────────────────────────────────────
# a role or description written where a name should be ('Then old society', '16 members of the society') is not a party
_PLACEHOLDER = re.compile(r"^\s*(?:unknown|not\s+(?:stated|mentioned|known|available|given|legible)|n\.?\s*a\.?|nil|none|"
                          r"-+|\?+|\[.*\]|(?:the\s+|then\s+|said\s+|its\s+|(?:then\s+)?old\s+|existing\s+|\d+\s+)*"
                          r"(?:vendor|purchaser|owner|society|developer|promoter|builder|members?(?:\s+of\s+the\s+society)?|"
                          r"advocate(?:\s+of\s+the\s+promoter)?|tenants?|occupants?|allottees?)s?)\s*\.?\s*$", re.I)


def _norm_doc(d: dict) -> dict:
    d = dict(d)
    d["doc_type"] = (d.get("doc_type") or "").strip()
    d["category"] = (d.get("category") or "other").strip().lower()
    d["level"] = (d.get("level") or "premises").strip().lower()
    if d["category"] in ("encumbrance", "title_transfer") and             re.search(r"permission|noc|no.?objection|consent|sanction", d["doc_type"], re.I) and             not re.search(r"deed|agreement|indenture", d["doc_type"], re.I):
        d["category"] = "approval"                 # e.g. CIDCO 'Permission to Mortgage Letter'
    for k in ("exec_date", "reg_date"):
        v = fmt_date(d.get(k))
        if v and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", v):
            d.setdefault("key_terms", [])
            d["key_terms"] = list(d["key_terms"] or []) + [f"Date as stated: {v}"]
            v = ""                                   # "prior to 1993" is not a date
        d[k] = v
    for k in ("consideration", "market_value", "stamp_duty", "registration_fee"):
        d[k] = clean_num(d.get(k))
    raw_num = str(d.get("doc_number") or "")
    if re.search(r"[A-Za-z\u0900-\u097F/\-]", raw_num):           # e.g. "KRL1-3174-2025" → 3174 / 2025
        pn, py = parse_doc_no_raw(raw_num)
        yr = str(d.get("doc_year") or "").strip() or py
        if not pn:                                                   # e.g. "P/515" (year given separately)
            groups = [g for g in re.findall(r"\d+", ascii_digits(raw_num)) if g != yr]
            pn = str(int(groups[-1])) if groups else ""
        num = pn
    else:
        num, yr = clean_num(raw_num).split(".")[0], str(d.get("doc_year") or "").strip()
    if not num or not yr:
        pn, py = parse_doc_no_raw(d.get("doc_no_raw", ""))
        num, yr = num or pn, yr or py
    if not yr:
        m2 = re.fullmatch(r"\s*0*(\d{1,6})\s*[/\-]\s*(\d{2})\s*", ascii_digits(d.get("doc_no_raw") or ""))
        if m2:                                   # '09915/06' = 9915/2006 (two-digit year of the old registers)
            num = num or m2.group(1)
            yr = m2.group(2)
    if re.fullmatch(r"\d{2}", yr or ""):
        yr = ("20" if int(yr) <= datetime.now().year % 100 else "19") + yr
    d["doc_number"], d["doc_year"] = num, yr
    # Index-II identifiers ('ओळख') and witnesses sign the registration but are never parties to the deed
    d["parties"] = [p for p in (d.get("parties") or []) if isinstance(p, dict) and p.get("name")
                    and not re.search(r"identif|witness|ओळख|साक्षी|attesting", p.get("role") or "", re.I)
                    and not _PLACEHOLDER.match(p["name"])]          # 'Unknown' / 'Not stated' is not a party
    for k in ("property_text", "short_description"):
        if isinstance(d.get(k), str):
            d[k] = clean_value(d[k])
    d["key_terms"] = [clean_value(t) for t in (d.get("key_terms") or [])]
    d["furnished"] = bool(d.get("furnished", True))
    st = str(d.get("execution_status") or "").lower()
    # only recited, with nothing identifying it (no date, number or reference) — a requirement, not a chain entry
    d["recited_only"] = (not d["furnished"]) and not (d["exec_date"] or d["reg_date"] or num or d.get("reference_no"))
    # a furnished agreement / deed that is not executed (blank date, no registration) — a draft, not a transfer
    d["draft"] = d["category"] in ("title_transfer", "development") and d["furnished"] and d.get("source", "pdf") != "igr" and (
        st.startswith("draft") or (st in ("", "unknown") and not d["exec_date"] and not d["reg_date"] and not num
                                   and bool(re.search(r"agreement|deed|conveyance", d["doc_type"], re.I))))
    d["key_terms"] = [k for k in (d.get("key_terms") or []) if k]
    d.setdefault("files", [d.get("file", "")])
    d.setdefault("source", "pdf")
    return d


def _key(d: dict):
    if d["category"] in ("receipt", "registration_record", "identity"):
        return None  # these attach to parents; never merge by deed number
    return docno_key(d.get("doc_number"), d.get("doc_year"))


_FAMILIES = ((r"\blease|indenture of lease|भाडेपट्टा", "lease deed"), (r"7\s*/\s*12|gaon\s*namuna|satbara|sat\s*bara|record of rights", "7-12 extract"),
             (r"commencement", "commencement certificate"), (r"occupa|completion", "occupancy certificate"),
             # 'Intimation of Disapproval', 'IOD - conditions pages', 'IOD / Sanction of Building Plans (Annexure B)'
             (r"intimation\s+of\s+dis-?approval|\bi\.?\s?o\.?\s?d\b", "iod"),
             (r"(?:society|societies)\b.{0,25}registration|registration\s+certificate.{0,40}societ", "society registration certificate"),
             (r"\bn\.?\s*a\.?\s*(?:order|permission)|non.?agricultural|बिनशेती", "na order"),
             (r"development\s+agreement|agreement\s+for\s+development|विकसन", "development agreement"),
             (r"title\s*cert|search\s*and\s*title|title\s*report", "title certificate"),
             (r"index\s*-?\s*(ii|2)|सूची", "index-ii"),
             # 'Property Card Extract', 'Property Register Card' and the City Survey 'Property Register' are one record
             (r"property\s*(?:register\s*)?card|property\s+register\b", "property card"))


def _doc_family(doc_type: str) -> str:
    t = re.sub(r"^\s*registered\s+", "", (doc_type or "").lower())
    for rx, fam in _FAMILIES:
        if re.search(rx, t):
            return fam
    return t


def _draft_family(doc_type: str) -> str:
    """'Agreement for Sale (RERA format)' and 'Agreement for Sale' read from parts of one draft are the same draft."""
    return re.sub(r"\s*\([^)]*\)\s*", " ", _doc_family(doc_type)).strip()


def _recital_family(doc_type: str) -> str:
    """Looser than _doc_family, only to tell whether a recited certificate is already listed: every RERA certificate
    of the project (registration, Form C, extension) answers a recital of 'the RERA Certificate'."""
    return "rera certificate" if re.search(r"\brera\b|maharera|real estate regulatory", doc_type or "", re.I) \
        else _doc_family(doc_type)


def _soft_key(d: dict):
    t = re.sub(r"[^a-z0-9]", "", _doc_family(d["doc_type"]))
    ref = re.sub(r"\W", "", (d.get("reference_no") or "").lower())
    if re.search(r"share\s*cert", d["doc_type"], re.I):
        # 'Certificate No. 04' and 'Share Certificate No. 04 (Member Register No. 04)' are the same certificate
        nums = re.findall(r"\d+", ascii_digits(d.get("reference_no") or ""))
        ref = str(int(nums[0])) if nums else ref
    return (t, d.get("exec_date") or d.get("reg_date") or "", ref)


# titles, also when written without a space ("M/s.VMS Enterprises", "Mr.Ashutosh") or in Marathi ("मे व्हि एम एस")
_HONORIFIC = re.compile(r"^\s*(?:(?:shri|shree|smt|sau|mr|mrs|ms|miss|dr|m/s|messrs|the|kum|late)(?:\.\s*|\s+)|"
                        r"(?:श्रीमती|श्री|सौ|कु|मेसर्स|मे|डॉ|स्व)(?:\.\s*|\s+))+", re.I)


def _near(a: str, b: str) -> bool:
    """Same reference number apart from one misread character (OCR: 9523 / 9423)."""
    if a == b:
        return True
    if not a or not b or abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 4:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = sorted((a, b), key=len)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def _ref_tail(d: dict) -> tuple:
    """The last two numbers of a reference ('REV/DESK-I/T-9/NAP/SR-88/2010' → ('88', '2010'))."""
    nums = re.findall(r"\d+", ascii_digits(d.get("reference_no") or ""))
    return tuple(n.lstrip("0") for n in nums[-2:]) if len(nums) >= 2 else ()


def _distinct_nums(d: dict) -> set:
    """Numbers of 5+ digits in a reference ('…/TC/10687/2014-2015' → {'10687'}): one misread prefix letter or office
    digit never changes them. Years are left out — every certificate of a year shares them."""
    return {n.lstrip("0") for n in re.findall(r"\d{5,}", ascii_digits(d.get("reference_no") or ""))}


def _same_instrument(d: dict, seen: List[dict]):
    """Copies of one instrument that the number / date keys cannot join:
       - a recital (not furnished) of a deed whose copy is furnished: same category, date and parties;
       - the same certificate / letter read twice (reference differing by one character, or its covering letter)."""
    fam = _doc_family(d["doc_type"])
    dt = d.get("exec_date") or d.get("reg_date") or ""
    for m in seen:
        if m["category"] in ("receipt", "registration_record", "identity"):
            continue
        mdt = m.get("exec_date") or m.get("reg_date") or ""
        ref, mref = (re.sub(r"\W", "", (x.get("reference_no") or "").lower()) for x in (d, m))
        if len(ref) >= 8 and _near(ref, mref) and _doc_family(m["doc_type"]) == fam and \
                d["category"] in ("approval", "title_certificate") and m["category"] == d["category"]:
            return m                      # same certificate number — the dates differ only by a misread digit
        if d["category"] in ("approval", "title_certificate", "society") and m["category"] == d["category"] and \
                _doc_family(m["doc_type"]) == fam and _distinct_nums(d) & _distinct_nums(m) and \
                not (dt and mdt and dt != mdt):
            return m                      # 'MUEM-3/WL/HSG/TC/10687/2014-2015' = 'MUM-2/WL/HSG/TC/10687/2014-2015'
        if not dt or dt != mdt:
            continue
        if d["category"] in ("approval", "title_certificate") and m["category"] == d["category"] and \
                _doc_family(m["doc_type"]) == fam and _ref_tail(d) and _ref_tail(d) == _ref_tail(m):
            return m                      # one order, its number transliterated differently (REV/… = Mahasul/… SR-88/2010)
        if not d["furnished"] and m["furnished"] and m["category"] == d["category"] and \
                d["category"] in MAIN_CATEGORIES + ("title_certificate", "approval", "society") and \
                (not d.get("parties") or _people(m) & _people(d)) and \
                not (d.get("doc_number") and m.get("doc_number") and d["doc_number"] != m["doc_number"]):
            return m
        if _doc_family(m["doc_type"]) == fam and m["category"] == d["category"] and \
                d["category"] in ("approval", "title_certificate", "society", "other") and (
                d.get("parent_ref") == m["ref"] or
                _near(re.sub(r"\W", "", (d.get("reference_no") or "").lower()),
                      re.sub(r"\W", "", (m.get("reference_no") or "").lower()))):
            return m
    return None


def _merge_leases(docs: List[dict], ref_alias: Dict[str, str]) -> List[dict]:
    """A plot is leased once to the same lessee: every land-level lease / agreement-to-lease copy between the same
    parties (pages read as separate documents, a recital of its registration) is ONE lease. The copy naming a Lessor
    and carrying a date is kept; a registration number (with year) from any copy is added to it."""
    leases = [d for d in docs if d["level"] == "land" and d["category"] in ("title_transfer", "development")
              and _doc_family(d["doc_type"]) == "lease deed" and d.get("parties")]
    groups: List[List[dict]] = []
    for d in leases:
        g = next((g for g in groups if any(_people(x, lessee_only=True) & _people(d, lessee_only=True) for x in g)), None)
        (g.append(d) if g is not None else groups.append([d]))
    drop = set()
    for g in groups:
        if len(g) < 2:
            continue
        score = lambda x: (x["furnished"], any(re.search(r"lessor|licensor", p.get("role") or "", re.I) for p in x["parties"]),
                           bool(x.get("exec_date")), bool(x.get("doc_number") and x.get("doc_year")))
        master = max(g, key=score)
        master["category"] = "title_transfer"
        # the registration number: a properly printed one (TNN3-10923-1999, 10923/1999) not older than the lease itself
        def reg_score(x):
            yr = int(x["doc_year"]) if str(x.get("doc_year") or "").isdigit() else 0
            ex = (parse_date(x.get("exec_date")) or parse_date(master.get("exec_date")) or date(1900, 1, 1)).year
            return (bool(parse_doc_no_raw(x.get("doc_no_raw") or "")[0]), yr >= ex)
        regs = [x for x in g if x.get("doc_number") and x.get("doc_year") and reg_score(x)[1]]
        reg = max(regs, key=reg_score) if regs else None
        if reg is not master:
            for f in ("doc_number", "doc_year", "doc_no_raw", "reg_date", "sro"):
                if f == "sro":
                    master[f] = (reg or {}).get(f) or master.get(f, "")
                else:                             # the registration details travel together, or not at all
                    master[f] = (reg or {}).get(f, "")
        for x in g:
            if x is master:
                continue
            for f in ("doc_number", "doc_year", "doc_no_raw", "reg_date"):
                x[f] = ""                         # page stamp / receipt number misread as the lease number
            _fill(master, x)
            ref_alias[x["ref"]] = master["ref"]
            drop.add(id(x))
    return [d for d in docs if id(d) not in drop]


def _fill(dst: dict, src: dict):
    for k, v in src.items():
        if k in ("files", "key_terms", "parties"):
            continue
        if v not in ("", None, [], {}) and dst.get(k) in ("", None, [], {}):
            dst[k] = v
    if src.get("furnished"):
        dst["furnished"] = True
    if len(src.get("parties", [])) > len(dst.get("parties", [])) and src.get("furnished", True):
        dst["parties"] = src["parties"]
    dst["files"] = sorted(set(dst.get("files", []) + src.get("files", [])) - {""})
    dst["key_terms"] = list(dict.fromkeys(dst.get("key_terms", []) + src.get("key_terms", [])))


def _amt(v) -> int:
    """5020835.336 and 5020835 are the same amount (IGR prints paise / decimals)."""
    try:
        return int(float(re.sub(r"[^\d.]", "", str(v)) or 0))
    except ValueError:
        return 0


def _people(d: dict, lessee_only: bool = False) -> set:
    """First letters of each party name (titles such as Shri / M/s dropped). lessee_only: the party that takes the
    lease (the lessor of a CIDCO / MIDC plot is written in many ways — 'CIDCO Ltd.', 'City & Industrial ...')."""
    ps = [p for p in d.get("parties") or [] if p.get("name")]
    if lessee_only:
        ps = [p for p in ps if not re.search(r"lessor|licensor|issuing|authority|representative|owner|vendor",
                                             p.get("role") or "", re.I)]
    # consonant skeleton: OCR / spelling variants of one name agree ('Sini' / 'Cini', 'Pravin Kumar' / 'Pravinkumar')
    return {skeleton(_HONORIFIC.sub("", p["name"].strip()))[:6] for p in ps} - {""}


def _support(d: dict, support: Counter) -> int:
    """How many documents of the file name the parties of this copy: a misread name ('Abhini Kumar Minha') is
    found nowhere else; the real vendor of a resale is the purchaser of the earlier deeds."""
    return sum(support[n] - 1 for n in _people(d))


def _same_deed(d: dict, seen: List[dict]):
    """A deed copy whose number / date was misread (e.g. a page stamp read as the document number) is the same deed
    when the family, the consideration and the parties agree."""
    amt = re.sub(r"\D", "", str(d.get("consideration") or ""))
    if not amt or int(amt or 0) < 1000:
        return None
    for m in seen:
        if m["category"] not in ("title_transfer", "development") or _doc_family(m["doc_type"]) != _doc_family(d["doc_type"]):
            continue
        if re.sub(r"\D", "", str(m.get("consideration") or "")) != amt:
            continue
        if _people(m) & _people(d) and not (m.get("exec_date") and d.get("exec_date") and m["exec_date"] != d["exec_date"]):
            return m
    return None


def dedupe_documents(docs: List[dict]) -> List[dict]:
    out: List[dict] = []
    by_key: Dict = {}
    by_soft: Dict = {}
    ref_alias: Dict[str, str] = {}
    support = Counter(n for x in docs for n in _people(_norm_doc(x)))
    # furnished copies first so they become the master record
    for d in sorted((_norm_doc(x) for x in docs), key=lambda x: not x["furnished"]):
        k, sk = _key(d), _soft_key(d)
        master = by_key.get(k) if k else None
        if master is None and d["category"] not in ("receipt", "registration_record", "identity") and (sk[1] or sk[2] or d["category"] in ("revenue_record", "plan")):
            master = by_soft.get(sk)
        if master is None and sk[1] and d["category"] not in ("receipt", "registration_record", "identity"):
            # same family and date, one copy without a reference number → same certificate / deed
            master = next((m for (f, dt, ref), m in by_soft.items() if f == sk[0] and dt == sk[1] and (not ref or not sk[2])), None)
        if master is None and d["category"] in ("title_transfer", "development"):
            master = _same_deed(d, out)
            if master is not None and d.get("exec_date") and d.get("doc_year") and not master.get("exec_date"):
                for f in ("doc_number", "doc_year", "doc_no_raw", "exec_date", "reg_date", "sro"):
                    if d.get(f):
                        master[f] = d[f]          # the copy with a proper date and registration number is the right one
                if _key(master):
                    by_key[_key(master)] = master
            if master is not None and _people(d) != _people(master) and                     _support(d, support) > _support(master, support):
                master["parties"] = d["parties"]
                master["parties_fixed"] = True
        if master is None and d.get("draft"):
            # one draft agreement read in several page parts (the later parts without parties) is one document
            master = next((m for m in out if m.get("draft") and _draft_family(m["doc_type"]) == _draft_family(d["doc_type"])
                           and (not d.get("parties") or not m.get("parties") or _people(m) & _people(d))), None)
        if master is None and d["category"] not in ("receipt", "registration_record", "identity"):
            master = _same_instrument(d, out)
        if master is not None:
            _fill(master, d)
            ref_alias[d["ref"]] = master["ref"]
            continue
        out.append(d)
        if k:
            by_key[k] = d
        by_soft.setdefault(sk, d)
    out = _merge_leases(out, ref_alias)
    for d in out:
        if d.get("parent_ref") in ref_alias:
            d["parent_ref"] = ref_alias[d["parent_ref"]]
    return out


# ── IGR → documents ─────────────────────────────────────────────────────────────
def _has_role(p: dict, roles) -> bool:
    """'Representative of M/s X Builders' signs for a party — it is not itself a builder / purchaser."""
    role = (p.get("role") or "").lower()
    return any(r in role for r in roles) and not re.search(r"represent|through|attorney|partner|director|signator", role)


def _on_side(name: str, side_skel: str) -> bool:
    sk = skeleton(_HONORIFIC.sub("", display_name(name).strip()))[:6]
    return len(sk) >= 3 and sk in side_skel


def _name_key(n: str) -> str:
    """'मे युनिवर्स इंटरप्राईजेस भागीदारी संस्था तर्फे …' and 'M/s. Universe Enterprises' → the same key."""
    n = re.sub(r"^\s*(?:मेसर्स|मे\.?)\s+", "", display_name(n or "").strip())
    return skeleton(_HONORIFIC.sub("", n))[:6]


_COUNTER_ROLE = {"vendor": "Purchaser", "seller": "Purchaser", "lessor": "Lessee", "transferor": "Transferee",
                 "assignor": "Assignee", "donor": "Donee", "releasor": "Releasee", "owner": "Purchaser"}


def _check_parties(d: dict, row: dict, igr_parties: List[dict], known: Optional[Dict[str, str]] = None) -> None:
    """The Index-II / IGR row is the registered record of who sold and who bought. When none of the purchasers read from
    the copy is a purchaser in IGR (e.g. the Index-II identifiers 'ओळख' read as purchasers, the real purchasers read
    as vendors), the parties are taken from IGR and the difference is reported for the advocate to verify."""
    if d["category"] not in ("title_transfer", "development") or is_mortgage_type(d["doc_type"] + " " + (row.get("docname_raw") or "")):
        return
    sel, pur = skeleton(row.get("seller_raw") or ""), skeleton(row.get("purchaser_raw") or "")
    # development agreement: the owners give, the developer takes ('developer' is a transferor role of a flat sale)
    dev = d["category"] == "development" or bool(re.search(r"develop|विकसन", d["doc_type"], re.I))
    take_roles = TRANSFEREE_ROLES + (("developer", "promoter", "builder") if dev else ())
    give_roles = ("owner", "vendor", "lessor", "principal", "landlord") if dev else TRANSFEROR_ROLES
    takers = [p for p in d.get("parties") or [] if _has_role(p, take_roles)]
    if dev:
        # 'Developer (POA holder)', 'Developer / Confirming Party': any developer read counts — the Index-II decides
        takers = [p for p in d.get("parties") or [] if re.search(r"develop|promot|builder|purchas|transferee", p.get("role") or "", re.I)]
    if not takers and pur and d.get("parties"):
        # only one side read (a deed recited as "Lease by MHADA" without the lessee): the Index-II names the other side
        g = next((p["role"] for p in d["parties"] if _has_role(p, give_roles)), "")
        t_role = "Developer" if dev else _COUNTER_ROLE.get(next((k for k in _COUNTER_ROLE if k in g.lower()), ""), "Purchaser")
        # the IGR columns are executant / claimant, not giver / taker (a lessee society is often the executant), so only
        # a registered party that the other papers already name (e.g. the society) is taken as the missing side
        have = {_name_key(p["name"]) for p in d["parties"]}
        for p in igr_parties or []:
            if re.search(r"represent|witness|identif", p.get("role") or "", re.I):
                continue
            psk = skeleton(p.get("name") or "")
            for key, name in (known or {}).items():
                if key not in have and (key == _name_key(p.get("name") or "") or _on_side(name, psk)):
                    d["parties"].append({"name": name, "role": t_role})
                    have.add(key)
                    break
        return
    if not sel or not pur or not takers or any(_on_side(p["name"], pur) for p in takers):
        return
    t_role = "Developer" if dev else next((p["role"] for p in takers), "Purchaser")
    g_role = "Owner" if dev else next((p["role"] for p in d["parties"] if _has_role(p, give_roles)), "Vendor")
    fixed = []
    for p in d["parties"]:
        if _on_side(p["name"], pur):
            fixed.append({**p, "role": t_role})
        elif _on_side(p["name"], sel):
            # an owner read as 'Developer' is still an owner when the Index-II lists him among the executants
            fixed.append({**p, "role": g_role} if dev or _has_role(p, give_roles + take_roles) else p)
        elif not (_has_role(p, give_roles + take_roles) or (dev and p in takers)):
            fixed.append(p)                       # representatives, confirming parties
    for p in igr_parties or []:                   # registered parties the copy does not show (e.g. the developer)
        sk = _name_key(p.get("name") or "")
        if not sk or any(_name_key(f["name"]) == sk for f in fixed):
            continue
        name = (known or {}).get(sk) or p["name"]   # the English spelling used in the papers, when the file has one
        if _on_side(p["name"], pur):
            fixed.append({"name": name, "role": t_role})
        elif _on_side(p["name"], sel):
            fixed.append({"name": name, "role": g_role})
    if not any(_has_role(p, take_roles) for p in fixed) or not any(_has_role(p, give_roles) for p in fixed):
        d.setdefault("igr_mismatch", []).append(
            "VERIFY: the purchasers read from the document (" + ", ".join(p["name"] for p in takers) +
            ") are not the purchasers registered in IGR — parties to be checked with the Index-II.")
        return
    dropped = [p["name"] for p in takers if not any(f["name"] == p["name"] for f in fixed)]
    fixed = list({(f["name"], f.get("role")): f for f in fixed}.values())      # a name read on both sides, now one
    d["parties"], d["parties_fixed"] = fixed, True
    d.setdefault("igr_mismatch", []).append(
        "VERIFY: parties taken from the IGR / Index-II record — the document as read named "
        + ", ".join(p["name"] for p in takers) + " as purchaser(s)"
        + (f" ({', '.join(dropped)} are not parties in IGR — identifiers / witnesses)" if dropped else "") + ".")


def _check_mortgage_parties(d: dict, row: dict, igr_parties: List[dict], known: Optional[Dict[str, str]] = None) -> None:
    """A mortgage of the papers linked to its IGR row: when the mortgagor read from the papers is not a party of the
    registered row at all (a recital misread as 'CRDL' for 'Shree Swami Samarth Builders & Developers'), the registered
    mortgagor / mortgagee are used and the difference is reported."""
    if not is_mortgage_type(d["doc_type"] + " " + (row.get("docname_raw") or "")) or is_charge_release(d["doc_type"]):
        return
    sides = skeleton((row.get("seller_raw") or "") + " " + (row.get("purchaser_raw") or ""))
    borrowers = [p for p in d.get("parties") or [] if re.search(r"mortgagor|borrower", p.get("role") or "", re.I)]
    reg = [p for p in igr_parties or [] if re.search(r"mortgagor|mortgagee", p.get("role") or "", re.I)]
    if not sides or not borrowers or not reg or any(_on_side(p["name"], sides) for p in borrowers):
        return
    # the lender read from the papers keeps its English spelling; only the side that is not registered is replaced
    lenders = [p for p in d.get("parties") or [] if re.search(r"mortgagee|lender", p.get("role") or "", re.I)]
    reg_borr = [p for p in reg if re.search(r"mortgagor", p.get("role") or "", re.I)]
    reg_lend = [p for p in reg if re.search(r"mortgagee", p.get("role") or "", re.I)]
    eng = lambda p: {**p, "name": (known or {}).get(_name_key(p.get("name") or "")) or p["name"]}
    d["parties"] = [eng(p) for p in reg_borr] + (lenders if lenders else [eng(p) for p in reg_lend])
    d.setdefault("igr_mismatch", []).append(
        "VERIFY: mortgagor read from the documents (" + ", ".join(p["name"] for p in borrowers) +
        ") is not a party to the registered mortgage in IGR — parties taken from the IGR record.")


_ABBR = ((r"\bco\s*-?\s*op(?:erative)?\b|\bcoop\b", "cooperative"), (r"\bhsg\b|\bhousing\b", "housing"),
         (r"\bsoc(?:iety)?\b|\bsoc\.", "society"), (r"\bltd\b|\blimited\b", "limited"), (r"\bchs\b", "cooperative housing society"),
         (r"\bbldg\b", "building"), (r"\bapts?\b", "apartment"), (r"\brd\b", "road"), (r"\bmg\b|\bmarg\b", "road"),
         (r"\b(?:w|west)\b", "west"), (r"\b(?:e|east)\b", "east"), (r"\bno\b", "number"))


def _trivial_discrepancy(text: str) -> bool:
    """"Building name 'Jilani Heights Co-op Hsg Soc Ltd' instead of 'JILANI HEIGHTS Co-op Hsg Society Ltd'" differs only
    in case and abbreviations — not a discrepancy worth an observation."""
    q = re.findall(r"'([^']+)'|\"([^\"]+)\"", text or "")
    vals = [a or b for a, b in q]
    if len(vals) != 2:
        return False

    def norm(v):
        v = v.lower().replace(".", " ")
        for rx, rep in _ABBR:
            v = re.sub(rx, rep, v)
        return re.sub(r"[^a-z0-9ऀ-ॿ]", "", v)
    return norm(vals[0]) == norm(vals[1])


def attach_igr(docs: List[dict], igr_rows: List[dict], classes: Dict[str, dict]) -> dict:
    by_key = {_key(d): d for d in docs if _key(d)}
    known = {}                                    # English party names of the papers, by name key
    for x in docs:
        for p in x.get("parties") or []:
            if p.get("name") and not re.search(r"[ऀ-ॿ]", p["name"]) and not re.search(r"represent|through", p.get("role") or "", re.I):
                known.setdefault(_name_key(p["name"]), display_name(p["name"]))
    entries, project_entries = [], []
    for r in igr_rows:
        c = classes.get(r["id"])
        if not c:
            continue
        e = {**r, **{k: c.get(k, "") for k in ("class", "level", "doc_type_en", "property_en", "unit_no",
                                                 "building", "reason", "discrepancy")},
             "parties_en": c.get("parties") or default_parties(r, c.get("doc_type_en") or "")}
        if is_charge_release((c.get("doc_type_en") or "") + " " + r["docname_raw"]):
            # a reconveyance is between the lender (releasor) and the borrower — never 'Executant' / 'Claimant' by column
            e["parties_en"] = default_parties({**r, "docname_raw": ""}, "Mortgage")
        cls = e["class"]
        if cls in ("A", "B", "D"):
            entries.append(e)
        elif cls == "C":
            project_entries.append(e)
        if cls not in ("A", "B"):
            continue
        k = docno_key(r["docno"], r["year"])
        d = by_key.get(k)
        if d:
            d["igr_ref"] = r["id"]
            d["verified_in_igr"] = True
            if cls == "B" and d["level"] == "premises":
                # registered on the land / project (e.g. the developer's mortgage recited in the agreement as if it
                # were a charge on the flat) — it belongs to the land chain, not the unit chain
                d["level"] = "project" if d["category"] == "encumbrance" else "land"
            d["reg_date"] = d["reg_date"] or r["reg_date"]
            d["sro_igr"] = r["sro"]
            for f in ("consideration", "market_value", "stamp_duty", "registration_fee"):
                if not d.get(f) and r.get(f) and r[f] != "0":
                    d[f] = r[f]
                elif d.get(f) and r.get(f) and r[f] != "0" and abs(_amt(d[f]) - _amt(r[f])) > 1:   # IGR prints paise
                    d.setdefault("igr_mismatch", []).append(f"{f}: document {d[f]} vs IGR {r[f]}")
            if e.get("discrepancy") and not _trivial_discrepancy(e["discrepancy"]):
                d.setdefault("igr_mismatch", []).append(e["discrepancy"])
            _check_parties(d, r, e["parties_en"], known)
            _check_mortgage_parties(d, r, e["parties_en"], known)
        else:
            dt = e["doc_type_en"] or r["docname_raw"]
            if cls == "B" and re.fullmatch(r"\s*(?:agreement for sale|agreement|करारनामा)\s*", dt, re.I):
                dt = "Agreement for Sale of Land"           # IGR "करारनामा" of land, not of a flat
            charge = is_mortgage_type(e["doc_type_en"] or r["docname_raw"]) or \
                is_charge_release((e["doc_type_en"] or "") + " " + r["docname_raw"])     # a reconveyance closes a charge
            nd = _norm_doc({
                "ref": f"IGR-{r['id']}", "source": "igr", "doc_type": dt,
                "category": "encumbrance" if charge else "title_transfer",
                "level": ("project" if charge else "land") if cls == "B" else "premises",
                "exec_date": r["exec_date"], "reg_date": r["reg_date"], "doc_number": r["docno"],
                "doc_year": str(r["year"] or ""), "doc_no_raw": f"{r['docno']}/{r['year']}", "sro": r["sro"],
                # a party the papers already name keeps the papers' English spelling ('M/s. Universe Enterprises')
                "parties": [{**p, "name": known.get(_name_key(p.get("name") or "")) or p.get("name")}
                            for p in e["parties_en"] or []],
                "consideration": r["consideration"], "market_value": r["market_value"],
                "stamp_duty": r["stamp_duty"], "registration_fee": r["registration_fee"],
                "property_text": e["property_en"], "furnished": False, "file": r["source_file"],
                "short_description": "", "key_terms": [],
            })
            nd["igr_ref"], nd["verified_in_igr"] = r["id"], True
            docs.append(nd)
            if _key(nd):
                by_key[_key(nd)] = nd
    # A deed the papers name by its registration number IS in the search data even when its row was not classed as the
    # subject (the owners' area-share agreement listing their own flats, a release of other hissas by the same family).
    # Same number + year and a common party = the same deed: verified from Index-II and listed in the Search Report.
    for r in igr_rows:
        d = by_key.get(docno_key(r["docno"], r["year"]))
        if not d or d.get("verified_in_igr") or d.get("source") == "igr" or d["category"] not in MAIN_CATEGORIES:
            continue
        if not _people(d) & (_side_names(r.get("seller_raw", "")) | _side_names(r.get("purchaser_raw", ""))):
            continue                              # same number, other parties: a misread number, not this deed
        c = classes.get(r["id"]) or {}
        e = {**r, **{k: c.get(k, "") for k in ("level", "doc_type_en", "property_en", "unit_no", "building", "discrepancy")},
             "class": "A" if d["level"] == "premises" else "B",
             "reason": (c.get("reason", "") + " | code: deed named in the documents furnished").strip(" |"),
             "parties_en": c.get("parties") or default_parties(r, c.get("doc_type_en") or r.get("docname_raw") or "")}
        entries.append(e)
        classes[r["id"]] = {**c, "id": r["id"], "class": e["class"], "reason": e["reason"]}
        project_entries[:] = [x for x in project_entries if x["id"] != r["id"]]
        d["igr_ref"], d["verified_in_igr"] = r["id"], True
        d["reg_date"] = d["reg_date"] or r["reg_date"]
        d["sro_igr"] = r["sro"]
        for f in ("consideration", "market_value", "stamp_duty", "registration_fee"):
            if not d.get(f) and r.get(f) and r[f] != "0":
                d[f] = r[f]
        _check_parties(d, r, e["parties_en"], known)
    return {"entries": entries, "project_entries": project_entries}


def _side(d: dict, roles) -> set:
    return {skeleton(_HONORIFIC.sub("", display_name(p["name"]).strip()))[:6] for p in d.get("parties") or []
            if _has_role(p, roles)} - {""}


def _page_span(d: dict) -> Optional[tuple]:
    """'46-48' → (46, 48); '1, 4' → (1, 4); '' → None."""
    nums = [int(n) for n in re.findall(r"\d+", ascii_digits(str(d.get("pages") or "")))]
    return (min(nums), max(nums)) if nums else None


def _page_part_owner(d: dict, docs: List[dict], fam: str) -> Optional[dict]:
    """The dated / registered deed of the same kind and level that shares a file with this page part; the nearest one
    starting before it when the file holds several."""
    files = set(d.get("files") or []) - {""}
    cands = [x for x in docs if x is not d and x["category"] in ("title_transfer", "development") and not x.get("draft")
             and _draft_family(x["doc_type"]) == fam and x["level"] == d["level"]
             and (x.get("exec_date") or x.get("doc_number")) and files & set(x.get("files") or [])]
    if len(cands) <= 1:
        return cands[0] if cands else None
    me = _page_span(d)
    before = [x for x in cands if me and _page_span(x) and _page_span(x)[0] <= me[0]]
    return max(before, key=lambda x: _page_span(x)[0]) if before else None


def absorb_misread(docs: List[dict]) -> List[dict]:
    """After the IGR check. Copies the number / date keys could not join:
       - a deed copy whose registration number was misread (a page stamp, a scan number) — not in the search, while the
         same kind of deed between the same parties IS in the search: one deed, the verified number stands;
       - an empty read of a deed (no date, number or parties — a cover page or a later page part): it adds nothing and
         belongs to the draft of the same kind, or to the verified deed of that kind."""
    deeds = ("title_transfer", "development")
    verified = [m for m in docs if m.get("verified_in_igr") and m["category"] in deeds]
    drafts = [m for m in docs if m.get("draft")]
    alias, out = {}, []
    for d in docs:
        m = None
        if d["category"] in deeds and not d.get("verified_in_igr") and not d.get("draft") and d.get("source") != "igr":
            empty = not (d.get("parties") or d.get("exec_date") or d.get("reg_date") or d.get("doc_number"))
            fam = _draft_family(d["doc_type"])
            if empty:
                m = next((x for x in drafts if _draft_family(x["doc_type"]) == fam), None) or \
                    next((x for x in verified if _draft_family(x["doc_type"]) == fam and x["level"] == d["level"]), None)
            else:
                takers, givers = _side(d, TRANSFEREE_ROLES), _side(d, TRANSFEROR_ROLES)
                amt = _amt(d.get("consideration"))
                same_time = lambda x: (not d.get("exec_date") or d["exec_date"] == x.get("exec_date")) and \
                    not (d.get("doc_year") and x.get("doc_year") and d["doc_year"] != x["doc_year"])
                m = next((x for x in verified if _draft_family(x["doc_type"]) == fam and takers and takers & _side(x, TRANSFEREE_ROLES)
                          and (not givers or givers & _side(x, TRANSFEROR_ROLES)) and same_time(x)), None)
                if m is None and not takers and amt >= 1000:
                    # no parties read, but the same kind of deed of the same level for the same price, same year
                    m = next((x for x in verified if _draft_family(x["doc_type"]) == fam and x["level"] == d["level"]
                              and _amt(x.get("consideration")) == amt and same_time(x)), None)
                if m is None and not takers and not d.get("exec_date") and not d.get("doc_number"):
                    # a later page part of a long deed (no date, no number, no purchaser; the builder's name misread
                    # 'J.Y. Payak / Irmad Yatub' for 'I.Y. Payak / Ismail Yakub'): it belongs to the dated deed of the
                    # same kind read from the same file — never a deed of its own
                    m = _page_part_owner(d, docs, fam)
        if m is not None:
            for f in ("doc_number", "doc_year", "doc_no_raw"):
                d[f] = ""                         # the misread number never travels to the verified deed
            keep = m.get("parties")
            _fill(m, d)
            if keep:
                m["parties"] = keep               # the verified deed's parties stand
            alias[d["ref"]] = m["ref"]
            continue
        out.append(d)
    for d in out:
        if d.get("parent_ref") in alias:
            d["parent_ref"] = alias[d["parent_ref"]]
    return out


def _repoint(docs: List[dict], alias: Dict[str, str]) -> None:
    for d in docs:
        if d.get("parent_ref") in alias:
            d["parent_ref"] = alias[d["parent_ref"]]


def supersede_drafts(docs: List[dict]) -> List[dict]:
    """A draft agreement furnished together with the REGISTERED agreement between the same vendors and purchasers is
    only an earlier copy of it: the registered deed is the transaction. Without this the draft decides the whole
    report ('unexecuted draft', builder-sale layout) although the sale is already registered."""
    deeds = [d for d in docs if d["category"] == "title_transfer" and not d.get("draft")
             and (d.get("verified_in_igr") or (d.get("doc_number") and d.get("doc_year")))]
    alias, out = {}, []
    for d in docs:
        if d.get("draft"):
            givers, takers = _side(d, TRANSFEROR_ROLES), _side(d, TRANSFEREE_ROLES)
            reg = next((x for x in deeds if _draft_family(x["doc_type"]) == _draft_family(d["doc_type"])
                        and givers and takers and givers & _side(x, TRANSFEROR_ROLES)
                        and takers & _side(x, TRANSFEREE_ROLES)), None)
            if reg is not None:
                note = "A draft of this agreement was also furnished; the registered agreement prevails."
                da, ra = _amt(d.get("consideration")), _amt(reg.get("consideration"))
                if da and ra and da != ra:
                    note += f" The draft stated a consideration of Rs.{da}, the registered agreement Rs.{ra}."
                reg["key_terms"] = list(dict.fromkeys((reg.get("key_terms") or []) + [note]))
                reg["files"] = sorted(set(reg.get("files", []) + d.get("files", [])) - {""})
                alias[d["ref"]] = reg["ref"]
                continue
        out.append(d)
    _repoint(out, alias)
    return out


def link_registration_records(docs: List[dict]) -> List[dict]:
    """A Dast Goshwara / Index-II read on its own makes the reader invent a parent ('Registered Document (earlier
    deed)', no type, no parties, not furnished). Its deed is the furnished instrument just before it in the same file
    (e.g. the Special Power of Attorney on pages 46-48 and its Dast Goshwara on 52-54): the registration number, date
    and office move to that instrument and the invented parent is dropped. The instrument's own number is dropped
    when it is not the registration number (a page / bundle stamp such as '2099/2008')."""
    by_ref = {d["ref"]: d for d in docs}
    alias, drop = {}, set()
    for c in docs:
        p = by_ref.get(c.get("parent_ref") or "")
        if c["category"] != "registration_record" or not c.get("furnished") or p is None or p["furnished"] \
                or p.get("parties") or p.get("exec_date") or not p.get("doc_number") or p.get("source") == "igr":
            continue
        cs = _page_span(c)
        if not cs:
            continue
        files = set(c.get("files") or []) - {""}
        cands = [x for x in docs if x is not p and x is not c and x.get("furnished") and not x.get("draft")
                 and x["category"] not in ("receipt", "registration_record", "identity", "plan", "revenue_record")
                 and re.search(r"attorney|agreement|deed|declaration|undertaking|affidavit|conveyance|lease|gift|"
                               r"release|assignment|indenture|partition|will\b", x["doc_type"], re.I)
                 and not x.get("verified_in_igr") and files & set(x.get("files") or []) and _page_span(x)
                 and 0 <= cs[0] - _page_span(x)[1] <= 8
                 and (not x.get("doc_year") or not p.get("doc_year") or x["doc_year"] == p["doc_year"])]
        if not cands:
            continue
        x = max(cands, key=lambda y: _page_span(y)[1])            # the instrument nearest before the record
        for f in ("doc_number", "doc_year", "doc_no_raw", "reg_date", "sro"):
            if p.get(f) or f in ("doc_number", "doc_no_raw"):
                x[f] = p.get(f, "")
        x["execution_status"] = "registered"
        x["key_terms"] = list(dict.fromkeys((x.get("key_terms") or []) +
                                            [f"Registration No. {p['doc_number']}/{p.get('doc_year', '')} read from its "
                                             f"Dast Goshwara / Index-II furnished with it"]))
        alias[p["ref"]] = x["ref"]
        drop.add(id(p))
    out = [d for d in docs if id(d) not in drop]
    _repoint(out, alias)
    return out


_LOST = re.compile(r"\blost\b|misplac|हरव|गहाळ", re.I)


def lost_originals(docs: List[dict]) -> List[str]:
    """A police lost-article report / public notice / affidavit of loss naming a deed: the ORIGINAL of that deed is not
    available. Marks the deed (original_lost) and returns one observation per deed."""
    reports = [d for d in docs if d["category"] not in MAIN_CATEGORIES and
               _LOST.search(" ".join([d.get("doc_type") or "", d.get("short_description") or ""] + (d.get("key_terms") or [])))]
    flags = []
    for deed in docs:
        if deed["category"] not in MAIN_CATEGORIES or deed.get("draft"):
            continue
        num, yr = str(deed.get("doc_number") or ""), str(deed.get("doc_year") or "")
        fam = _draft_family(deed["doc_type"])
        hits = []
        for r in reports:
            txt = ascii_digits(" ".join([r.get("short_description") or ""] + (r.get("key_terms") or [])))
            by_no = len(num) >= 3 and yr and re.search(rf"{num}\W{{0,3}}{yr}(?!\d)", txt)
            by_date = deed.get("exec_date") and deed["exec_date"] in txt and fam and fam.split()[0] in txt.lower()
            if by_no or by_date:
                hits.append(r)
        if not hits:
            continue
        deed["original_lost"] = True
        ev = "; ".join(f"{r['doc_type']}{' dated ' + (r.get('exec_date') or r.get('reg_date')) if r.get('exec_date') or r.get('reg_date') else ''}"
                       f"{' (No. ' + r['reference_no'] + ')' if r.get('reference_no') else ''}" for r in hits)
        dt = deed.get("exec_date") or deed.get("reg_date")
        no = f"{num}/{yr}" if num and yr else ""
        flags.append(f"The ORIGINAL {deed['doc_type']}{' dated ' + dt if dt else ''}{' (Doc No. ' + no + ')' if no else ''} "
                     f"is reported LOST / misplaced as per the {ev} furnished. The original is not available for deposit; "
                     f"certified copy of the said document from the Sub-Registrar office, the police lost report, the "
                     f"public notice with proof of publication and confirmation that no claims / objections were received "
                     f"within the notice period, and an indemnity bond from the owner are required to be obtained before "
                     f"disbursement.")
    return flags


# ── ordering, owner, flags ─────────────────────────────────────────────────────
def _sort_date(d: dict) -> date:
    yr = str(d.get("doc_year") or "")
    # no date but a registration year (e.g. 494/2020): the end of that year, not after every dated deed
    return parse_date(d.get("exec_date")) or parse_date(d.get("reg_date")) or \
        (date(int(yr), 12, 31) if re.fullmatch(r"(?:19|20)\d\d", yr) else date(9999, 1, 1))


def order_documents(docs: List[dict]) -> List[dict]:
    refs = {d["ref"] for d in docs}
    main = [d for d in docs if not d.get("parent_ref") or d["parent_ref"] not in refs]
    kids: Dict[str, List[dict]] = {}
    for d in docs:
        if d.get("parent_ref") in refs:
            kids.setdefault(d["parent_ref"], []).append(d)
    ordered, seen = [], set()

    def place(d):
        if id(d) in seen:
            return
        seen.add(id(d))
        ordered.append(d)
        for k in sorted(kids.get(d["ref"], []), key=_sort_date):    # a child's own children (any depth) follow it
            place(k)
    for d in sorted(main, key=lambda x: (_sort_date(x), x["category"] != "title_transfer")):
        place(d)
    # parent links that loop (A → B → A) or end nowhere must never drop a document from the reports
    for d in sorted((x for x in docs if id(x) not in seen), key=lambda x: (_sort_date(x), x["category"] != "title_transfer")):
        d["parent_ref"] = "" if d.get("parent_ref") and id(d) not in seen else d.get("parent_ref", "")
        place(d)
    for i, d in enumerate(ordered, 1):
        d["sr"] = i
    return ordered


def original_or_copy(d: dict, by_ref: Dict[str, dict]) -> str:
    if not d.get("furnished"):
        return "Not furnished"
    if d.get("original_lost"):
        return "Xerox copy (original lost)"
    if d["category"] in ("title_transfer", "encumbrance") and d["level"] == "premises":
        return "Original"
    parent = by_ref.get(d.get("parent_ref") or "")
    if d["category"] in ("receipt", "registration_record") and parent and parent["category"] == "title_transfer" \
            and parent["level"] == "premises":
        return "Original"
    return "Xerox copy"


def doc_no_display(d: dict) -> str:
    raw = (d.get("doc_no_raw") or "").strip()
    if d.get("doc_number") and d.get("doc_year"):
        if raw and not re.search(r"[ऀ-ॿ]", raw):
            return raw
        return f"{d['doc_number']}/{d['doc_year']}"
    return raw


def _roles(d: dict, roles) -> List[str]:
    return [p["name"] for p in d.get("parties", []) if any(r in (p.get("role") or "").lower() for r in roles)]


def display_name(n: str) -> str:
    """'M/s. JAS Financial Services Pvt. Ltd. through its Director Mr. X' -> 'M/s. JAS Financial Services Pvt. Ltd.'"""
    return re.split(r"\s+(?:through|thru|tarfe|तर्फे)\b", n, 1, flags=re.I)[0].strip(" ,")


def _names_unit(d: dict, unit: str) -> bool:
    """The deed describes the subject unit itself ('Flat No. 04, 1st Floor, ...')."""
    num = re.sub(r"\D", "", unit or "").lstrip("0")
    return bool(num) and bool(re.search(
        r"(?:flat|unit|shop|gala|office|apartment|premises|सदनिका|गाळा|दुकान)\s*(?:no\.?|number|नं\.?|क्र\.?)?\s*[:\-]?\s*"
        r"[A-Za-z]?\s*-?\s*0*" + num + r"(?!\d)", ascii_digits(d.get("property_text") or ""), re.I))


def current_owner(docs: List[dict], subject: Optional[dict] = None) -> dict:
    # a deed only MENTIONED (no copy, no registration number, no date — e.g. 'the Agreement for Sale to be executed'
    # in an allotment letter) is no evidence that the transfer took place: it cannot make anyone the owner
    mentioned_only = lambda d: (not d.get("furnished", True) and not (d.get("doc_number") or d.get("doc_no_raw"))
                                and not (d.get("exec_date") or d.get("reg_date")))
    # a leave & licence gives occupation for a term, never title: the licensee is not the owner
    transfers = [d for d in docs if d["category"] == "title_transfer" and _roles(d, TRANSFEREE_ROLES)
                 and not is_mortgage_type(d.get("doc_type", "")) and not mentioned_only(d)
                 and not re.search(r"leave\s*(?:and|&)\s*licen|लिव्ह", d.get("doc_type", ""), re.I)]
    unit = (subject or {}).get("unit_no", "")
    # a deed of the unit tagged 'land' / 'project' by the reader is still a deed of the unit when it names the unit
    prem = [d for d in transfers if d["level"] == "premises" or _names_unit(d, unit)] or transfers
    if not prem:
        # old society flats: the share certificate is often the only proof of the member's title
        share = [d for d in docs if re.search(r"share\s*cert", d.get("doc_type", ""), re.I)]
        if share:
            last = max(share, key=_sort_date)
            holders = [p["name"] for p in last.get("parties", [])
                       if re.search(r"member|holder|owner|transferee|allottee", p.get("role") or "", re.I)]
            if holders:
                return {"names": [display_name(n) for n in holders], "names_full": holders, "via_ref": last["ref"],
                        "from": [], "share_certificate_only": True, "deal": "registered"}
        # a new flat with only the promoter's / authority's Allotment Letter (CIDCO, MHADA lottery flats): the allottees
        # are the proposed purchasers, the issuer is the promoter, the Agreement for Sale is still to be executed
        allot = [d for d in docs if d.get("furnished") and re.search(r"allot", d.get("doc_type", ""), re.I)
                 and not re.search(r"cancel|surrender", d.get("doc_type", ""), re.I)]
        if allot:
            last = max(allot, key=_sort_date)
            ps = [p for p in last.get("parties") or [] if p.get("name")]
            rep = lambda p: re.search(r"represent|signator|officer|through|attorney", p.get("role") or "", re.I)
            takers = [p["name"] for p in ps if not rep(p) and
                      re.search(r"allottee|addressee|applicant|purchaser|holder|beneficiar", p.get("role") or "", re.I)]
            givers = [p["name"] for p in ps if not rep(p) and
                      re.search(r"issu|authorit|promoter|developer|builder|allott?or|owner", p.get("role") or "", re.I)]
            if takers:
                return {"names": [display_name(n) for n in takers], "names_full": takers, "via_ref": last["ref"],
                        "from": [display_name(n) for n in givers], "allotment_only": True, "deal": "new_sale"}
        return {"names": [], "names_full": [], "via_ref": "", "from": [], "deal": ""}

    def held_by(last):
        full = _roles(last, TRANSFEREE_ROLES)
        return {"names": [display_name(n) for n in full], "names_full": full, "via_ref": last["ref"],
                "from": [n for n in _roles(last, TRANSFEROR_ROLES)
                         if not any("confirm" in (p.get("role") or "").lower() and p["name"] == n for p in last["parties"])]}

    # The type of deal decides the whole report. A draft whose VENDOR already holds the unit under a registered deed is a
    # RESALE: the vendor is the owner and the draft's purchasers are the proposed purchasers. A draft from the builder
    # with no earlier deed of the unit is a NEW SALE: the purchasers are the proposed purchasers of the builder's flat.
    drafts = [d for d in prem if d.get("draft")]
    registered = [d for d in prem if not d.get("draft")]
    draft = max(drafts, key=lambda x: (len(_side(x, TRANSFEREE_ROLES)), len(x.get("parties") or []))) if drafts else None
    if draft and registered:
        last = max(registered, key=_sort_date)
        if _side(last, TRANSFEREE_ROLES) & _side(draft, TRANSFEROR_ROLES):
            out = held_by(last)
            out.update(deal="resale", proposed_via_ref=draft["ref"],
                       proposed_purchasers=[display_name(n) for n in _roles(draft, TRANSFEREE_ROLES)])
            return out
    last = draft or max(prem, key=_sort_date)
    return {**held_by(last), "deal": "new_sale" if last.get("draft") else "registered"}


_CHARGE_RELEASE = re.compile(r"reconvey|re-?conveyance|satisfaction|discharge|release\s+of\s+(?:mortgage|charge|security)|"
                             r"रिकन्व्हेन्स|रिकन्हेन्स|रिकन्वेन्स|परिमोचन|पुनर्हस्तांतरण", re.I)
# 'दस्त क्रमांक 9984/ 2017', 'टनन10/8711/2016', 'दस्त क्रमांक 788- 2022' — never the tail of a date '27/11/2017'
_CITED = re.compile(r"(?<!\d)(?<!\d\.)(\d{1,6})\s*[/\-]\s*((?:19|20)\d{2})(?!\d)")


def is_charge_release(text: str) -> bool:
    """Reconveyance / release of a mortgage — not a release (relinquishment) deed between co-owners."""
    return bool(_CHARGE_RELEASE.search(text or ""))


_DEED_CTX = re.compile(r"दस्त|दसत|\bdoc|deed|टनन|करल|[A-Z]{2,5}\s*-?\s*\d{1,2}\s*[/\-]\s*$", re.I)
_NOT_DEED_CTX = re.compile(r"अभि\s*नि|अभिर्नि|अभीनि|प्र\.?\s*क्र|एडिजे|आईजीआर|\badj|\bigr\b", re.I)


def cited_docs(text: str) -> set:
    """Registered deeds a row names: 'दस्त क्रमांक 9984/ 2017', 'टनन10/8711/2016'. Not the adjudication case number
    ('अभिनिर्णय प्र.क्र. 475/2023', 'एडिजे/आईजीआर 108/196/2024') nor a date."""
    t = ascii_digits(text or "")
    out = set()
    for m in _CITED.finditer(t):
        if int(m.group(1)) <= 12 and re.search(r"\d{1,2}\s*[/\-.]\s*$", t[max(0, m.start() - 4):m.start()]):
            continue                      # '27/11/2017' — the month and year of a date
        before = t[max(0, m.start() - 30):m.start()]
        near = t[max(0, m.start() - 14):m.start()]
        if _NOT_DEED_CTX.search(near) or not _DEED_CTX.search(before):
            continue
        out.add(docno_key(m.group(1), m.group(2)))
    return out - {None}


def _side_names(raw: str) -> set:
    return {skeleton(_HONORIFIC.sub("", n))[:6] for n in _split_names(raw)} - {""}


def link_releases(entries: List[dict]) -> Dict[tuple, dict]:
    """Mortgage → the reconveyance that closes it. A reconveyance names the deed it releases ('दस्त क्रमांक 9984/2017
    … रिकन्व्हेन्स'); when it names none, it releases a mortgage of the same parties (lender and borrower) whose whole
    land it reconveys. A partial reconveyance of other land (e.g. one building's plot) releases nothing of ours."""
    from .igr import land_refs
    morts = [e for e in entries if is_mortgage_type((e.get("doc_type_en") or "") + " " + (e.get("docname_raw") or ""))
             and not is_charge_release((e.get("doc_type_en") or "") + " " + (e.get("docname_raw") or ""))]
    rels = sorted((e for e in entries if is_charge_release((e.get("doc_type_en") or "") + " " + (e.get("docname_raw") or ""))),
                  key=lambda x: _sort_date({"reg_date": x.get("reg_date", "")}))
    out: Dict[tuple, dict] = {}
    for m in morts:
        mk = docno_key(m["docno"], m["year"])
        m_date = _sort_date({"reg_date": m.get("reg_date", "")})
        for r in rels:
            if _sort_date({"reg_date": r.get("reg_date", "")}) < m_date or docno_key(r["docno"], r["year"]) == mk:
                continue
            cites = cited_docs(r.get("property_raw", ""))
            if cites:
                if mk in cites:
                    out[mk] = r
                    break
                continue                      # it names the deeds it releases — and this mortgage is not one of them
            m_sides = [_side_names(m.get("seller_raw", "")), _side_names(m.get("purchaser_raw", ""))]
            r_names = _side_names(r.get("seller_raw", "")) | _side_names(r.get("purchaser_raw", ""))
            m_land = {(k, a, s) for k, a, s in land_refs(m.get("property_raw", "")) if k != "old"}
            r_land = {(k, a, s) for k, a, s in land_refs(r.get("property_raw", "")) if k != "old"}
            if all(side & r_names for side in m_sides) and m_land and len(m_land & r_land) >= 0.9 * len(m_land):
                out[mk] = r
                break
    return out


_OFFICE_CODES = {"mbi": "mumbai", "bom": "mumbai", "tnn": "thane", "krl": "kurla", "vsi": "vasai", "bdr": "bandra",
                 "adh": "andheri", "pvl": "panvel", "blp": "belapur", "knn": "kalyan", "ulh": "ulhasnagar",
                 "bwd": "bhiwandi", "hvl": "haveli", "brl": "borivali", "plg": "palghar"}


def _office(text: str):
    """('mumbai', '28') from 'MBI-28-17601-2025', 'Joint Sub-Registrar Mumbai-28', 'Sub-Registrar Kurla-2', 'TNN7'.
    The Kurla offices 1-5 were renamed Mumbai 27-31 (a deed cited 'KRL-3/1600/2019' is indexed at Mumbai-29,
    'KRL-2-24310-2023' at Mumbai-28): both names give the same office."""
    t = (text or "").lower()
    m = re.match(r"^\s*p?([a-z]{3})\s*[-/]?\s*(\d{1,2})\s*[-/]", t)
    if m and m.group(1) in _OFFICE_CODES:
        place, num = _OFFICE_CODES[m.group(1)], m.group(2)
    else:
        m = re.search(r"(mumbai|thane|kurla|vasai|bandra|andheri|panvel|belapur|kalyan|ulhasnagar|bhiwandi|haveli|borivali|"
                      r"palghar|virar|mira|bhayander|navi mumbai)\D{0,6}(\d{1,2})?\b", t)
        place, num = (m.group(1), m.group(2) or "") if m else ("", "")
    if place == "kurla" and num.isdigit() and 1 <= int(num) <= 5:
        place, num = "mumbai", str(26 + int(num))
    return place, num


def _days(a: str, b: str):
    """Days between two dd/mm/yyyy dates (None when either cannot be read)."""
    from datetime import date
    out = []
    for v in (a, b):
        m = re.match(r"\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", v or "")
        if not m:
            return None
        try:
            out.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            return None
    return abs((out[0] - out[1]).days)


def link_paper_numbers(docs: List[dict], rows: List[dict], classes: Dict[str, dict]) -> Dict[str, dict]:
    """A registered deed the PAPERS cite by number (e.g. MHADA's sale and lease 'MBI-28-17601-2025' recited in the
    agreement) is linked to the IGR row of that number, year and office even when the row was not short-listed or
    the matcher rejected it — the citation itself identifies it. Same number + year at ANOTHER office is never linked;
    without an office in the papers, the date must agree as well."""
    by_no = {}
    for r in rows:
        by_no.setdefault(docno_key(r["docno"], r["year"]), []).append(r)
    for d in docs:
        if not (d.get("doc_number") or d.get("doc_no_raw")) or d.get("draft"):
            continue
        cands = by_no.get(_key(d)) or []
        place, num = _office(d.get("doc_no_raw") or "")
        if not place:
            place, num = _office(d.get("sro") or "")
        dates = {x for x in (d.get("exec_date"), d.get("reg_date")) if x}
        for r in cands:
            rplace, rnum = _office(r.get("sro") or "")
            # same number + year and a date within a week: the same deed even when the papers name the office
            # differently ('Sub-Registrar Kurla' in a recital, 'Joint Sub-Registrar Mumbai-28' in the index)
            near = any(x is not None and x <= 7 for a in dates for b in (r.get("exec_date"), r.get("reg_date"))
                       for x in [_days(a, b)])
            if place and rplace:
                if (place != rplace or (num and rnum and num != rnum)) and not near:
                    continue
            elif not near:
                continue
            c = classes.get(r["id"]) or {}
            if c.get("class") in ("A", "B"):
                break
            unit_deed = d.get("level") == "premises" and d.get("category") in ("title_transfer", "encumbrance")
            classes[r["id"]] = {**c, "id": r["id"], "class": "A" if unit_deed else "B",
                                "doc_type_en": c.get("doc_type_en") or d.get("doc_type", ""),
                                "reason": (c.get("reason", "") + f" | code: cited by number in the documents furnished "
                                           f"({d.get('doc_type', '')} {d.get('doc_no_raw') or r['docno']})").strip(" |")}
            break
    return classes


def link_cited_rows(rows: List[dict], classes: Dict[str, dict]) -> Dict[str, dict]:
    """A supplemental mortgage / further charge / reconveyance that names a matched mortgage ('ठाणे 7 येथिल दस्त क्रमांक
    8088/2024 व 20573/2024 या गहाणखताचे सप्लीमेंट्री डीड ऑफ मॉरगेज') is a deed of that same charge — even when its own
    property clause only says 'सर्वे नं.118/7 पै व इतर'. It takes the class of the mortgage it names."""
    from .igr import assign_mortgage_roles
    typ = lambda r: ((classes.get(r["id"]) or {}).get("doc_type_en") or "") + " " + (r.get("docname_raw") or "")
    for _ in range(3):                                   # a supplement of a supplement
        matched = {docno_key(r["docno"], r["year"]): classes[r["id"]]["class"] for r in rows
                   if (classes.get(r["id"]) or {}).get("class") in ("A", "B") and is_mortgage_type(typ(r))}
        added = False
        for r in rows:
            c = classes.get(r["id"]) or {}
            if c.get("class") in ("A", "B") or not (is_mortgage_type(typ(r)) or is_charge_release(typ(r))):
                continue
            hits = sorted(cited_docs(r.get("property_raw", "")) & set(matched))
            if not hits:
                continue
            new = {**c, "id": r["id"], "class": matched[hits[0]],
                   "reason": (c.get("reason", "") + f" | code: names the matched charge Doc No. "
                              f"{', '.join(f'{n}/{y}' for n, y in hits)}").strip(" |"),
                   "parties": c.get("parties") or default_parties(r, r.get("docname_raw") or "")}
            assign_mortgage_roles(r, new)
            classes[r["id"]] = new
            added = True
        if not added:
            break
    return classes


def encumbrances(docs: List[dict], entries: List[dict]) -> List[dict]:
    out, seen = [], set()
    links = link_releases(entries)
    for e in entries:
        if docno_key(e["docno"], e["year"]) in seen:
            continue                      # one deed exported as several IGR rows (unit list + property clause)
        typ = (e.get("doc_type_en") or "") + " " + (e.get("docname_raw") or "")
        if e["class"] in ("A", "B", "D") and is_mortgage_type(typ) and not is_charge_release(typ):
            seen.add(docno_key(e["docno"], e["year"]))
            level = "project" if e["class"] == "B" else "unit"
            rel = links.get(docno_key(e["docno"], e["year"]))
            supplements = sorted(cited_docs(e.get("property_raw", "")) - {docno_key(e["docno"], e["year"])}) \
                if re.search(r"सप्लीमेंट्री|सप्लिमेंटरी|पुरवणी|supplement|फ[र]?दर|further", typ + " " + e.get("property_raw", ""), re.I) else []
            out.append({"igr_ref": e["id"], "class": e["class"], "level": level, "doc_type": e.get("doc_type_en"),
                        "doc_no": f"{e['docno']}/{e['year']}", "date": e["exec_date"] or e["reg_date"], "sro": e["sro"],
                        "parties": e.get("parties_en"), "property": e.get("property_en"),
                        "supplemental_to": [f"{n}/{y}" for n, y in supplements],
                        "released_by": f"{rel['docno']}/{rel['year']}" if rel else "",
                        "released_on": (rel.get("reg_date") or rel.get("exec_date") or "") if rel else "",
                        "status": (f"Released by Reconveyance Doc No. {rel['docno']}/{rel['year']} ({rel['sro']}) registered "
                                   f"{rel.get('reg_date') or rel.get('exec_date')}") if rel else "No release found in search"})
    return out


def _receipt_other_property(r: dict, subject: dict) -> bool:
    """An e-Search receipt whose village AND survey both differ from the subject's was paid for another property."""
    village = skeleton(r.get("village") or "")
    if len(village) < 3:
        return False
    text = " ".join(str(subject.get(k) or "") for k in ("village", "full_description", "road_locality", "taluka"))
    if village in skeleton(text):
        return False
    from .igr import land_refs, subject_land_refs
    svy = {m for k, m, s in land_refs("S.No. " + ascii_digits(str(r.get("survey_searched") or "")))}
    return not (svy & {m for k, m, s in subject_land_refs(subject)})


_OWN_FIRM = re.compile(r"proficient\s+law\s+junction", re.I)
_REPORT = re.compile(r"search\s*report|title\s*(?:certificate|report|opinion|search)|legal\s*title|professional\s*bill", re.I)


def reclass_reports(docs: List[dict]) -> List[dict]:
    """Reports are not title deeds: a search / title report is a title certificate, and a report or bill of this firm
    (an earlier report for the same flat left in the bank's bundle) is not part of the documents perused."""
    for d in docs:
        if not _REPORT.search(d.get("doc_type") or ""):
            continue
        who = " ".join([p.get("name") or "" for p in d.get("parties") or []] + [d.get("short_description") or ""])
        if _OWN_FIRM.search(who):
            d["category"] = "other"
        elif d.get("category") in MAIN_CATEGORIES:
            d["category"] = "title_certificate"
    return docs


def build_analysis(lead: dict, opts: dict, extractions: List[dict], subject: dict, igr_rows: List[dict],
                   classes: Dict[str, dict], receipts: List[dict], igr_files: List[str]) -> dict:
    docs = reclass_reports(dedupe_documents([d for ex in extractions for d in ex.get("documents", [])]))
    link_paper_numbers(docs, igr_rows, classes)         # deeds the papers cite by number (updates classes in place)
    link_cited_rows(igr_rows, classes)                  # updates classes in place
    igr = attach_igr(docs, igr_rows, classes)
    docs = order_documents(supersede_drafts(link_registration_records(absorb_misread(docs))))
    lost_flags = lost_originals(docs)
    encs = encumbrances(docs, igr["entries"])
    enc_by_key = {docno_key(*e["doc_no"].split("/")): e for e in encs}
    by_ref = {d["ref"]: d for d in docs}
    for d in docs:
        d["original_or_copy"] = original_or_copy(d, by_ref)
        d["doc_no_display"] = doc_no_display(d)
        d["sro_display"] = d.get("sro_igr") or d.get("sro") or ""
        d["in_search"] = bool(d.get("verified_in_igr"))
        if d["category"] == "encumbrance" and is_mortgage_type(d["doc_type"]) and not is_charge_release(d["doc_type"]):
            en = enc_by_key.get(_key(d))
            d["charge_status"] = en["status"] if en else (
                "No release found in search" if d["in_search"] else "Not found in the search data supplied")
            d["released_by"] = (en or {}).get("released_by", "")

    owner = current_owner(docs, subject)
    # a land deed named in the papers whose Index-II describes OTHER survey / hissa numbers: say so, never present it
    # as a deed of the subject land (e.g. a family release of S.No. 47/5, 48/5, 51/19 recited in the title of 47/9)
    from .igr import land_match, land_refs, subject_land_refs
    subj_refs = subject.get("_land_refs") or subject_land_refs(subject)
    rows_by_id = {r["id"]: r for r in igr_rows}
    for d in docs:
        r = rows_by_id.get(d.get("igr_ref"))
        if not r or d["level"] not in ("land", "project") or not subj_refs:
            continue
        refs = [x for x in land_refs(r.get("property_raw", "")) if x[0] != "old"]
        lm = land_match(refs, subj_refs)
        if refs and not (lm["strict"] or lm["partial"]):
            d.setdefault("igr_mismatch", []).append(
                "VERIFY: its Index-II describes S.No. " + ", ".join(f"{m}/{s}" if s else m for k, m, s in refs[:6]) +
                " — not the subject land; its link to the title of the subject land to be verified.")

    # search period & coverage
    from .igr import coverage_years
    covered = coverage_years(igr_rows, igr_files)
    # the e-Search receipt states the period actually searched (e.g. 2002 to 2026); IGR gives no export file for a
    # year without entries, so every year of that period counts as searched
    # a receipt for another village / survey (a stale receipt left in the bundle) does not tell the period searched
    other_receipts = [r for r in receipts if _receipt_other_property(r, subject)]
    paid = [(r["period_from"], r["period_to"]) for r in receipts
            if r.get("period_from") and r.get("period_to") and r not in other_receipts]
    # …but a year with no rows at all is NOT "searched, nil" when a deed of this chain was registered in it, or when
    # every other year of the data holds many entries — its export was simply not supplied
    row_years = Counter(r["year"] for r in igr_rows if r.get("year"))
    dense = bool(row_years) and sum(row_years.values()) / len(row_years) >= 20
    deed_years = {int(m.group(0)) for d in docs if d.get("doc_no_display") or d.get("doc_number")
                  for m in [re.search(r"(?:19|20)\d{2}", d.get("reg_date") or d.get("doc_year") or "")] if m}
    for a, b in paid:
        covered = sorted(set(covered) | {y for y in range(a, b + 1)
                                         if y in row_years or not (dense or y in deed_years)})
    to_y = int(opts.get("search_to_year") or (max(b for a, b in paid) if paid else datetime.now().year))
    fallback = min(covered) if (other_receipts and covered) else to_y - config.DEFAULT_SEARCH_YEARS
    from_y = int(opts.get("search_from_year") or (min(a for a, b in paid) if paid else fallback))
    missing = [y for y in range(from_y, to_y + 1) if y not in covered]
    # offices searched: most frequent place in the IGR data and its office numbers (e.g. Vasai 1 to 6)
    offs = [re.search(r"([A-Za-z][A-Za-z ]*?)[\s\-]*(\d+)?$", re.sub(r"^(Joint\s+)?Sub-Registrar\s*", "", r.get("sro") or ""))
            for r in igr_rows]
    places = Counter(m.group(1).strip() for m in offs if m and m.group(1).strip())
    total = sum(places.values()) or 1
    parts = []
    first_seen = {}
    for r, m in sorted(zip(igr_rows, offs), key=lambda x: _sort_date({"reg_date": x[0].get("reg_date", "")})):
        if m and m.group(1).strip():
            first_seen.setdefault(m.group(1).strip(), len(first_seen))
    # offices where a deed of the chain is registered are always named (e.g. Thane 2: the 2008-2009 land deeds)
    chain_ids = {e["id"] for e in igr["entries"] if e["class"] in ("A", "B")}
    chain_offs = {(m.group(1).strip(), int(m.group(2))) for r, m in zip(igr_rows, offs)
                  if r["id"] in chain_ids and m and m.group(2)}
    for place, cnt in sorted(places.items(), key=lambda x: first_seen.get(x[0], 99)):      # oldest office first
        if cnt < 0.15 * total and not any(p == place for p, n in chain_offs):
            continue
        nums = Counter(int(m.group(2)) for m in offs if m and m.group(2) and m.group(1).strip() == place)
        keep = sorted(n for n, c in nums.items() if c >= max(3, 0.03 * cnt) or (place, n) in chain_offs)
        parts.append(f"{place} No.{keep[0]} to {keep[-1]}" if len(keep) > 1 else (f"{place} No.{keep[0]}" if keep else place))
    sro_place = " and ".join(parts)
    sro_nums = []
    rec = {}
    for r in receipts:
        k = r.get("grn") or r.get("receipt_no") or r.get("file")
        rec.setdefault(k, {}).update({x: v for x, v in r.items() if v})

    # flags (deterministic — fed to the drafting step and shown in reports)
    flags = []
    if missing:
        flags.append(f"IGR search data not available for the year(s): {_ranges(missing)} within the search period "
                     f"{from_y}-{to_y}; search for these years is required.")
    flags += lost_flags
    for f in opts.get("foreign_files") or []:
        flags.append(f"VERIFY: the document(s) in {f['file'] or 'one file furnished'} relate to another property "
                     f"({f['description'] or 'description not stated'}) and have not been considered in this report.")
    for r in other_receipts:
        flags.append(f"VERIFY: the e-Search receipt {r.get('receipt_no') or r.get('grn') or ''} furnished is for another "
                     f"property (Village {r.get('village')}, S.No. {r.get('survey_searched') or '-'}"
                     f"{', period ' + str(r['period_from']) + ' to ' + str(r['period_to']) if r.get('period_from') else ''}"
                     f"); the search receipt for the subject property to be obtained.")
    igr_only = []
    drafts_flagged = set()
    if owner.get("share_certificate_only"):
        flags.append("VERIFY: no registered agreement / conveyance in favour of the present owner was furnished — title "
                     "rests on the Society Share Certificate only. Previous chain agreements, Society NOC and transfer "
                     "records (share register, Society minutes) to be obtained and verified.")
    for d in docs:
        if d.get("draft"):
            if _draft_family(d["doc_type"]) in drafts_flagged:
                continue                  # the same draft read in several page parts — one observation
            drafts_flagged.add(_draft_family(d["doc_type"]))
            d = max((x for x in docs if x.get("draft") and _draft_family(x["doc_type"]) == _draft_family(d["doc_type"])),
                    key=lambda x: len(x.get("parties") or []))
            names = lambda roles: " & ".join(_roles(d, roles)) or "the parties"
            flags.append(f"The {d['doc_type']} between {names(TRANSFEROR_ROLES)} and {names(TRANSFEREE_ROLES)} furnished is an "
                         f"UNEXECUTED DRAFT (date blank, not stamped / registered). It must be executed, duly stamped and "
                         f"registered before disbursement; the registered copy, Index-II and registration receipt to be obtained.")
            continue
        if d.get("recited_only") and d["category"] in MAIN_CATEGORIES + ("title_certificate", "revenue_record", "approval"):
            if any(x is not d and _recital_family(x["doc_type"]) == _recital_family(d["doc_type"]) and
                   (x["furnished"] or x.get("exec_date") or x.get("reference_no") or x.get("doc_number")) for x in docs):
                continue                  # e.g. "the Occupancy Certificate" recited — and the OC itself is listed
            flags.append(f"{d['doc_type']} referred to in the documents furnished (date / registration details not stated) "
                         f"is not furnished; copy to be obtained and verified.")
            continue
        if d["category"] == "encumbrance" and d.get("charge_status"):
            for m in d.get("igr_mismatch", []):
                flags.append(f"Discrepancy in {d['doc_type']} {d['doc_no_display']}: {m}")
            continue                      # charges are reported once each, below (subsisting / released / not searched)
        if not d["furnished"] and d["category"] in MAIN_CATEGORIES and d.get("source") == "igr":
            if d["category"] != "encumbrance":
                igr_only.append(f"{d['doc_type']} Doc No. {d['doc_no_display']} dated {d['exec_date'] or d['reg_date']}")
            continue
        if not d["furnished"] and d["category"] == "encumbrance" and any(
                x["furnished"] and is_release_type(x["doc_type"]) and _people(x) & _people(d) for x in docs):
            continue                      # a mortgage recited in the lender's release / no-dues letter — it is closed
        if not d["furnished"] and d["category"] in MAIN_CATEGORIES:
            dt = d["exec_date"] or d["reg_date"]
            yr = (parse_date(dt) or date(9999, 1, 1)).year if dt else int(d["doc_year"]) if str(d.get("doc_year") or "").isdigit() else 9999
            how = "verified in IGR search" if d.get("verified_in_igr") else \
                "registered before the search period" if yr < from_y else "not found in IGR data supplied"
            flags.append(f"{d['doc_type']}{' dated ' + dt if dt else ''}"
                         f"{' (Doc No. ' + d['doc_no_display'] + ')' if d['doc_no_display'] else ''} "
                         f"is not furnished ({how}); certified copy required.")
        for m in d.get("igr_mismatch", []):
            flags.append(f"Discrepancy in {d['doc_type']} {d['doc_no_display']}: {m}")
    # safety net: say clearly when the code could not identify something, instead of reporting it as clear
    ab = [e for e in igr["entries"] if e["class"] in ("A", "B")]
    tdr = [e for e in igr["project_entries"] if (classes.get(e["id"]) or {}).get("tdr")]
    if tdr:
        flags.append(f"{len(tdr)} IGR entries record sale / transfer of TDR (DRC) rights generated from the same survey "
                     f"numbers (e.g. Doc No. {', '.join(sorted({e['docno'] + '/' + str(e['year']) for e in tdr})[:4])}); "
                     f"these are not transfers of the subject land and are not part of the chain.")
    if len(igr["project_entries"]) >= 20 and not ab:
        flags.append(f"VERIFY: {len(igr['project_entries'])} IGR entries relate to the subject project / land, but none could be "
                     f"identified as the subject unit or its land title deeds — the search data must be checked manually.")
    # CIDCO / MIDC / MHADA leasehold land is identified by its plot (and sector), not by a survey / CTS number
    if not (subject.get("_land_refs") or subject.get("survey_no") or subject.get("cts_no") or subject.get("plot_no")):
        flags.append("VERIFY: the survey / CTS / plot number of the property could not be read from the documents — land "
                     "search and land chain to be checked manually.")
    if not re.sub(r"\D", "", subject.get("unit_no", "") or "") and (subject.get("unit_type") or "").lower() not in ("plot", "land", "bungalow"):
        flags.append("VERIFY: the unit / flat number could not be read from the documents — IGR matching of the unit to be "
                     "checked manually.")
    if any(e["class"] == "D" for e in igr["entries"]):
        flags.append("VERIFY: some IGR entries only partly match the property (class D) — see the possible matches listed.")
    if igr_only:
        flags.append("The following registered documents were found in the IGR search but are not furnished; certified "
                     "copies to be obtained and verified: " + "; ".join(igr_only) + ".")
    # a registered deed of the unit furnished to us but absent from IGR data of a year that WAS searched: the search
    # (or the deed's registration details) needs checking — never presented as "verified"
    searched = set(covered) - set(missing)
    for d in docs:
        y = year_of(d.get("reg_date") or "") or (int(d["doc_year"]) if str(d.get("doc_year") or "").isdigit() else None)
        if (d.get("furnished", True) and not d["in_search"] and d.get("doc_number") and d["level"] == "premises"
                and d["category"] in ("title_transfer", "encumbrance") and not d.get("draft") and y in searched):
            flags.append(f"VERIFY: the registered {d['doc_type']} Doc No. {d['doc_no_display']} ({y}) furnished is not found in "
                         f"the IGR search data supplied for {y}; its Index-II / registration to be verified with the "
                         f"Sub-Registrar and the search to be re-checked.")
    # a share certificate cannot precede the deed by which its holder acquired the unit
    for sc in [d for d in docs if re.search(r"share\s*cert", d["doc_type"], re.I) and parse_date(d.get("exec_date"))]:
        holders = {_name_key(p["name"]) for p in sc.get("parties") or [] if not _has_role(p, ("society", "issuing"))}
        for d in docs:
            got = parse_date(d.get("exec_date")) or parse_date(d.get("reg_date"))
            buyers = {_name_key(p["name"]) for p in d.get("parties") or []
                      if _has_role(p, ("purchaser", "allottee", "transferee", "donee"))}
            if (d["category"] == "title_transfer" and d["level"] == "premises" and got and holders & buyers
                    and got > parse_date(sc["exec_date"])):
                flags.append(f"VERIFY: the Share Certificate dated {sc['exec_date']} in the name of the holder is dated "
                             f"before the {d['doc_type']} dated {d.get('exec_date') or d.get('reg_date')} by which the holder "
                             f"acquired the said premises; the dates to be verified with the Society.")
                break
    if not any(re.search(r"occupa|completion", d["doc_type"], re.I) for d in docs):
        flags.append("Occupancy / Completion Certificate not furnished.")
    for en in encs:
        lender = next((p.get("name") for p in en.get("parties") or [] if "mortgagee" in (p.get("role") or "").lower()), "") \
            or "the project lender"
        sup = f" (supplemental to Doc No. {', '.join(en['supplemental_to'])})" if en.get("supplemental_to") else ""
        if en["level"] == "project" and en["released_by"]:
            flags.append(f"Released project / land mortgage: {en['doc_type'] or 'Mortgage Deed'} Doc No. {en['doc_no']} "
                         f"({en['sro']}) dated {en['date']} in favour of {lender}{sup} — {en['status']}. Certified copy of the "
                         f"reconveyance to be verified.")
        elif en["level"] == "project":
            flags.append(f"Project / land mortgage {en['doc_type'] or 'Mortgage Deed'} Doc No. {en['doc_no']} ({en['sro']}) "
                         f"dated {en['date']} in favour of {lender}{sup} is subsisting — NOC / release letter from {lender} "
                         f"for the subject unit to be obtained before disbursement.")
    for d in docs:
        if d.get("charge_status") == "Not found in the search data supplied":
            lender = next((p["name"] for p in d.get("parties") or [] if "mortgagee" in (p.get("role") or "").lower()), "the lender")
            flags.append(f"{'Project / land mortgage' if d['level'] in ('project', 'land') else 'Mortgage'} {d['doc_type']}"
                         f"{' dated ' + (d['exec_date'] or d['reg_date']) if d['exec_date'] or d['reg_date'] else ''}"
                         f"{' (Doc No. ' + d['doc_no_display'] + (', ' + d['sro_display'] if d['sro_display'] else '') + ')' if d['doc_no_display'] else ''}"
                         f" in favour of {lender} is disclosed in the documents but is not in the IGR search data supplied "
                         f"(registered in an office not searched) — its subsistence cannot be verified from the search; "
                         f"NOC / release letter from {lender} to be obtained before disbursement.")
    for e in igr["entries"]:
        if e["class"] == "D":
            flags.append(f"Possible IGR match to verify: {e.get('doc_type_en') or e['docname_raw']} "
                         f"Doc No. {e['docno']}/{e['year']} ({e['sro']}) — {e.get('reason', '')}")
    if not owner["names"]:
        flags.append("Current owner could not be established from the documents provided.")
    elif owner.get("allotment_only"):
        al = next((d for d in docs if d["ref"] == owner["via_ref"]), {})
        flags.append(f"Only the {al.get('doc_type', 'Allotment Letter')}"
                     f"{' dated ' + al['exec_date'] if al.get('exec_date') else ''} issued by "
                     f"{' & '.join(owner['from']) or 'the Promoter'} in favour of {' & '.join(owner['names'])} is furnished; the "
                     f"Agreement for Sale is yet to be executed and registered. The registered Agreement for Sale with its "
                     f"Index-II and registration receipt, the payment receipts, and the NOC / permission to mortgage (or "
                     f"tripartite agreement) of {' & '.join(owner['from']) or 'the Promoter'} in favour of the Bank are "
                     f"required to be obtained before disbursement.")

    flags = list(dict.fromkeys(flags))           # the same observation from two sources, once
    discrepancies = list(dict.fromkeys(x for ex in extractions for x in ex.get("discrepancies", []) if x))
    key_terms = [{"ref": d["ref"], "doc": f"{d['doc_type']} {d['exec_date']}", "term": t}
                 for d in docs for t in d.get("key_terms", [])]

    return {
        "lead_id": lead.get("leadid"),
        "applicant": lead.get("custname", ""),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "report_date": today_str(),
        "bank": {"name": opts.get("bank_name", ""), "branch": opts.get("bank_branch", "")},
        "subject_property": {k: v for k, v in subject.items() if not k.startswith("_")},
        "current_owner": owner,
        "documents": docs,
        "igr": {"files": igr_files, "rows_total": len(igr_rows), "coverage_years": covered,
                "period_from": from_y, "period_to": to_y, "missing_years": missing,
                "sro_place": sro_place, "sro_num_from": str(sro_nums[0]) if sro_nums else "",
                "sro_num_to": str(sro_nums[-1]) if sro_nums else "",
                "entries": igr["entries"], "project_entries": igr["project_entries"],
                "receipts": list(rec.values())},
        "encumbrances": encs,
        "flags": flags,
        "discrepancies": discrepancies,
        "key_terms": key_terms,
    }


def _ranges(years: List[int]) -> str:
    out, start, prev = [], None, None
    for y in years:
        if start is None:
            start = prev = y
        elif y == prev + 1:
            prev = y
        else:
            out.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = y
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(out)
