"""The FP&A month: actual against the approved plan by department x cost element, for the month and the year to
date, after the actuals are matched to the books (the trial balance).

Inputs come from engine.bod_inputs.load: the cost detail (Entry, one line per posting), the plan files (PlanRow,
every version) and the client's configuration workbook (department and cost element names). Amounts are Decimal
đồng; every line keeps the cells it adds up, so each figure traces back to its source rows. Nothing here is
written by the LLM, and nothing is left out in silence: detail lines outside the year, without a month or without
a department are counted and named.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from engine.bod_check import fiscal_year, fy_months
from engine.bod_review import VND_TOLERANCE
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
UNASSIGNED = "Chưa gán bộ phận"
# Versions that hold last year's actual, not a plan.
_NOT_PLAN = {"ly", "thuc te", "actual"}
# Plan lines a P&L states (folded 'Chỉ tiêu'): revenue, cost of goods sold, gross profit. They belong to the sales
# P&L (engine.sales_pnl), not to the department x cost element matrix.
PNL_LINE = {"revenue": re.compile(r"^(doanh thu thuan|dtt|net revenue|net sales|doanh thu)\b"),
            "cogs": re.compile(r"^(gia von|gvhb|cogs|cost of goods)"),
            "gp": re.compile(r"^(loi nhuan gop|lai gop|lng|gross profit|gross margin)\b")}


# Operating cost totals a P&L plan states by line ('Chi phí bán hàng', 'Chi phí quản lý doanh nghiệp').
OPEX_LINE = re.compile(r"^(tong )?(chi phi (ban hang|quan ly|hoat dong|qldn|bh|ql)|cp ?(ban hang|quan ly|bh|ql|qldn)\b|"
                       r"cpbh|cpql|opex|operating (expenses?|costs?)|selling|general (and|&) admin)")


def summary_line(row) -> bool:
    """A plan row that names a P&L line and no department or cost element: a figure of the P&L, never a line of
    the split by department x cost element."""
    return not (getattr(row, "dept", "") or getattr(row, "element", "")) and bool(key(getattr(row, "line", "") or ""))


def opex_line(row) -> bool:
    return summary_line(row) and bool(OPEX_LINE.match(key(getattr(row, "line", "") or "")))


def pnl_line(row) -> str:
    """'revenue', 'cogs' or 'gp' for a plan row that is a P&L line with no department or cost element, else ''."""
    if getattr(row, "dept", "") or getattr(row, "element", ""):
        return ""
    k = key(getattr(row, "line", "") or "")
    return next((code for code, rx in PNL_LINE.items() if rx.match(k)), "")


class PickVersion(ValueError):
    """More than one plan and nothing says which one was approved: Clicky asks, it never picks (not by file name,
    not by date modified). versions are the labels to ask with; choices the (version, file) behind each."""

    def __init__(self, choices: list):
        clash = len({key(v) for v, _f in choices}) < len(choices)        # one version in two files
        self.choices = choices
        self.versions = [f"{v} ({f})" if clash else v for v, f in choices]
        super().__init__("chọn bản kế hoạch: " + ", ".join(self.versions))


@dataclass
class Plan:
    version: str
    file: str
    approved: str = ""              # 'YYYY-MM-DD' when the file says it was approved

    def basis(self, only: bool) -> str:
        said = f"Kế hoạch: file {self.file}, phiên bản {self.version}"
        if self.approved:
            return said + f", duyệt ngày {self.approved[8:10]}/{self.approved[5:7]}/{self.approved[:4]}"
        return said + (", bản kế hoạch duy nhất trong thư mục" if only else "")


def plan_choices(plans: list) -> list:
    """Each plan in the folder: one per version and file, last year's actual left out."""
    out = {}
    for r in plans:
        v = (r.version or "").strip()
        if not v or key(v) in _NOT_PLAN:
            continue
        p = out.setdefault((key(v), r.file), Plan(v, r.file))
        p.approved = max(p.approved, r.approved or "")
    return [out[k] for k in sorted(out)]


