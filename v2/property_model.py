"""Property model: what exactly is the property, at every level, and what do the papers say about its title history.

    unit      → number, floor, wing, areas (carpet / built-up / RERA) — from the documents OF THE UNIT
    building  → building number, every name it has had (current / society / project / old) with sources
    project   → developer(s), scheme signals (SRA / MHADA / CIDCO / society redevelopment / new sale / resale)
    land      → every parcel (survey / hissa / CTS / plot / final plot), WHOLE or PART, village, taluka, district
    title     → the transfers, development rights and charges the papers recite, in date order, with parties

Every value keeps its sources. Disagreeing documents give a CONFLICT flag; absent information is 'not stated'.
Nothing here is specific to any case: the same code runs for a resale flat, a new flat, an SRA / MHADA / CIDCO
scheme, a shop, a gala or a plot.
"""
import re
from datetime import date
from typing import Dict, List, Optional

from app.common import ascii_digits, parse_date
from app.igr import land_refs

from .evidence import FieldBuilder, cite, evidence, field_flags, flag
from .names import compare
from .parties import find_party, role_family

UNIT_FIELDS = ("unit_type", "unit_no", "floor", "wing")
_KIND_LABEL = {"cts": "CTS", "survey": "Survey", "gat": "Gat", "hissa": "Survey", "plot": "Plot",
               "final_plot": "Final Plot", "sector": "Sector", "old_survey": "Old Survey"}
_IDENTITY = re.compile(r"aadhaa?r|\bpan\b|pan\s*card|passport|voter|election\s*card|driving\s*licen|\bkyc\b|"
                       r"identity|photograph|signature\s*verification", re.I)
AREA_FIELDS = ("carpet_area", "builtup_area")
LAND_FIELDS = ("village", "taluka", "district", "pin")
TITLE_CATEGORIES = ("title_transfer", "development", "encumbrance")


# ── value comparison (general) ──────────────────────────────────────────────────
def norm_unit(v: str) -> str:
    """One code however it is written: 'Flat No. 04' = 'सदनिका क्र. ४' → '4'; 'B-1801' = 'B 1801' → 'B1801';
    'E-B-10' = 'EB10'; 'Wing-B' = 'B'."""
    t = ascii_digits(v or "").upper()
    t = re.sub(r"\b(?:FLAT|APARTMENT|APT|UNIT|SHOP|OFFICE|GALA|ROOM|NO|NUMBER|WING|BUILDING|BLDG|TOWER)\b\.?", " ", t)
    t = re.sub(r"[^A-Z0-9]", "", t)
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", t)


def _composite_tail(v: str) -> str:
    """'Plot - 39 / E-B-10 / 10 / 1008' (plot / building / floor / unit) → '1008'; '' when v is not a composite code."""
    parts = [p for p in re.split(r"\s*/\s*", ascii_digits(v or "")) if p.strip()]
    return norm_unit(parts[-1]) if len(parts) >= 3 else ""


_UNIT_KINDS = [("residential", r"flat|apartment|residential|सदनिका|फ्लॅट|tenement|room|रूम|row\s*house|bungalow|villa"),
               ("shop", r"shop|दुकान|showroom"), ("office", r"office|ऑफिस"),
               ("industrial", r"gala|गाळा|industrial|warehouse|godown"), ("plot", r"plot|प्लॉट|land")]


def _unit_kind(v: str) -> str:
    """'Flat' = 'Apartment' = 'Residential Unit' = 'सदनिका' — the same kind of unit."""
    for k, rx in _UNIT_KINDS:
        if re.search(rx, v or "", re.I):
            return k
    return (v or "").lower().strip()


def same_unit(a: str, b: str) -> bool:
    """'A-2601' = '2601' (the wing letter written on one side only); 'B-1801' ≠ 'C-1801'; '904' ≠ '2'."""
    x, y = norm_unit(a), norm_unit(b)
    if x == y:
        return True
    if (_composite_tail(a) and _composite_tail(a) == y) or (_composite_tail(b) and _composite_tail(b) == x):
        return True                                  # a composite code ending in the same unit number
    dx, dy = re.sub(r"\D", "", x), re.sub(r"\D", "", y)
    lx, ly = re.sub(r"\d", "", x), re.sub(r"\d", "", y)
    # 'A-2601' = '2601' (letter on one side only); '1801B' = 'B-1801' (same letter, before or after)
    return bool(dx) and dx == dy and (not lx or not ly or sorted(lx) == sorted(ly))


