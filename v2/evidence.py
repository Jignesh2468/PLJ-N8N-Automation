"""Evidence and facts.

Every value in the v2 model is a Fact: the value plus the evidence it rests on. A fact read from several documents
keeps every source. When the documents disagree, the field is a CONFLICT listing each value with its sources —
the code never picks one silently. A field no document states is NOT_STATED, never guessed.
"""
from typing import Callable, Dict, List, Optional


def evidence(file: str = "", pages: str = "", doc_ref: str = "", doc_type: str = "", quote: str = "",
             page_exact: bool = False) -> dict:
    """Where a fact was read. page_exact=False means only the document's page RANGE is known (saved readings);
    True means the reader reported the exact page (and normally a quote) for this fact."""
    return {"file": file or "", "pages": str(pages or ""), "doc_ref": doc_ref or "", "doc_type": doc_type or "",
            "quote": (quote or "").strip()[:300], "page_exact": bool(page_exact)}


def cite(ev: dict) -> str:
    """'Agreement for Sale (agreement.pdf p. 3)' — for the review pages and flags."""
    where = ev.get("file") or "?"
    if ev.get("pages"):
        where += (" p. " if ev.get("page_exact") else " pp. ") + ev["pages"]
    return f"{ev.get('doc_type') or ev.get('doc_ref') or 'document'} ({where})"


class FieldBuilder:
    """Collects candidate values of ONE field from many documents, then states the outcome:
    confirmed (one value, possibly many sources) / conflict (different values) / not_stated."""

    def __init__(self, name: str, same: Optional[Callable[[str, str], bool]] = None,
                 norm: Optional[Callable[[str], str]] = None, prefer: Optional[Callable[[str, str], str]] = None):
        self.name = name
        self.prefer = prefer                          # which of two equal spellings to show (default: the fullest)
        self.norm = norm or (lambda v: " ".join(str(v).lower().split()))
        self.same = same or (lambda a, b: self.norm(a) == self.norm(b))
        self.values: List[dict] = []                  # [{"value", "evidence": [..]}]

    def add(self, value, ev: dict, weight: int = 1) -> None:
        """weight = the standing of the source (see property_model.authority): a registered deed outranks a letter,
        a letter outranks a valuation report."""
        if value is None or not str(value).strip():
            return
        value = " ".join(str(value).split())
        ev = dict(ev, authority=weight)
        for v in self.values:
            if self.same(v["value"], value):
                v["evidence"].append(ev)
                v["weight"] += weight
                v["top"] = max(v["top"], weight)
                if self.prefer:
                    v["value"] = self.prefer(v["value"], value)
                elif len(value) > len(v["value"]) and self.norm(value) == self.norm(v["value"]):
                    v["value"] = value                # keep the fullest spelling of the same value
                return
        self.values.append({"value": value, "evidence": [ev], "weight": weight, "top": weight})

    def result(self) -> dict:
        if not self.values:
            return {"status": "not_stated", "value": None, "evidence": []}
        if len(self.values) == 1:
            v = self.values[0]
            return {"status": "confirmed", "value": v["value"], "evidence": v["evidence"]}
        vals = sorted(self.values, key=lambda v: (-v["top"], -v["weight"]))
        lead, rest = vals[0], vals[1:]
        if all(r["top"] < lead["top"] for r in rest):
            # the documents of the highest standing agree; only lower-ranked papers (a valuation report, a receipt)
            # differ — the value stands, the difference is listed for verification
            return {"status": "confirmed", "value": lead["value"], "evidence": lead["evidence"],
                    "discrepancies": [{"value": r["value"], "evidence": r["evidence"]} for r in rest]}
        return {"status": "conflict", "value": None,
                "candidates": [{"value": v["value"], "evidence": v["evidence"]} for v in vals],
                "evidence": [e for v in vals for e in v["evidence"]]}


def flag(level: str, topic: str, text: str, evs: Optional[List[dict]] = None) -> dict:
    """level: 'verify' (lawyer must check) / 'missing' (not stated in the papers) / 'conflict' (papers disagree)."""
    return {"level": level, "topic": topic, "text": text, "sources": [cite(e) for e in (evs or [])][:6]}


def field_flags(topic: str, res: dict) -> List[dict]:
    if res.get("status") == "conflict":
        parts = "; ".join(f"'{c['value']}' in {', '.join(cite(e) for e in c['evidence'][:3])}" for c in res["candidates"])
        return [flag("conflict", topic, f"Documents of equal standing state different values — {parts}.", res["evidence"])]
    out = []
    for d in res.get("discrepancies") or []:
        out.append(flag("verify", topic, f"'{res['value']}' per {', '.join(cite(e) for e in res['evidence'][:2])}, but "
                                         f"'{d['value']}' in {', '.join(cite(e) for e in d['evidence'][:2])} — "
                                         f"confirm which is correct.", res["evidence"][:2] + d["evidence"][:2]))
    return out


def merge_results(results: Dict[str, dict]) -> List[dict]:
    out = []
    for k, r in results.items():
        out += field_flags(k, r)
    return out
