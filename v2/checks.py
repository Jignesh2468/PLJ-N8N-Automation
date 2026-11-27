"""Stage 3 — the checks a lawyer makes once the chain of title and the search are assembled.

Every check is general (no case knowledge) and produces FLAGS — never a filled-in fact:
  coverage     years of the period with no search data (never reported as NIL)
  continuity   an owner in the chain with no deed by which they acquired title
  not_in_search   a registered deed in the papers, within the searched years, that the search does not show
  no_copy      a deed found in the search whose copy is not among the papers
  charges      mortgages with no release found (subsisting) / releases linked to their mortgage
  mismatch     a deed whose search entry disagrees with the papers (date, parties)
  holder       the present holder's own deed is a draft / not registered
plus the Stage-1 flags (conflicts between documents, items to verify, missing facts).
"""
from datetime import date
from typing import Dict, List, Optional

from app.common import ascii_digits, docno_key, parse_date, parse_doc_no_raw

from .names import compare

TRANSFER = {"conveyance", "sale", "agreement", "gift", "partition", "exchange", "release_rights", "development", "lease",
            "paaa"}


def _key_of(doc_no: str):
    n, y = parse_doc_no_raw(ascii_digits(doc_no or ""))
    return docno_key(n, y) if n else None


def _flag(level: str, topic: str, text: str) -> dict:
    return {"level": level, "topic": topic, "text": text, "sources": []}


def chain_steps(stage1: dict, stage2: dict) -> List[dict]:
    """The chain of title in date order: every transfer / charge recited in the papers and every confirmed search
    entry, the same deed counted once (matched by registration number)."""
    steps, by_key = [], {}
    for t in stage1["property"]["title_history"]:
        k = _key_of(t.get("doc_no"))
        s = {"date": t.get("date") or "", "type": t.get("type") or "", "doc_no": t.get("doc_no") or "",
             "office": t.get("office") or "", "from": [x["name"] for x in t.get("from") or []],
             "to": [x["name"] for x in t.get("to") or []], "in_papers": True,
             "copy": "furnished" if t.get("copy_furnished") else "recited only", "in_search": False,
             "status": t.get("status") or "", "source": t.get("source") or "", "level": t.get("level") or "",
             "family": "", "key": k}
        if k and k in by_key:
            continue
        if k:
            by_key[k] = s
        steps.append(s)
    for d in stage2["decisions"].values():
        if d["tier"] != "confirmed":
            continue
        r = d["row"]
        k = docno_key(r["docno"], r["year"])
        if k and k in by_key:
            s = by_key[k]
            s["in_search"], s["search_date"], s["search_office"] = True, r["reg_date"], r["office"]
            s["search_from"] = [p["name"] for p in r["sellers"] if not p["confirming"]]
            s["search_to"] = [p["name"] for p in r["buyers"] if not p["confirming"]]
            s["family"] = r["kind"]["family"]
            s["why"] = d["why"]
            continue
        s = {"date": r["exec_date"] or r["reg_date"] or "", "type": r["kind"]["label"], "doc_no": f"{r['docno']}/{r['year']}",
             "office": r["office"] or "", "from": [p["name"] for p in r["sellers"] if not p["confirming"]],
             "to": [p["name"] for p in r["buyers"] if not p["confirming"]], "in_papers": False, "copy": "not furnished",
             "in_search": True, "search_date": r["reg_date"], "status": "registered", "source": "IGR search",
             "level": "", "family": r["kind"]["family"], "key": k, "why": d["why"], "consideration": r.get("consideration")}
        if k:
            by_key[k] = s
        steps.append(s)
    steps.sort(key=lambda s: (parse_date(s["date"]) or parse_date(s.get("search_date") or "") or date(1900, 1, 1)))
    return _merge_same_deed(steps)


def _family(s: dict) -> str:
    t = (s.get("family") or s.get("type") or "").lower()
    for k in ("mortgage", "release", "development", "power of attorney", "lease", "gift", "partition", "conveyance",
              "sale", "agreement"):
        if k in t:
            return k
    return t


def _merge_same_deed(steps: List[dict]) -> List[dict]:
    """One deed described twice — recited in a document without its number, and again with its number (Index-II /
    the search) — is ONE step: same kind of deed, same date, and the unnumbered one adds nothing new. A mention with
    no date, no number and no parties is not a step of the chain."""
    out: List[dict] = []
    for s in steps:
        if not s["date"] and not s["doc_no"] and not s["from"] and not s["to"]:
            continue
        twin = next((o for o in out if o["date"] and o["date"] == s["date"] and _family(o) == _family(s)
                     and (not o["doc_no"] or not s["doc_no"] or o.get("key") == s.get("key"))), None)
        if twin:
            keep, drop = (twin, s) if twin["doc_no"] or not s["doc_no"] else (s, twin)
            for k in ("from", "to"):
                if not keep[k]:
                    keep[k] = drop[k]
            keep["in_papers"] = keep["in_papers"] or drop["in_papers"]
            keep["in_search"] = keep["in_search"] or drop["in_search"]
            if keep is s:
                out[out.index(twin)] = s
            continue
        out.append(s)
    return out


