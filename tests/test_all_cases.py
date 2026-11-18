"""Every real case received so far, run end-to-end through the backend (IGR parse → match → chain → legalinfo).

The AI is replaced by two deliberately BAD mocks, and both must give the same correct result:
  * "misses"  – the AI calls every IGR row E (not a match)
  * "greedy"  – the AI calls every row A or B (a match)
So the result depends on the code checks, not on the AI being right.

Run:  python tests/test_all_cases.py            (all cases)
      python tests/test_all_cases.py 520        (one case)
Add a new case: copy tests/cases/<lead>/ with igr/*.xls + case.json (subject, documents, expected results).
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import common, english, extract, igr, legalinfo, llm, merge  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "cases")
english._ai_map = lambda strings: {}          # no network: fallback transliteration only


def mock(mode, subject):
    unit = re.sub(r"\D", "", subject.get("unit_no", ""))

    def f(task, system, blocks, **kw):
        if task != "igr_classify":
            return {}
        rows = json.loads(blocks[0]["text"].split("IGR ROWS:\n")[1])
        out = []
        for r in rows:
            if mode == "misses":
                cls = "E"
            else:
                t = common.ascii_digits(r["property_description"])
                cls = "A" if unit and re.search(rf"(?<!\d){unit}(?!\d)", t) else "B"
            out.append({"id": r["id"], "class": cls, "doc_type_en": english.DOC_TYPES.get(r["document_type"].strip(), r["document_type"]),
                        "parties": []})
        return {"rows": out}
    return f


def run(case_dir, mode):
    case = json.load(open(os.path.join(case_dir, "case.json"), encoding="utf-8"))
    rows, files = [], []
    igr_dir = os.path.join(HERE, case["igr_dir"], "igr") if case.get("igr_dir") else os.path.join(case_dir, "igr")
    for f in sorted(glob.glob(os.path.join(igr_dir, "*.xls"))):
        files.append(os.path.basename(f))
        rows += igr.parse_igr_file(open(f, "rb").read(), os.path.basename(f))
    rows = igr.normalise_rows(rows)
    # per-file extractions (as the pipeline reads them): files about another property are set aside first
    foreign = []
    if case.get("extractions"):
        kept, foreign = extract.split_foreign(case["extractions"], case.get("borrower", ""))
        case["documents"] = [d for ex in kept for d in ex["documents"]]
        case.setdefault("subject", extract.merge_subjects_ranked(kept))
    case.setdefault("chain_parties", sorted({p["name"] for d in case["documents"] for p in d.get("parties") or []}))
    case["_foreign"] = foreign
    subject = igr.set_row_stats(dict(case["subject"]), rows)
    cands, _ = igr.prefilter(rows, igr.build_keys(subject, case["chain_parties"]))
    llm.set_override(mock(mode, subject))
    classes = igr.classify(cands, subject, case["chain_parties"])
    by_id = {r["id"]: r for r in rows}
    for c in classes.values():                               # the real AI also returns doc types; the mock may not
        if not c.get("doc_type_en"):
            c["doc_type_en"] = english.DOC_TYPES.get(by_id[c["id"]]["docname_raw"].strip(), by_id[c["id"]]["docname_raw"])
    an = merge.build_analysis({"leadid": case["lead"], "custname": "TEST"},
                              {"search_from_year": 1996, "search_to_year": 2026, "foreign_files": foreign},
                              [{"documents": case["documents"]}], subject, rows, classes, case["receipts"], files)
    an = english.to_english(an)
    li = english.to_english(legalinfo.build_legalinfo(an, {"opinion_paragraph": ""}))
    found = {f"{by_id[c['id']]['docno']}/{by_id[c['id']]['year']}": c["class"] for c in classes.values() if c["class"] in "AB"}
    an["_foreign"] = foreign
    return case["expect"], found, an, li


def check(lead, mode):
    exp, found, an, li = run(os.path.join(HERE, lead), mode)
    errs = []
    for doc, cls in exp.get("must_find", {}).items():
        if found.get(doc, "-") not in cls:
            errs.append(f"{doc} should be {cls}, got {found.get(doc, 'not matched')}")
    if exp.get("no_other_AB"):
        extra = sorted(set(found) - set(exp.get("must_find", {})))
        if extra:
            errs.append(f"false matches: {extra}")
    wrong = [d for d in exp.get("must_not_find", []) if d in found]
    if wrong:
        errs.append(f"must not be matched: {wrong}")
    got_foreign = " ".join(f["file"] for f in an.get("_foreign") or [])
    for f in exp.get("foreign_files", []):
        if f not in got_foreign:
            errs.append(f"{f} relates to another property — must be set aside (set aside: '{got_foreign}')")
    if "foreign_files" in exp and not exp["foreign_files"] and got_foreign:
        errs.append(f"no file should be set aside as another property, got '{got_foreign}'")
    enc = {e["doc_no"]: e for e in an["encumbrances"]}
    for m, rel in exp.get("released", {}).items():
        if (enc.get(m) or {}).get("released_by") != rel:
            errs.append(f"mortgage {m} should be released by {rel}, got {(enc.get(m) or {}).get('status', 'not listed')}")
    for m in exp.get("subsisting", []):
        if not (enc.get(m) or {}).get("status", "").startswith("No release"):
            errs.append(f"mortgage {m} should be subsisting, got {(enc.get(m) or {}).get('status', 'not listed')}")
    for no in exp.get("not_in_search", []):
        hits = [d for d in li["docchain"] if re.search(rf"(?<!\d){no.split('/')[0]}\D+{no.split('/')[1]}", d["doc_no"])]
        if not hits or any(d["in_search"] for d in hits):
            errs.append(f"{no} is not in the IGR data — must be marked in_search=false (found {len(hits)} docchain rows)")
    drafts = [f for f in an["flags"] if "UNEXECUTED DRAFT" in f]
    if "max_draft_flags" in exp and len(drafts) > exp["max_draft_flags"]:
        errs.append(f"{len(drafts)} draft observations (one draft read in parts must give one)")
    for txt in exp.get("no_flag_contains", []):
        if any(txt in f for f in an["flags"]):
            errs.append(f"flag must not contain '{txt}'")
    for txt in exp.get("flag_contains", []):
        if not any(txt in f for f in an["flags"]):
            errs.append(f"an observation must contain '{txt}'")
    for name in exp.get("share_holders", []):
        row = next((d for d in li["docchain"] if re.search(r"share\s*cert", d["type"], re.I)), None)
        if not row or name.lower() not in row["purchaser"].lower():
            errs.append(f"share certificate holders '{(row or {}).get('purchaser')}' should contain {name}")
    if re.search(r"GRN\s+Receipt|Rs\.\.|Rs\.\s*$", li.get("search_fee_paid_details", "")):
        errs.append(f"blank receipt in fee line: '{li['search_fee_paid_details']}'")
    for no, org in exp.get("original_or_copy", {}).items():
        row = next((d for d in li["docchain"] if no in d["doc_no"]), None)
        if not row or org.lower() not in row["org"].lower():
            errs.append(f"{no}: original/copy should be '{org}', got {(row or {}).get('org')}")
    if "land_value" in exp:
        got = next((s["type_value"] for s in li["searches"] if s["type"] == "Survey No."), "")
        if got != exp["land_value"]:
            errs.append(f"land line '{got}', expected '{exp['land_value']}'")
    for no, name in exp.get("releasor", {}).items():
        row = next((d for d in li["docchain"] if d["doc_no"] == no), None)
        if not row or name.lower() not in row["seller"].lower():
            errs.append(f"reconveyance {no}: releasor (seller) should be the lender {name}, got {(row or {}).get('seller')}")
    chain = json.dumps(li["docchain"], ensure_ascii=False)
    for txt in exp.get("not_in_chain", []):
        if txt in chain:
            errs.append(f"'{txt}' must not be in the chain of documents")
    blob = json.dumps(li, ensure_ascii=False)
    for txt in exp.get("no_text", []):
        if txt in blob:
            errs.append(f"'{txt}' must not appear in legalinfo")
    if exp["sro"] not in li["search_sro"]:
        errs.append(f"SRO '{li['search_sro']}' should contain '{exp['sro']}'")
    owners = " ".join(o["name"] for o in li["owners"])
    if exp["owner_contains"].lower() not in owners.lower():
        errs.append(f"owner '{owners}' should contain {exp['owner_contains']}")
    if exp.get("owner_excludes") and exp["owner_excludes"].lower() in owners.lower():
        errs.append(f"owner '{owners}' must not contain {exp['owner_excludes']} (identifier / witness, not a purchaser)")
    if exp.get("seller_excludes") and exp["seller_excludes"].lower() in li["prop_purchase_from_details"].lower():
        errs.append(f"purchased from '{li['prop_purchase_from_details']}' must not contain {exp['seller_excludes']}")
    for no in exp.get("in_search", []):
        hits = [d for d in li["docchain"] if re.search(rf"(?<!\d)0*{no.split('/')[0]}\D+(?:{no.split('/')[1]}|{no.split('/')[1][2:]})\b", d["doc_no"])]
        if not hits or not all(d["in_search"] for d in hits):
            errs.append(f"{no} is in the IGR data — must be in_search=true (found {len(hits)} docchain rows)")
    for no in exp.get("no_doc_no", []):
        if any(no in d["doc_no"] for d in li["docchain"]):
            errs.append(f"misread number {no} must not be listed as a deed")
    if "deal" in exp and li.get("deal_type") != exp["deal"]:
        errs.append(f"deal type '{li.get('deal_type')}', expected '{exp['deal']}'")
    if exp.get("proposed_contains") and exp["proposed_contains"].lower() not in \
            " ".join(p["name"] for p in li.get("proposed_purchasers", [])).lower():
        errs.append(f"proposed purchasers {li.get('proposed_purchasers')} should contain {exp['proposed_contains']}")
    for no, name in exp.get("developer", {}).items():
        row = next((d for d in li["docchain"] if no in d["doc_no"]), None)
        if not row or name.lower() not in row["purchaser"].lower():
            errs.append(f"{no}: developer should be {name}, got {(row or {}).get('purchaser')}")
    for rx, most in exp.get("max_count", {}).items():
        n = sum(bool(re.search(rx, d["type"])) for d in li["docchain"])
        if n > most:
            errs.append(f"{n} documents of type /{rx}/ listed — one instrument read several times must be listed once")
    if exp.get("society_no") and not any(exp["society_no"] in d["doc_no"] for d in li["docchain"]):
        errs.append(f"society registration No. {exp['society_no']} not shown")
    is_draft = li["status_of_owner"].lower().startswith("proposed")
    if is_draft != exp["draft"]:
        errs.append(f"draft agreement detection {is_draft}, expected {exp['draft']}")
    pm = any(f.startswith("Project / land mortgage") for f in an["flags"])
    if "project_mortgage" in exp and pm != exp["project_mortgage"]:          # absent: left to the AI
        errs.append(f"project mortgage flag {pm}, expected {exp['project_mortgage']}")
    if exp.get("no_share_cert") and li["society_cert_no"]:
        errs.append(f"fake share certificate {li['society_cert_no']}")
    if exp.get("one_lease") and sum(bool(re.search(r"\blease", d["type"], re.I)) for d in li["docchain"]) != 1:
        errs.append("lease deed listed more than once")
    if any(re.search(r"[ऀ-ॿ]", json.dumps(v, ensure_ascii=False)) for v in li.values()):
        errs.append("Marathi text left in legalinfo")
    if any(d["seller"] and d["seller"] == d["purchaser"] for d in li["docchain"]):
        errs.append("same party on both sides of a document")
    return errs, found


if __name__ == "__main__":
    leads = sys.argv[1:] or sorted(os.listdir(HERE))
    bad = 0
    for lead in leads:
        for mode in ("misses", "greedy"):
            errs, found = check(lead, mode)
            bad += bool(errs)
            print(f"{'OK  ' if not errs else 'FAIL'} lead {lead} (AI {mode}): matched {sorted(found)}")
            for e in errs:
                print("      -", e)
    assert not bad, f"{bad} case run(s) failed"
    print("ALL CASES PASSED")
