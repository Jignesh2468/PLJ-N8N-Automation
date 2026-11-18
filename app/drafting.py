"""Stage 4: AI drafting of the legal text from the verified analysis, with a number guard."""
import json
import re

from . import llm, prompts
from .common import ascii_digits

QUESTIONS = {
    "A1": "Name of the Borrower with the Address:",
    "A2": "Name of the Person offering Mortgage with Parentage/ constitution and address",
    "A3": "Details of Property to be mortgaged:",
    "A4": "As per Title Deed:",
    "A5": "As per present position:",
    "B1": "Details of the title deeds/documents (including Link Deeds/Parent Deeds) to be deposited for creation of the mortgage (with full particulars regarding nature of document, date of execution and details of registration).",
    "B2": "Whether the Certified copies have been obtained from the Registrar’s Office.",
    "B3": "Whether the documents in hand are compared with the certified copy and whether the documents given raise any doubt or suspicion?",
    "B4": "Whether particulars of registration as number & date and page particulars as given in the title deed shown to the counsel tally with particulars as stated in the records of the registrar’s office?",
    "B5": "Whether the particulars of registration as number & date and page particulars as given in the title deed tally with particulars as stated in Certified copy as obtained from the registrar’s office?",
    "B6": "Whether the photographs of parties as affixed in conveyance deed/title deed tally with the photograph seen in the certified copy as obtained from the registrar’s office?",
    "B7": "Whether contents of the as given in the title deed tally verbatim with the contents as stated in the certified copy obtained from the registrars office? If not, variation be specified, what is its effect?",
    "B8": "Whether the property has been mutated in the name of the person offering the mortgage?",
    "B9": "Whether equitable mortgage can be created at the place where the branch disbursing the loan is situated?",
    "B10": "Whether there is any bar under local law for creation of the mortgage of the property to be mortgaged? (In some states there are legal restrictions on creation of the mortgage of agricultural property for non-agricultural purpose)",
    "B11": "Whether there are any restrictions regarding sale of property to be mortgaged? (In some states there are restrictions for sale of property to residents outside the State)",
    "B12": "Whether all the approvals, clearance/sanctions required for creation of the mortgage have been obtained? If not obtained approvals what are such sanctions, approvals and clearances yet to be obtained?",
    "B13": "Whether the property is ancestral /or under joint ownership or the minor is having interest in the property? If so, its effect thereof.",
    "B14": "Whether the property to be mortgage has been acquired under Land Acquisition Act, 1894?",
    "B15": "Whether Urban Land (Ceiling and Regulation) Act 1976 is applicable in the state where the property is located?",
    "B16": "In case of leasehold property, whether permission/NOC from the lessor is required for creation of mortgage? Whether permission of the Lessor/NOC is obtained?",
    "B17": "What is the rate of sharing of unearned income with lessor, the event of sale of the property?",
    "B18": "Whether copy of title deed favoring lessor (other than Govt.) is made available to examine the validity of the lease?",
    "B19": "Whether terms & conditions given in the lease deed have been complied with? If any condition is violated, effect thereof?",
    "B20": "Whether any permission of Income Tax Authorities/ Assessing officer is required under the provision of Income Tax Act for creation of mortgage or any certificate is to be submitted to the bank to show that no dues are outstanding to the Income Tax Department.",
    "B21": "In respect of agricultural land, whether land is declare surplus or under consolidation of holdings?",
    "B22": "Whether certified copies of Revenue Records has been obtained and examined to confirm that no dues are outstanding towards the mortgagor?",
    "B23": "Whether the Property to be mortgaged is SARFAESI compliant",
    "B24": "Whether the owner’s title valid, absolute, clear and marketable one?",
    "B25": "Based on the documents available, whether the owner’s title is saleable?",
    "B26": "Based on the documents available, whether the owners title transferable?",
}


def _compact(analysis: dict) -> dict:
    """What the drafter sees: everything factual, nothing it does not need."""
    keep = ("ref", "sr", "doc_type", "category", "level", "exec_date", "reg_date", "doc_no_display", "sro_display",
            "parties", "issued_by", "reference_no", "consideration", "market_value", "stamp_duty",
            "registration_fee", "property_text", "furnished", "verified_in_igr", "original_or_copy",
            "parent_ref", "key_terms", "short_description", "igr_mismatch")
    docs = [{k: d.get(k) for k in keep if d.get(k) not in (None, "", [], False) or k == "furnished"}
            for d in analysis["documents"]]
    ig = analysis["igr"]
    return {
        "report_date": analysis["report_date"], "bank": analysis["bank"], "applicant": analysis["applicant"],
        "subject_property": analysis["subject_property"], "current_owner": analysis["current_owner"],
        "documents": docs,
        "igr_search": {"period_from": ig["period_from"], "period_to": ig["period_to"],
                       "years_with_data": ig["coverage_years"], "missing_years": ig["missing_years"],
                       "entries": [{k: e.get(k) for k in ("class", "doc_type_en", "docno", "year", "sro",
                                                          "exec_date", "reg_date", "parties_en", "property_en",
                                                          "consideration", "reason", "discrepancy")}
                                   for e in ig["entries"]],
                       "receipts": ig["receipts"]},
        "encumbrances": analysis["encumbrances"], "flags": analysis["flags"],
        "discrepancies": analysis["discrepancies"],
    }


def _numbers(s: str):
    return {n.lstrip("0") for n in re.findall(r"\d{4,}", ascii_digits(s).replace(",", "")) if n.lstrip("0")}


def number_guard(draft: dict, source: dict) -> list:
    """Every 4+ digit number in the draft must exist in the source facts (dates, doc nos, amounts)."""
    src = ascii_digits(json.dumps(source, ensure_ascii=False)).replace(",", "")
    src_nums = _numbers(src)
    # dates in source also allow their dd/mm/yyyy parts
    statutes = {"1882", "1894", "1899", "1908", "1925", "1956", "1960", "1961", "1963", "1966", "1970", "1976",
                "2002", "2013", "2016"}  # Transfer of Property, Registration, Stamp, MRTP, ULC, SARFAESI, RERA etc.
    unknown = sorted(n for n in _numbers(json.dumps(draft, ensure_ascii=False))
                     if n not in src_nums and n not in statutes)
    return unknown


def draft(analysis: dict) -> dict:
    facts = _compact(analysis)
    text = ("QUESTIONS:\n" + json.dumps(QUESTIONS, ensure_ascii=False) +
            "\n\nANALYSIS:\n" + json.dumps(facts, ensure_ascii=False))
    out = llm.call_json("draft_title", prompts.TITLE_SYSTEM, [llm.text_block(text)], max_tokens=16000)
    if not isinstance(out, dict):
        raise llm.LLMError("drafting returned non-object")
    for k in ("subject_to", "defects", "chain_paragraphs", "documents_to_obtain", "precautions",
              "search_observations", "oral_confirmations"):
        out[k] = [x for x in (out.get(k) or []) if x]
    out["special_report"] = {q: (out.get("special_report") or {}).get(q, "") or
                             "Not established from the documents/search data provided." for q in QUESTIONS}
    unknown = number_guard(out, facts)
    out["unverified_numbers"] = unknown
    return out
