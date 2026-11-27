"""Stage 1 runner: documents → party table + property model (+ flags), all with sources."""
from typing import List

from .evidence import evidence
from .parties import build_party_table, mention
from .property_model import build_property_model


def run_stage1(docs: List[dict]) -> dict:
    mentions = []
    for d in docs:
        for p in d.get("parties") or []:
            ev = evidence(d.get("file"), p.get("page") or d.get("pages"), d.get("ref"), d.get("doc_type"),
                          p.get("quote") or "", page_exact=bool(p.get("page")))
            mentions.append(mention(p.get("name"), p.get("role"), ev))
    table = build_party_table(mentions)
    model = build_property_model(docs, table)
    flags, seen = [], {}
    for f in model.pop("flags") + table["flags"]:
        key = (f["level"], f["topic"], f["text"])
        if key in seen:                            # the same finding from many copies of one document: say it once
            seen[key]["repeats"] = seen[key].get("repeats", 1) + 1
            continue
        seen[key] = f
        flags.append(f)
    order = {"conflict": 0, "verify": 1, "missing": 2}
    doc_list = [{"ref": d.get("ref"), "doc_type": d.get("doc_type"), "category": d.get("category"), "level": d.get("level"),
                 "date": d.get("exec_date") or d.get("reg_date") or "", "doc_no": d.get("doc_no") or "",
                 "office": d.get("sro") or "", "file": d.get("file"), "pages": d.get("pages"),
                 "furnished": d.get("furnished", True), "status": d.get("execution_status") or "",
                 "parties": [{"name": p.get("name"), "role": p.get("role")} for p in d.get("parties") or []]}
                for d in docs]
    return {"property": model, "parties": table["parties"],
            "flags": sorted(flags, key=lambda f: (order.get(f["level"], 3), f["topic"])),
            "documents": len(docs), "doc_list": doc_list,
            "page_exact": all(d.get("page_exact") for d in docs) if docs else False}