def choose_plan(plans: list, version: Optional[str] = None, file: Optional[str] = None) -> tuple:
    """(Plan or None, the only one in the folder?). One approved plan among several is the one; otherwise asked."""
    found = plan_choices(plans)
    if version:
        found = [p for p in found if key(p.version) == key(version)]
    if file:
        found = [p for p in found if p.file == file]
    if len(found) > 1:
        approved = [p for p in found if p.approved]
        if len(approved) != 1:
            raise PickVersion([(p.version, p.file) for p in found])
        return approved[0], False
    return (found[0] if found else None), len(plan_choices(plans)) == 1


@dataclass
class Line:
    """One department x cost element, for one month or the year to date."""
    dept: str                       # "" when the detail names no department
    element: str
    dept_name: str = ""
    element_name: str = ""
    actual: Decimal = ZERO
    plan: Decimal = ZERO
    actual_from: list = field(default_factory=list)     # "Sheet!row" of each detail line
    plan_from: list = field(default_factory=list)       # plan cells ("Sheet!F12")
    accounts: set = field(default_factory=set)          # accounts the actual was posted to
    unmatched: bool = False                             # posted to an account that does not match the books

    @property
    def variance(self) -> Decimal:
        """Actual minus plan: above zero the cost ran over plan."""
        return self.actual - self.plan

    @property
    def pct(self) -> Optional[Decimal]:
        """Variance as a share of plan, in percent, one decimal; None without a plan."""
        if not self.plan:
            return None
        return (self.variance / abs(self.plan) * 100).quantize(Decimal("0.1"))

    @property
    def over(self) -> bool:
        return self.variance > VND_TOLERANCE

    @property
    def unplanned(self) -> bool:
        """Spent with no plan for it."""
        return not self.plan and abs(self.actual) > VND_TOLERANCE

    @property
    def unspent(self) -> bool:
        """Planned and nothing spent."""
        return not self.actual and abs(self.plan) > VND_TOLERANCE

    @property
    def label(self) -> str:
        dept = self.dept_name or self.dept or UNASSIGNED
        return f"{dept} · {self.element_name or self.element}"


@dataclass
class Variance:
    period: str                                         # "YYYY-MM"
    months: list                                        # the year to date, ending with period
    version: str
    plan: Optional[Plan] = None                         # the plan compared against; None when the folder has none
    basis: str = ""                                     # which plan and why, for the report and the Biên bản
    month: list = field(default_factory=list)           # [Line]
    ytd: list = field(default_factory=list)             # [Line]
    no_month: list = field(default_factory=list)        # [(where, amount)] detail lines without a readable month
    outside: list = field(default_factory=list)         # [(where, period, amount)] detail outside the year to date
    unknown: dict = field(default_factory=dict)         # {"bộ phận": [codes], "yếu tố chi phí": [codes]} not in the catalogue
    no_element: list = field(default_factory=list)      # [where] detail lines without a cost element (grouped by account)

    @staticmethod
    def totals(lines: list) -> tuple:
        """(actual, plan)."""
        return sum((l.actual for l in lines), ZERO), sum((l.plan for l in lines), ZERO)

    def biggest(self, n: int = 5, ytd: bool = False) -> list:
        """The lines that moved most against plan, largest first (by size, either way)."""
        lines = [l for l in (self.ytd if ytd else self.month) if abs(l.variance) > VND_TOLERANCE]
        return sorted(lines, key=lambda l: (-abs(l.variance), l.dept, l.element))[:n]

    def left_out(self) -> list:
        """What the figures do not include, in plain words, one line each (rule 4: never in silence)."""
        out = []
        if self.no_month:
            out.append(f"{len(self.no_month)} dòng chi phí không rõ tháng, tổng {fmt(sum(a for _, a in self.no_month))} đ")
        later = [x for x in self.outside if x[1] > self.period]
        earlier = [x for x in self.outside if x[1] < self.period]
        if later:
            out.append(f"{len(later)} dòng chi phí sau tháng {_month_text(self.period)}, "
                       f"tổng {fmt(sum(a for _, _, a in later))} đ")
        if earlier:
            out.append(f"{len(earlier)} dòng chi phí trước năm tài chính này, tổng {fmt(sum(a for _, _, a in earlier))} đ")
        if self.no_element:
            out.append(f"{len(self.no_element)} dòng chi phí không có mã yếu tố chi phí, em tạm xếp theo tài khoản")
        for what, codes in self.unknown.items():
            if codes:
                out.append(f"Mã {what} chưa có trong danh mục: {', '.join(codes[:5])}"
                           + (f" và {len(codes) - 5} mã khác" if len(codes) > 5 else "")
                           + "; em vẫn tính theo mã, cần chị cho biết gán vào đâu")
        if self.plan is None:
            out.append("Không có file kế hoạch: em chỉ có số thực hiện, chưa so được với kế hoạch")
        return out


