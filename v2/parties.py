"""Party table: every person / firm / society / lender / authority named in the papers, as ONE entry each, with all
spellings, former names, the people who sign for it, and every document (and page) where it appears with its role.

Rules (general, no case knowledge):
  - The person signing FOR a party (partner, director, constituted attorney, authorised signatory) is recorded as a
    signatory of that party, never as a party.
  - Two spellings are merged only when the names are clearly the same ('same'). A 'possible' match (one letter apart,
    a missing word) is NOT merged: it is listed for the lawyer to verify.
  - A person is never merged with a firm; a lender or authority never with anyone else.
"""
import re
from typing import Dict, List, Optional

from .evidence import cite, flag
from .names import compare, party_kind, split_agent, split_alias

# role text → role family (what the party DOES in that document)
_ROLE_FAMILIES = [
    ("agent", r"represent|signator|attorney|\bpoa\b|power\s+of\s+attorney|partner|director|proprietor|chairman|"
              r"secretary|treasurer|authori[sz]ed|karta|guardian|executor|administrator|मुखत्यार|भागीदार|संचालक"),
    ("confirming", r"confirm|consent|मान्यता"),
    ("witness", r"witness|identif|साक्षी|ओळख"),
    ("mortgagor", r"mortgagor|borrower|कर्ज\s*घेणार|co-?applicant|applicant"),
    ("mortgagee", r"mortgagee|lender|कर्ज\s*देणार|trustee"),
    ("releasor", r"releasor|relinquish"),
    ("releasee", r"releasee"),
    ("lessor", r"lessor|licensor"),
    ("lessee", r"lessee|licensee|tenant"),
    ("donor", r"donor"),
    ("donee", r"donee"),
    ("promoter", r"promoter|developer|builder"),
    ("owner", r"owner|land\s*holder|holder|occupant|कब्जेदार|मालक"),
    ("vendor", r"vendor|seller|transferor|assignor|executant|लिहून\s*देणार|विक्रेता"),
    ("purchaser", r"purchaser|buyer|allottee|transferee|assignee|vendee|claimant|लिहून\s*घेणार|खरेदीदार"),
    ("society", r"society"),
    ("issuer", r"issu|authority|officer|registrar|advocate|valuer|architect|engineer|consultant"),
    ("addressee", r"addressee|applicant|recipient|beneficiary|payee|member|shareholder|consumer|assessee"),
]


_IDENTITY_DOC = re.compile(r"aadhaa?r|\bpan\b|pan\s*card|passport|voter|election\s*card|driving\s*licen|\bkyc\b|"
                           r"identity\s*(?:card|proof|document)|photo\s*id", re.I)
_ABBREV = re.compile(r"^\s*(?:m/s\.?\s*)?[A-Z][A-Z.&\s]{1,8}\s*$")


def role_family(role: str) -> str:
    r = (role or "").lower()
    for fam, rx in _ROLE_FAMILIES:
        if re.search(rx, r, re.I):
            return fam
    return "other"


def mention(name: str, role: str, ev: dict) -> dict:
    return {"raw": name or "", "role": role or "", "evidence": ev}


class _Party:
    def __init__(self, pid: str, name: str, kind: str):
        self.id, self.kind = pid, kind
        self.names: List[dict] = []          # [{"name", "evidence": [..]}]
        self.former: List[dict] = []         # former names / aliases
        self.roles: List[dict] = []          # [{"role", "family", "evidence"}]
        self.signatories: List[dict] = []    # [{"name", "evidence": [..]}]
        self.near_signatories: List[tuple] = []
        self.add_name(name, None)

    def all_names(self) -> List[str]:
        return [n["name"] for n in self.names] + [n["name"] for n in self.former]

    def add_name(self, name: str, ev: Optional[dict], former: bool = False):
        lst = self.former if former else self.names
        for n in lst:
            if n["name"].lower() == name.lower():
                if ev:
                    n["evidence"].append(ev)
                return
        lst.append({"name": name, "evidence": [ev] if ev else []})

    def add_signatory(self, name: str, ev: dict):
        if re.fullmatch(r"(?i)\s*(?:and\s+)?(?:others?|ors\.?|another|anr\.?|इतर|वगैरे)\s*", name or ""):
            return                                      # 'partner X and others' — 'others' is not a name
        for s in self.signatories:
            c = compare(s["name"], name, "individual", "individual")
            if c == "same":
                s["evidence"].append(ev)
                return
            if c == "possible":
                self.near_signatories.append((s["name"], name, ev))
        self.signatories.append({"name": name, "evidence": [ev]})

    def display(self) -> str:
        # the spelling seen most often; English preferred over Devanagari when equally common
        best = sorted(self.names, key=lambda n: (-len(n["evidence"]), bool(re.search(r"[ऀ-ॿ]", n["name"])),
                                                 -len(n["name"])))
        return best[0]["name"] if best else ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.display(), "kind": self.kind,
                "spellings": [{"name": n["name"], "sources": [cite(e) for e in n["evidence"]]} for n in self.names],
                "former_names": [{"name": n["name"], "sources": [cite(e) for e in n["evidence"]]} for n in self.former],
                "signatories": [{"name": s["name"], "sources": [cite(e) for e in s["evidence"]]} for s in self.signatories],
                "roles": [{"role": r["role"], "family": r["family"], "document": r["evidence"].get("doc_type"),
                           "source": cite(r["evidence"])} for r in self.roles]}


