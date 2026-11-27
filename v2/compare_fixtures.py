"""Measure Stage 2 against the expectations recorded in tests/cases/*/case.json (NOT lawyer-finalised — written
during earlier development; a sanity check only, never a target to tune to). No API use.

  python -m v2.compare_fixtures
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
# case → fixture map: a LOCAL file (client names — never committed)
FIXTURE_OF = json.load(open(os.path.join(ROOT, "sample_out", "v2_fixture_map.json"), encoding="utf-8")) \
    if os.path.exists(os.path.join(ROOT, "sample_out", "v2_fixture_map.json")) else {}


def main():
    tot = {"found": 0, "must": 0, "wrong": 0, "must_not": 0}
    for case, fx in FIXTURE_OF.items():
        p = os.path.join(ROOT, "sample_out", case, "v2_stage2", "stage2.json")
        f = os.path.join(ROOT, "backend", "tests", "cases", fx, "case.json")
        if not (os.path.exists(p) and os.path.exists(f)):
            continue
        res = json.load(open(p, encoding="utf-8"))
        exp = json.load(open(f, encoding="utf-8"))["expect"]
        tier = {d["doc"]: d["tier"] for d in res["decisions"]}
        must, must_not = list(exp.get("must_find", {})), exp.get("must_not_find", [])
        found = [d for d in must if tier.get(d) == "confirmed"]
        flagged = [d for d in must if tier.get(d) == "possible"]
        missed = [d for d in must if tier.get(d) not in ("confirmed", "possible")]
        wrong = [d for d in must_not if tier.get(d) == "confirmed"]
        extra = sorted(d for d, t in tier.items() if t == "confirmed" and d not in must)
        tot["found"] += len(found)
        tot["must"] += len(must)
        tot["wrong"] += len(wrong)
        tot["must_not"] += len(must_not)
        print(f"{case:18} expected {len(must):2}: confirmed {len(found):2}, possible {len(flagged)}, missed {missed} | "
              f"must-not confirmed {wrong} | other confirmed {len(extra)}")
    print(f"\nTOTAL expected entries confirmed {tot['found']}/{tot['must']}; must-not entries confirmed {tot['wrong']}/{tot['must_not']}")


if __name__ == "__main__":
    main()
