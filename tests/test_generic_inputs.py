"""Bundles arrive in any shape. The pipeline must cope with what real cases contain:
  * a stray agreement of ANOTHER property in the property files (set aside, never in the chain or the subject)
  * an earlier report / tracker / CSV saved in the search folder (not IGR data)
  * an Index-II or a bank's list of documents in the search folder (read as a document, not as a fee receipt)
Run:  python tests/test_generic_inputs.py"""
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pypdf import PdfWriter  # noqa: E402

from app import english, extract, igr, llm, pipeline  # noqa: E402
from run_local import LocalStore  # noqa: E402

english._ai_map = lambda strings: {}          # no network


def ex(unit, building, village, docs, file):
    return {"subject_property": {"unit_no": unit, "building_name": building, "village": village},
            "documents": [dict(d, file=file) for d in docs]}


def deed(no, seller, buyer, unit):
    return {"ref": "D1", "doc_type": "Agreement for Sale", "category": "title_transfer", "level": "premises",
            "doc_number": no, "doc_year": "2018", "exec_date": "01/01/2018", "furnished": True,
            "parties": [{"name": seller, "role": "Vendor"}, {"name": buyer, "role": "Purchaser"}],
            "property_text": f"Flat No. {unit}"}


# 1. a stray file of another flat in another building and village is set aside; the borrower's flat stays
ours1 = ex("2304", "Vivant D", "Bhayanderpada", [deed("2326", "Lodha Developers", "Manavjeet Singh", "2304")], "a.pdf")
ours2 = ex("2304", "Vivant-D Lodha Splendora", "Bhayanderpada", [deed("11813", "Manavjeet Singh", "Sheetal Mayekar", "2304")], "b.pdf")
stray = ex("1202", "Aaradhya One", "Chembur", [deed("90", "Monica Soni", "Champaklal Chheda", "1202")], "Sale_Agreement_1.pdf")
land = ex("", "Vivant B, C & D", "Bhayanderpada", [], "oc.pdf")
kept, foreign = extract.split_foreign([land, stray, ours1, ours2], "Sheetal Mayekar")
assert [f["file"] for f in foreign] == ["Sale_Agreement_1.pdf"], foreign
assert stray not in kept and land in kept and ours1 in kept and ours2 in kept
assert extract.merge_subjects_ranked(kept)["unit_no"] == "2304"
# ties go to the borrower's property; one property only → nothing is set aside
kept, foreign = extract.split_foreign([stray, ours2], "Sheetal Mayekar")
assert [f["file"] for f in foreign] == ["Sale_Agreement_1.pdf"], foreign
assert extract.split_foreign([ours1, ours2], "")[1] == []
# the same building with a misread unit number is NOT another property
misread = ex("2340", "Vivant D", "Bhayanderpada", [deed("1", "x", "y", "2340")], "c.pdf")
assert extract.split_foreign([ours1, ours2, misread], "")[1] == []
print("OK  stray file of another property set aside")

# 2. tables that are not IGR index exports give no rows
report = "<html><table><tr><th>Sr.</th><th>Name of Document</th><th>Date of Document</th></tr>" \
         "<tr><td>01.</td><td>Share Certificate</td><td>—</td></tr></table></html>"
assert igr.parse_igr_file(report.encode(), "chain_of_title_523.html") == []
assert igr.parse_igr_file(b"Run,Status\n1,ok\n", "Tracker - Run Log.csv") == []
print("OK  earlier reports / trackers in the search folder are not IGR data")


# 3. pipeline: an Index-II in the search folder is read as a document; a challan with a GRN stays a receipt
def pdf_with_text(text):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    s = DecodedStreamObject()
    s.set_data(("BT /F1 10 Tf 20 250 Td (" + text + ") Tj ET").encode())
    page[NameObject("/Contents")] = w._add_object(s)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


read = []


def fake(task, system, blocks):
    if task != "extract_documents":
        return {"rows": []} if task == "igr_classify" else {}
    name = re.search(r"File: ([^.\n]+\.pdf)", " ".join(b.get("text", "") for b in blocks)).group(1)
    read.append(name)
    doc = deed("16367", "M/s. Vinayak Associate", "Pradyuman Chauhan", "603")
    if name.startswith("INDEX"):
        doc = dict(doc, doc_type="Index-II", category="registration_record")
    return {"subject_property": {"unit_no": "603", "building_name": "Vinayak Heights", "village": "Ghatkopar"},
            "documents": [doc], "discrepancies": []}


tmp = tempfile.mkdtemp()
try:
    store = LocalStore(tmp)
    pre = "local/x/1/uploads"
    store.put(f"{pre}/prop_docs/agreement.pdf", pdf_with_text("Agreement"), "application/pdf")
    store.put(f"{pre}/search_docs/INDEX_2_16367_2025.pdf", pdf_with_text("16367 Generated Through eSearch Module 05/09/2025"),
              "application/pdf")
    store.put(f"{pre}/search_docs/eChallan.pdf", pdf_with_text("CHALLAN MTR Form Number-6 GRN MH012647975202627U Date 25/10/2026"),
              "application/pdf")
    store.put(f"{pre}/search_docs/chain_of_title_1.html", report.encode(), "text/html")
    llm.set_override(fake)
    out = pipeline.run_pipeline({"leadid": 1, "custname": "Pradyuman Chauhan"},
                                {"prefix": "local/x/1", "mode": "json", "upload": False}, store)
    assert "INDEX_2_16367_2025.pdf" in read and "eChallan.pdf" not in read, read
    an = store.get_json("local/x/1/legal_analysis_ai.json") or {}
    assert not an or "chain_of_title_1.html" not in json.dumps(an.get("igr", {}).get("files", [])), an.get("igr")
    print("OK  Index-II in the search folder read as a document; challan kept as receipt; old report ignored")
finally:
    llm.set_override(None)
    shutil.rmtree(tmp, ignore_errors=True)

# 4. parent links of any shape (a loop A↔B, a grandchild, a parent that does not exist) never drop a document
from app.merge import order_documents  # noqa: E402
mk = lambda r, p, dt: {"ref": r, "parent_ref": p, "exec_date": dt, "category": "development", "doc_type": r}
docs = [mk("JDA", "SUPP", "17/09/2013"), mk("SUPP", "JDA", "17/09/2013"), mk("A", "", "01/01/2010"),
        mk("IDX", "B", "01/01/2012"), mk("B", "A", "01/01/2011"), mk("ORPH", "GONE", "01/01/2015")]
assert sorted(d["ref"] for d in order_documents(docs)) == sorted(["JDA", "SUPP", "A", "IDX", "B", "ORPH"])
print("OK  no document lost through looping / deep parent links")

print("ALL GENERIC INPUT TESTS PASSED")
