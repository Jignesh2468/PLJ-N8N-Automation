"""Party names: one person / company however it is written (English or Marathi, initials, spelling variants,
'formerly known as'), the party kept apart from the person who signs for it, and a three-way decision when two
names are compared: SAME, POSSIBLE (lawyer to verify) or DIFFERENT. Nothing here knows about any particular case."""
import re
from functools import lru_cache
from typing import List, Optional, Tuple

from app.igr import skeleton          # script-independent consonant skeleton (read-only use of production helper)

# ── what kind of party ──────────────────────────────────────────────────────────
_AUTHORITY = re.compile(
    r"m\.?h\.?a\.?d\.?a\b|housing\s*(?:and|&)\s*area\s*development|म्हाडा|c\.?i\.?d\.?c\.?o\b|सिडको|m\.?m\.?r\.?d\.?a\b|"
    r"slum\s*rehabilitation|झोपडपट्टी|\bs\.?r\.?a\b|collector|जिल्हाधिकारी|tahsildar|तहसीलदार|talathi|तलाठी|"
    r"municipal|corporation\s+of|महानगरपालिका|महापालिका|नगरपालिका|मुनि?सिपल|म्युनिसिपल|nagar\s*parishad|"
    r"government|शासन|state\s+of\s+maharashtra|union\s+of\s+india|railway|रेल्वे|\bboard\b|मंडळ|authority|प्राधिकरण|"
    r"registrar|निबंधक|city\s*survey|नगर\s*भूमापन|planning|नियोजन|revenue|महसूल|department|विभाग|commissioner|आयुक्त|"
    r"income\s*tax|aadhaar|unique\s+identification|msedcl|mahavitaran|महावितरण|(?<!high\s)court|न्यायालय|tribunal|"
    r"\brera\b|maharera|inspector\s+general|sub[\s-]*registrar|दुय्यम\s*निबंधक|ministry|मंत्रालय|office\s+of", re.I)
# an advocate / valuer / architect named with the profession is a person, not an authority
_PROFESSIONAL = re.compile(r"advocate|solicitor|attorney\s+at\s+law|\bvaluer|chartered\s+accountant|architect|notary|"
                           r"वकील|अधिवक्ता", re.I)
# ', Advocate High Court' / ', Chartered Accountant' after a person's name: the profession, not the name
_PROF_SUFFIX = re.compile(r"\s*,?\s*(?:advocate|solicitor|attorney\s+at\s+law|valuer|chartered\s+accountant|architect|"
                          r"notary|वकील|अधिवक्ता)\b.*$", re.I)
_LEGAL_END = re.compile(r"(limited|ltd\.?|llp|private|pvt\.?|लिमिटेड|मर्यादित|लि\.|एलएलपी)\s*,\s*[^,]{1,40}$", re.I)


def clean_name(name: str) -> str:
    """Drop what is not the name itself: a profession after a person's name, a place after a firm's legal name
    ('… Society Limited, Mumbai'), a trailing place after an office ('Inspector General of Registration, Maharashtra')."""
    n = " ".join((name or "").split())
    if _PROFESSIONAL.search(n) and not _COMPANY.search(n):
        return _PROF_SUFFIX.sub("", n).strip(" ,")
    n = _LEGAL_END.sub(r"\1", n)
    parts = [p.strip() for p in n.split(",")]
    if len(parts) >= 2 and len(parts[-1].split()) <= 2 and not re.search(r"\d", parts[-1]) and \
            len(tokens_raw(",".join(parts[:-1]))) >= 2 and party_kind(n) in ("authority", "society", "company", "lender"):
        n = ", ".join(parts[:-1])
    return n
_LENDER = re.compile(r"\bbank\b|बँक|बॅक|बैंक|बॅंक|financ|फायनान्स|फायनांस|housing\s+finance|credit|trustee(?:ship)?|"
                     r"ट्रस्टीशिप|nbfc|\bhdfc\b|\blic\b|capital\s+(?:ltd|limited)|co-?operative\s+credit|पतसंस्था|"
                     r"home\s*loans?", re.I)