def norm_floor(v: str) -> str:
    t = ascii_digits(v or "").lower()
    if re.search(r"ground|तळ|stilt|podium", t):
        return "0" if "ground" in t or "तळ" in t else t.strip()
    m = re.search(r"(\d+)", t)
    return m.group(1) if m else t.strip()


_SQM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*m(?:trs?|eters?|etres?)?\b|sq\.?\s*mt|square\s*met|चौ\.?\s*मी|चौ\.?\s*मि|"
                  r"चौरस\s*मी|चौ\.?\s*मीटर)", re.I)
_SQF = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*f(?:ee)?t|square\s*f|चौ\.?\s*फू|चौ\.?\s*फु)", re.I)


def area_sqft(v: str) -> Optional[float]:
    t = ascii_digits(v or "")
    m = _SQF.search(t)
    if m:
        return float(m.group(1).replace(",", ""))
    m = _SQM.search(t)
    if m:
        return round(float(m.group(1).replace(",", "")) * 10.7639, 1)
    return None


def same_area(a: str, b: str) -> bool:
    x, y = area_sqft(a), area_sqft(b)
    if x is None or y is None:
        return " ".join(a.lower().split()) == " ".join(b.lower().split())
    return abs(x - y) <= max(1.0, 0.015 * max(x, y))         # '371 sq ft' = '34.47 sq m'


def same_text(a: str, b: str) -> bool:
    k = lambda s: re.sub(r"[^a-z0-9ऀ-ॿ]", "", ascii_digits(s).lower())
    if k(a) == k(b) or compare(a, b, "society", "society") == "same":
        return True
    # 'Kurla' / 'Kurla-East' / 'Kurla (West)' / 'कुर्ला TP03' = 'Kurla': the same place in
    # either script, with a qualifier added — compared word by word on the script-independent skeleton
    from app.igr import skeleton
    sk = lambda s: [w for w in (skeleton(x) for x in re.split(r"[\s,\-()/]+", ascii_digits(s).lower()) if x) if len(w) >= 2]
    wa, wb = sk(a), sk(b)
    return bool(wa and wb) and (wa[0] == wb[0]) and (set(wa) <= set(wb) or set(wb) <= set(wa))


# ── land parcels ────────────────────────────────────────────────────────────────
_PART_AFTER = r"\s*\(?\s*(?:part|pt\.?|p\.(?=\s|\))|पार्ट|पैकी|पै\.|हिस्सा\s*पैकी)"


def _parcel_part(text: str, main: str, sub: Optional[str]) -> bool:
    t = ascii_digits(text or "")
    pat = re.escape(main) + (r"\s*/\s*" + r"\s*/\s*".join(re.escape(x) for x in sub.split("/")) if sub else "")
    return bool(re.search(r"(?<![\d/])" + pat + r"(?![\d/])" + _PART_AFTER, t, re.I))


def parcels_from(text: str) -> List[dict]:
    out = []
    for kind, main, sub in land_refs(text or ""):
        out.append({"kind": {"cts": "CTS", "sv": "Survey", "old": "Old Survey"}.get(kind, kind), "main": main,
                    "sub": sub or "", "part": _parcel_part(text, main, sub)})
    return out


# ── the model ───────────────────────────────────────────────────────────────────
def _ev(d: dict, field: str = "") -> dict:
    prop = d.get("property") or {}
    page = (prop.get("_pages") or {}).get(field) if field else None
    quote = (prop.get("_quotes") or {}).get(field) if field else ""
    return evidence(d.get("file"), page or d.get("pages"), d.get("ref"), d.get("doc_type"), quote,
                    page_exact=bool(page))


def authority(d: dict) -> int:
    """The standing of a document as evidence of a FACT about the property, as a lawyer weighs it:
    registered instrument / Index-II (5) > executed agreement / development / charge (4) > draft, revenue record,
    approval, title certificate, allotment (3) > receipts, letters, bills, plans (2) > valuation report, anything
    else (1)."""
    cat, typ, st = d.get("category") or "", (d.get("doc_type") or "").lower(), d.get("execution_status") or ""
    if re.search(r"valuation|मूल्यांकन|mulyankan|moolyankan", typ):
        return 1
    if cat == "registration_record" or re.search(r"index\s*-?\s*i{1,2}\b|सूची\s*क्र|dast\s*goshwara", typ):
        return 5
    if cat in TITLE_CATEGORIES:
        if st == "registered":
            return 5
        return 3 if st == "draft" else 4
    if cat in ("revenue_record", "approval", "title_certificate") or re.search(r"allotment|property\s*card|7\s*/\s*12", typ):
        return 3
    if cat in ("receipt", "plan", "other"):
        return 2
    return 1


