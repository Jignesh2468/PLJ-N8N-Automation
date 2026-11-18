"""City / redevelopment pattern (CTS shared by a whole MHADA layout, society name in English vs Marathi).
Run:  python tests/test_city_layout.py"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import igr, merge  # noqa: E402

S = {"unit_no": "804", "building_name": "Shree Krishna Heights", "plot_no": "351", "cts_no": "5681",
     "society_name": "Pant Nagar Shri Krishnangan Co-operative Housing Society Limited"}
DA = ("इतर माहिती: विकसनकरारनामा - जमीन व बांधकाम - मौजे घाटकोपर-किरोळ,सी टी एस नं. 5681,फायनल प्लॉट नं. 351,टीपीएस III "
      "व त्यावरील बांधकाम पंतनगर श्री कृष्णांगन को-ऑपरेटिव्ह हौसिंग सोसायटी लिमिटेड,बिल्डींग नं. 24, सेक्युरिटी फ्लॅट शुल्क रु. 2,18,73,000/-")
OTHER_BLDG = "मौजे घाटकोपर, सी टी एस नं. 5681, बिल्डींग नं 31, पंतनगर गणेश कृपा को-ऑप हौसिंग सोसायटी लिमिटेड"
BOND = "मौजे घाटकोपर - किरोळ,सीटीएस नं. 5681,फायनल प्लॉट नं. 351,पंतनगर श्री कृष्णांगन को-ऑ हौ सो लि"
FLAT = "सदनिका नं: सदनिका क्र. 804, माळा नं: 8 वा मजला, इमारतीचे नाव: श्री कृष्णा हाईट्स, बिल्डींग नं 24, सी टी एस नं. 5681"
OTHER_FLAT = "मौजे घाटकोपर फ्लॅट नं 714, बि नं 24 , पंतनगर श्री कृष्णांगन को ऑ हौ सा लि, सिटीएस नं 5681"

subj = dict(S)
filler = [{"property_raw": "सी टी एस नं. 5681 बिल्डींग नं %d" % i} for i in range(300)]
igr.set_row_stats(subj, filler + [{"property_raw": DA}])
assert subj["_land_common"], "CTS used by the whole layout must be marked common"

cases = [(DA, "विकसनकरारनामा", "E", "B"), (OTHER_BLDG, "विकसनकरारनामा", "B", "E"), (BOND, "35-क्षतिपूर्ती बंधपत्र", "B", "C"),
         (FLAT, "करारनामा", "E", "A"), (OTHER_FLAT, "अभिहस्तातंरणपत्र", "E", "E")]
bad = 0
for prop, dn, ai, want in cases:
    c = {"class": ai}
    igr.verify_class({"property_raw": prop, "seller_raw": "", "purchaser_raw": "", "docname_raw": dn}, c, subj)
    ok = c["class"] == want
    bad += not ok
    print("OK  " if ok else "FAIL", ai, "→", c["class"], "(want", want + ")", prop[:60])
for raw, yr, want in (("KRL1-3174-2025", "", ("3174", "2025")), ("P/515", "1987", ("515", "1987"))):
    d = merge._norm_doc({"doc_number": raw, "doc_year": yr})
    ok = (d["doc_number"], d["doc_year"]) == want
    bad += not ok
    print("OK  " if ok else "FAIL", raw, "→", d["doc_number"], d["doc_year"])
assert igr.skeleton("कृष्णांगन") == igr.skeleton("Krishnangan")

# new-project pattern: CTS list, unit with wing letter (1801B), several towers, developer's land deeds
T = {"unit_no": "1801B", "wing": "B", "building_name": "North Imperial Greens Tower 1",
     "project_name": "North Imperial Greens – Tower 1 (formerly Codename Lottery – Tower 1)",
     "cts_no": "450 Pt., 449 Pt., 451, 455/A Pt., 464/1 Pt., 538/1, 463 & 467/A",
     "full_description": "Flat No. 1801B, Tower 1, North Imperial Greens, CTS Nos. 450 Pt., 449 Pt., 451, 455/A Pt., 464/1 Pt., 538/1, 463 & 467/A, Village Owale"}
igr.set_row_stats(T, [{"property_raw": "x"}])
cases2 = [
    ("सदनिका क्र.1801सी,18वा मजला,कोडनेम लॉटरी,व्हिलेज ओवळे", "करारनामा", "A", "C"),          # same floor, wing C
    ("1801A NORTH IMPERIAL GREENS TOWER-2 GHODBUNDER ROAD OWALE", "Agreement Relating to Deposit of Title Deeds", "A", "E"),
    ("सदनिका क्र. 1801बी,18वा मजला,नॉर्थ इम्पेरियल ग्रीन्स टाॅवर-1,व्हिलेज ओवळे", "करारनामा", "E", "A"),
    ("मौजे ओवळे येथील सर्वे नं.67 हिस्सा नं.1 चा सीटीएस नं. 538/1 पार्ट,464/1पार्ट,463,461 पार्ट चे क्षेत्रफळ 1670 चौ.मी.", "खरेदीखत", "E", "B"),
    ("1905A NORTH IMPERIAL GREENS TOWER-1 C.T.S NUMBER 450 PART 449 PART 451", "Agreement Relating to Deposit of Title Deeds", "B", "C"),
    ("जुना स.नं. 82, नविन स नं 77 हि नं 2 क्षेत्र 450 चौ मी", "करारनामा", "B", "E"),          # area 450, not CTS 450
]
for prop, dn, ai, want in cases2:
    c = {"class": ai}
    igr.verify_class({"property_raw": prop, "seller_raw": "", "purchaser_raw": "", "docname_raw": dn}, c, T)
    ok = c["class"] == want
    bad += not ok
    print("OK  " if ok else "FAIL", ai, "→", c["class"], "(want", want + ")", prop[:60])
assert not bad, f"{bad} city-layout test(s) failed"
print("ALL CITY LAYOUT TESTS PASSED")
