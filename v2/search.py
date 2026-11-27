"""Stage 2 — reconciling the IGR search with the property, the way a lawyer does it.

The lawyer already knows (Stage 1): the unit, every name of its building / society / project, the land parcels and
whether the property stands on the WHOLE or a PART of each, and the chain of owners recited in the papers. For each
search entry the lawyer asks:
  1. Does it name OUR unit in OUR building?                          → the unit's own deed
  2. Does it name OUR building / society / project (no unit)?       → a deed of the building / project
  3. Is it a deed of OUR land?  On land held WHOLE, any deed of the parcel is ours. On land held in PART (or a
     parcel shared by a whole layout), only deeds by which OUR chain of owners receives / charges title are ours —
     the other portions belong to other people.
  4. Does anything CONTRADICT it — another building or society named, another sub-division of the survey?
and walks the chain of owners backwards: each accepted transfer adds the person who gave the title.

Outcome per entry: CONFIRMED (in the report) / POSSIBLE (listed for the lawyer to verify, never silently included) /
NOT RELATED — always with the reasons. No case is known to this code.
"""
import re
from typing import Dict, List, Optional, Tuple

from app.common import ascii_digits, docno_key, parse_doc_no_raw
from app.igr import land_match, skeleton

from .igr_read import TITLE_FAMILIES, read_row
from .names import compare, tokens
from .property_model import norm_unit, same_unit

_KIND_CODE = {"CTS": "cts", "Survey": "sv", "Gat": "sv", "Old Survey": "old"}