def _date(d: dict) -> Optional[date]:
    return parse_date(d.get("exec_date")) or parse_date(d.get("reg_date"))


def build_property_model(docs: List[dict], party_table: dict) -> dict:
    flags: List[dict] = []
    # identity papers (Aadhaar, PAN, passport, KYC) carry the PERSON's address, never the property's description
    prop_docs = [d for d in docs if d.get("property") and not _IDENTITY.search(d.get("doc_type") or "")
                 and d.get("category") != "identity"]
    unit_docs = [d for d in prop_docs if d.get("level") == "premises"]
    all_prop_docs = prop_docs

    # ── unit ──
    fb = {"unit_type": FieldBuilder("unit_type", same=lambda a, b: _unit_kind(a) == _unit_kind(b)),
          "unit_no": FieldBuilder("unit_no", same=same_unit,
                                  prefer=lambda a, b: b if (_composite_tail(a) and not _composite_tail(b)) else a),
          "floor": FieldBuilder("floor", same=lambda a, b: norm_floor(a) == norm_floor(b)),
          "wing": FieldBuilder("wing", same=lambda a, b: norm_unit(a) == norm_unit(b)),
          "carpet_area": FieldBuilder("carpet_area", same=same_area),
          "builtup_area": FieldBuilder("builtup_area", same=same_area)}
    for d in unit_docs:
        p = d["property"]
        for k in fb:
            fb[k].add(p.get(k), _ev(d, k), authority(d))
    unit = {k: v.result() for k, v in fb.items()}
    for k, r in unit.items():
        flags += field_flags(f"unit {k.replace('_', ' ')}", r)
    for k in ("unit_no", "floor", "carpet_area"):
        if unit[k]["status"] == "not_stated":
            flags.append(flag("missing", "unit", f"Unit {k.replace('_', ' ')} is not stated in any document of the unit."))
    if not unit_docs:
        flags.append(flag("missing", "unit", "No document describing the unit itself (agreement, deed, allotment) was "
                                             "found in the papers."))

    # ── building / society / project names ──
    names: List[dict] = []                      # [{"name", "as": {building, society, project, old}, "evidence"}]
    bno = FieldBuilder("building_no", same=lambda a, b: norm_unit(a) == norm_unit(b))
    for d in all_prop_docs:
        p = d["property"]
        if d.get("level") == "premises":
            bno.add(p.get("building_no"), _ev(d, "building_no"), authority(d))
        for field, label in (("building_name", "building"), ("society_name", "society"), ("project_name", "project"),
                             ("old_name", "old name")):
            v = (p.get(field) or "").strip()
            if not v or (d.get("level") != "premises" and label != "old name"):
                continue
            for part in re.split(r"\s*(?:\(|\)|formerly known as|earlier known as|previously known as|known as|"
                                 r"उर्फ|पूर्वीचे)\s*", v, flags=re.I):
                part = part.strip(" ,.-'\"‘’")
                if len(part) < 3 or re.fullmatch(r"(?i)(?:prop\.?|proposed|part|bldg\.?\s*no\.?\s*\d+)", part):
                    continue
                as_ = "old name" if re.search(r"formerly|earlier|previously|old|पूर्वी", v[:v.find(part)], re.I) \
                    and v.find(part) > 0 else label
                hit = next((n for n in names if same_text(n["name"], part)), None)
                if hit:
                    hit["as"].add(as_)
                    hit["evidence"].append(_ev(d, field))
                else:
                    names.append({"name": part, "as": {as_}, "evidence": [_ev(d, field)]})
    building = {"number": bno.result(),
                "names": [{"name": n["name"], "used_as": sorted(n["as"]), "sources": [cite(e) for e in n["evidence"]],
                           "documents": len({e.get('doc_ref') for e in n["evidence"]})} for n in
                          sorted(names, key=lambda n: -len(n["evidence"]))]}
    flags += field_flags("building number", building["number"])
    if not names:
        flags.append(flag("missing", "building", "No building / society / project name is stated in the documents of "
                                                 "the unit."))

    # ── land ──
    lf = {k: FieldBuilder(k, same=same_text) for k in LAND_FIELDS}
    parcels: Dict[tuple, dict] = {}
    for d in all_prop_docs:
        p = d["property"]
        for k in LAND_FIELDS:
            lf[k].add(p.get(k), _ev(d, k), authority(d))
        text = " ".join(str(p.get(k) or "") for k in ("survey_no", "hissa_no", "cts_no", "plot_no", "text"))
        if p.get("survey_no") and not re.search(r"survey|s\.\s*no|सर्वे|स\.\s*न|गट|gat", text, re.I):
            text = "Survey No. " + str(p.get("survey_no")) + (" Hissa No. " + str(p["hissa_no"]) if p.get("hissa_no") else "") \
                + " " + text
        if p.get("cts_no") and not re.search(r"c\.?\s*t\.?\s*s|सी\.?\s*टी|सि\.?\s*टी|न\.\s*भू", text, re.I):
            text = "CTS No. " + str(p.get("cts_no")) + " " + text
        # whether a parcel is WHOLE or PART is decided by the title deeds and revenue records; a valuation report,
        # receipt or letter that omits '(part)' says nothing about the extent
        decisive = d.get("category") in TITLE_CATEGORIES or d.get("category") == "revenue_record" or \
            re.search(r"property\s*card|7\s*/\s*12|record\s+of\s+rights|index\s*-?\s*ii", d.get("doc_type") or "", re.I)
        found = []
        if p.get("parcels"):                         # given by the evidence reader, each with its own page + quote
            for x in p["parcels"]:
                main, _, sub = str(x["number"]).replace(" ", "").partition("/")
                ev_x = evidence(d.get("file"), x.get("page") or d.get("pages"), d.get("ref"), d.get("doc_type"),
                                x.get("quote") or "", page_exact=bool(x.get("page")))
                found.append(({"kind": _KIND_LABEL.get(x["kind"].lower().replace(" ", "_"), x["kind"].title() or "Parcel"),
                               "main": main, "sub": sub, "part": x.get("part")}, ev_x))
        else:
            found = [(pc, _ev(d)) for pc in parcels_from(text)]
        for pc, pev in found:
            key = (pc["kind"], pc["main"], pc["sub"])
            e = parcels.setdefault(key, {**pc, "key": key, "part_in": [], "whole_in": [], "unit_docs": 0, "evidence": [],
                                         "part_other": []})
            if decisive and pc["part"]:
                e["part_in"].append(cite(pev))
            elif not pc["part"] and d.get("category") in TITLE_CATEGORIES:
                e["whole_in"].append(cite(pev))        # only a deed shows a parcel is WHOLE (a property card always
                #                                        records the whole CTS, whatever share the subject has)
            elif pc["part"]:
                e["part_other"].append(cite(pev))
            e["evidence"].append(pev)
            if d.get("level") == "premises":
                e["unit_docs"] += 1
    land = {k: v.result() for k, v in lf.items()}
    for k, r in land.items():
        flags += field_flags(f"land {k}", r)
    plist = []
    for e in sorted(parcels.values(), key=lambda e: (e["kind"], int(e["main"]) if e["main"].isdigit() else 0, e["sub"])):
        if e["part_in"] and not e["whole_in"]:
            status = "part"
        elif e["whole_in"] and not e["part_in"]:
            status = "whole" if not e["part_other"] else "unclear"
        elif not e["part_in"] and not e["whole_in"]:
            status = "part" if e["part_other"] else "not stated in a title document"
        else:
            status = "unclear"
        if status == "unclear" and not e["part_in"]:
            e["part_in"] = e["part_other"]
        plist.append({"kind": e["kind"], "number": e["main"] + ("/" + e["sub"] if e["sub"] else ""), "extent": status,
                      "in_unit_documents": e["unit_docs"] > 0, "documents": len({x.get("doc_ref") for x in e["evidence"]}),
                      "sources": [cite(x) for x in e["evidence"]], "_evidence": e["evidence"]})
        if status == "unclear":
            both_deeds = bool(e["whole_in"]) and not set(e["part_in"]) <= set(e["part_other"])
            flags.append(flag("conflict" if both_deeds else "verify", "land",
                              f"{e['kind']} {e['main']}{'/' + e['sub'] if e['sub'] else ''} is described "
                                                  f"as PART in {', '.join(e['part_in'][:2])} but without 'part' in "
                                                  f"{', '.join(e['whole_in'][:2])} — confirm the extent.", e["evidence"]))
    unit_parcels = [p for p in plist if p["in_unit_documents"]]
    for p in plist:
        if not p["in_unit_documents"] and unit_parcels and p["kind"] != "Old Survey":
            flags.append(flag("verify", "land", f"{p['kind']} {p['number']} appears only in land / project documents, "
                                                f"not in the description of the unit — confirm whether it is part of the "
                                                f"land on which the unit stands.", p["_evidence"]))
    if not plist:
        flags.append(flag("missing", "land", "No survey / hissa / CTS / plot number is stated in the documents."))
    if any(p["extent"] == "part" for p in unit_parcels):
        flags.append(flag("verify", "land", "The unit stands on PART of the land parcel(s) marked 'part': other portions "
                                            "of the same parcel belong to other holders, so a search entry naming the "
                                            "same parcel is not by itself an entry of this property."))

    # ── developer, holder, scheme ──
    developer = _people(docs, party_table, ("promoter",), levels=("premises", "project"))
    holder_doc = _latest_unit_deed(docs)
    holder = None
    if holder_doc:
        holder = {"document": cite(_ev(holder_doc)), "date": holder_doc.get("exec_date") or holder_doc.get("reg_date") or "",
                  "doc_no": holder_doc.get("doc_no") or "", "status": holder_doc.get("execution_status") or "",
                  "holders": [x for x in _side(holder_doc, party_table, ("purchaser", "donee", "releasee", "lessee"))]}
        if holder_doc.get("execution_status") in ("draft", "executed_not_registered"):
            flags.append(flag("verify", "title", f"The latest document of the unit ({cite(_ev(holder_doc))}) is "
                                                 f"{holder_doc['execution_status'].replace('_', ' ')} — the purchasers named "
                                                 f"in it are not yet the registered holders.", [_ev(holder_doc)]))
    else:
        flags.append(flag("missing", "title", "No deed / agreement of the unit itself is in the papers; the present "
                                              "holder cannot be established from the documents."))

    # ── title history recited in the papers ──
    history = []
    for d in sorted([d for d in docs if d.get("category") in TITLE_CATEGORIES],
                    key=lambda d: (_date(d) or date(1900, 1, 1), d.get("doc_no") or "")):
        history.append({"date": d.get("exec_date") or d.get("reg_date") or "", "type": d.get("doc_type"),
                        "level": d.get("level"), "doc_no": d.get("doc_no") or "", "office": d.get("sro") or "",
                        "copy_furnished": bool(d.get("furnished")),
                        "status": d.get("execution_status") or "",
                        "from": _side(d, party_table, ("vendor", "owner", "promoter", "lessor", "donor", "releasor",
                                                       "mortgagor", "society")),
                        "to": _side(d, party_table, ("purchaser", "lessee", "donee", "releasee", "mortgagee")),
                        "confirming": _side(d, party_table, ("confirming",)),
                        "source": cite(_ev(d))})
        if not (d.get("exec_date") or d.get("reg_date")):
            flags.append(flag("missing", "title", f"{d.get('doc_type')} ({cite(_ev(d))}) has no date in the papers.",
                              [_ev(d)]))
    # the title history the documents RECITE ('the land was sold by X to Y by deed No. …'), each step with its page
    for d in docs:
        for rc in d.get("recitals") or []:
            if not isinstance(rc, dict) or not (rc.get("from") or rc.get("to") or rc.get("doc_no")):
                continue
            ev = evidence(d.get("file"), rc.get("page") or d.get("pages"), d.get("ref"), d.get("doc_type"),
                          rc.get("quote") or "", page_exact=bool(rc.get("page")))
            who = lambda names: [({"id": e["id"], "name": e["name"]} if e else {"id": None, "name": n})
                                 for n in names or [] if n for e in [find_party(party_table, n)]]
            history.append({"date": rc.get("date") or "", "type": rc.get("event") or "recited transaction",
                            "level": "recital", "doc_no": rc.get("doc_no") or "", "office": "",
                            "copy_furnished": False, "status": "recited", "from": who(rc.get("from")),
                            "to": who(rc.get("to")), "confirming": [], "source": cite(ev)})

    for p in plist:
        p.pop("_evidence", None)
    return {"unit": unit, "building": building, "land": {**land, "parcels": plist},
            "project": {"developers": developer, "scheme_signals": scheme_signals(docs)},
            "present_holder": holder, "title_history": history, "flags": flags}