_SOCIETY = re.compile(r"co\s*-?\s*op(?:erative)?\.?\s*(?:hsg|housing)|housing\s+society|\bc\.?h\.?s\.?(?:l\.?)?\b|"
                      r"सहकारी\s*गृहनिर्माण|गृहनिर्माण\s*संस्था|हौसिंग\s*सोसायटी|हाउसिंग\s*सोसायटी|को\s*-?\s*ऑ(?:प|परेटिव्ह)?\.?\s*"
                      r"(?:हौ|हा)|सोसायटी|society\b|association|असोसिएशन|federation|trust\b|ट्रस्ट|condominium|"
                      r"sahakari|sanstha|संस्था", re.I)
_COMPANY = re.compile(r"\bm/s\.?|messrs|मेसर्स|\bमे\.|private|pvt|limited|\bltd\b|\bllp\b|एलएलपी|प्रा\.?\s*लि|लिमिटेड|"
                      r"developers?|builders?|construction|realty|realtors?|infra|estates?|enterprises?|associates|"
                      r"corporation|company|\bco\.?\b|projects?|properties|promoters?|group|industries|traders|"
                      r"डेव्हल|डेवल|बिल्डर|कन्स्ट्रक्श|रि?यल्टी|रिअल्टी|रीअल्टी|रियालटी|रियल्टर|रिअल्टर|इन्फ्रा|एंटरप्रा|"
                      r"एन्टरप्रा|इंटरप्रा|इन्टरप्रा|असोसिए|प्रोजेक्ट|प्रॉपर्टी|कॉर्पोरेशन|कंपनी|ग्रुप|होम्स|इस्टेट|"
                      r"लँडस्केप|स्पेसेस|प्रिमायसेस|प्रीमायसेस|प्रेमायसेस|फर्म|^\s*मे\.?\s|ट्रेडर्स|इंडस्ट्रीज|"
                      r"\bfirm\b|\bhuf\b|एच\s*यु\s*एफ|consultants?|services|solutions|agency|and\s+company|कंसल्टंट",
                      re.I)


def party_kind(name: str) -> str:
    """individual / company / society / lender / authority — from the name only (roles are kept separately)."""
    n = name or ""
    if _PROFESSIONAL.search(n) and not _COMPANY.search(n):
        return "individual"                     # an advocate / valuer / architect is a person, not an authority
    if _AUTHORITY.search(n):
        return "authority"
    if _LENDER.search(n):
        return "lender"
    if _SOCIETY.search(n):
        return "society"
    if _COMPANY.search(n):
        return "company"
    return "individual"


# ── the party vs the person signing for it ─────────────────────────────────────
_AGENT_SPLIT = re.compile(
    r"\s*(?:,\s*)?(?:\bthrough\b|\bthr\.?\s|\bthro\.?\s|represented\s+by|acting\s+(?:through|by)|by\s+(?:its|their)\s+"
    r"(?:partners?|directors?|constituted\s+attorney|authori[sz]ed\s+(?:signatory|representative))|"
    r"यांच्या\s*तर्फे|तर्फे|(?:चे|ची|च्या|यांचे)\s*(?:संचालक|भागीदार|भागिदार|डायरेक्टर|पार्टनर|अधिकृत\s*सही|प्रोप्रायटर|"
    r"प्रोपरायटर|सचिव|अध्यक्ष|चेअरमन|सेक्रेटरी|खजिनदार|मालक))", re.I)
_AGENT_WORDS = re.compile(r"^\s*(?:its\s+|their\s+|the\s+)?(?:partners?|directors?|proprietor|constituted\s+attorney|"
                          r"power\s+of\s+attorney\s+holder|poa\s+holder|attorney|authori[sz]ed\s+(?:signatory|representative|person)|"
                          r"chairman|secretary|treasurer|कु\.?\s*मु\.?(?:\s*म्हणून)?|मुखत्यार(?:\s*म्हणून)?|कुलमुखत्यार|"
                          r"भागीदार|संचालक|अधिकृत\s*सही\s*(?:करणार|कर्ता)?)\s*[:\-–]?\s*", re.I)
_ALIAS = re.compile(r"\s*\(?\s*(?:formerly\s+(?:known\s+as\s+)?|earlier\s+known\s+as\s+|previously\s+known\s+as\s+|"
                    r"alias\s+|a\.?k\.?a\.?\s+|@\s*|उर्फ\s+|ऊर्फ\s+|पूर्वी(?:चे)?\s*(?:नाव|ओळखले\s*जात\s*होते)?\s*|"
                    r"पूर्वाश्रमीची\s+|पुर्वाश्रमीची\s+|पूर्वाश्रमीचे\s+|nee\s+|née\s+)", re.I)