def _month_text(period: str) -> str:
    return f"{period[5:7]}/{period[:4]}"


def latest_month(entries: list) -> Optional[str]:
    """The month under review: the latest month with a real share of the detail, so a few lines posted early
    into next month do not make it the month (they are named as outside the period instead)."""
    from collections import Counter
    counts = Counter(e.period for e in entries if e.period)
    if not counts:
        return None
    return max(p for p, n in counts.items() if n >= max(counts.values()) / 4)


def _names(config) -> tuple:
    depts = {code: d.get("ten", "") for code, d in (getattr(config, "depts", {}) or {}).items()}
    elements = {code: e.get("ten", "") for code, e in (getattr(config, "elements", {}) or {}).items()}
    return depts, elements


def _element_of(entry) -> str:
    """The cost element; a detail line without one is grouped by its account so it is never lost."""
    return entry.element or (f"TK {entry.account}" if entry.account else "")


def compute(gl: list, plans: list, period: Optional[str] = None, version: Optional[str] = None, fy_start: int = 1,
            config=None, plan_file: Optional[str] = None) -> Variance:
    """Actual (the cost detail) against plan, by department x cost element, for period and the year to date.

    period defaults to the latest month of the detail. version (and plan_file) name the plan when the folder holds
    more than one and only one of them says it was approved (PickVersion otherwise). fy_start is the month the
    fiscal year starts (1: the calendar year)."""
    chosen, only = choose_plan(plans, version, plan_file)
    period = period or latest_month(gl)
    if not period:
        raise ValueError("không có dòng chi phí nào có tháng")
    fy = fiscal_year(period, fy_start)
    year = fy_months(fy, fy_start)
    months = year[:year.index(period) + 1]
    v = Variance(period, months, chosen.version if chosen else "", chosen, chosen.basis(only) if chosen else "")
    depts, elements = _names(config)
    month, ytd = {}, {}

    def line(book: dict, dept: str, element: str) -> Line:
        k = (dept, element)
        if k not in book:
            book[k] = Line(dept, element, depts.get(dept, ""), elements.get(element, ""))
        return book[k]

    for e in gl:
        if not e.period:
            v.no_month.append((e.where, e.amount))
            continue
        if e.period not in months:
            v.outside.append((e.where, e.period, e.amount))
            continue
        if not e.element:
            v.no_element.append(e.where)
        targets = [line(ytd, e.dept, _element_of(e))] + ([line(month, e.dept, _element_of(e))] if e.period == period else [])
        for t in targets:
            t.actual += e.amount
            t.actual_from.append(e.where)
            if e.account:
                t.accounts.add(e.account)
    for r in plans if chosen else []:
        if key(r.version or "") != key(chosen.version) or r.file != chosen.file or r.month not in months:
            continue
        if summary_line(r):                             # a P&L line (revenue, a cost total): not a line of the split
            continue
        targets = [line(ytd, r.dept, r.element)] + ([line(month, r.dept, r.element)] if r.month == period else [])
        for t in targets:
            t.plan += r.amount
            t.plan_from.append(r.where)
    known_depts = set(getattr(config, "depts", {}) or {})
    known_elements = set(getattr(config, "elements", {}) or {})
    if known_depts:
        v.unknown["bộ phận"] = sorted({d for d, _e in ytd if d and d not in known_depts})
    if known_elements:
        v.unknown["yếu tố chi phí"] = sorted({e for _d, e in ytd if e and e not in known_elements
                                              and not e.startswith("TK ")})
    order = lambda l: (l.dept == "", l.dept, l.element)       # noqa: E731 - unassigned last
    v.month = sorted(month.values(), key=order)
    v.ytd = sorted(ytd.values(), key=order)
    return v


# ── Actual against the books ──────────────────────────────────────────────────