def _side(d: dict, table: dict, families) -> List[dict]:
    out = []
    for p in d.get("parties") or []:
        if role_family(p.get("role")) in families:
            entry = find_party(table, _party_part(p.get("name")))
            label = {"id": entry["id"], "name": entry["name"]} if entry else {"id": None, "name": p.get("name")}
            if label not in out:
                out.append(label)
    return out


def _party_part(name: str) -> str:
    from .names import split_agent, split_alias
    return split_alias(split_agent(name)[0])[0]


def _people(docs, table, families, levels) -> List[dict]:
    seen = {}
    for d in docs:
        if d.get("level") not in levels:
            continue
        for x in _side(d, table, families):
            k = x["id"] or x["name"]
            seen.setdefault(k, {**x, "sources": []})["sources"].append(cite(_ev(d)))
    return list(seen.values())


def _latest_unit_deed(docs: List[dict]) -> Optional[dict]:
    deeds = [d for d in docs if d.get("level") == "premises" and d.get("category") == "title_transfer"
             and any(role_family(p.get("role")) in ("purchaser", "donee", "releasee") for p in d.get("parties") or [])]
    if not deeds:
        return None
    return max(deeds, key=lambda d: (_date(d) or date(1900, 1, 1), d.get("execution_status") == "registered"))