# ── the subject, as established from the papers (Stage 1) ──────────────────────
def subject_profile(stage1: dict) -> dict:
    m, parties = stage1["property"], {p["id"]: p for p in stage1["parties"]}
    u = m["unit"]["unit_no"]
    units = [u["value"]] if u.get("value") else [c["value"] for c in u.get("candidates", [])]
    # a NAME must say which building it is: 'B Wing', 'Building No. 4', 'Tower T1' or 'Flat No. 1B-1203 in Orchid'
    # are labels / fragments — the distinctive words left after removing them are the name ('Orchid')
    names = []
    for n in m["building"]["names"]:
        core = re.sub(r"(?i)\b(?:flat|apartment|unit|shop|office|room|wing|building|bldg|tower|block|chawl|no|nos|"
                      r"number|in|of|the)\b\.?|\b[A-Za-z]?\d+[A-Za-z]?(?:\s*[-/]\s*[A-Za-z]?\d+[A-Za-z]?)*\b|"
                      r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", " ", n["name"])
        core = " ".join(core.split())
        if core and len("".join(tokens(core))) >= 3 and core not in names:
            names.append(core)
    bno = m["building"]["number"].get("value") or ""
    parcels = []
    for p in m["land"]["parcels"]:
        code = _KIND_CODE.get(p["kind"])
        if not code:
            continue                       # plot / final-plot numbers are not written in the IGR land index format
        main, _, sub = p["number"].partition("/")
        parcels.append({"kind": code, "main": main, "sub": sub, "part": p["extent"] == "part",
                        "label": f"{p['kind']} {p['number']}" + (" (part)" if p["extent"] == "part" else "")})

    # the chain of owners starts with the present holder and the developer, and grows BACKWARDS through the
    # papers' title history: whoever passed title TO someone already in the chain joins it. Other people named in the
    # papers (a neighbour's holding recited in a property card) do not.
    chain_ids = {x.get("id") for x in (m.get("present_holder") or {}).get("holders", []) + m["project"]["developers"]}
    for _ in range(10):
        before = len(chain_ids)
        for t in m["title_history"]:
            if any(x.get("id") in chain_ids for x in t["to"]) or \
                    (t.get("level") != "premises" and any(x.get("id") in chain_ids for x in t["from"]) and not t["to"]):
                chain_ids |= {x.get("id") for x in t["from"]}
        if len(chain_ids) == before:
            break
    chain_ids.discard(None)
    holders = {x.get("id") for x in (m.get("present_holder") or {}).get("holders", [])}
    devs = {x.get("id") for x in m["project"]["developers"]}
    chain = []
    for pid in chain_ids:
        p = parties.get(pid)
        if not p or p["kind"] in ("lender", "authority"):
            continue                       # a lender never owns; an authority holds whole areas (not one chain)
        chain.append({"id": pid, "names": [s["name"] for s in p["spellings"]] + [f["name"] for f in p["former_names"]],
                      "kind": p["kind"], "holder": pid in holders, "developer": pid in devs})
    paper_docs = {}
    for t in m["title_history"]:
        n, y = parse_doc_no_raw(ascii_digits(t.get("doc_no") or ""))
        k = docno_key(n, y) if n else None
        if k:
            paper_docs[k] = t
    return {"units": units, "names": names, "building_no": bno, "parcels": parcels, "chain": chain,
            "paper_docs": paper_docs, "village": (m["land"]["village"].get("value") or "")}


# ── evidence tests (each returns a fact, never a verdict) ──────────────────────
def _name_hit(text: str, names: List[str]) -> Optional[str]:
    """Which of the subject's building / society / project names the text contains (either script)."""
    sk_text = skeleton(text)
    words = {skeleton(w) for w in re.split(r"[\s,./()\-:;\"'{}]+", text) if w}
    for n in names:
        tk = tokens(n)
        joined = "".join(tk)
        if len(joined) >= 5 and joined in sk_text:
            return n
        if tk and len(joined) >= 3 and all(t in words for t in tk):
            return n
    return None


def _other_named(row: dict, names: List[str]) -> Optional[str]:
    for nm in row["named"]:
        if _name_hit(nm, names):
            continue
        if any(compare(nm, n, "society", "society") in ("same", "possible") for n in names):
            continue
        if len("".join(tokens(nm))) >= 3:
            return nm
    return None


def _land(row: dict, parcels: List[dict]) -> Tuple[str, List[dict]]:
    """'strict' / 'partial' / 'conflict' / 'other' / 'none', and the subject parcels it touches."""
    refs = [(x["kind"], x["main"], x["sub"] or None) for x in row["land"] if x["kind"] != "old"]
    if not refs or not parcels:
        return "none", []
    subj = [(p["kind"], p["main"], p["sub"] or None) for p in parcels]
    lm = land_match(refs, subj)
    touched = [p for p in parcels if any(r[0] == p["kind"] and r[1] == p["main"] for r in refs)]
    for k in ("strict", "partial", "conflict", "other"):
        if lm.get(k):
            return k, touched
    return "none", touched


def _chain_party(side: List[dict], chain: List[dict]) -> Optional[str]:
    for p in side:
        if p["confirming"] or p["lender"]:
            continue
        for c in chain:
            if any(compare(p["name"], n, p["kind"], c["kind"]) == "same" for n in c["names"]):
                return p["name"]
    return None


def _receiver_giver(row: dict) -> Tuple[List[dict], List[dict]]:
    fam = row["kind"]["family"]
    s, b = row["sellers"], row["buyers"]
    if fam in ("mortgage", "release_charge"):
        borrowers = [p for p in s + b if p["borrower"]] or [p for p in s + b if not p["lender"]]
        return borrowers, []
    if fam in ("release_rights", "partition", "exchange"):
        return s + b, []
    if not b:
        return s, []                         # a declaration / affidavit by one party
    return b, s


# ── the decision ────────────────────────────────────────────────────────────────
def judge(row: dict, subj: dict, chain: List[dict], shared: Dict[tuple, bool], confirmed_keys: set) -> Tuple[str, List[str]]:
    why: List[str] = []
    fam = row["kind"]["family"]
    key = docno_key(row["docno"], row["year"])
    name = _name_hit(row["text"], subj["names"])
    other = None if name else _other_named(row, subj["names"])
    bno_rows = [norm_unit(b) for b in row["building_nos"]]
    bno_hit = bool(subj["building_no"]) and norm_unit(subj["building_no"]) in bno_rows
    bno_other = bool(subj["building_no"]) and bool(bno_rows) and not bno_hit
    land, touched = _land(row, subj["parcels"])
    recv, give = _receiver_giver(row)
    recv_chain, give_chain = _chain_party(recv, chain), _chain_party(give, chain)

    if key and key in subj["paper_docs"]:
        return "confirmed", [f"the papers cite this deed by its number ({row['docno']}/{row['year']})"]
    if fam == "release_charge":
        if any(c in confirmed_keys for c in row["cites"]):
            return "confirmed", ["releases a charge already found to be on the property"]
        if recv_chain and land in ("strict", "partial"):
            # a release matters only together with ITS charge; that charge has not been found on the property
            return "possible", [f"a release of a charge given by {recv_chain} (in our chain) on our land, but the charge "
                                f"it releases ({', '.join(f'{n}/{y}' for n, y in row['cites']) or 'not stated'}) was not "
                                f"found on the property — confirm"]

    if row["units"]:                        # ── an entry about a unit ──
        unit_hit = next((u for u in row["units"] if any(same_unit(u, s) for s in subj["units"])), None)
        place = name or (subj["building_no"] if bno_hit else None)
        listed = len(row["units"]) >= 3
        if unit_hit and place and not other and not bno_other:
            return "confirmed", [f"names our unit {unit_hit}" + (" in a list of units" if listed else ""),
                                 f"names our building / society / project ({place})"]
        owner = _chain_party(row["sellers"] + row["buyers"], [c for c in chain if c.get("holder") or c.get("developer")])
        if unit_hit and owner and not other and not bno_other:
            return "confirmed", [f"names our unit {unit_hit}", f"with {owner} — our present holder / developer — as a party"]
        if unit_hit and (other or bno_other):
            return "not", [f"same unit number {unit_hit}, but in another building ({other or 'building no. ' + ', '.join(row['building_nos'])})"]
        if unit_hit and land in ("strict", "partial"):
            # any deed of OUR unit involves its holder or the developer (before the sale) — someone in our chain
            if recv_chain or give_chain:
                return "possible", [f"names our unit number {unit_hit} on our land ({', '.join(p['label'] for p in touched)})"
                                    f" with {recv_chain or give_chain} (in our chain)",
                                    "but does not name our building — confirm from the Index-II"]
            return "not", [f"same unit number {unit_hit} on the same land, but none of its parties is in our chain — "
                           f"a unit of another building"]
        if place and fam == "mortgage" and listed:
            return "possible", [f"a mortgage of units in our building ({place}); our unit is not in the list printed",
                                "confirm whether our unit is charged"]
        if place:
            return "not", [f"another unit of our building ({', '.join(row['units'][:3])})"]
        return "not", ["a unit entry that names neither our unit nor our building"]

    # ── an entry about land / the building / the project ──
    if other and not name:
        return "not", [f"names another building / society: {other}"]
    if bno_other and not name and land != "strict":
        return "not", [f"names another building number ({', '.join(row['building_nos'])})"]
    if land == "conflict" and not name:
        return "not", ["same survey / CTS but another sub-division (hissa)"]
    title_like = fam in TITLE_FAMILIES or fam in ("mortgage", "release_charge", "poa", "rectification", "cancellation")
    if name:
        why.append(f"names our building / society / project ({name})")
        people = [p for p in row["sellers"] + row["buyers"] if not p["lender"] and not p["confirming"]]
        if title_like and not recv_chain and not give_chain and people and all(p["kind"] == "individual" for p in people):
            # a flat buyer's loan or resale in our building, between people outside our chain, with no unit printed:
            # some OTHER unit of the building — our unit's deeds always involve its holder or the developer
            return "not", why + ["but its parties are individuals outside our chain of owners — a deed of another "
                                 "unit of the building (no unit number printed)"]
        if title_like:
            return "confirmed", why
        return "possible", why + [f"a {row['kind']['label'].lower()} — not a transfer; the lawyer decides whether to report it"]
    if land in ("strict", "partial"):
        labels = ", ".join(p["label"] for p in touched) or "our land"
        is_shared = any(shared.get((p["kind"], p["main"], p["sub"])) for p in touched)
        if not is_shared:
            if title_like:
                return "confirmed", [f"a deed of our land ({labels}), which the property holds whole"]
            return "possible", [f"concerns our land ({labels})", f"a {row['kind']['label'].lower()} — not a transfer"]
        if recv_chain:
            if title_like:
                return "confirmed", [f"our land ({labels}) is shared with other holders, and in this deed the title "
                                     f"passes to / is charged by {recv_chain}, who is in our chain of owners"]
            return "possible", [f"a {row['kind']['label'].lower()} by {recv_chain} (in our chain) on our land ({labels})"]
        if give_chain:
            # our chain passes land to someone the papers never mention: another portion sold off — or a step of
            # our own chain the papers do not recite. The papers cannot settle it: the lawyer verifies.
            return "possible", [f"{give_chain} (in our chain) passes part of the shared land ({labels}) to "
                                f"{', '.join(p['name'] for p in recv[:2]) or 'another party'}, who is not named in the "
                                f"papers — another portion sold off, or a step of our chain not recited; verify"]
        return "not", [f"another portion of shared land ({labels}): none of its parties is in our chain of owners"]
    if land == "other" and recv_chain and fam in TITLE_FAMILIES:
        same_main = [x for x in row["land"] if any(x["kind"] == p["kind"] or (x["kind"] == "sv" and p["kind"] == "sv")
                                                   for p in subj["parcels"] if x["main"] == p["main"])]
        if same_main:
            return "possible", [f"{recv_chain} (in our chain) acquires land on the same survey number, but the land "
                                f"numbers differ from the property's description — confirm whether it is part of the "
                                f"project land"]
    return "not", ["no identifier of the property"]


# ── the search as a whole ───────────────────────────────────────────────────────
def reconcile(rows: List[dict], stage1: dict) -> dict:
    subj = subject_profile(stage1)
    facts = [read_row(r) for r in rows]
    n = len(facts)
    # a parcel is SHARED when the papers say 'part', or when the search shows it is the land of a whole layout
    # (named in many entries of many different buildings)
    shared = {}
    for p in subj["parcels"]:
        hits = sum(1 for f in facts if any(x["kind"] == p["kind"] and x["main"] == p["main"] and
                                           (not p["sub"] or x["sub"] in ("", p["sub"])) for x in f["land"]))
        shared[(p["kind"], p["main"], p["sub"])] = p["part"] or hits > max(25, 0.02 * n)
        p["shared_reason"] = "held in part per the papers" if p["part"] else (
            f"named in {hits} search entries — land of a whole layout" if shared[(p["kind"], p["main"], p["sub"])] else "")

    # only entries with SOME link are judged in detail (land number, unit number, a name, or a chain party)
    mains = {p["main"] for p in subj["parcels"]}
    chain_sk = [("".join(tokens(nm)))[:8] for c in subj["chain"] for nm in c["names"]]
    def linked(f):
        if any(x["main"] in mains for x in f["land"]) or _name_hit(f["text"], subj["names"]):
            return True
        if f["units"] and any(same_unit(u, s) for u in f["units"] for s in subj["units"]):
            return True
        parties = skeleton(" ".join(p["name"] for p in f["sellers"] + f["buyers"])).replace(" ", "")
        return any(len(s) >= 6 and s in parties for s in chain_sk)
    cands = [f for f in facts if linked(f)]

    chain = list(subj["chain"])
    decisions: Dict[str, dict] = {}
    for _ in range(8):                      # walk the chain backwards until nothing new is accepted
        confirmed_keys = {docno_key(d["row"]["docno"], d["row"]["year"]) for d in decisions.values() if d["tier"] == "confirmed"}
        added = False
        for f in cands:
            tier, why = judge(f, subj, chain, shared, confirmed_keys)
            prev = decisions.get(f["id"])
            decisions[f["id"]] = {"tier": tier, "why": why, "row": f}
            if tier == "confirmed" and (not prev or prev["tier"] != "confirmed") and f["kind"]["family"] in TITLE_FAMILIES:
                _, give = _receiver_giver(f)
                for p in give:
                    if not p["confirming"] and not p["lender"] and p["kind"] not in ("authority",) and \
                            not any(compare(p["name"], nm, p["kind"], c["kind"]) == "same" for c in chain for nm in c["names"]):
                        chain.append({"id": None, "names": [p["name"]] + p["former"], "kind": p["kind"],
                                      "via": f"{f['docno']}/{f['year']}"})
                        added = True
        if not added:
            break
    return {"subject": subj, "chain": chain, "decisions": decisions, "rows_total": n, "candidates": len(cands)}


def year_table(result: dict, years: List[int], period: Tuple[int, int]) -> List[dict]:
    """Search Report rows: every year of the period; each year lists the confirmed entries, or NIL; years with no
    search data say so (never NIL without data)."""
    by_year: Dict[int, List[dict]] = {}
    for d in result["decisions"].values():
        if d["tier"] == "confirmed" and d["row"]["year"]:
            by_year.setdefault(int(d["row"]["year"]), []).append(d)
    out = []
    for y in range(period[0], period[1] + 1):
        if y not in years:
            out.append({"year": y, "status": "not searched", "entries": []})
        else:
            es = sorted(by_year.get(y, []), key=lambda d: (d["row"]["reg_date"] or "", int(d["row"]["docno"] or 0)))
            out.append({"year": y, "status": "entries" if es else "NIL", "entries": es})
    return out
