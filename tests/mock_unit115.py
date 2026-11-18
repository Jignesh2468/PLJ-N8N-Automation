"""Offline stand-in for Claude, using facts verified by hand from the Unit 115 Aaren Plaza files.
Lets the whole pipeline (parsing, matching, merge, legalinfo, DOCX) run without an API key."""
import json
import re

SUBJECT = {
    "unit_type": "Industrial Unit", "unit_no": "115", "floor": "First", "building_name": "Aaren Plaza",
    "builtup_area": "1176 sq.ft. (109.29 sq.mtrs.) built-up", "survey_no": "16", "hissa_no": "4",
    "land_area": "H-R-P 0-30-7", "village": "Sativali", "taluka": "Vasai", "district": "Palghar (formerly Thane)",
    "road_locality": "National Highway No.8, Near G.R. Hotel, Tungareshwar Phata",
    "full_description": "Industrial Unit/Premises No.115, admeasuring 1176 sq. ft. (109.29 sq. mtrs.) Built-up area on the "
                        "First Floor of the Building known as “AAREN PLAZA”, constructed on the land bearing Survey No.16, "
                        "Hissa No.4, admeasuring H-R-P 0-30-7, assessed at Rs.0.54, situated at National Highway No.8, Near "
                        "G.R. Hotel, Tungareshwar Phata, Village Sativali, Vasai (East), Taluka Vasai, District Palghar "
                        "(formerly District Thane)",
    "search_terms_marathi": ["आरेन प्लाझा", "आरएन प्लाझा", "रुपनवल", "रुपनवाल", "सातिवली", "भाटी"],
    "search_terms_english": ["Aaren Plaza", "Roopnaval", "Rupnawal", "JAS Financial"],
}

CONVEYANCE = {"doc_type": "Deed of Conveyance", "category": "title_transfer", "level": "land",
              "exec_date": "15/02/2007", "reg_date": "15/02/2007", "doc_no_raw": "01597/2007", "doc_number": "1597",
              "doc_year": "2007", "sro": "Sub-Registrar Vasai",
              "parties": [{"name": "Mr. Abdulla Bhikulla Khan and others", "role": "Vendor"},
                          {"name": "M/s. Roopnaval Developers", "role": "Purchaser"}],
              "property_text": "Land bearing S.No.16, H.No.4, H-R-P 0-30-7, Village Sativali", "furnished": False,
              "key_terms": [], "short_description": ""}

DOCS_A = [
    dict(CONVEYANCE, ref="D1"),
    {"ref": "D2", "doc_type": "Agreement for Sale", "category": "title_transfer", "level": "premises",
     "exec_date": "31/12/2010", "reg_date": "19/01/2011", "doc_no_raw": "वसइ3-00925-2011", "doc_number": "925",
     "doc_year": "2011", "sro": "Sub-Registrar Vasai-3",
     "parties": [{"name": "M/s. Roopnaval Developers through its partner Mr. Navin Gangji Rita", "role": "Builder"},
                 {"name": "Mrs. Ganga Rameshkumar Bhati", "role": "Purchaser"},
                 {"name": "Mr. Rameshkumar Pukhrajji Bhati", "role": "Purchaser"}],
     "consideration": "1803500", "market_value": "1803500", "stamp_duty": "90180", "registration_fee": "18040",
     "property_text": "Unit/Premises No.115, First Floor, Aaren Plaza, 1176 sq.ft built-up, S.No.16 H.No.4, Village Sativali",
     "furnished": True, "pages": "2-21",
     "key_terms": ["Earnest money Rs.2,00,000/- paid vide cheque No.188117 dated 13/11/2010 on Bassein Catholic Co-op. Bank"],
     "short_description": ""},
    {"ref": "D3", "doc_type": "Registration Receipt", "category": "receipt", "level": "premises",
     "exec_date": "19/01/2011", "reference_no": "948", "issued_by": "Sub-Registrar of Assurances, Vasai-3",
     "parent_ref": "D2", "furnished": True,
     "short_description": "Registration Receipt No.948 dated 19/01/2011 for Rs.18,840/- issued by Sub-Registrar of Assurances, Vasai-3, under Document No.925/2011."},
    {"ref": "D4", "doc_type": "Franking Receipt", "category": "receipt", "level": "premises",
     "exec_date": "29/12/2010", "issued_by": "Bassein Catholic Co-op. Bank Ltd., Nallasopara (E)", "parent_ref": "D2",
     "furnished": True, "short_description": "Franking of Rs.90,180/- paid towards stamp duty issued by Bassein Catholic Co-op. Bank Ltd., Nallasopara (East) Branch."},
    {"ref": "D5", "doc_type": "Dast Goshwara (Document Summary) Part I & II", "category": "registration_record",
     "level": "premises", "exec_date": "19/01/2011", "parent_ref": "D2", "furnished": True,
     "short_description": "Document Summary (Dast Goshwara Part-I & II) in respect of Document No.925/2011 issued by Sub-Registrar of Assurances, Vasai-3."},
    {"ref": "D6", "doc_type": "Title Certificate", "category": "title_certificate", "level": "land",
     "exec_date": "07/04/2007", "reference_no": "RUPNAVAL/SATIVALI/04/2007",
     "issued_by": "Shri William J. Pen, Advocate High Court", "furnished": True,
     "short_description": ""},
    {"ref": "D7", "doc_type": "7/12 Extract", "category": "revenue_record", "level": "land", "furnished": True,
     "parties": [{"name": "M/s. Rupnawal Developers through partner Navin Gangji Rita", "role": "Occupant"}],
     "short_description": "7/12 Extract of land bearing Survey No.16, Hissa No.4, Village Sativali, in the name of M/s. Rupnawal Developers through partner Navin Gangji Rita."},
    {"ref": "D8", "doc_type": "Commencement Certificate", "category": "approval", "level": "project",
     "exec_date": "25/03/2008", "reference_no": "CIDCO/VVSR/CC/BP-4296/E/1061",
     "issued_by": "CIDCO (Special Planning Authority, Vasai-Virar Sub-Region)", "furnished": True,
     "key_terms": ["Industrial building, Ground + 2 floors, 34 galas, built-up 2029.73 sq.m."], "short_description": ""},
]