_SCHEMES = [
    ("Slum rehabilitation (SRA) scheme", r"slum|\bs\.?r\.?a\b|झोपडपट्टी|letter\s+of\s+intent|\bloi\b|annexure\s*-?\s*ii"),
    ("MHADA layout / redevelopment", r"m\.?h\.?a\.?d\.?a|म्हाडा|housing\s+and\s+area\s+development"),
    ("CIDCO lease land", r"c\.?i\.?d\.?c\.?o|सिडको"),
    ("Society redevelopment (existing members re-housed)",
     r"permanent\s+alternate\s+accommodation|\bp\.?a\.?a\.?a\b|redevelop|पर्यायी\s*जागेचा"),
    ("Leasehold land", r"\blease\b|लीज|भाडेपट्टा"),
    ("Tenanted / cessed building", r"cess|tenant|tenancy|भाडेकरू"),
    ("Industrial / commercial unit", r"\bgala\b|गाळा|industrial|shop\b|office\s+(?:no|premises)"),
]


def scheme_signals(docs: List[dict]) -> List[dict]:
    """What kind of scheme / transaction the papers show. With the evidence reader, this is what the documents
    themselves STATE (with page + quote) — never guessed. Only the old saved readings fall back to keyword signals,
    labelled as such."""
    stated: Dict[str, dict] = {}
    for d in docs:
        for s in d.get("scheme") or []:
            k = " ".join(str(s["type"]).lower().split())
            e = stated.setdefault(k, {"scheme": s["type"], "documents": [], "count": 0, "basis": "stated in the documents"})
            e["documents"].append(cite(evidence(d.get("file"), s.get("page") or d.get("pages"), d.get("ref"),
                                                d.get("doc_type"), s.get("quote") or "", page_exact=bool(s.get("page")))))
            e["count"] += 1
    if stated or any(d.get("page_exact") for d in docs):
        return list(stated.values())
    out = []
    for label, rx in _SCHEMES:
        hits = [d for d in docs if re.search(rx, " ".join([d.get("doc_type") or "", (d.get("property") or {}).get("text") or "",
                                                         " ".join(p.get("name") or "" for p in d.get("parties") or [])]), re.I)]
        if hits:
            out.append({"scheme": label, "documents": [cite(_ev(d)) for d in hits[:5]], "count": len(hits)})
    unit_deeds = [d for d in docs if d.get("level") == "premises" and d.get("category") == "title_transfer"]
    if any(role_family(p.get("role")) == "promoter" for d in unit_deeds for p in d.get("parties") or []):
        out.append({"scheme": "Sale by developer / promoter (new unit)",
                    "documents": [cite(_ev(d)) for d in unit_deeds if any(role_family(p.get("role")) == "promoter"
                                                                          for p in d.get("parties") or [])][:5], "count": 1})
    if len([d for d in unit_deeds if d.get("execution_status") != "draft"]) >= 2:
        out.append({"scheme": "Resale (unit transferred more than once)",
                    "documents": [cite(_ev(d)) for d in unit_deeds][:5], "count": len(unit_deeds)})
    return out
