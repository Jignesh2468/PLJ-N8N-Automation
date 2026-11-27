"""Marathi → English glossary for report text (no AI): names of banks / finance companies as they are written in the
IGR index, company-form words, and legal phrases. Applied BEFORE letter-by-letter transliteration, so that
'आयसीआयसीआय बँक' is 'ICICI Bank' and not 'Ayasiayasiay Bank'. Domain vocabulary — not specific to any case."""
import re

PHRASES = [
    # legal phrases
    (r"मयत\s*(?:यांचे|यांच्या)\s*(?:कायदेशीर\s*)?वारसदार", "(deceased) through legal heir"),
    (r"(?:यांचे|यांच्या)\s*(?:कायदेशीर\s*)?वारस(?:दार)?", "through legal heir"), (r"\bमयत\b", "(deceased)"),
    (r"यांच्या\s*तर्फे|तर्फे", "through"), (r"कु\.?\s*मु\.?\s*(?:म्हणून)?|कुलमुखत्यार", "constituted attorney"),
    (r"पुर्वाश्रमीची|पूर्वाश्रमीची|पुर्वाश्रमीचे", "formerly"), (r"पु?ू?र्वीचे\s*नाव|पूर्वीचे\s*नाव|पुर्वीचे\s*नाव", "formerly"), (r"उर्फ|ऊर्फ", "alias"),
    (r"अज्ञान\s*पालनकर्ता", "minor through guardian"), (r"अज्ञान", "minor"), (r"पालनकर्ता", "guardian"),
    (r"भागीदार|भागिदार", "partner"), (r"संचालक|डायरेक्टर", "director"), (r"अधिकृत\s*सही\s*(?:करणार|कर्ता)", "authorised signatory"),
    (r"चेअरमन|अध्यक्ष", "chairman"), (r"सेक्रेटरी|सचिव", "secretary"), (r"खजि?नदार", "treasurer"),
    (r"\bD/O\.?", "D/o"), (r"\bS/O\.?", "S/o"), (r"\bW/O\.?", "W/o"),
    # company / organisation words
    (r"प्रायव्हेट|प्रायवेट|प्राईवेट|प्राइवेट|प्रायव्हेट", "Private"), (r"प्रा\.?\s*लि\.?|प्रा\.?\s*ली\.?", "Pvt. Ltd."),
    (r"लिमि?टेड|लिमीटेड|लि\.", "Limited"), (r"एल\s*\.?\s*एल\s*\.?\s*पी|एलएलपी", "LLP"), (r"मेसर्स|मे\.", "M/s."),
    (r"बिल्डर्स", "Builders"), (r"डेव्हल?पर्स|डेव्हलोपर्स|डेव्हल्पर्स", "Developers"), (r"कन्स्ट्रक्शन्?स?", "Constructions"),
    (r"इन्फ्रा", "Infra"), (r"रि?यल्टी|रिअल्टी|रीअल्टी|रियालटी", "Realty"), (r"रि?यल्टर्स|रिअल्टर्स|रियलटर्स", "Realtors"),
    (r"प्रोजेक्ट्स", "Projects"), (r"प्रोजेक्ट", "Project"), (r"एंटरप्रायजेस|इंटरप्राईजेस|एन्टरप्रायझेस", "Enterprises"),
    (r"असोसिएट्स|असोसिएटस", "Associates"), (r"सर्व्हिसेस|सर्विसेस|सव्हिर्सेस", "Services"), (r"कॉर्पोरेशन", "Corporation"),
    (r"इस्टेट्स", "Estates"), (r"इस्टेट", "Estate"), (r"प्रॉपर्टीज", "Properties"), (r"होम्स", "Homes"), (r"ग्रुप", "Group"),
    (r"को\s*-?\s*ऑप(?:रेटिव्ह|रेटीव्ह)?\.?\s*(?:हौसिंग|हाउसिंग|हाऊसिंग|हौ\.?)\s*(?:सोसायटी|सोसा\.?|सो\.?)\s*(?:लि(?:मिटेड)?\.?)?",
     "Co-operative Housing Society Ltd."), (r"सोसायटी", "Society"), (r"ट्रस्टीशिप", "Trusteeship"), (r"ट्रस्ट", "Trust"),
    # banks and housing-finance companies (as written in IGR entries)
    (r"आय\s*सी\s*आय\s*सी\s*आय|आयसीआयसीआय", "ICICI"), (r"एच\s*डी\s*एफ\s*सी|एचडीएफसी", "HDFC"), (r"एल\s*आय\s*सी|एलआयसी", "LIC"),
    (r"स्टेट\s*बँक\s*ऑफ\s*इंडिया", "State Bank of India"), (r"एस\s*बी\s*आय|एसबीआय", "SBI"), (r"ॲक्सिस|अॅक्सिस|अ‍ॅक्सिस", "Axis"),
    (r"कोटक\s*महिंद्रा", "Kotak Mahindra"), (r"बजाज", "Bajaj"), (r"टाटा\s*कॅपिटल", "Tata Capital"), (r"आदित्य\s*बिर्ला", "Aditya Birla"),
    (r"बँक\s*ऑफ\s*इंडिया", "Bank of India"), (r"बँक\s*ऑफ\s*बडोदा", "Bank of Baroda"), (r"बँक\s*ऑफ\s*महाराष्ट्र", "Bank of Maharashtra"),
    (r"पंजाब\s*नॅशनल", "Punjab National"), (r"युनियन\s*बँक", "Union Bank"), (r"कॅनरा", "Canara"), (r"आय\s*डी\s*बी\s*आय|आयडीबीआय", "IDBI"),
    (r"येस\s*बँक", "Yes Bank"), (r"इंडसइंड", "IndusInd"), (r"फेडरल", "Federal"), (r"एस\s*एम\s*एफ\s*जी|एसएमएफजी", "SMFG"),
    (r"पिरामल", "Piramal"), (r"जे\s*एम\s*फायनान्शियल", "JM Financial"), (r"कॅटलिस्ट|कॅटॅलीस्ट", "Catalyst"),
    (r"हौसिंग\s*फायनान्स|हाउसिंग\s*फायनान्स|हौसिंग\s*फायनांस|हाऊसिंग\s*फायनान्स", "Housing Finance"),
    (r"होम\s*फायनान्स|होम\s*फायनांस", "Home Finance"), (r"फायनान्स|फायनांस", "Finance"), (r"कॅपिटल", "Capital"),
    (r"सहकारी\s*बँक", "Co-operative Bank"), (r"बँक|बॅक|बैंक|बॅंक", "Bank"), (r"कंपनी", "Company"), (r"फायनान्शियल", "Financial"),
    # authorities
    (r"म्हाडा", "MHADA"), (r"सिडको", "CIDCO"), (r"महानगरपालिका", "Municipal Corporation"),
    (r"(?:मुंबई|बॉम्बे)\s*हाऊसिंग\s*अँड\s*एरिया\s*डेव्हलपमेंट\s*(?:बोर्ड|ऑथोरिटी)", "Mumbai Housing and Area Development Board (MHADA)"),
    (r"महाराष्ट्र\s*हाऊसिंग\s*अँड\s*एरिया\s*डेव्हलपमेंट\s*ऑथोरि?टी", "Maharashtra Housing and Area Development Authority (MHADA)"),
    # land words
    (r"सर्वे\s*नं\.?|सर्व्हे\s*नं\.?|स\.\s*नं\.?", "Survey No."), (r"हिस्सा\s*नं\.?|हि\.\s*नं\.?", "Hissa No."),
    (r"सि\s*\.?\s*टी\s*\.?\s*एस\s*\.?\s*नं\.?|सीटीएस\s*नं\.?", "CTS No."), (r"पैकी|पार्ट", "(part)"), (r"मौजे", "Village"),
]
_COMPILED = [(re.compile(p), r) for p, r in PHRASES]


def apply(text: str) -> str:
    t = text or ""
    for rx, rep in _COMPILED:
        t = rx.sub(f" {rep} ", t)
    return re.sub(r"\s+", " ", t).strip()