_ROLE_PREFIX = re.compile(r"^\s*\(?\s*(?:कर्ज\s*घेणार|कर्ज\s*देणार|लिहून\s*देणार|लिहून\s*घेणार|मान्यता\s*देणार|"
                          r"borrower|mortgagor|mortgagee|lender|vendor|purchaser|confirming\s+party|owner|developer|"
                          r"promoter|allottee|lessor|lessee)\s*\d*\s*\)?\s*[:\-–/]*\s*", re.I)


def split_agent(raw: str) -> Tuple[str, str]:
    """'M/s X Developers through its partner Mr. Y' → ('M/s X Developers', 'Mr. Y').
    'X तर्फे कु.मु. Y' → ('X', 'Y'). Returns (party, signatory or '')."""
    t = _ROLE_PREFIX.sub("", " ".join((raw or "").replace('"', " ").split()))
    m = _AGENT_SPLIT.search(t)
    if not m or m.start() < 3:
        return t.strip(" ,.-–"), ""
    party, agent = t[:m.start()], t[m.end():]
    agent = _AGENT_WORDS.sub("", agent)
    agent = _AGENT_SPLIT.split(agent)[-1] if _AGENT_SPLIT.search(agent) else agent   # 'through X through POA Y' → Y
    return party.strip(" ,.-–"), _AGENT_WORDS.sub("", agent).strip(" ,.-–")


def split_alias(name: str) -> Tuple[str, List[str]]:
    """'ABC Realty Pvt Ltd (formerly known as XYZ Realtors Pvt Ltd)' → ('ABC Realty Pvt Ltd',
    ['XYZ Realtors Pvt Ltd']). 'Asha Ravi Joshi पुर्वाश्रमीची Asha Mohan Rao' likewise."""
    m = _ALIAS.search(name or "")
    if not m or m.start() < 3:
        return (name or "").strip(), []
    main = name[:m.start()].strip(" ,(")
    rest = re.sub(r"\b(?:या\s*नावाने|नावाने)\b", " ", name[m.end():]).strip(" ,)")
    rest = re.sub(r"\)\s*$", "", rest).strip()
    return main, [rest] if rest else []


# ── comparing two names ─────────────────────────────────────────────────────────
_HONORIFIC = re.compile(r"(?<![A-Za-z])(?:mr|mrs|ms|miss|smt|shri|shree|sri|kum|dr|late|m/s|messrs|mess)(?![A-Za-z])\.?|"
                        r"श्रीमती|श्री\.?(?=\s)|श्री\.|सौ\.?|कु\.(?=\s)|कै\.?|मे\.|(?<!\S)मे(?=\s)|मेसर्स|स्व\.", re.I)
_FORM = {skeleton(w).replace("c", "k") for w in (
    "private", "pvt", "limited", "ltd", "llp", "प्रा", "प्रायव्हेट", "प्रायवेट", "प्राईवेट", "लिमिटेड", "लिमीटेड", "लि",
    "एलएलपी", "and", "अँड", "अॅन्ड", "अॅण्ड", "the", "of", "co", "since", "deceased", "मयत", "decd")}
# words that describe the business, not WHO it is ('X Developers' vs 'Y Developers' share nothing that matters)
_TRADE_WORDS = {skeleton(w).replace("c", "k") for w in (
    "developers", "developer", "builders", "builder", "realty", "realtors", "constructions", "construction", "infra",
    "infrastructure", "enterprises", "enterprise", "associates", "projects", "estates", "estate", "properties", "group",
    "company", "corporation", "holdings", "homes", "promoters", "sons", "housing", "society", "cooperative", "operative",
    "hsg", "chs", "landscapes", "spaces", "premises", "traders", "industries", "services", "consultants",
    "डेव्हलपर्स", "बिल्डर्स", "सोसायटी", "हौसिंग", "एंटरप्राइजेस", "एन्टरप्रायजेस", "असोसिएट्स", "ग्रुप")}


class _TradeSet(frozenset):
    """Membership with one letter of tolerance: Marathi writes an English plural '-s' as '-जेस' ('एंटरप्राइजेस')."""
    def __contains__(self, tok):
        return frozenset.__contains__(self, tok) or (len(tok) >= 5 and any(_near(tok, t) for t in self if len(t) >= 5))

    def __rsub__(self, other):
        return {t for t in other if t not in self}

    def __rand__(self, other):
        return {t for t in other if t in self}


