"""Build legalinfo_ai.json in the portal's existing schema (same top-level keys as legal-info.json)."""
import re

TRANSFER_TYPES = ("title_transfer", "development", "encumbrance", "approval", "title_certificate",
                  "revenue_record", "society")
NON_DEED_TYPES = ("approval", "title_certificate", "revenue_record")
# the promoter's / authority's allotment of the unit (CIDCO / MHADA lottery flats): part of the chain of title
_ALLOT = re.compile(r"\ballot(?:ment|ted)\b(?!.*\b(?:cancel|surrender))", re.I)


_REP = ("partner", "director", "authoris", "authoriz", "signatory", "attorney", "representative", "through",
        "karta", "secretary", "chairman", "constituted")


def _survey_value(sv: str, hs: str) -> str:
    """'102, 88' + '26, 13' → '102/26, 88/13'; '16' + '4' → '16/4'."""
    svs = [x.strip() for x in re.split(r"\s*(?:,|&|and)\s*", sv or "") if x.strip()]
    hss = [x.strip() for x in re.split(r"\s*(?:,|&|and)\s*", hs or "") if x.strip()]
    if len(svs) > 1 and len(svs) == len(hss):
        return ", ".join(f"{a}/{b}" for a, b in zip(svs, hss))
    return "/".join(x for x in (sv, hs) if x)


def _land_value(sp: dict) -> str:
    """'Survey No.125 Hissa Nos. 4 (pt) and 7/2 (pt); Survey No.126 Hissa Nos. 1 (pt) …' → '125/4, 125/7/2, 126/1, …'.
    Two separate lists (survey '125, 126' + hissa '4, 7/2, 1, 2') cannot be paired by position, so the parsed survey /
    hissa pairs of the subject are used whenever they carry hissas."""
    from .igr import subject_land_refs
    refs = [(m, s) for k, m, s in subject_land_refs(sp) if k == "sv"]
    if any(s for m, s in refs):
        return ", ".join(f"{m}/{s}" if s else m for m, s in refs)
    return _survey_value(sp.get("survey_no", ""), sp.get("hissa_no", ""))


_GENERIC_WORDS = {"shri", "shree", "smt", "private", "limited", "through", "company", "corporation", "maharashtra",
                  "developers", "builders", "industrial", "development", "and"}


def _signs_for(rep_role: str, names) -> bool:
    """'Representative of M/s. OLIVE BUILDERS & DEVELOPERS' signs for Olive Builders only — never for the other side."""
    tail = re.split(r"\b(?:of|for)\b", rep_role.lower(), 1)
    if len(tail) < 2:
        return False
    words = {w for n in names for w in re.findall(r"[a-z]{4,}", n.lower())} - _GENERIC_WORDS
    return any(w in tail[1] for w in words)


def _party(d, roles):
    """Names with the wanted roles; persons who only sign for a firm are shown as 'through …', not as parties."""
    ps = d.get("parties", [])
    main = [p["name"] for p in ps if any(r in (p.get("role") or "").lower() for r in roles)
            and not any(x in (p.get("role") or "").lower() for x in _REP)]
    if not main:
        return ""
    reps = [p["name"] for p in ps if any(x in (p.get("role") or "").lower() for x in _REP)
            and (_signs_for(p.get("role") or "", main) or (
                not re.search(r"\b(?:of|for)\b", (p.get("role") or "").lower())
                and any(r in (p.get("role") or "").lower() for r in roles + ("partner", "director"))))]
    txt = " & ".join(main)
    return txt + (" through " + " & ".join(reps) if reps and len(main) == 1 else "")


def _first_party(d, idx):
    ps = d.get("parties", [])
    return ps[idx]["name"] if len(ps) > idx else ""


def _sro_split(s: str):
    m = re.search(r"^(.*?)[\s\-]*(\d+)\s*$", s or "")
    return (m.group(1).strip(), m.group(2)) if m else ((s or "").strip(), "")


