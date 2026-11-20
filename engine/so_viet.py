"""Read a figure the way a Vietnamese finance team writes it, from free text (an answer, a PDF line, a pasted cell).

    "1.500.000"   -> 1500000          "1.234,5"  -> 1234.5        "(25,99)" -> -25.99
    "−0,2 tỷ"     -> -0.2, unit tỷ    "7,03%"    -> 7.03, unit %  "3,2 tỷ đồng" -> 3.2, unit tỷ
    "1.500" with Vietnamese marks (vn=True, the default) is one thousand five hundred; with vn=False it is 1.5.
    "1,500" with vn=True is 1.5 only when nothing else says otherwise; a figure with both marks is never guessed:
    the last mark is the decimal one.

Never a float: every figure is a Decimal. A text that is not a figure gives None, never 0.
`find_all(text)` lists every figure in a sentence with its unit, for the golden Q&A grader (tools/golden_qa.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

UNITS = {"tỷ": "tỷ", "ty": "tỷ", "tỉ": "tỷ", "triệu": "triệu", "trieu": "triệu", "tr": "triệu", "nghìn": "nghìn",
         "ngàn": "nghìn", "k": "nghìn", "%": "%", "điểm %": "điểm %", "đ": "đồng", "đồng": "đồng", "₫": "đồng",
         "vnd": "đồng", "vnđ": "đồng", "bn": "tỷ", "billion": "tỷ", "mn": "triệu", "million": "triệu",
         "percentage points": "điểm %", "pp": "điểm %"}
SCALE = {"tỷ": Decimal(10) ** 9, "triệu": Decimal(10) ** 6, "nghìn": Decimal(1000), "đồng": Decimal(1)}

_NUM = re.compile(r"(?P<neg>[(\-−–]\s*)?(?P<n>\d[\d.,  ]*\d|\d)(?P<close>\))?"
                  r"(?:\s*(?P<u>điểm\s*%|percentage points|%|tỷ|tỉ|ty|triệu|trieu|tr|nghìn|ngàn|k|đồng|đ|₫|vnđ|vnd|billion|bn|million|mn|pp)(?![\w]))?", re.I)


@dataclass(frozen=True)
class So:
    value: Decimal          # as written, in its own unit (3,2 tỷ -> 3.2)
    unit: str               # "tỷ", "triệu", "%", "đồng", ... or ""
    text: str               # the characters it was read from

    @property
    def dong(self) -> Optional[Decimal]:
        """The figure in đồng, when its unit is money."""
        return self.value * SCALE[self.unit] if self.unit in SCALE else None


def _digits(s: str, vn: bool) -> Optional[Decimal]:
    s = s.replace(" ", "").replace(" ", "")
    dots, commas = s.count("."), s.count(",")
    if dots and commas:                                  # both marks: the last one is the decimal mark
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        th = "." if dec == "," else ","
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(th) + r"\d{3})*" + re.escape(dec) + r"\d+", s):
            return None
        s = s.replace(th, "").replace(dec, ".")
    elif dots or commas:
        mark = "." if dots else ","
        groups = s.split(mark)
        thousands = len(groups) > 2 or (vn and mark == "." and len(groups[-1]) == 3) or (not vn and mark == "," and len(groups[-1]) == 3)
        if len(groups) > 2 and not all(len(g) == 3 for g in groups[1:]):
            return None
        if thousands and all(len(g) == 3 for g in groups[1:]) and 1 <= len(groups[0]) <= 3:
            s = s.replace(mark, "")
        elif len(groups) == 2:
            s = groups[0] + "." + groups[1]
        else:
            return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse(text, vn: bool = True) -> Optional[So]:
    """The one figure in a short text («(3,2) tỷ», «1.500.000 ₫»), or None."""
    hits = find_all(text, vn)
    return hits[0] if len(hits) == 1 else None


def find_all(text, vn: bool = True) -> list:
    out = []
    s = str(text or "")
    for m in _NUM.finditer(s):
        v = _digits(m.group("n").strip(), vn)
        if v is None:
            continue
        neg = (m.group("neg") or "").strip()
        if neg == "(" and not m.group("close"):
            neg = ""                                     # «(em tính: 11,8 − 12,0)»: a bracket that opens a note
        if neg in ("-", "−", "–"):
            before = s[:m.start()].rstrip()
            if before and (before[-1].isdigit() or before[-1] in ")%"):
                neg = ""                                 # «11,8 − 12,0»: a minus between two figures
        unit = UNITS.get(re.sub(r"\s+", " ", (m.group("u") or "").lower()), "")
        out.append(So(-v if neg else v, unit, m.group(0).strip()))
    return out


def same(a: So, value, unit: str = "", tol=Decimal("0")) -> bool:
    """a says value (in unit) within tol. Money in different units is compared in đồng."""
    want = Decimal(str(value))
    tol = Decimal(str(tol))
    if unit and a.unit != unit:
        if a.unit in SCALE and unit in SCALE:
            return abs(a.dong - want * SCALE[unit]) <= tol * SCALE[unit]
        return False
    return abs(a.value - want) <= tol


def same_figures(source: str, other: str, other_vn: bool = False) -> tuple:
    """The number lock for an answer said in another language: every figure of the Vietnamese answer must be in the
    other one with the same value and unit, and the other must add none. (ok, missing, extra) as texts.
    other_vn: the other text writes Vietnamese marks too (a Vietnamese rewording); False for English (1,234.5)."""
    a = find_all(source, vn=True)
    b = find_all(other, vn=other_vn)
    def key(x):
        return (x.dong if x.dong is not None else x.value, x.unit if x.unit not in SCALE else "đồng")
    left = [key(x) for x in b]
    missing = []
    for x in a:
        k = key(x)
        if k in left:
            left.remove(k)
        else:
            missing.append(x.text)
    extra = [x.text for x in b if key(x) in left]
    return (not missing and not extra), missing, extra