DOCS_B = [
    dict(CONVEYANCE, ref="D1"),
    {"ref": "D2", "doc_type": "Agreement for Sale", "category": "title_transfer", "level": "premises",
     "exec_date": "31/12/2010", "reg_date": "19/01/2011", "doc_no_raw": "", "doc_number": "", "doc_year": "",
     "sro": "Sub-Registrar Vasai-3", "parties": [{"name": "M/s Roopnaval Developers", "role": "Builder"},
                                               {"name": "Mr. Rameshkumar Pukhrajji Bhati & Mrs. Ganga Rameshkumar Bhati", "role": "Purchaser"}],
     "furnished": False, "key_terms": [], "short_description": ""},
    {"ref": "D3", "doc_type": "Agreement for Sale", "category": "title_transfer", "level": "premises",
     "exec_date": "24/12/2012", "reg_date": "24/12/2012", "doc_no_raw": "वसइ3-15914-2012", "doc_number": "15914",
     "doc_year": "2012", "sro": "Joint Sub-Registrar Vasai-3",
     "parties": [{"name": "Mr. Rameshkumar Pukhrajji Bhati", "role": "Vendor"},
                 {"name": "Mrs. Ganga Rameshkumar Bhati", "role": "Vendor"},
                 {"name": "M/s. JAS Financial Services Pvt. Ltd. through its Director Mr. Jignesh Arvind Sanglani", "role": "Purchaser"},
                 {"name": "M/s. Roopnaval Developers through its Partner Shri Navin Rita", "role": "Confirming Party"}],
     "consideration": "3152111", "market_value": "3148000", "stamp_duty": "189180", "registration_fee": "30000",
     "property_text": "Unit/Premises No.115, First Floor, Aaren Plaza, 1176 sq.ft built-up, S.No.16 H.No.4, Village Sativali",
     "furnished": True, "pages": "4-10",
     "key_terms": ["Consideration payable: Rs.52,111/- on execution; Rs.5,00,000/- on or before 25/11/2012; balance Rs.26,00,000/- on or before 31/01/2013 by way of loan from a financial institution",
                   "Vendors acknowledged receipt of Rs.5,52,111/- only",
                   "Vendors and Confirming Party declare the unit free from encumbrances; purchaser put in possession"],
     "short_description": ""},
    {"ref": "D4", "doc_type": "Franking Receipt", "category": "receipt", "level": "premises", "exec_date": "18/12/2012",
     "issued_by": "Bassein Catholic Co-op. Bank Ltd., Zenda Bazar Branch", "parent_ref": "D3", "furnished": True,
     "short_description": "Franking of Rs.1,89,180/- paid towards stamp duty issued by Bassein Catholic Co-op. Bank Ltd., Zenda Bazar Branch."},
    {"ref": "D5", "doc_type": "Registration Receipt", "category": "receipt", "level": "premises", "exec_date": "24/12/2012",
     "reference_no": "16144", "parent_ref": "D3", "furnished": True,
     "short_description": "Registration Receipt No.16144 dated 24/12/2012 for Rs.30,480/- issued by Jt. Sub-Registrar of Assurances, Vasai-3, under Document No.15914/2012."},
    {"ref": "D6", "doc_type": "Dast Goshwara (Document Summary) Part I & II", "category": "registration_record",
     "level": "premises", "exec_date": "24/12/2012", "parent_ref": "D3", "furnished": True,
     "short_description": "Document Summary (Dast Goshwara Part-I & II) in respect of Document No.15914/2012 issued by Jt. Sub-Registrar of Assurances, Vasai-3."},
    {"ref": "D7", "doc_type": "Receipt", "category": "receipt", "level": "premises", "parent_ref": "D3", "furnished": True,
     "short_description": "Receipt of Rs.5,52,111/- issued by Mr. Rameshkumar P. Bhati & Mrs. Ganga Rameshkumar Bhati in favour of M/s. JAS Financial Services Pvt. Ltd. (part payment)."},
    {"ref": "D8", "doc_type": "Commencement Certificate", "category": "approval", "level": "project",
     "exec_date": "25/03/2008", "reference_no": "CIDCO/VVSR/CC/BP-4296/E/1061",
     "issued_by": "CIDCO (Special Planning Authority, Vasai-Virar Sub-Region)", "furnished": True, "short_description": ""},
    {"ref": "D9", "doc_type": "7/12 Extract", "category": "revenue_record", "level": "land", "furnished": True,
     "short_description": "7/12 Extract of land bearing Survey No.16, Hissa No.4, Village Sativali."},
]