_TRADE = _TradeSet(_TRADE_WORDS)


# 'Co-op. Hsg. Soc. Ltd.', 'CHS', 'सहकारी गृहनिर्माण संस्था मर्यादित' — the legal form of a society, not its name
_SOC_WORDS = re.compile(r"(?<![A-Za-z])(?:co\s*-?\s*op(?:erative)?|hsg|housing|society|soc|c\.?\s*h\.?\s*s\.?\s*(?:l\.?)?|"
                        r"premises|association|proposed|prop|partnership\s+firm|proprietary\s+firm|a\s+partnership)"
                        r"(?![A-Za-z])\.?|भागीदारी\s*संस्था|भागीदारी\s*फर्म|प्रोप्रायटरी\s*फर्म|सहकारी|गृहनिर्माण|संस्था|मर्यादित|"
                        r"को\s*-?\s*ऑप(?:रेटिव्ह|रेटीव्ह)?\.?|को\s*-?\s*ऑ\.?|हौसिंग|हाउसिंग|हाऊसिंग|हौ\.?(?=\s)|सोसायटी|"
                        r"सोसा\.?|सो\.(?=\s)|प्रस्तावित|नियोजित|प्रपोज्ड", re.I)


@lru_cache(maxsize=200000)
def _tokens_cached(name: str) -> tuple:
    return tuple(tokens_raw(clean_name(name)) or tokens_raw(clean_name(name), keep_honorific=True))


def tokens(name: str) -> List[str]:
    return list(_tokens_cached(name or ""))


def _tokens_uncached(name: str) -> List[str]:
    """Word skeletons of a name, honorifics and company-form words removed, initials joined:
    'Mr. Ramesh Suresh Patil' = 'रमेश सुरेश पाटील' → ['rms', 'srs', 'ptl'];
    'A. B. Infra Realty Pvt. Ltd.' = 'ए. बी. इन्फ्रा रियल्टी प्रा. लि.' → ['b', 'nfr', 'rlt']."""
    return tokens_raw(clean_name(name)) or tokens_raw(clean_name(name), keep_honorific=True)


def _sk(word: str) -> str:
    """Skeleton of one word, with 'c' and 'k' as one sound: Latin 'ch' and Devanagari 'च' must agree."""
    return skeleton(word).replace("c", "k")


@lru_cache(maxsize=200000)
def joined(name: str) -> str:
    """The whole name as one skeleton, short words included — for names written without spaces
    ('रमेशसुरेशपाटील' = 'Ramesh Suresh Patil')."""
    t = _SOC_WORDS.sub(" ", _HONORIFIC.sub(" ", clean_name(name or "")))
    t = re.sub(r"\([^)]*\)|\d+", " ", t)
    return re.sub(r"(.)\1+", r"\1", "".join(_sk(x) for x in re.split(r"[\s,./&()\-\"'{}:;]+", t) if x))


def tokens_raw(name: str, keep_honorific: bool = False) -> List[str]:
    """keep_honorific: when the honorific is the only word ('Shree Co-op Housing Society' is named 'Shree')."""
    if keep_honorific:
        t = _SOC_WORDS.sub(" ", name or "")
        t = re.sub(r"\([^)]*\)|\d+", " ", t)
        return [w for w in (_sk(x) for x in re.split(r"[\s,./&()\-\"'{}:;]+", t) if x) if len(w) >= 2 and w not in _FORM]
    t = _HONORIFIC.sub(" ", name or "")
    t = _SOC_WORDS.sub(" ", t)
    t = re.sub(r"\([^)]*\)|\d+", " ", t)
    out, run = [], ""
    for w in (_sk(x) for x in re.split(r"[\s,./&()\-\"'{}:;]+", t) if x):
        if not w:
            continue
        if len(w) == 1:
            run += w
            continue
        if run:
            out.append(run)
            run = ""
        out.append(w)
    if run:
        out.append(run)
    return [w for w in out if len(w) >= 2 and w not in _FORM]


