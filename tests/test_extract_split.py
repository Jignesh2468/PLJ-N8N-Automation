"""A PDF part whose answer is too long for one AI call is read again in halves (never fails the whole job).
Run:  python tests/test_extract_split.py"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pypdf import PdfWriter  # noqa: E402

from app import config, extract, llm  # noqa: E402

w = PdfWriter()
for _ in range(23):
    w.add_blank_page(width=200, height=200)
buf = io.BytesIO()
w.write(buf)
calls = []


def fake(task, system, blocks):
    p1, p2 = map(int, re.search(r"pages (\d+)-(\d+)", blocks[1]["text"]).groups())
    calls.append((p1, p2))
    if p2 - p1 + 1 > 6:                       # pretend the answer for more than 6 pages does not fit
        raise llm.TruncatedError("[extract_documents] output truncated")
    return {"documents": [{"ref": f"D{p1}", "doc_type": "Page set", "pages": f"{p1}-{p2}"}], "subject_property": {}}


llm.set_override(fake)
config.PDF_CHUNK_MAX_PAGES = 20
out = extract.extract_file(buf.getvalue(), "big.pdf")
covered = sorted(p for d in out["documents"] for a, b in [map(int, d["pages"].split("-"))] for p in range(a, b + 1))
assert covered == list(range(1, 24)), covered
print("calls:", calls)
print("ALL EXTRACT SPLIT TESTS PASSED")
