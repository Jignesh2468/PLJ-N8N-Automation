"""Small deterministic helpers shared by all stages."""
import re
from datetime import date, datetime
from typing import Optional, Tuple

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def ascii_digits(s: str) -> str:
    return str(s or "").translate(_DEV_DIGITS)


def has_devanagari(s: str) -> bool:
    return any("ऀ" <= c <= "ॿ" for c in str(s or ""))


def clean_num(v) -> str:
    """'1803500.0' / 'Rs.18,03,500/-' -> '1803500'; '' when not a number."""
    s = ascii_digits(str(v or "")).strip()
    if not s:
        return ""
    s = re.sub(r"[^\d.]", "", s)
    if not s or s == ".":
        return ""
    try:
        f = float(s)
    except ValueError:
        return ""
    return str(int(f)) if f == int(f) else str(f)


def inr(v) -> str:
    """Indian grouping: 3152111 -> 31,52,111"""
    s = clean_num(v)
    if not s:
        return ""
    whole = s.split(".")[0]
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def rs(v) -> str:
    s = inr(v)
    return f"Rs.{s}/-" if s else ""


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(s) -> Optional[date]:
    t = ascii_digits(str(s or "")).strip()
    if not t:
        return None
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t, flags=re.I)
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", t)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*[,\s]+(\d{4})", t)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def fmt_date(s) -> str:
    d = parse_date(s)
    return d.strftime("%d/%m/%Y") if d else str(s or "").strip()


def year_of(s) -> Optional[int]:
    d = parse_date(s)
    if d:
        return d.year
    m = re.search(r"(19|20)\d{2}", ascii_digits(str(s or "")))
    return int(m.group(0)) if m else None


def docno_key(number, year) -> Optional[Tuple[int, int]]:
    n = re.sub(r"\D", "", ascii_digits(str(number or "")).split(".")[0])
    y = year_of(year) if year else None
    if not n or not y:
        return None
    return int(n), int(y)


def parse_doc_no_raw(raw: str) -> Tuple[str, str]:
    """'वसइ3-00925-2011' -> ('925','2011'); '01597/2007' -> ('1597','2007'); 'TNN-2-7377-2013' -> ('7377','2013')"""
    t = ascii_digits(str(raw or ""))
    m = re.search(r"(\d+)\s*[/\-]\s*((?:19|20)\d{2})\s*$", t.strip())
    if m:
        return str(int(m.group(1))), m.group(2)
    return "", ""


def today_str() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", ascii_digits(str(s or ""))).strip()


# a reader's note about the pages it was given ('The unit is not identified on these pages', 'The schedule defining the
# unit is not in the pages read') is not property data
_PAGE_NOTE = re.compile(
    r"[^.;]*\b(?:not\s+(?:identified|visible|available|mentioned|stated|shown|found|given|legible|specified)|unidentified)\b"
    r"[^.;]*(?:pages?|document|extract|portion)[^.;]*[.;]?"
    r"|[^.;]*\b(?:these|other)\s+pages\b[^.;]*[.;]?"
    r"|[^.;]*\bnot\s+(?:in|on|within)\s+(?:the|these)\s+pages?\b[^.;]*[.;]?"
    r"|[^.;]*\bpages?\s+(?:read|given|provided|supplied)\b[^.;]*[.;]?", re.I)


def clean_value(v):
    """Drop the reader's notes from a field; '' when nothing real is left."""
    if not isinstance(v, str):
        return v
    t = re.sub(r"\s+", " ", _PAGE_NOTE.sub(" ", v)).strip(" .;,")
    return t if re.search(r"[A-Za-z0-9ऀ-ॿ]", t) else ""
