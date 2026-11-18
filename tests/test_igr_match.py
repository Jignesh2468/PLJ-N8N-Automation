"""IGR matching regression test (real rows from the Aaren Plaza search). Run: python tests/test_igr_match.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import igr  # noqa: E402

SUBJECT = {"unit_no": "202", "building_name": "Aaren Plaza", "survey_no": "16", "hissa_no": "4",
           "search_terms_marathi": ["आरेन प्लाझा"], "search_terms_english": ["Aaren Plaza"]}
ROWS = [  # (property text, docname, sloppy AI class, expected class after the code check)
    ("मौजे सातिवली, विभगा क्र 25, युनिट नं 202, दुसरा मजला, आरएन प्लाझा, नॅशनल हायवे नं 8", "करारनामा", "E", "A"),
    ("सदनिका नं: युनिट नं. 202 , माळा नं: दुसरा मजला , इमारतीचे नाव: आरेन प्लाझा", "करारनामा", "A", "A"),
    ("UNIT NO. 202, 2ND FLOOR, AAREN PLAZA, NATIONAL HIGHWAY NO. 8, SATIVALI", "Agreement Relating to Deposit of Title Deeds", "A", "A"),
    ("गाव मौजे सातीवली येथिल स.न.16 हि.न.4 क्षे.0-13-8 हे आर , पो. ख.0-16-9", "अभिहस्तातंरणपत्र", "E", "B"),
    ("मौजे सातिवली तालुका वसई सर्व्हे नं 16 हिस्सा नं 7 या जमिनीवरील निधी पॅराडाइज तळ मजला बिल्डींग नं 1 शॉप नं 3ए", "हक्कसोडपत्र", "B", "E"),
    ("प्लॉट नं.16,सर्वे नं. 7,8/1,8/2,8/3,11,12/1/1,67पार्ट 1 ए बिल्डींग नं. 1 सत्यम", "गहाणखत", "B", "E"),
    ("GALA 118  BLDG. NO. 02, NIDHI PARADISE  VASAI EAST", "Agreement Relating to Deposit of Title Deeds", "B", "E"),
    ("स.न. 16,हि.न. 4,या जमिनीवरील आरएनए प्लाझा या इमारतीच्या तळमजल्यावरील औद्योगिक गाळा क्र. 1", "रिकन्व्हेन्स", "B", "C"),
    ("गाव मौजे सातीवली सर्वे नं 16 हीस्सा नं 4 गाळा क्रं 118 पहीला मजला आरेन प्लाझा", "करारनामा", "B", "C"),
    ("GALA NO. 202, 2ND FLOOR, A WING, KUSHAL MANGAL INDUSTRIAL ESTATE CO OP SOC LTD, SATIVALI", "Agreement Relating to Deposit of Title Deeds", "A", "E"),
    ("गाळा नंबर 101 पहिला मजला क्षेत्र 202.01 चौरस मिटर बिल्डींग नंबर 1 निधी पॅराडाईज सर्वे नंबर 16 हिस्सा नंबर 7", "गहाणखत", "A", "E"),
]
bad = 0
for i, (prop, dn, ai, want) in enumerate(ROWS):
    row = {"property_raw": prop, "seller_raw": "", "purchaser_raw": "", "docname_raw": dn}
    c = {"class": ai, "parties": [], "doc_type_en": dn}
    igr.verify_class(row, c, SUBJECT)
    ok = c["class"] == want
    bad += not ok
    print(("OK  " if ok else "FAIL"), ai, "→", c["class"], "(want", want + ")", prop[:60])
assert igr.file_year_range("2002-07.xls") == (2002, 2007) and igr.file_year_range("IGR 2016.xls") == (2016, 2016)
p = igr.default_parties({"seller_raw": "", "purchaser_raw": '{"JAS FINANCIAL SERVICES PVT LTD THROUGH DIRECTOR X"}',
                         "docname_raw": "Agreement Relating to Deposit of Title Deeds"}, "")
assert p[0]["role"] == "Mortgagor" and "JAS" in p[0]["name"], p
assert not bad, f"{bad} IGR match test(s) failed"
print("ALL IGR MATCH TESTS PASSED")