def build_party_table(mentions: List[dict]) -> dict:
    """mentions: [{"raw", "role", "evidence"}] from every document. Returns {"parties": [...], "flags": [...]}."""
    parties: List[_Party] = []
    possible: Dict[tuple, dict] = {}
    pending_agents: List[dict] = []
    kyc: List[tuple] = []
    flags: List[dict] = []

    def find_or_add(name: str, ev: dict) -> _Party:
        kind = party_kind(name)
        same, maybe = [], []
        for p in parties:
            outcomes = [compare(name, n, kind, p.kind) for n in p.all_names()]
            if "same" in outcomes:
                same.append(p)
            elif "possible" in outcomes:
                maybe.append(p)
        if same:
            target = same[0]
            for other in same[1:]:                  # the new spelling links two entries: they are one party
                _absorb(target, other)
                parties.remove(other)
            target.add_name(name, ev)
            return target
        p = _Party(f"P{len(parties) + 1}", name, kind)
        p.names[0]["evidence"].append(ev)
        parties.append(p)
        for q in maybe if kind in ("individual", "company", "society", "lender") else []:
            # near-identical government offices are different offices, not a title question — no flag
            key = tuple(sorted((p.id, q.id)))
            possible.setdefault(key, {"a": p, "b": q, "evidence": []})["evidence"].append(ev)
        return p

    for m in mentions:
        raw, role, ev = m["raw"], m["role"], m["evidence"]
        if not raw or len(re.sub(r"\W", "", raw)) < 2:
            continue
        fam = role_family(role)
        party_name, agent = split_agent(raw)
        main, former = split_alias(party_name)
        if fam == "agent" and not agent:
            # 'Ravi Kumar — Partner' listed on its own: a signatory; the party it signs for is decided below
            pending_agents.append({"name": main, "evidence": ev, "role_text": role})
            continue
        if fam == "witness":
            continue                                   # witnesses / identifiers are not parties to the title
        if _IDENTITY_DOC.search(ev.get("doc_type") or ""):
            kyc.append((main, ev))                     # an Aadhaar / PAN proves who someone is, not a role in the title
            continue
        p = find_or_add(main, ev)
        for f in former:
            p.add_name(f, ev, former=True)
        p.roles.append({"role": role, "family": fam, "evidence": ev})
        if agent:
            for a in re.split(r"\s*(?:,|;|&|\band\b|\bव\b)\s*", agent):
                if len(re.sub(r"\W", "", a)) >= 3:
                    p.add_signatory(a.strip(), ev)

    # a signatory listed on its own line: attach to the party the role names, to the GRANTOR of a power of attorney,
    # or to the ONE firm / society of that document; otherwise one flag per document lists who is unresolved
    unresolved: Dict[tuple, dict] = {}
    for a in pending_agents:
        ev = a["evidence"]
        same_doc = lambda p: any(r["evidence"].get("doc_ref") == ev.get("doc_ref") and r["evidence"].get("file") == ev.get("file")
                                 for r in p.roles)
        firms = [p for p in parties if p.kind in ("company", "society", "lender") and same_doc(p)]
        if re.search(r"power\s+of\s+attorney|\bpoa\b|कुलमुखत्यार|मुखत्यारपत्र", ev.get("doc_type") or "", re.I):
            grantors = [p for p in firms if any(r["evidence"].get("doc_ref") == ev.get("doc_ref") and
                                                role_family(r["role"]) in ("vendor", "owner", "promoter", "other", "addressee")
                                                and not re.search(r"attorney|agent", r["role"], re.I) for r in p.roles)]
            if len(grantors) == 1:
                firms = grantors                       # the attorneys of a POA act for the party that granted it
        known = [p for p in parties if any(s["name"] and compare(s["name"], a["name"], "individual", "individual") == "same"
                                           for s in p.signatories)]
        named = [p for p in parties if p.kind in ("company", "society", "lender")
                 and any(compare(n, a.get("role_text", ""), None, p.kind) == "same" or
                         (len(n) > 6 and n.lower() in a.get("role_text", "").lower()) for n in p.all_names())]
        if known:
            known[0].add_signatory(a["name"], ev)
        elif len(named) == 1:
            named[0].add_signatory(a["name"], ev)      # 'Chairman of X Society' — the role names the party
        elif len(firms) == 1:
            firms[0].add_signatory(a["name"], ev)
        else:
            u = unresolved.setdefault((ev.get("file"), ev.get("doc_ref")), {"ev": ev, "names": []})
            if a["name"] not in u["names"]:
                u["names"].append(a["name"])
    for u in unresolved.values():
        names = ", ".join(f"'{n}'" for n in u["names"][:8]) + (f" and {len(u['names']) - 8} more" if len(u["names"]) > 8 else "")
        flags.append(flag("verify", "party", f"{names} sign in {cite(u['ev'])} as agent(s), but the party they sign for "
                                             f"is not clear from that document.", [u["ev"]]))

    # a 'party' that is really someone's signatory elsewhere (named alone in one document)
    for p in list(parties):
        if p.kind != "individual":
            continue
        signs_for = [q for q in parties if q is not p and any(
            compare(s["name"], n, "individual", "individual") == "same" for s in q.signatories for n in p.all_names())]
        if signs_for and all(r["family"] in ("other", "vendor", "purchaser", "promoter", "owner") for r in p.roles):
            names = ", ".join(q.display() for q in signs_for)
            flags.append(flag("verify", "party", f"'{p.display()}' is named as a party in "
                                                 f"{', '.join(cite(r['evidence']) for r in p.roles[:3])} but signs for "
                                                 f"{names} elsewhere — confirm whether they are a party in their own right "
                                                 f"or only a signatory.", [r["evidence"] for r in p.roles]))

    # identity papers: attached to the party / signatory they identify; a holder named nowhere else is listed for
    # completeness only (no role in the title)
    for name, ev in kyc:
        hit = next((p for p in parties if any(compare(name, n, "individual", p.kind) == "same" for n in p.all_names())), None)
        sig = next((p for p in parties if any(compare(name, s["name"], "individual", "individual") == "same"
                                              for s in p.signatories)), None)
        if hit:
            hit.add_name(name, ev)
        elif sig:
            sig.add_signatory(name, ev)
        else:
            q = find_or_add(name, ev)
            q.roles.append({"role": "identity document holder", "family": "kyc", "evidence": ev})

    for p in parties:
        for a, b, ev in p.near_signatories:
            flags.append(flag("verify", "party", f"Signatories '{a}' and '{b}' of {p.display()} have nearly the same name "
                                                 f"— one person spelt two ways, or two people; confirm.", [ev]))
        if all(_ABBREV.match(n["name"]) for n in p.names) and p.kind not in ("authority",):
            flags.append(flag("verify", "party", f"'{p.display()}' is written only as an abbreviation in "
                                                 f"{', '.join(cite(r['evidence']) for r in p.roles[:3])} — identify the "
                                                 f"full name of this party from the documents.",
                              [r["evidence"] for r in p.roles]))

    for v in possible.values():
        a, b = v["a"], v["b"]
        if a in parties and b in parties:
            flags.append(flag("verify", "party",
                              f"'{a.display()}' and '{b.display()}' may be the same {a.kind} (names nearly identical) — "
                              f"not merged; confirm from the documents.", v["evidence"]))
    for i, p in enumerate(parties, 1):
        p.id = f"P{i}"
    return {"parties": [p.to_dict() for p in parties], "flags": flags, "_objects": parties}


def _absorb(dst: "_Party", src: "_Party"):
    for n in src.names:
        for e in n["evidence"] or [None]:
            dst.add_name(n["name"], e)
    for n in src.former:
        for e in n["evidence"] or [None]:
            dst.add_name(n["name"], e, former=True)
    dst.roles += src.roles
    for s in src.signatories:
        for e in s["evidence"]:
            dst.add_signatory(s["name"], e)


def find_party(table: dict, name: str) -> Optional[dict]:
    """The party-table entry for a name ('same' match only)."""
    for p in table.get("_objects", []):
        if any(compare(name, n, None, p.kind) == "same" for n in p.all_names()):
            return next(x for x in table["parties"] if x["id"] == p.id)
    return None
