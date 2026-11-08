"""The month the fiscal year starts, for the FP&A month, the forecast and the opex split: read from the folder, never
assumed in silence.

In order: the catalogue rule ("Tháng bắt đầu" / "Niên độ"); the month columns of a plan (T01 .. T12, or T07 .. T06,
in the order the file prints them); a plan version that runs over twelve straight months; the trial balance's year
to date against the ledger's months; a ledger that runs across a new calendar year. Else January, and that is said.
Each answer comes with the sentence the receipt prints. Nothing here is a guess the user cannot see.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Optional

JANUARY = 1


def _month_text(n: int) -> str:
    return f"tháng {n}"


def _from_rule(config) -> Optional[int]:
    from engine.bod_inputs import num
    fy = num(config.rule("thang bat dau")) or num(config.rule("nien do"))
    return int(fy) if fy is not None and 1 <= int(fy) <= 12 else None


def _from_plan_columns(b) -> Optional[tuple]:
    """A plan printed with one column per month: the first of twelve months is the year's first."""
    for file, order in getattr(b, "month_orders", []):
        if len(order) >= 12 and len(set(order[:12])) == 12:
            return order[0], file
    return None


def _from_plan_months(b) -> Optional[tuple]:
    """A plan version whose rows name twelve straight months (with their year): the earliest is the year's first."""
    by = {}
    for r in list(b.plans) + list(b.sku_plans):
        if r.month and r.month_no is None:               # the row named its year, not only a month
            by.setdefault((r.file, r.version), set()).add(r.month)
    for (file, _v), months in sorted(by.items()):
        ms = sorted(months)
        if len(ms) == 12 and _straight(ms):
            return int(ms[0][5:]), file
    return None


def _straight(ms: list) -> bool:
    for a, b in zip(ms, ms[1:]):
        y, m = int(a[:4]), int(a[5:])
        if (y + (m == 12), 1 if m == 12 else m + 1) != (int(b[:4]), int(b[5:])):
            return False
    return True


def _from_books(b, tb, period: Optional[str], books_ytd: Optional[Decimal]) -> Optional[int]:
    """The trial balance's year to date of operating costs equals the ledger's months from one month to the period:
    that month is the year's first. Only when exactly one month fits."""
    if tb is None or books_ytd is None or not period or not b.gl:
        return None
    by = Counter()
    for e in b.gl:
        if e.period and getattr(e, "side", "") != "cr":     # the books' Nợ: a cost written down is on Có
            by[e.period] += e.amount
    months = sorted(m for m in by if m <= period)
    fits = []
    for i in range(len(months)):
        run = months[i:]
        if len(run) <= 12 and _straight(run) and run[-1] == period and sum((by[m] for m in run), Decimal(0)) == books_ytd:
            fits.append(int(run[0][5:]))
    return fits[0] if len(fits) == 1 else None


def _from_ledger(b) -> Optional[int]:
    """A ledger that runs from one month across a new calendar year, with no gap and at most twelve months."""
    months = sorted({e.period for e in b.gl if e.period} | {s.month for s in b.sales if s.month})
    if len(months) < 2 or len(months) > 12 or not _straight(months):
        return None
    return int(months[0][5:]) if months[0][:4] != months[-1][:4] else None


def start(b, tb=None, period: Optional[str] = None, books_ytd: Optional[Decimal] = None) -> tuple:
    """(first month of the fiscal year, the receipt's sentence)."""
    n = _from_rule(b.config)
    if n is not None:
        return n, f"Năm tài chính bắt đầu từ {_month_text(n)}, theo danh mục của chị."
    hit = _from_plan_columns(b)
    if hit:
        return hit[0], f"Năm tài chính bắt đầu từ {_month_text(hit[0])}: file kế hoạch {hit[1]} xếp cột tháng từ " \
                       f"{_month_text(hit[0])}."
    hit = _from_plan_months(b)
    if hit:
        return hit[0], f"Năm tài chính bắt đầu từ {_month_text(hit[0])}: file kế hoạch {hit[1]} có đủ 12 tháng bắt " \
                       f"đầu từ {_month_text(hit[0])}."
    n = _from_books(b, tb, period, books_ytd)
    if n is not None:
        return n, f"Năm tài chính bắt đầu từ {_month_text(n)}: lũy kế chi phí trên bảng cân đối bằng tổng các tháng " \
                  f"từ {_month_text(n)} của sổ chi tiết."
    n = _from_ledger(b)
    if n is not None:
        return n, f"Năm tài chính bắt đầu từ {_month_text(n)}: sổ chi tiết chạy liền từ {_month_text(n)} sang năm sau."
    return JANUARY, ("Thư mục không ghi năm tài chính bắt đầu tháng nào, nên em tính từ tháng 1. Nếu công ty bắt đầu "
                     "tháng khác, chị ghi “Tháng bắt đầu” trong sheet Tham số của danh mục rồi làm lại.")