def build_legalinfo(analysis: dict, drafted: dict) -> dict:
    sp = analysis["subject_property"]
    owner = analysis["current_owner"]
    docs = analysis["documents"]
    ig = analysis["igr"]

    docchain, other_docs, society_docs = [], [], []
    n = 0
    for d in docs:
        if d.get("recited_only"):
            continue                      # not perused, nothing identifying it — appears only as an observation
        roles = " ".join((p.get("role") or "").lower() for p in d.get("parties") or [])
        if re.search(r"owner|lessor|principal|licensor", roles) and re.search(r"developer|lessee|attorney|licensee", roles):
            # development agreement / POA / lease: owners (lessor, principal) on one side, developer (lessee, attorney) on the other
            seller = _party(d, ("owner", "lessor", "principal", "licensor", "confirming")) or _first_party(d, 0)
            buyer = _party(d, ("developer", "lessee", "attorney", "licensee")) or _first_party(d, 1)
        else:
            seller = _party(d, ("vendor", "seller", "transferor", "builder", "promoter", "developer", "owner",
                                "mortgagor", "donor", "executant", "lessor", "releasor", "assignor")) \
                or _first_party(d, 0) or d.get("issued_by", "")
            buyer = _party(d, ("purchaser", "transferee", "allottee", "buyer", "mortgagee", "donee", "claimant", "lessee",
                               "releasee", "assignee")) or _first_party(d, 1)
        sro_name, sro_num = _sro_split(d.get("sro_display", ""))
        oc = d.get("original_or_copy", "")
        if d["category"] == "encumbrance" and re.search(r"reconvey|re-conveyance|release of|satisfaction|discharge|रिकन्व्हेन्स",
                                                       d.get("doc_type", ""), re.I):
            # the lender releases the charge to the borrower: Mortgagee (Releasor) → Mortgagor (Releasee)
            seller = _party(d, ("mortgagee",)) or seller
            buyer = _party(d, ("mortgagor",)) or buyer
        allotment = d.get("furnished") and _ALLOT.search(d.get("doc_type", "")) and d["category"] not in TRANSFER_TYPES
        if d["category"] in NON_DEED_TYPES or allotment:
            # certificates / approvals / 7-12 / allotment letters: issuing authority and holder(s) / allottee(s) only
            seller = d.get("issued_by") or _party(d, ("issuing", "authority", "advocate", "talathi")) or ""
            buyer = _party(d, ("allottee", "addressee", "holder", "occupant", "owner", "applicant", "developer", "promoter"))
        if d["category"] == "society":
            # share certificate: the society issues it to ALL its holders / members (joint holders), by role — never
            # 'the second party in the list' (that is the first holder only, the society being listed first)
            holders = _party(d, ("holder", "member", "allottee", "owner", "purchaser", "transferee"))
            if holders:
                buyer = holders
                seller = _party(d, ("society", "issuing", "authority")) or seller
        if seller and buyer and seller.strip().lower() == buyer.strip().lower():
            seller = ""                   # same name on both sides = not stated in the document
        if d["category"] in TRANSFER_TYPES or allotment:
            n += 1
            yr = d.get("doc_year") or (d.get("exec_date", "")[-4:])
            docchain.append({
                "num": f"DC-{n}/{yr}" if yr else f"DC-{n}",
                "date": d.get("exec_date") or d.get("reg_date", ""), "seen": oc, "org": oc,   # only Index-II date known
                "reg": ("Draft - yet to be executed and registered" if d.get("draft") else
                        "Registered" if d.get("doc_number") and d["category"] not in NON_DEED_TYPES else "Not Registered"),
                "type": d.get("doc_type", ""), "type_other": "",
                "seller": seller, "purchaser": buyer,
                "regdate": d.get("reg_date", ""), "sro": sro_name, "sro_num": sro_num,
                "fees": d.get("registration_fee", ""),
                "doc_no": ((d.get("reference_no") or "") if d["category"] in NON_DEED_TYPES or allotment else
                           (("Share Certificate No. " + re.sub(r"^\s*(?:share\s*)?certificate\s*(?:no\.?|number)?\s*[:\-]?\s*", "",
                                                               d["reference_no"], flags=re.I))
                            if d["category"] == "society" and d.get("reference_no")
                            and re.search(r"share", d.get("doc_type", ""), re.I) else
                            d.get("reference_no", "") if d["category"] == "society" and not d.get("doc_no_display")
                            else d.get("doc_no_display", ""))),       # society registration No. PLR (VSI) HSG/(TC) 2454/2024
                "consideration_amt": d.get("consideration", ""), "market_value": d.get("market_value", ""),
                "stamp_duty": d.get("stamp_duty", ""), "property_description": d.get("property_text", ""),
                "level": d.get("level", ""),          # land / project / premises — used by the chain-of-title node
                # found in the IGR search data supplied: only these belong in the year-wise Search Report
                "in_search": bool(d.get("in_search")),
                # mortgages only: 'No release found in search' / 'Released by Reconveyance Doc No. …' /
                # 'Not found in the search data supplied' — decided by the backend, the report must not re-guess it
                "charge_status": d.get("charge_status", ""),
                "released_by": d.get("released_by", ""),
            })
        else:
            row = {"num": f"OD-{len(other_docs) + 1}", "date": d.get("exec_date") or d.get("reg_date", ""),
                   "seen": oc, "org": oc, "type": d.get("doc_type", ""),
                   "ref_no": d.get("reference_no") or d.get("doc_no_display", ""),
                   "auth_type": "Issuing Authority" if d.get("issued_by") else "",
                   "auth_name": d.get("issued_by", "")}
            (society_docs if d["category"] == "society" else other_docs).append(row)

    blank_other = {"num": "", "date": "", "seen": "", "org": "", "type": "", "ref_no": "", "auth_type": "", "auth_name": ""}
    # only real search-fee receipts: an entry with no GRN, receipt number or fee (e.g. the date of an Index-II print)
    # would print as 'GRN  Receipt  dated 21/09/2026 Rs..'
    receipts = [r for r in (ig.get("receipts") or []) if r.get("grn") or r.get("receipt_no") or r.get("fee")]
    fee = sum(int(r["fee"]) for r in receipts if str(r.get("fee", "")).isdigit())
    rec = receipts[0] if receipts else {}
    society = [d for d in docs if d["category"] == "society"]
    share_cert = next((d for d in society if re.search(r"share\s*cert", d.get("doc_type", ""), re.I)), None)
    society_reg = next((d for d in society if re.search(r"registration|नोंदणी", d.get("doc_type", ""), re.I)), None)
    oc_doc = next((d for d in docs if re.search(r"occupa|completion", d["doc_type"], re.I)), None)
    vet = {"complete_address": sp.get("full_description", ""), "cersai_search": "", "cersai_id": "",
           "creation_date": "", "notice_of_intimation": "", "date_of_registration": "",
           "amount_of_facility_for_noi_registration": "", "name_and_no_of_sro_office": "",
           "date_of_noi_registration": "", "date_of_document": ""}
    owner_doc = next((d for d in docs if d.get("ref") == owner.get("via_ref")), None)
    sro_main = (owner_doc or {}).get("sro_display") or next(
        (d.get("sro_display") for d in reversed(docs) if d.get("sro_display") and d["category"] == "title_transfer"), "")
    searches = []
    survey_is_cts = bool(sp.get("survey_no")) and bool(re.search(r"c\.?\s*t\.?\s*s", sp.get("full_description", ""), re.I)) \
        and not re.search(r"survey|s\.\s*no|gat|hissa", sp.get("full_description", ""), re.I)
    for typ, val in (("CTS No.", sp.get("cts_no") or (sp.get("survey_no") if survey_is_cts else "")),
                     ("Survey No.", "" if survey_is_cts else _land_value(sp))):
        if val:
            searches.append({"type": typ, "type_value": val, "village": sp.get("village", ""), "taluka": sp.get("taluka", ""),
                             "district": sp.get("district", ""), "fees": str(fee or "") if not searches else ""})
    if not searches and sp.get("plot_no"):
        # CIDCO / MIDC / MHADA plots: the plot (and sector) is the land search key
        plot = re.sub(r"^\s*(?:residential\s+)?plot\s*(?:no\.?|number)?\s*[:\-]?\s*", "", sp["plot_no"], flags=re.I)
        searches.append({"type": "Plot No.", "type_value": plot, "village": sp.get("village", ""),
                         "taluka": sp.get("taluka", ""), "district": sp.get("district", ""), "fees": str(fee or "")})
    remarks = [{"remarks": drafted.get("opinion_paragraph", "")}] + [{"remarks": f} for f in analysis["flags"]]

    return {
        "owners_num": str(len(owner["names"])),
        "owners": [{"name": x} for x in owner["names"]],
        "defendants": [{"name": "", "address": ""} for _ in owner["names"]],
        "current_address": "",
        "status_of_owner": ("Owner / Vendor (resale) - Agreement for Sale to the proposed purchaser(s) yet to be executed "
                            "and registered" if owner.get("deal") == "resale" else
                            "Proposed Purchaser(s) - Agreement for Sale yet to be executed and registered"
                            if (owner_doc or {}).get("draft") else
                            "Proposed Purchaser(s) (Allottees) - Agreement for Sale yet to be executed and registered"
                            if owner.get("allotment_only") else ""),
        # what the proposed purchasers hold today: 'draft' agreement or only the promoter's 'allotment' letter
        "deal_basis": "allotment" if owner.get("allotment_only") else ("draft" if (owner_doc or {}).get("draft") else ""),
        # resale | new_sale | registered — the report layout follows it; never re-guessed by the report generator
        "deal_type": owner.get("deal", ""),
        "proposed_purchasers": [{"name": x} for x in owner.get("proposed_purchasers", [])],
        "prop_address": sp.get("full_description", ""),
        "prop_surround_east": "", "prop_surround_west": "", "prop_surround_north": "", "prop_surround_south": "",
        "138m_bank_account_name": "", "138m_bank_account_num": "", "138m_bank_account_type": "", "138m_bank_name": "",
        "138m_bank_branch": "", "138m_bank_ifsc": "", "138m_bank_chq_presented_date": "", "138m_chq_bank_name": "",
        "138m_chq_bank_branch": "", "138m_chq_bank_ifsc": "", "138m_chq_dated": "", "138m_chq_num": "",
        "138m_chq_amount": "", "138m_returned_memo_date": "", "138m_returned_memo_endorsement": "",
        "searches": searches or [{"type": "", "type_value": "", "village": "", "taluka": "", "district": "", "fees": ""}],
        "search_for": sp.get("unit_type", ""),
        "search_years": str(ig["period_to"] - ig["period_from"]),
        "search_from_year": str(ig["period_from"]),
        "search_to_year": str(ig["period_to"]),
        "search_sro": f"Sub-Registrar of Assurances, {ig['sro_place']}" if ig.get("sro_place") else sro_main,  # e.g. "Kurla No.1 to 5 and Mumbai No.27 to 31"
        "search_sro_num_from": ig.get("sro_num_from", ""), "search_sro_num_to": ig.get("sro_num_to", ""),
        "search_sro_others_specify": "",
        "search_fee_paid": "Yes" if fee else "NA",
        "search_fee_paid_details": "; ".join(
            " ".join(x for x in (f"GRN {r['grn']}" if r.get("grn") else "", f"Receipt {r['receipt_no']}" if r.get("receipt_no") else "",
                                 f"dated {r['date']}" if r.get("date") else "", f"Rs.{r['fee']}" if r.get("fee") else "") if x)
            for r in receipts),
        "search_by_clerk_name": "",
        "search_on_date": rec.get("date", ""),
        "search_receipt_num": rec.get("receipt_no", ""),
        "search_cert_truecopy_req": "Yes",
        "search_cert_truecopy_applied": "", "search_cert_truecopy_applied_appdate": "",
        "search_cert_truecopy_applied_appname": "", "search_cert_truecopy_received": "",
        "prop_purchase_from": "Previous Owner" if owner.get("from") else "",
        "prop_purchase_from_details": " & ".join(owner.get("from", [])),
        "search_titleclear": drafted.get("certificate_opinion", ""),
        "search_titleclear_advfirmname": "", "search_titleclear_lsrdate": "",
        "property_status": "", "property_muncipal_jurisdiction_name": "",
        "society_formed": "Yes" if society else "NO",
        "society_formed_details": next((p["name"] for d in society for p in d.get("parties", [])
                                        if re.search(r"society|सोसा", p.get("name", ""), re.I)), ""),
        "society_reg_date": (society_reg or {}).get("exec_date", ""),
        # share certificate fields ONLY from an actual share certificate (never from the society registration)
        "society_cert_issued": "Yes" if share_cert else "",
        "society_cert_no": (share_cert or {}).get("reference_no", ""),
        "society_cert_dateofissue": (share_cert or {}).get("exec_date", ""),
        "society_cert_distinct_no_from": "", "society_cert_distinct_no_to": "",
        "oc_received": "Yes" if oc_doc else "No",
        "oc_receipt_date": oc_doc.get("exec_date", "") if oc_doc else "",
        "confirming_party": "YES" if any("confirm" in (p.get("role") or "").lower() for d in docs for p in d.get("parties", [])) else "NO",
        "chain_of_doc_missing": "YES" if any(not d["furnished"] for d in docs if d["category"] in TRANSFER_TYPES) else "NO",
        "owner_death": "YES" if any("death" in d["doc_type"].lower() for d in docs) else "NO",
        "specific_doc": "NO",
        "chain_num": str(len(docchain)),
        "vetting_prime_security_immovable_assets": [dict(vet, name_and_no_of_sro_office=sro_main)],
        "vetting_prime_security_movable_assets": [{"nature_of_asset": "", "nature_of_asset_other": "", "asset_charged_transport_dept": ""}],
        "vetting_collateral_security_immovable_assets": [dict(vet)],
        "vetting_collateral_security_movable_assets": [{"nature_of_asset": "", "nature_of_asset_other": "", "asset_charged_transport_dept": ""}],
        "vetting_guarantors": [{"name": "", "network": ""}],
        "vetting_docs_ref": [{"name": "", "description": ""}],
        "vetting_legal_docs_ref": [{"name": "", "date": "", "stamp_duty_paid_date": "", "stamp_duty_paid_amount": "", "remarks": ""}],
        "vetting_observation_remarks": remarks,
        "docchain": docchain,
        "other_docs": other_docs or [dict(blank_other)],
        "society_docs": society_docs or [dict(blank_other)],
        "common_docs": [dict(blank_other)],
        "bank_precautions": [{"type": "NOI", "details": "Notice of Intimation u/s 89B Registration Act within 30 days of mortgage"},
                             {"type": "CERSAI", "details": "Register the charge on CERSAI"},
                             {"type": "LEIN", "details": "Bank's charge to be noted with Society / Builder"}]
                            + [{"type": "OTHER", "details": p} for p in drafted.get("precautions", [])],
        "bank_precautions_others": "",
    }
