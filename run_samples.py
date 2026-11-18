"""Run every case in 'sample cases/' end to end (real AI) and render both HTML reports for each.

  python run_samples.py                       # all cases
  python run_samples.py "Sheetal Mayekar"     # one or more cases by folder name
  python run_samples.py --mock tests/mock_unit115.py --limit 1    # offline smoke test of this runner

Each case folder holds 'Property files' and a search folder (any name with 'search' / 'serach').
Output: sample_out/<case>/  legal_analysis_ai.json, legalinfo_ai.json, cot<case>.html, sr<case>.html, log.txt
        sample_out/SUMMARY.md  one line per case (owner, deal, continuity status, flags, errors)
Stops before spending anything when the AI key has no credit."""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from app import config, llm, pipeline  # noqa: E402
from run_local import LocalStore  # noqa: E402

WORKFLOW = os.path.join(ROOT, "n8n", "2 PLJ Chain of Title + Search Report.json")
RENDER = os.path.join(ROOT, "tools", "run_workflow2_offline.js")


def preflight():
    """One tiny call: no credit / bad key → stop now, not after reading half the PDFs."""
    import anthropic
    try:
        anthropic.Anthropic(api_key=config.anthropic_key()).messages.create(
            model=config.CLAUDE_MODEL, max_tokens=5, messages=[{"role": "user", "content": "ok"}])
    except Exception as e:
        sys.exit(f"Claude is not usable ({str(e)[:200]}). Top up / fix the key, then run again.")


def case_dirs(base, names):
    dirs = sorted(d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d))
    return [d for d in dirs if not names or os.path.basename(d) in names]


def stage(case_dir, out_dir, lead):
    """Copy the case into the local store layout the pipeline reads: {prefix}/uploads/prop_docs|search_docs."""
    prefix = f"local/x/{lead}"
    for sub in ("prop_docs", "search_docs"):
        shutil.rmtree(os.path.join(out_dir, prefix, "uploads", sub), ignore_errors=True)
        os.makedirs(os.path.join(out_dir, prefix, "uploads", sub))
    for d in glob.glob(os.path.join(case_dir, "*")):
        if not os.path.isdir(d):
            continue
        sub = "search_docs" if re.search(r"search|serach", os.path.basename(d), re.I) else "prop_docs"
        for f in glob.glob(os.path.join(d, "*")):
            if os.path.isfile(f) and os.path.getsize(f) > 0:
                shutil.copy(f, os.path.join(out_dir, prefix, "uploads", sub))
    return prefix


def render(out_dir, lead, li_path):
    r = subprocess.run(["node", RENDER, WORKFLOW, li_path], cwd=out_dir, env={**os.environ, "CASE": str(lead)},
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("report rendering failed: " + (r.stderr or r.stdout)[-500:])


def continuity(cot_path):
    t = open(cot_path, encoding="utf-8").read()
    m = re.search(r'class="status">([^<]+)<', t)
    return m.group(1) if m else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="case folder names (default: all)")
    ap.add_argument("--base", default=os.path.join(ROOT, "sample cases"))
    ap.add_argument("--out", default=os.path.join(ROOT, "sample_out"))
    ap.add_argument("--mock", help="python file defining mock(task, system, blocks) — offline runner test only")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    if a.mock:
        import importlib.util
        spec = importlib.util.spec_from_file_location("mock", a.mock)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        llm.set_override(m.mock)
    else:
        preflight()

    dirs = case_dirs(a.base, set(a.cases))
    if a.limit:
        dirs = dirs[:a.limit]
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for i, d in enumerate(dirs, 1):
        name = os.path.basename(d)
        lead = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
        out_dir = os.path.join(a.out, lead)
        os.makedirs(out_dir, exist_ok=True)
        log = open(os.path.join(out_dir, "log.txt"), "w", encoding="utf-8")
        t0 = time.time()
        print(f"[{i}/{len(dirs)}] {name}", flush=True)
        try:
            prefix = stage(d, out_dir, lead)
            res = pipeline.run_pipeline({"leadid": lead, "custname": name},
                                        {"prefix": prefix, "mode": "json", "upload": False}, LocalStore(out_dir),
                                        progress=lambda s: (log.write(s + "\n"), log.flush(), print("   ·", s, flush=True)))
            an, li = res["_result"]["analysis"], res["_result"]["legalinfo"]
            for fn, obj in (("legal_analysis_ai.json", an), ("legalinfo_ai.json", li)):
                json.dump(obj, open(os.path.join(out_dir, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            render(out_dir, lead, os.path.join(out_dir, "legalinfo_ai.json"))
            rows.append({"case": name, "owner": " & ".join(o["name"] for o in li.get("owners", []) if o.get("name")),
                         "deal": li.get("deal_type", ""), "docs": len(li.get("docchain", [])),
                         "status": continuity(os.path.join(out_dir, f"cot{lead}.html")), "flags": len(an.get("flags", [])),
                         "secs": int(time.time() - t0), "error": ""})
        except Exception as e:
            log.write(traceback.format_exc())
            rows.append({"case": name, "owner": "", "deal": "", "docs": 0, "status": "-", "flags": 0,
                         "secs": int(time.time() - t0), "error": str(e)[:160]})
            print("   ERROR:", e, flush=True)
        finally:
            log.close()

    lines = ["| Case | Owner (current) | Deal | Chain docs | Continuity | Flags | Time | Error |",
             "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['owner']} | {r['deal']} | {r['docs']} | {r['status']} | {r['flags']} | {r['secs']}s | {r['error']} |"
              for r in rows]
    open(os.path.join(a.out, "SUMMARY.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\nAI calls:", llm.usage())


if __name__ == "__main__":
    main()