@dataclass
class BookLine:
    account: str
    name: str
    books: Decimal                  # Phát sinh Nợ of the trial balance, as the P&L reads it (engine.monthly_report)
    detail: Decimal                 # the cost detail posted on the debit side, the same month (a red entry is negative)
    reductions: Decimal = ZERO      # the detail posted on the credit side (cost written down): in the books' Có
    rows: list = field(default_factory=list)            # trial balance rows
    lines: int = 0                  # detail lines

    @property
    def diff(self) -> Decimal:
        return self.detail - self.books


@dataclass
class BookMatch:
    period: str
    lines: list = field(default_factory=list)

    @property
    def gaps(self) -> list:
        return [l for l in self.lines if abs(l.diff) > VND_TOLERANCE]

    @property
    def ok(self) -> bool:
        return bool(self.lines) and not self.gaps

    @property
    def totals(self) -> tuple:
        """(books, detail, reductions)."""
        return (sum((l.books for l in self.lines), ZERO), sum((l.detail for l in self.lines), ZERO),
                sum((l.reductions for l in self.lines), ZERO))

    def sentence(self) -> str:
        """One line for the receipt."""
        books, _detail, reductions = self.totals
        month = _month_text(self.period)
        if not self.lines:
            return "Em chưa khớp được chi phí với sổ cân đối: không có tài khoản chi phí nào để so."
        if not self.gaps:
            down = (f" Có thêm {fmt(reductions)} đ ghi giảm chi phí bên Có, em đã trừ vào số thực tế."
                    if reductions else "")
            return (f"Chi phí theo bộ phận tháng {month} khớp sổ cân đối: {fmt(books)} đ phát sinh Nợ "
                    f"trên {len(self.lines)} tài khoản.{down}")
        g = self.gaps[0]
        more = f" và {len(self.gaps) - 1} tài khoản khác" if len(self.gaps) > 1 else ""
        return (f"Chi phí theo bộ phận tháng {month} lệch sổ cân đối ở TK {g.account}{more}: "
                f"sổ cân đối {fmt(g.books)} đ, file chi tiết {fmt(g.detail)} đ. Em chưa giải thích chênh lệch kế "
                "hoạch của các dòng dùng tài khoản này.")


def match_books(gl: list, tb, period: str) -> BookMatch:
    """The month's cost detail against the trial balance, account by account, on the debit side (Phát sinh Nợ,
    as the P&L reads it). Detail posted on the credit side (a cost written down) is in the books' Có next to
    the closing entry to 911, so it is shown beside the match, not in it. Without debit/credit account columns
    every detail line counts as a debit.

    The accounts compared are the trial balance's accounts under every 3-digit parent the detail posts to. A
    detail account finer than the books (6421.01 under 6421) is summed into the books' account; one the books
    split further (6421 over 64211, 64212) takes the sum of those. An account on one side only is a gap too."""
    m = BookMatch(period)
    entries = [e for e in gl if e.period == period and e.account]
    parents = {e.account[:3] for e in entries}
    leaves = [l for l in tb.leaves if l.account[:3] in parents]
    codes = {l.account for l in tb.lines}

    def books_key(account: str) -> str:
        if account in codes:
            return account
        coarser = [l.account for l in leaves if account.startswith(l.account)]
        return max(coarser, key=len) if coarser else account

    debit, credit, count = {}, {}, {}
    for e in entries:
        k = books_key(e.account)
        if getattr(e, "side", "") == "cr":
            credit[k] = credit.get(k, ZERO) - e.amount
        else:
            debit[k] = debit.get(k, ZERO) + e.amount
        count[k] = count.get(k, 0) + 1
    keys = set(debit) | set(credit)
    keys |= {l.account for l in leaves if not any(l.account.startswith(k) for k in keys)}
    names = {l.account: l.name for l in tb.lines}
    for k in sorted(keys):
        under = [l for l in leaves if l.account.startswith(k)]
        m.lines.append(BookLine(k, names.get(k, ""), sum((l.dr for l in under), ZERO), debit.get(k, ZERO),
                                credit.get(k, ZERO), [l.row for l in under], count.get(k, 0)))
    return m


def flag_unmatched(v: Variance, match: BookMatch) -> list:
    """Mark the month's lines posted to an account that does not match the books; returns them."""
    gaps = [g.account for g in match.gaps]
    hit = []
    for l in v.month:
        if any(a.startswith(g) or g.startswith(a) for a in l.accounts for g in gaps):
            l.unmatched = True
            hit.append(l)
    return hit