def run_checks(stage1: dict, stage2: dict, years_searched: List[int], period, nm=None, tx=None) -> List[dict]:
    nm = nm or (lambda x: x)          # party name -> English (report language)
    tx = tx or (lambda x: x)          # any text / deed type -> English
    flags: List[dict] = []
    steps = chain_steps(stage1, stage2)
    m = stage1["property"]

    # coverage
    if period and period[1] >= period[0]:
        missing = [y for y in range(period[0], period[1] + 1) if y not in years_searched]
        if missing:
            flags.append(_flag("verify", "search", f"No search file was supplied for {_years(missing)} (period "
                                                   f"{period[0]}–{period[1]}). IGR exports no file for a year without "
                                                   f"entries, so these years are reported as NIL — confirm that the "
                                                   f"search for them was taken."))

    # present holder
    holder = m.get("present_holder")
    if not holder:
        flags.append(_flag("missing", "title", "The papers contain no deed of the unit itself: the present holder "
                                               "cannot be established."))
    elif holder.get("status") in ("draft", "executed_not_registered"):
        flags.append(_flag("verify", "title", f"The present holders' document ({holder['document']}) is "
                                              f"{holder['status'].replace('_', ' ')} — they are proposed purchasers until "
                                              f"it is executed and registered."))

    # continuity: every person who GIVES title in the chain should have received it by an earlier step
    received: List[str] = []
    for s in steps:
        if _family(s) in ("mortgage", "release") or s["level"] == "recital":
            received += s["to"]
            continue
        unseen = [g for g in s["from"] if not _is_authority(g) and not any(compare(g, r) == "same" for r in received)]
        if unseen and parse_date(s["date"]):
            # one observation per deed: who gave title in it, and that their own acquisition is not shown
            who = " & ".join(nm(x) for x in unseen[:4]) + (f" & {len(unseen) - 4} others" if len(unseen) > 4 else "")
            flags.append(_flag("verify", "continuity",
                               f"By the {tx(s['type'])}{(' No. ' + s['doc_no']) if s['doc_no'] else ''} dated {s['date']}, "
                               f"title passes from {who}; the deed / record by which they acquired it is not in the papers "
                               f"or the search data — obtain the earlier title deeds / revenue records."))
        received += s["from"] + s["to"]

    # deeds of the papers vs the search
    years = set(years_searched)
    for s in steps:
        k = s.get("key")
        if s["in_papers"] and not s["in_search"] and k and k[1] in years:
            flags.append(_flag("verify", "search", f"{tx(s['type'])} No. {s['doc_no']} ({s['date'] or 'date not stated'}) is "
                                                   f"in the papers but not found in the search data supplied — obtain the "
                                                   f"certified copy / Index-II and verify."))
        if s["in_search"] and not s["in_papers"]:
            flags.append(_flag("verify", "papers", f"{tx(s['type'])} No. {s['doc_no']} dated {s['date']} is registered "
                                                   f"(found in the search) but its copy is not among the papers — obtain "
                                                   f"the certified copy."))
        if s["in_papers"] and s["in_search"]:
            pd, sd = parse_date(s["date"]), parse_date(s.get("search_date"))
            if pd and sd and pd > sd:
                flags.append(_flag("conflict", "mismatch", f"{tx(s['type'])} No. {s['doc_no']}: dated {s['date']} in the "
                                                           f"papers but registered on {s.get('search_date')} per the search "
                                                           f"— a deed cannot be executed after its registration."))
            for side, sname in (("from", "search_from"), ("to", "search_to")):
                for n in s.get(side) or []:
                    if s.get(sname) and not any(compare(n, x) in ("same", "possible") for x in s[sname]):
                        flags.append(_flag("verify", "mismatch", f"{tx(s['type'])} No. {s['doc_no']}: {nm(n)} is a party in the "
                                                                 f"papers but is not shown in its search entry."))
                        break

    # charges
    decisions = list(stage2["decisions"].values())
    released = {c for d in decisions if d["tier"] == "confirmed" and d["row"]["kind"]["family"] == "release_charge"
                for c in d["row"]["cites"]}
    for s in steps:
        if s.get("family") == "mortgage" or ("mortgage" in s["type"].lower() and "release" not in s["type"].lower()):
            if s.get("key") in released:
                continue
            flags.append(_flag("verify", "charge", f"{tx(s['type'])} No. {s['doc_no']} dated {s['date']} by "
                                                   f"{' & '.join(nm(x) for x in s['from'][:2]) or '—'} in favour of "
                                                   f"{' & '.join(nm(x) for x in s['to'][:2]) or '—'}: no release found in the search "
                                                   f"data — subsisting; NOC / release to be obtained."))

    # entries the lawyer must decide
    # entries that MAY concern the property: not in the Search Report — each listed here for the advocate to decide
    for d in sorted((d for d in decisions if d["tier"] == "possible"), key=lambda d: d["row"]["year"] or 0):
        r = d["row"]
        frm = " & ".join(nm(p["name"]) for p in r["sellers"][:2] if not p["confirming"]) or "—"
        to = " & ".join(nm(p["name"]) for p in r["buyers"][:2] if not p["confirming"]) or "—"
        flags.append(_flag("verify", "possible entry",
                           f"The search shows {tx(r['kind']['label'])} No. {r['docno']}/{r['year']} dated "
                           f"{r['exec_date'] or r['reg_date']} ({frm} → {to}), which may relate to the property: "
                           f"{tx('; '.join(d['why']))} — to be verified."))
    order = {"conflict": 0, "verify": 1, "missing": 2}
    return sorted(flags + list(stage1["flags"]), key=lambda f: (order.get(f["level"], 3), f["topic"]))


def _is_authority(name: str) -> bool:
    from .names import party_kind
    return party_kind(name) == "authority"


def _years(ys: List[int]) -> str:
    out, start, prev = [], None, None
    for y in sorted(ys):
        if start is None:
            start = prev = y
        elif y == prev + 1:
            prev = y
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = y
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)