_EN = {  # IGR rows we know
    ("1597", 2007): ("B", "land", "Deed of Conveyance",
                     [{"name": "Shri Abdulla Bhikulla Khan & 9 others", "role": "Vendor"},
                      {"name": "M/s. Roop Naval Developers through partner Shri Navin Gangji Rita", "role": "Purchaser"}],
                     "land bearing Survey No.16, Hissa No.4, area 0-13-8 H-R plus pot kharaba 0-16-9, Village Sativali", ""),
    ("925", 2011): ("A", "premises", "Agreement for Sale",
                    [{"name": "Rupnawal Developers through partner Navin Gangji Rita", "role": "Builder"},
                     {"name": "Ganga Rameshkumar Bhati & Rameshkumar Pukhrajji Bhati", "role": "Purchaser"}],
                    "Unit No.115, First Floor, RN Plaza, National Highway No.8, Tungareshwar Phata, Sativali, Vasai (East)",
                    "IGR index records the building as 'RN Plaza' (आरएन प्लाझा) instead of 'Aaren Plaza'."),
    ("15914", 2012): ("A", "premises", "Agreement for Sale",
                      [{"name": "Rameshkumar Pukhrajji Bhati & Ganga Rameshkumar Bhati", "role": "Vendor"},
                       {"name": "M/s. Rupnawal Developers through partner Navin Rita", "role": "Confirming Party"},
                       {"name": "M/s. JAS Financial Services Pvt. Ltd. through Director Jignesh Arvind Sanglani", "role": "Purchaser"}],
                      "Unit No.115, First Floor, Aaren Plaza, Sativali, Vasai", ""),
}


def mock(task, system, blocks):
    import os, time
    if task == "extract_documents":
        time.sleep(float(os.environ.get("MOCK_SLOW", "0")))
    text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if task == "extract_documents":
        m = re.search(r"pages (\d+)-(\d+)", text)
        first_chunk = (not m) or m.group(1) == "1"
        if "115_B" in text or "115B" in text:
            docs = DOCS_B if first_chunk else []
        else:
            docs = DOCS_A if first_chunk else []
        return {"subject_property": SUBJECT if first_chunk else {}, "documents": json.loads(json.dumps(docs)),
                "discrepancies": []}
    if task == "igr_classify":
        rows = json.loads(text.split("IGR ROWS:\n", 1)[1])
        out = []
        for r in rows:
            key = (r["doc_no"], int((r["registration_date"] or "0000")[-4:]))
            if key in _EN:
                c, lvl, dt, parties, prop, disc = _EN[key]
                out.append({"id": r["id"], "class": c, "level": lvl, "doc_type_en": dt, "parties": parties,
                            "property_en": prop, "unit_no": "115" if c == "A" else "", "building": "Aaren Plaza",
                            "reason": "unit, building, survey and parties match", "discrepancy": disc})
            elif re.search(r"आरेन|आरएन|AAREN", r["property_description"], re.I):
                out.append({"id": r["id"], "class": "C", "level": "project", "doc_type_en": "Agreement / Mortgage",
                            "parties": [], "property_en": "another unit in Aaren Plaza", "reason": "same building, different unit"})
            else:
                out.append({"id": r["id"], "class": "E", "level": "other", "doc_type_en": "", "parties": [],
                            "property_en": "", "reason": "different building (e.g. Subhadra Industrial Complex)"})
        return {"rows": out}
    if task == "draft_title":
        return DRAFT
    raise ValueError(task)


