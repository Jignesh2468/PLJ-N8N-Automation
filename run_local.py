"""Run the full pipeline on local files — no Supabase needed (uses the real Claude API unless --mock).

Usage:
  python run_local.py --prop 115_A.pdf 115_B.pdf --search SearchResult*.xls "JIGNESH RECEIPT.pdf" \
         --out local_out --lead 513 --name "JAS Financial Services" --bank "Bank of India" --branch "Vasai"
"""
import argparse
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import pipeline  # noqa: E402


class LocalStore:
    def __init__(self, root: str):
        self.root = root

    def _p(self, path):
        return os.path.join(self.root, path)

    def list(self, prefix):
        d = self._p(prefix)
        return sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))) if os.path.isdir(d) else []

    def get(self, path):
        with open(self._p(path), "rb") as f:
            return f.read()

    def put(self, path, data, mime):
        os.makedirs(os.path.dirname(self._p(path)), exist_ok=True)
        with open(self._p(path), "wb") as f:
            f.write(data)
        return self._p(path)

    def delete(self, path):
        try:
            os.remove(self._p(path))
        except FileNotFoundError:
            pass

    def get_json(self, path):
        try:
            with open(self._p(path), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prop", nargs="+", required=True, help="property documents (pdf/docx/jpg/png)")
    ap.add_argument("--search", nargs="*", default=[], help="IGR xls/xlsx exports and e-search receipt PDFs")
    ap.add_argument("--out", default="local_out")
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--name", default="")
    ap.add_argument("--bank", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--from-year", type=int)
    ap.add_argument("--to-year", type=int)
    ap.add_argument("--mode", default="full", choices=["full", "json", "reports"])
    ap.add_argument("--mock", help="python file defining mock(task, system, blocks) for offline tests")
    a = ap.parse_args()

    if a.mock:
        import importlib.util
        from app import llm
        spec = importlib.util.spec_from_file_location("mock", a.mock)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        llm.set_override(m.mock)

    prefix = f"local/x/{a.lead}"
    store = LocalStore(a.out)
    for sub, files in (("prop_docs", a.prop), ("search_docs", a.search)):
        dst = os.path.join(a.out, prefix, "uploads", sub)
        os.makedirs(dst, exist_ok=True)
        for pattern in files:
            for f in glob.glob(pattern) or [pattern]:
                shutil.copy(f, dst)

    lead = {"leadid": a.lead, "custname": a.name}
    opts = {"prefix": prefix, "mode": a.mode, "bank_name": a.bank, "bank_branch": a.branch,
            "search_from_year": a.from_year, "search_to_year": a.to_year}
    out = pipeline.run_pipeline(lead, opts, store, progress=lambda s: print("·", s))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
