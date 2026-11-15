"""A latest-estimate file kept at P&L level, the way FP&A teams keep one ('Uoc tinh LE 9+3.xlsx'): one sheet, a title
saying full year actual plus estimate, a subtitle with the made-on date and which months are actual (TH) and which
are estimate (UT), month columns 'T10 TH' / 'T11 UT', a 'Cả năm (9+3)' column, rows named by P&L line. No version
column.

The forecast takes from it only the estimate (UT) columns of the months after the report month: the report month
and earlier are actual from the books, even when the file still shows the report month as an estimate (it was made
before the month closed). Lines are revenue, cost of goods sold and operating costs; gross and operating profit are
worked out from them, so the file's own profit rows are never mixed with the books'. The unit comes from the file
('ĐVT: triệu đồng'), else from the annual plan's size. Amounts are Decimal; nothing here is written by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from engine.tables import key

MONTH_COL = re.compile(r"^\s*(?:t|thang)\s*0?(\d{1,2})(?:\s+(\d{4}|\d{2}))?\s+(th|ut|uoc|thuc hien|du bao|fc|act|a|e)\b",
                       re.IGNORECASE)                     # on folded text: 'T10 TH', 'T11/2026 UT', 'Tháng 12 UT' 
ACTUAL_WORDS = ("th", "thuc hien", "act", "a")
LINES = (("revenue", ("doanh thu thuan", "doanh thu", "net revenue", "revenue", "doanh so")),
         ("cogs", ("gia von hang ban", "gia von", "cogs", "cost of goods sold")),
         ("opex_total", ("chi phi hoat dong", "tong chi phi hoat dong", "opex", "chi phi ban hang va quan ly")),
         ("selling", ("chi phi ban hang",)),
         ("admin", ("chi phi quan ly doanh nghiep", "chi phi quan ly", "chi phi qldn")))
SKIP = ("loi nhuan", "lai gop", "ebit", "ty le", "%", "bien")
UNITS = (("ty dong", Decimal(10) ** 9), ("ty", Decimal(10) ** 9), ("trieu dong", Decimal(10) ** 6),
         ("trieu", Decimal(10) ** 6), ("tr", Decimal(10) ** 6), ("nghin dong", Decimal(1000)),
         ("ngan dong", Decimal(1000)), ("nghin", Decimal(1000)), ("dong", Decimal(1)), ("vnd", Decimal(1)))
UNIT_SAID = {"ty dong": "tỷ đồng", "ty": "tỷ đồng", "trieu dong": "triệu đồng", "trieu": "triệu đồng",
             "tr": "triệu đồng", "nghin dong": "nghìn đồng", "ngan dong": "nghìn đồng", "nghin": "nghìn đồng",
             "dong": "đồng", "vnd": "đồng"}
DATE_RE = re.compile(r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})")


@dataclass
class Estimate:
    file: str
    sheet: str
    made_on: str = ""                                   # 'YYYY-MM-DD' from the subtitle, '' when it has none
    unit: Decimal = Decimal(1)
    unit_said: str = ""
    months: dict = field(default_factory=dict)          # 'YYYY-MM' -> {code: Decimal đồng}, estimate columns only
    actual_months: list = field(default_factory=list)   # 'YYYY-MM' the file marks as actual

    def said(self) -> str:
        when = f", lập ngày {self.made_on[8:10]}/{self.made_on[5:7]}/{self.made_on[:4]}" if self.made_on else ""
        return f"file ước tính {self.file}{when}"


def _num(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    t = str(v).strip().replace(" ", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if neg else d


def _code(label) -> Optional[str]:
    k = key(label)
    if not k or any(w in k for w in SKIP):
        return None
    for code, words in LINES:
        if any(k == w or k.startswith(w + " ") for w in words):
            return code
    return None


def read_sheet(rows: list, file: str, sheet: str, year: int) -> Optional[Estimate]:
    """The estimate in one sheet, or None when the sheet has no 'Txx TH/UT' month columns."""
    head_i, cols = None, {}
    for i, r in enumerate(rows[:20]):
        found = {}
        for j, v in enumerate(r or []):
            m = MONTH_COL.match(key(v)) if isinstance(v, str) else None
            if m and 1 <= int(m.group(1)) <= 12:
                yr = int(m.group(2)) if m.group(2) else year
                yr = yr + 2000 if yr < 100 else yr
                found[j] = (f"{yr}-{int(m.group(1)):02d}", m.group(3).lower() in ACTUAL_WORDS)
        if len(found) >= 3:
            head_i, cols = i, found
            break
    if head_i is None:
        return None
    e = Estimate(file, sheet)
    top = " ".join(str(v) for r in rows[:head_i] for v in (r or []) if isinstance(v, str))
    d = DATE_RE.search(top)
    if d:
        e.made_on = f"{int(d.group(3)):04d}-{int(d.group(2)):02d}-{int(d.group(1)):02d}"
    k = key(top)
    unit = re.search(r"(dvt|don vi tinh|don vi)\s*:?\s*([a-z ]{2,12})", k)
    if unit:
        for word, scale in UNITS:
            if unit.group(2).strip().startswith(word):
                e.unit, e.unit_said = scale, UNIT_SAID.get(word, word)
                break
    e.actual_months = sorted({mo for mo, act in cols.values() if act})
    for r in rows[head_i + 1:]:
        label = next((v for v in (r or []) if isinstance(v, str) and v.strip()), None)
        code = _code(label)
        if code is None:
            continue
        for j, (mo, act) in cols.items():
            if act or j >= len(r):
                continue
            n = _num(r[j])
            if n is not None:
                e.months.setdefault(mo, {})
                e.months[mo][code] = e.months[mo].get(code, Decimal(0)) + abs(n) * e.unit
    for mo, got in e.months.items():
        if "opex_total" in got:
            got["opex"] = got.pop("opex_total")
            got.pop("selling", None), got.pop("admin", None)
        elif "selling" in got or "admin" in got:
            got["opex"] = got.pop("selling", Decimal(0)) + got.pop("admin", Decimal(0))
    return e if e.months else None


def find(folder: Path, year: int, is_estimate) -> list:
    """Every P&L-level estimate file in the folder (named as an estimate), newest made-on date last."""
    from engine.tables import sheets_of
    out = []
    for p in sorted(Path(folder).iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".csv") or not is_estimate(p.stem):
            continue
        try:
            sheets = sheets_of(p)
        except Exception:                                # noqa: BLE001 - the intake card says why a file is unread
            continue
        for sheet, rows in sheets.items():
            e = read_sheet(rows, p.name, sheet, year)
            if e is not None:
                out.append(e)
    return sorted(out, key=lambda e: e.made_on)


def scale_to(e: Estimate, plan_month: dict) -> Estimate:
    """With no unit printed, size the file against the annual plan of the same months (millions or thousands)."""
    if e.unit != 1 or not plan_month:
        return e
    ratios = []
    for mo, got in e.months.items():
        p = plan_month.get(mo, {}).get("revenue")
        v = got.get("revenue")
        if p and v:
            ratios.append(p / v)
    if not ratios:
        return e
    r = sorted(ratios)[len(ratios) // 2]
    for scale, word in ((Decimal(10) ** 9, "tỷ đồng"), (Decimal(10) ** 6, "triệu đồng"), (Decimal(1000), "nghìn đồng")):
        if scale / 5 <= r <= scale * 5:
            e.unit, e.unit_said = scale, word + " (suy ra từ cỡ số so với kế hoạch năm)"
            e.months = {mo: {c: v * scale for c, v in got.items()} for mo, got in e.months.items()}
            break
    return e