DRAFT = {
    "belonging_to": "M/s. JAS Financial Services Pvt. Ltd.",
    "borrower_details": "M/s. JAS Financial Services Pvt. Ltd. through its Director Mr. Jignesh Arvind Sanglani",
    "title_status": "clear_subject_to",
    "subject_to": ["proof of payment of balance consideration of Rs.26,00,000/-",
                   "search for the years not available", "Occupancy Certificate"],
    "opinion_paragraph": "I am of the opinion that M/s. JAS Financial Services Pvt. Ltd. is having right, title and interest in the said Premises and its title is clear and marketable, subject to (i) proof of payment of the balance consideration of Rs.26,00,000/- under the Agreement for Sale dated 24/12/2012, (ii) search of the years for which IGR data was not available, and (iii) production of the Occupancy Certificate. The said M/s. JAS Financial Services Pvt. Ltd., through its authorised Director, can create equitable mortgage in relation to the said Premises by depositing with the Bank the title deeds in relation to the same.",
    "certificate_opinion": "I hereby certify that, subject to stated hereinabove, M/s. JAS Financial Services Pvt. Ltd. has got a clear, valid and marketable title over the above said property, and is competent to create the Equitable Mortgage as per law.",
    "defects": ["Full and final receipt of the balance consideration of Rs.26,00,000/- under the Agreement for Sale dated 24/12/2012 is not produced.",
                "IGR search data for 1996-2001 and 2016 was not available in the data supplied.",
                "Occupancy Certificate of the building is not furnished.",
                "Deed of Conveyance dated 15/02/2007 (Doc No. 1597/2007) is only recited; certified copy to be obtained."],
    "chain_paragraphs": [
        "On perusal of the aforesaid documents, it is observed that by a Deed of Conveyance dated 15/02/2007 entered between Mr. Abdulla Bhikulla Khan and others as ‘the Vendors’ AND M/s. Roopnaval Developers as ‘the Purchasers’, the land bearing Survey No.16, Hissa No.4, admeasuring H-R-P 0-30-7, Village Sativali was conveyed to M/s. Roopnaval Developers. The said Deed is registered with the Sub-Registrar of Assurances, Vasai-1 under Document No.1597/2007 on 15/02/2007 [recited in the subsequent Agreements and verified from the IGR search; copy not furnished].",
        "Further, it is observed that Shri William J. Pen, Advocate High Court, vide Title Certificate bearing Ref. No.RUPNAVAL/SATIVALI/04/2007 dated 07/04/2007, certified the title of M/s. Roopnaval Developers to the said land as clear and marketable.",
        "Further, it is observed that CIDCO, the Special Planning Authority, issued Commencement Certificate No.CIDCO/VVSR/CC/BP-4296/E/1061 dated 25/03/2008 in favour of M/s. Roop Naval Developers for construction of an industrial building on the said land.",
        "Further, it is observed that by an Agreement for Sale dated 31/12/2010 entered between M/s. Roopnaval Developers through its partner Mr. Navin Gangji Rita as ‘the Builders’ of the One Part AND Mrs. Ganga Rameshkumar Bhati & Mr. Rameshkumar Pukhrajji Bhati as ‘the Purchasers’ of the Other Part, the said Builders agreed to sell Unit/Premises No.115 on the First Floor of “AAREN PLAZA” for the total consideration of Rs.18,03,500/-. The said Agreement has been adequately stamped by paying stamp duty of Rs.90,180/- and Registration fees of Rs.18,040/- and is duly registered with the Sub-Registrar of Assurances, Vasai-3 under Document No.925/2011 on 19/01/2011. [The IGR index records the building as ‘RN Plaza’.]",
        "Further, it is observed that by an Agreement for Sale dated 24/12/2012 entered between Mr. Rameshkumar Pukhrajji Bhati & Mrs. Ganga Rameshkumar Bhati as ‘the Vendors’ of the First Part AND M/s. JAS Financial Services Pvt. Ltd. through its Director Mr. Jignesh Arvind Sanglani as ‘the Purchaser’ of the Second Part AND M/s. Roopnaval Developers through its Partner Shri Navin Rita as ‘the Confirming Party’ of the Third Part, the said Vendors sold the said Unit/Premises No.115 to the Purchaser for the total consideration of Rs.31,52,111/-, of which Rs.26,00,000/- was payable on or before 31/01/2013 by way of loan. The said Agreement has been adequately stamped by paying stamp duty of Rs.1,89,180/- and Registration fees of Rs.30,000/- and is duly registered with the Joint Sub-Registrar Vasai-3 under Document No.15914/2012 on 24/12/2012.",
        "The Mortgagor shall produce proof of payment of the balance consideration of Rs.26,00,000/- and, if paid out of a loan, the No Dues / Release letter of the lender.",
        "The Mortgagor shall produce the Occupancy Certificate of the building “Aaren Plaza” and NOC of the Builder / Society for mortgage in favour of the Bank.",
    ],
    "special_report": {"A1": "M/s. JAS Financial Services Pvt. Ltd.", "A2": "M/s. JAS Financial Services Pvt. Ltd. through its Director Mr. Jignesh Arvind Sanglani",
                       "A3": "Industrial Unit/Premises No.115, First Floor, “AAREN PLAZA”, Survey No.16, Hissa No.4, Village Sativali, Taluka Vasai",
                       "A4": "M/s. JAS Financial Services Pvt. Ltd.", "A5": "M/s. JAS Financial Services Pvt. Ltd.",
                       "B1": "As per list of Documents mentioned in the Certificate attached herewith",
                       "B2": "Application for certified copies to be made with the concerned Sub-Registrar of Assurances.",
                       "B3": "To be compared on receipt of certified copies.", "B4": "Yes, as per IGR search records.",
                       "B5": "Application for certified copies to be made.", "B6": "Application for certified copies to be made.",
                       "B7": "To be compared on receipt of certified copies.", "B8": "N.A.", "B9": "Yes, Equitable Mortgage shall be created by deposit of title deeds; Notice of Intimation u/s 89B of the Registration Act, 1908 to be filed within thirty days.",
                       "B10": "No.", "B11": "No.", "B12": "Subject to NOC of the Builder / Society and documents stated herein.", "B13": "No.", "B14": "No.",
                       "B15": "N.A.", "B16": "No., Freehold land", "B17": "N.A.", "B18": "N.A.", "B19": "N.A.", "B20": "No.", "B21": "N.A.",
                       "B22": "N.A.", "B23": "Yes", "B24": "Yes, subject to the conditions stated in the Certificate.",
                       "B25": "Yes, subject to the conditions stated in the Certificate.", "B26": "Yes, subject to the conditions stated in the Certificate."},
    "documents_to_obtain": [{"description": "Full and final receipt of balance consideration of Rs.26,00,000/-", "original_or_copy": "Original"},
                            {"description": "Certified copy of Deed of Conveyance dated 15/02/2007 (Doc No. 1597/2007)", "original_or_copy": "Xerox copy"},
                            {"description": "Occupancy Certificate of the building “Aaren Plaza”", "original_or_copy": "Xerox copy"},
                            {"description": "NOC for mortgage of Unit No.115 from the Builder / Society in favour of the Bank", "original_or_copy": "Original"}],
    "precautions": ["To have physical inspection of the said Unit/Premises No.115.",
                    "To obtain full and final receipt of the balance consideration of Rs.26,00,000/- and, if any loan was availed, No Dues / Release letter from the lender.",
                    "To take NOC from the Builder / Society for creation of equitable mortgage and to note the Bank’s charge in their records.",
                    "To file Notice of Intimation u/s 89B of the Registration Act, 1908 within thirty days and register the charge with CERSAI.",
                    "Before disbursing the loan amount the Branch Manager should confirm the paying capacity of the borrower and obtain the Board Resolution of the borrower company."],
    "search_observations": ["The land-level Deed of Conveyance dated 15/02/2007 (Doc No.1597/2007, Sub-Registrar Vasai-1) is reflected in the search.",
                            "Two direct transactions relating to Unit No.115 are found: Doc No.925/2011 and Doc No.15914/2012.",
                            "No mortgage, charge or deposit of title deeds relating to Unit No.115 is found in the IGR data supplied.",
                            "IGR data for 1996-2001 and 2016 was not available in the data supplied."],
    "oral_confirmations": ["Whether any loan was availed for the balance consideration of Rs.26,00,000/- payable by 31/01/2013 and whether any charge was created on Unit No.115."],
}
