"""Compare the REAL pipeline output for each sample case (sample_out/<case>/) with the hand-verified facts in its
regression fixture (tests/cases/<fixture>/case.json). Prints one block per case and writes sample_out/EVALUATION.md.

  python tests/eval_samples.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "sample_out")
CASES = os.path.join(HERE, "cases")
# sample folder → fixture whose facts were verified by hand from the same documents
FIXTURE = {"ashfak_kalaniya": "523", "bindumati_yadav": "550", "mahesh_ghopade": "ghorpade", "manaji_sindhe": "cidco_eb10",
           "meenakashi_pawar": "529", "pradyuman_chauhan": "pradyuman", "rahul_pathak": "530", "sandeep_najare": "549",
           "sarfaraj": "544", "sheetal_mayekar": "mayekar", "vishaka_nambiar": "nambiar", "vishal_ekanath": "520"}


def words(s):
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower())}


def nkey(no):
    """'MBI28-21857-2026', '21857/2026', 'TNN-2/2326/2014' → '21857/2026'."""
    m = re.search(r"0*(\d+)\s*[/\-]\s*((?:19|20)\d{2})\s*$", str(no or ""))
    return f"{int(m.group(1))}/{m.group(2)}" if m else ""


SUB = "recheck" if "--recheck" in sys.argv else ""


def evaluate(lead, fx):
    an = json.load(open(os.path.join(OUT, lead, SUB, "legal_analysis_ai.json"), encoding="utf-8"))
    li = json.load(open(os.path.join(OUT, lead, SUB, "legalinfo_ai.json"), encoding="utf-8"))
    case = json.load(open(os.path.join(CASES, fx, "case.json"), encoding="utf-8"))
    exp = case["expect"]
    foreign_files = set(exp.get("foreign_files", []))
    docs = case.get("documents") or [d for e in case.get("extractions", []) for d in e["documents"]
                                     if d.get("file") not in foreign_files]
    subj = case.get("subject") or next((e["subject_property"] for e in case.get("extractions", [])
                                        if e["subject_property"].get("unit_no") and e["subject_property"].get("building_name") ==
                                        "Vivant D"), {})
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    got_unit = re.sub(r"\D", "", an["subject_property"].get("unit_no", ""))
    check("subject flat number", got_unit == re.sub(r"\D", "", subj.get("unit_no", "")),
          f"got '{an['subject_property'].get('unit_no')}', expected '{subj.get('unit_no')}'")
    owners = " ".join(o["name"] for o in li.get("owners", []))
    check("current owner", exp["owner_contains"].lower() in owners.lower(), f"got '{owners}'")
    if exp.get("owner_excludes"):
        check("owner excludes witness / other party", exp["owner_excludes"].lower() not in owners.lower(), owners)
    is_draft = li.get("status_of_owner", "").lower().startswith("proposed")
    check("draft / executed agreement", is_draft == exp["draft"], f"got draft={is_draft}, expected {exp['draft']}")
    if exp.get("deal"):
        check("deal type", li.get("deal_type") == exp["deal"], f"got {li.get('deal_type')}")
    # every registered deed number the fixture knows must be in the chain (read from the papers or the search)
    chain_nos = {nkey(d.get("doc_no_display") or d.get("doc_no_raw")) for d in an["documents"]} | \
                {nkey(d.get("doc_no")) for d in li.get("docchain", [])}
    want = {nkey(d.get("doc_no_raw") or (f"{d['doc_number']}/{d.get('doc_year', '')}" if d.get("doc_number") else ""))
            for d in docs if (d.get("doc_number") or d.get("doc_no_raw")) and str(d.get("doc_number")) != "0"
            and d.get("category") not in ("receipt", "registration_record")}
    want.discard("")
    want -= set(exp.get("no_doc_no", []))           # numbers the fixture records as misreads
    for no in exp.get("no_doc_no", []):
        check(f"misread {no} not listed as a deed", no not in chain_nos)
    missing = sorted(w for w in want if w not in chain_nos)
    check("registered deeds found", not missing, f"missing {missing}" if missing else f"{len(want)} deeds")
    # IGR classification
    cls = {f"{e['docno']}/{e['year']}": e["class"] for e in an["igr"]["entries"]}
    for no, allowed in exp.get("must_find", {}).items():
        check(f"IGR {no} matched as {allowed}", cls.get(no, "-") in allowed, f"got {cls.get(no, 'not matched')}")
    for no in exp.get("must_not_find", []):
        check(f"IGR {no} NOT matched", cls.get(no, "E") not in "AB", f"got {cls.get(no)}")
    ab = sorted(k for k, v in cls.items() if v in "AB")
    if exp.get("no_other_AB"):
        extra = [k for k in ab if k not in exp.get("must_find", {})]
        check("no false IGR matches", not extra, f"extra {extra}")
    # stray files / text that must not reach the chain
    flags = " ".join(an.get("flags", []))
    for f in exp.get("foreign_files", []):
        check(f"{f} set aside as another property", f in flags)
    chain = json.dumps(li.get("docchain", []), ensure_ascii=False)
    for t in exp.get("not_in_chain", []):
        check(f"'{t}' not in chain", t not in chain)
    if "project_mortgage" in exp:
        pm = any(f.startswith("Project / land mortgage") for f in an.get("flags", []))
        check("project mortgage flagged", pm == exp["project_mortgage"], f"got {pm}")
    for t in exp.get("flag_contains", []):
        check(f"observation '{t[:40]}'", t in flags)
    report_checks(lead, an, li, check)
    return checks, an, li, ab


def _text(path):
    import html as h
    t = open(path, encoding="utf-8").read()
    t = re.sub(r"(?s)<style.*?</style>", "", t)
    return re.sub(r"\s+", " ", h.unescape(re.sub(r"<[^>]+>", " ", t)))


def report_checks(lead, an, li, check):
    """The two HTML reports themselves: sections present, every matched deed shown in its year, nothing leaked."""
    base = os.path.join(OUT, lead, SUB)
    cot_p, sr_p = os.path.join(base, f"cot{lead}.html"), os.path.join(base, f"sr{lead}.html")
    if not (os.path.exists(cot_p) and os.path.exists(sr_p)):
        check("both HTML reports generated", False, "missing report file")
        return
    cot, sr = _text(cot_p), _text(sr_p)
    for sec in ("DOCUMENTS PERUSED", "TRANSACTION-WISE", "CURRENT IDENTIFIABLE TITLE POSITION", "CHAIN CONTINUITY STATUS",
                "SCHEDULE OF PROPERTY", "PRECAUTIONS"):
        check(f"chain: section {sec}", sec in cot)
    leaks = [w for w in ("undefined", "NaN", "[object Object]", " null ") if w in cot + sr]
    check("reports: no undefined / null / NaN text", not leaks, f"found {leaks}")
    check("reports: no untranslated Marathi", not re.search(r"[ऀ-ॿ]", cot + sr), "Devanagari text in report")
    check("search report: adverse-entry statement", re.search(r"No adverse document was identified|Entries requiring "
                                                             r"attention|No IGR search record was provided", sr) is not None)
    # one row per year of the period; a year not searched never says NIL
    fy, ty = int(li.get("search_from_year") or 0), int(li.get("search_to_year") or 0)
    if fy and ty:
        missing_rows = [y for y in range(fy, ty + 1) if not re.search(rf"\b{y}\b", sr)]
        check("search report: a row for every year", not missing_rows, f"no row for {missing_rows[:6]}")
        miss = set(an["igr"].get("missing_years") or [])
        bad = [y for y in miss if re.search(rf"\b{y}\b NIL\b", sr)]
        check("search report: unsearched years not shown as NIL", not bad, f"NIL for {bad}")
    # every deed matched in the search (A/B) is listed in the search report and in the chain
    ab = {f"{e['docno']}/{e['year']}" for e in an["igr"]["entries"] if e["class"] in "AB"}
    def nos(t):
        out = {f"{int(n)}/{y}" for n, y in re.findall(r"(?<!\d)0*(\d{1,6})\s*(?:[/\-]|\bof\b)\s*((?:19|20)\d{2})(?!\d)", t)}
        # '09915/06' — two-digit year of the old registers
        return out | {f"{int(n)}/20{y}" for n, y in re.findall(r"(?<![\d/])0*(\d{3,6})\s*/\s*(\d{2})(?![\d/])", t)}
    lost_sr = sorted(ab - nos(sr))
    check("search report: every matched deed listed", not lost_sr, f"not listed {lost_sr}")
    lost_cot = sorted(ab - nos(cot))
    check("chain: every matched deed listed", not lost_cot, f"not listed {lost_cot}")


def main():
    lines, total, passed = [], 0, 0
    for lead, fx in FIXTURE.items():
        if not os.path.exists(os.path.join(OUT, lead, SUB, "legalinfo_ai.json")):
            print(f"\n## {lead}: NO OUTPUT (failed or not run)")
            lines.append(f"## {lead}\n\nNo output — see sample_out/{lead}/log.txt\n")
            continue
        checks, an, li, ab = evaluate(lead, fx)
        ok = sum(c[1] for c in checks)
        total += len(checks)
        passed += ok
        print(f"\n## {lead} (fixture {fx}): {ok}/{len(checks)} checks")
        lines.append(f"## {lead} — {ok}/{len(checks)} checks (fixture {fx})\n")
        for name, good, detail in checks:
            print(f"  {'OK  ' if good else 'FAIL'} {name}  {detail if not good else ''}")
            lines.append(f"- {'✅' if good else '❌'} {name}{'' if good else ' — ' + detail}")
        print("  IGR A/B:", ab)
        lines.append(f"- IGR matched A/B: {', '.join(ab) or 'none'}\n")
    print(f"\nTOTAL {passed}/{total} checks")
    lines.append(f"**Total: {passed}/{total} checks passed**\n")
    open(os.path.join(OUT, "EVALUATION.md"), "w", encoding="utf-8").write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
