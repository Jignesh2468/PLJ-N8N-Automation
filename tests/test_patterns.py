"""Property types and file layouts not yet seen in a real lead, checked in advance.
Run:  python tests/test_patterns.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import igr, legalinfo, merge  # noqa: E402

bad = 0


def check(name, ok, got=""):
    global bad
    bad += not ok
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else f"  → got {got}"))


def cls(subject, prop, ai="E", docname="करारनामा"):
    subject = dict(subject)
    igr.set_row_stats(subject, [{"property_raw": "x"}])
    c = {"class": ai}
    row = {"property_raw": prop, "seller_raw": "", "purchaser_raw": "", "docname_raw": docname}
    pc = igr.precheck(row, subject)
    if pc:
        return pc[0]
    igr.verify_class(row, c, subject)
    return c["class"]


def owner(docs):
    return merge.current_owner(merge.dedupe_documents(docs))["names"]


def doc(ref, typ, date, parties, cat="title_transfer", **k):
    d = {"ref": ref, "doc_type": typ, "category": cat, "level": "premises", "exec_date": date, "parties": parties,
         "furnished": True}
    d.update(k)
    return d


# 1. IGR files in another layout: English headings with a title row above, number like TNN4-24821-2024
html = ("<table><tr><td>Search Result for Village Mire</td></tr>"
        "<tr><th>Sr</th><th>Document No.</th><th>Document Type</th><th>Registration Date</th><th>SRO Name</th>"
        "<th>Seller Name</th><th>Purchaser Name</th><th>Property Description</th></tr>"
        "<tr><td>1</td><td>TNN4-24821-2024</td><td>Agreement</td><td>13/12/2024</td><td>Joint S.R. Thane 4</td>"
        "<td>Sanghvi Premises</td><td>Pawar</td><td>Flat 2601 A Wing</td></tr></table>").encode("utf-8")
rows = igr.normalise_rows(igr.parse_igr_file(html, "x.xls"))
check("English headings + title row", len(rows) == 1 and rows[0]["docno"] == "24821" and rows[0]["year"] == 2024
      and rows[0]["seller_raw"] == "Sanghvi Premises", rows)
html_mr = ("<table><tr><th>दस्त क्रमांक</th><th>दस्त प्रकार</th><th>नोंदणी दिनांक</th><th>दुय्यम निबंधक</th>"
           "<th>लिहून देणार</th><th>लिहून घेणार</th><th>मिळकतीचे वर्णन</th></tr>"
           "<tr><td>1597</td><td>खरेदीखत</td><td>15/02/2007</td><td>वसई 1</td><td>अ</td><td>ब</td>"
           "<td>स.न.16 हि.न.4</td></tr></table>").encode("utf-8")
rows = igr.normalise_rows(igr.parse_igr_file(html_mr, "y.xls"))
check("Marathi headings", len(rows) == 1 and rows[0]["docno"] == "1597" and rows[0]["property_raw"].startswith("स.न.16"), rows)

# 2. plots / bungalows
plot = {"unit_type": "Plot", "unit_no": "12", "survey_no": "45", "hissa_no": "2",
        "full_description": "Plot No. 12, Survey No. 45 Hissa No. 2, Village Nilemore"}
check("plot: own plot number → A", cls(plot, "मौजे निळेमोरे स.न.45 हि.न.2 पैकी प्लॉट नं. 12 क्षेत्र 200 चौ.मी.") == "A")
check("plot: other plot of same land → not A",
      cls(plot, "मौजे निळेमोरे स.न.45 हि.न.2 पैकी प्लॉट नं. 13 क्षेत्र 200 चौ.मी.", "A") in ("C", "E"))

# 3. joined flats
joined = {"unit_no": "1704 & 1804", "wing": "A", "building_name": "Woods Building No. 4",
          "full_description": "Flat No. 1704 & 1804, A Wing, Woods, Building No. 4"}
check("joined flats: row with both numbers → A",
      cls(joined, "1704 ,1804 17TH ,18TH FLR A WING BLDG NO 4 WOODS BLDG MIRA ROAD EAST", "E",
          "Agreement Relating to Deposit of Title Deeds") == "A")
check("joined flats: other flat → not A", cls(joined, "A/1006 WOODS BUILDING,BLDG NO 4,MIRA ROAD EAST", "A") in ("C", "E"))

# 4. shop / gala
gala = {"unit_type": "Gala", "unit_no": "5", "building_name": "Shiv Shakti Industrial Estate",
        "full_description": "Gala No. 5, Shiv Shakti Industrial Estate, Survey No. 20, Village Gokhivare"}
check("gala: own gala → A", cls(gala, "गाळा नं. 5, तळमजला, शिव शक्ती इंडस्ट्रियल इस्टेट, सर्वे नं. 20") == "A")
check("gala: gala 15 → not A", cls(gala, "गाळा नं. 15, तळमजला, शिव शक्ती इंडस्ट्रियल इस्टेट, सर्वे नं. 20", "A") in ("C", "E"))

# 5. sale through a power of attorney holder — the principal is the party
check("POA seller → principal only", igr._split_names('{"रमेश पाटील तर्फे कु.मु. सुरेश जाधव"}') == ["रमेश पाटील"],
      igr._split_names('{"रमेश पाटील तर्फे कु.मु. सुरेश जाधव"}'))

# 6. gift / release / will in the chain → the right present owner
A, B, C = "Mr. Anil Shah", "Mrs. Bina Shah", "Mr. Chetan Shah"
check("gift deed → donee owns", owner([
    doc("1", "Agreement for Sale", "01/01/2005", [{"name": "X Builders", "role": "Promoter"}, {"name": A, "role": "Purchaser"}],
        doc_number="100", doc_year="2005"),
    doc("2", "Gift Deed", "01/01/2015", [{"name": A, "role": "Donor"}, {"name": B, "role": "Donee"}], doc_number="200", doc_year="2015"),
]) == [B])
check("release deed → releasee owns", owner([
    doc("1", "Agreement for Sale", "01/01/2005", [{"name": "X", "role": "Promoter"}, {"name": A, "role": "Purchaser"},
                                                   {"name": B, "role": "Purchaser"}], doc_number="100", doc_year="2005"),
    doc("2", "Release Deed", "01/01/2016", [{"name": A, "role": "Releasor"}, {"name": B, "role": "Releasee"}],
        doc_number="300", doc_year="2016"),
]) == [B])
check("will / heirship → legatee owns", owner([
    doc("1", "Agreement for Sale", "01/01/2001", [{"name": "X", "role": "Vendor"}, {"name": A, "role": "Purchaser"}],
        doc_number="10", doc_year="2001"),
    doc("2", "Will / Probate", "01/01/2020", [{"name": A, "role": "Testator"}, {"name": C, "role": "Legatee"}],
        reference_no="Probate 55/2020"),
]) == [C])

# 7. CIDCO / MHADA leasehold: lease then tripartite transfer → transferee owns
check("CIDCO leasehold → last transferee owns", owner([
    doc("1", "Agreement to Lease", "01/01/1998", [{"name": "CIDCO Ltd", "role": "Lessor"}, {"name": A, "role": "Lessee"}],
        doc_number="500", doc_year="1998"),
    doc("2", "Tripartite Agreement", "01/01/2018", [{"name": "CIDCO Ltd", "role": "Lessor"}, {"name": A, "role": "Transferor"},
                                                     {"name": B, "role": "Transferee"}], doc_number="900", doc_year="2018"),
]) == [B])

# 8. SRA flat: allotment by SRA, no agreement
check("SRA allotment → allottee owns", owner([
    doc("1", "Allotment Letter (SRA)", "01/01/2012", [{"name": "Slum Rehabilitation Authority", "role": "Issuing Authority"},
                                                     {"name": A, "role": "Allottee"}], reference_no="SRA/ENG/123"),
]) == [A])

# 9. old society flat: only a share certificate
docs = [doc("1", "Share Certificate", "01/01/1990", [{"name": "Shanti CHS Ltd", "role": "Society"},
                                                      {"name": A, "role": "Member"}], cat="society", reference_no="45")]
own = merge.current_owner(merge.dedupe_documents(docs))
check("share certificate only → member owns, with VERIFY", own["names"] == [A] and own.get("share_certificate_only"), own)

# 10. survey / hissa lists and the bank letter never overrides the registered deed
check("survey + hissa lists paired", legalinfo._survey_value("102, 88", "26, 13") == "102/26, 88/13")
from app import extract  # noqa: E402
subj = extract.merge_subjects_ranked([
    {"subject_property": {"village": "Mire", "survey_no": "102, 88"},
     "documents": [{"category": "title_transfer", "doc_number": "24821", "furnished": True}]},
    {"subject_property": {"village": "Mahajanwadi", "cts_no": "102/26"},
     "documents": [{"category": "other", "doc_type": "Sanction Letter"}]},
])
check("registered deed beats sanction letter", subj["village"] == "Mire", subj)

assert not bad, f"{bad} pattern test(s) failed"
print("ALL PATTERN TESTS PASSED")