def _near(a: str, b: str) -> bool:
    """One consonant apart: 'Kadam'/'Kdam' are equal skeletons already; 'Kokane'/'Konane' (kkn/knn) are near."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 3:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l_ = (a, b) if len(a) < len(b) else (b, a)
    return any(l_[:i] + l_[i + 1:] == s for i in range(len(l_)))


def compare(a: str, b: str, kind_a: Optional[str] = None, kind_b: Optional[str] = None) -> str:
    return _compare_cached(a or "", b or "", kind_a, kind_b)


@lru_cache(maxsize=500000)
def _compare_cached(a: str, b: str, kind_a: Optional[str], kind_b: Optional[str]) -> str:
    """'same' / 'possible' / 'different'. 'possible' is never merged — it becomes a verification flag."""
    ka, kb = kind_a or party_kind(a), kind_b or party_kind(b)
    if {ka, kb} & {"authority", "lender"} and ka != kb:
        return "different"
    if (ka == "individual") != (kb == "individual") and "society" not in (ka, kb):
        return "different"                       # a person is never the same party as a firm
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return "different"
    sa, sb = set(ta), set(tb)
    if sa == sb or "".join(ta) == "".join(tb):
        return "same"                            # 'Shanti Nagar Gokul' = 'शांतीनगर गोकुळ' (spacing differs)
    ja_, jb_ = joined(a), joined(b)
    if (len(ta) == 1 or len(tb) == 1) and min(len(ja_), len(jb_)) >= 8 and (ja_ == jb_ or _near(ja_, jb_)):
        return "same"                            # one side written without spaces: 'रमेशसुरेशपाटील'
    if ka != "individual" and len("".join(ta)) >= 8 and len("".join(tb)) >= 8:
        ja, jb = "".join(t for t in ta if t not in _TRADE), "".join(t for t in tb if t not in _TRADE)
        if ja and ja == jb:
            return "same"
    if ka == "individual" and kb == "individual":
        return _compare_people(ta, tb)
    # firms / societies: the distinctive (non-trade) words decide
    da, db = sa - _TRADE, sb - _TRADE
    if not da or not db:
        return "possible" if (sa & sb) else "different"
    exact = da & db
    fuzzy = {x for x in da - exact if any(_near(x, y) for y in db - exact)}
    short = min(len(da), len(db))
    trade_a, trade_b = sa & _TRADE, sb & _TRADE
    if da == db:
        # same distinctive name: the business words decide. 'Sai Krupa Developers' = 'Sai Krupa Builders and
        # Developers' (one set inside the other); 'Alpha Estate' ≠ 'Alpha Developers' (group companies)
        if trade_a <= trade_b or trade_b <= trade_a:
            return "same" if (short >= 2 or len(next(iter(da))) >= 5 or trade_a == trade_b) else "possible"
        return "different"
    if len(exact) == short:
        # the longer name adds distinctive words ('Zenith' / 'Zenith Fiscal Services'): a short form of the same
        # firm, or another company of the same group — never merged
        return "possible" if (short >= 2 or len(next(iter(exact))) >= 5) else "different"
    if len(exact) + len(fuzzy) == short and exact:
        return "possible"                         # one letter apart: spelling or a different firm — verify
    if exact and len(exact) >= short - 1 and short >= 3:
        return "possible"
    return "different"


def _compare_people(ta: List[str], tb: List[str]) -> str:
    """Indian names: given name + father's/husband's name + surname, any of which may be omitted or misspelt.
    Same surname and same given name = same person; middle name may be missing. A married woman's earlier name
    ('पुर्वाश्रमीची') is handled as an alias, not here."""
    sa, sb = set(ta), set(tb)
    first_a, first_b, last_a, last_b = ta[0], tb[0], ta[-1], tb[-1]
    same_first, same_last = _near(first_a, first_b), _near(last_a, last_b)
    exact_first, exact_last = first_a == first_b, last_a == last_b
    if exact_first and exact_last:
        mids_a, mids_b = sa - {first_a, last_a}, sb - {first_b, last_b}
        if not mids_a or not mids_b or (mids_a & mids_b) or any(_near(x, y) for x in mids_a for y in mids_b):
            return "same"
        return "possible"                         # same first + last, different father's name: two people?
    if same_first and same_last:
        return "possible"                         # one letter apart (Kokane / Konane): verify
    if same_first and len(sa & sb) >= 2 and len(ta) >= 3 and len(tb) >= 3:
        return "possible"
    return "different"                            # different given names: different people (a wife's middle name is
    #                                               often her husband's — 'Sunita Ramesh Patil' is not 'Ramesh Patil')
