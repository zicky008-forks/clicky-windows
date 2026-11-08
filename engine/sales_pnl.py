"""Revenue, cost of goods sold and gross profit for the FP&A month, from the sales ledger by SKU x channel, against
the approved plan by SKU, for the month and the year to date. Then the gross profit bridge of engine.gp_bridge, and
the ledger matched to the books (trial balance).

Volumes are converted to each SKU's standard unit from the catalogue before anything is added or compared (FP&A
principle 2); a printed total row in an export is never added (engine.tables skips and names it). A SKU missing
from the catalogue, or a unit the catalogue cannot convert, is named and still counted in revenue and cost; only
the bridge, which needs volumes, waits for it. Amounts are Decimal; nothing here is written by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from engine import gp_bridge
from engine.bod_check import fiscal_year, fy_months
from engine.bod_review import VND_TOLERANCE
from engine.fpa import Plan, _month_text, choose_plan, pnl_line
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
NO_CHANNEL = "Chưa rõ kênh"
LINES = (("revenue", "Doanh thu thuần"), ("cogs", "Giá vốn hàng bán"), ("gp", "Lợi nhuận gộp"))



# ── Units ─────────────────────────────────────────────────────────────────────

def standard_qty(config, sku: str, unit: str, qty: Optional[Decimal]) -> tuple:
    """(volume in the SKU's standard unit or None, standard unit as printed, how): how is '' when converted as the
    catalogue says, 'assumed' when the row names no unit and the standard is taken, 'no catalogue' when the
    catalogue lists no SKUs (the row's unit stands), 'unknown sku' or 'no factor' when it cannot be converted."""
    if qty is None:
        return None, "", "no qty"
    skus = getattr(config, "skus", {}) or {}
    if not skus:
        return qty, unit, "no catalogue"
    if sku not in skus:
        return None, "", "unknown sku"
    std = skus[sku]["dvt"]
    if not unit:
        return qty, std, "assumed"
    if key(unit) == key(std):
        return qty, std, ""
    units = getattr(config, "units", {}) or {}
    hit = units.get((sku, key(unit)))
    if hit is None:
        generic = units.get(("", key(unit)))
        hit = generic if generic and generic[1] == key(std) else None
    if hit is None:
        return None, std, "no factor"
    return qty * hit[0], std, ""


# ── Lines ─────────────────────────────────────────────────────────────────────

@dataclass
class SkuLine:
    """One SKU x channel for the month or the year to date. qty is in the standard unit, None when a row of it
    could not be converted (it is named in SalesPnl.unconverted)."""
    sku: str
    channel: str
    name: str = ""
    unit: str = ""
    qty: Optional[Decimal] = ZERO
    revenue: Decimal = ZERO
    cogs: Decimal = ZERO
    plan_qty: Optional[Decimal] = ZERO
    plan_revenue: Decimal = ZERO
    plan_cogs: Decimal = ZERO
    actual_from: list = field(default_factory=list)      # 'Sheet!row' of each ledger row
    plan_from: list = field(default_factory=list)

    @property
    def gp(self) -> Decimal:
        return self.revenue - self.cogs

    @property
    def plan_gp(self) -> Decimal:
        return self.plan_revenue - self.plan_cogs


@dataclass
class PnlLine:
    """Revenue, cost of goods sold or gross profit, for the month or the year to date."""
    code: str                       # 'revenue', 'cogs', 'gp'
    name: str
    actual: Decimal = ZERO
    plan: Decimal = ZERO

    @property
    def variance(self) -> Decimal:
        return self.actual - self.plan

    @property
    def pct(self) -> Optional[Decimal]:
        if not self.plan:
            return None
        return (self.variance / abs(self.plan) * 100).quantize(Decimal("0.1"))

    @property
    def favourable(self) -> Optional[bool]:
        """Revenue and profit above plan, cost below it; None within a đồng."""
        if abs(self.variance) <= VND_TOLERANCE:
            return None
        return (self.variance < 0) if self.code == "cogs" else (self.variance > 0)


class CannotBridge(ValueError):
    """The bridge needs every volume in one unit and a cost for every SKU; reasons say what is missing."""

    def __init__(self, reasons: list):
        self.reasons = reasons
        super().__init__("chưa tính được cầu nối lợi nhuận gộp: " + "; ".join(reasons))


@dataclass
class SalesPnl:
    period: str
    months: list
    version: str = ""
    plan: Optional[Plan] = None
    basis: str = ""
    month: list = field(default_factory=list)            # [SkuLine]
    ytd: list = field(default_factory=list)
    no_month: list = field(default_factory=list)         # [(where, net)]
    outside: list = field(default_factory=list)          # [(where, month, net)]
    unknown_skus: list = field(default_factory=list)     # SKU codes sold or planned, not in the catalogue
    unconverted: list = field(default_factory=list)      # [(where, sku, unit, month)] volumes the catalogue cannot convert
    assumed_unit: list = field(default_factory=list)     # [where] rows without a unit, taken as the standard
    no_qty: list = field(default_factory=list)           # [(where, month)] ledger rows without a volume
    no_cogs: list = field(default_factory=list)          # [(where, month)] ledger rows without cost of goods sold
    no_sku: list = field(default_factory=list)           # [where] ledger rows without a SKU
    plan_no_cost: list = field(default_factory=list)     # [where] plan rows without a cost
    mixed_units: list = field(default_factory=list)      # units seen when the catalogue lists no SKUs and they differ
    plan_issues: list = field(default_factory=list)      # sentences: the plan file disagrees with itself

    def lines(self, ytd: bool = False) -> list:
        book = self.ytd if ytd else self.month
        rev = sum((l.revenue for l in book), ZERO)
        cogs = sum((l.cogs for l in book), ZERO)
        prev = sum((l.plan_revenue for l in book), ZERO)
        pcogs = sum((l.plan_cogs for l in book), ZERO)
        return [PnlLine("revenue", LINES[0][1], rev, prev), PnlLine("cogs", LINES[1][1], cogs, pcogs),
                PnlLine("gp", LINES[2][1], rev - cogs, prev - pcogs)]

    def bridge_blockers(self, ytd: bool = False) -> list:
        """Why the bridge cannot be computed yet, in plain words; [] when it can."""
        months = set(self.months if ytd else [self.period])
        out = []
        if self.plan is None:
            out.append("không có kế hoạch theo mã hàng")
        if self.mixed_units:
            out.append("danh mục không có bảng mã hàng mà sổ bán hàng dùng nhiều đơn vị ("
                       + ", ".join(self.mixed_units) + "), em chưa quy được về một đơn vị")
        bad = sorted({s for _w, s, _u, m in self.unconverted if m in months})
        if bad:
            out.append("chưa quy đổi được sản lượng của mã " + ", ".join(bad[:5])
                       + (f" và {len(bad) - 5} mã khác" if len(bad) > 5 else ""))
        missing = [w for w, m in self.no_cogs if m in months]
        if missing:
            out.append(f"{len(missing)} dòng bán hàng không có giá vốn")
        noqty = [w for w, m in self.no_qty if m in months]
        if noqty:
            out.append(f"{len(noqty)} dòng bán hàng không có sản lượng")
        return out

    def bridge(self, ytd: bool = False) -> gp_bridge.Bridge:
        """The four parts of the gross profit gap, by SKU with all channels together (the published convention).
        Raises CannotBridge when a volume or a cost is missing, rather than bridging part of the month."""
        reasons = self.bridge_blockers(ytd)
        if reasons:
            raise CannotBridge(reasons)
        actual, plan = {}, {}
        for l in self.ytd if ytd else self.month:
            if l.actual_from:
                a = actual.setdefault(l.sku, gp_bridge.Sku())
                a.qty += l.qty
                a.revenue += l.revenue
                a.cogs += l.cogs
                a.sources += l.actual_from
            if l.plan_from:
                b = plan.setdefault(l.sku, gp_bridge.Sku())
                b.qty += l.plan_qty
                b.revenue += l.plan_revenue
                b.cogs += l.plan_cogs
                b.sources += l.plan_from
        return gp_bridge.bridge(actual, plan)

    def left_out(self) -> list:
        """What the figures do not include or had to assume, one line each, in plain words."""
        out = []
        if self.no_month:
            out.append(f"{len(self.no_month)} dòng bán hàng không rõ tháng, doanh thu {fmt(sum(a for _, a in self.no_month))} đ")
        later = [x for x in self.outside if x[1] > self.period]
        if later:
            out.append(f"{len(later)} dòng bán hàng sau tháng {_month_text(self.period)}, "
                       f"doanh thu {fmt(sum(a for _, _, a in later))} đ")
        earlier = [x for x in self.outside if x[1] < self.period]
        if earlier:
            out.append(f"{len(earlier)} dòng bán hàng trước năm tài chính này, doanh thu {fmt(sum(a for _, _, a in earlier))} đ")
        if self.unknown_skus:
            out.append("Mã hàng chưa có trong danh mục: " + ", ".join(self.unknown_skus[:5])
                       + (f" và {len(self.unknown_skus) - 5} mã khác" if len(self.unknown_skus) > 5 else "")
                       + "; em vẫn tính doanh thu và giá vốn, cần chị cho biết đơn vị chuẩn")
        units = sorted({(s, u) for _w, s, u, _m in self.unconverted if s not in self.unknown_skus and u})
        if units:
            out.append("Danh mục chưa có hệ số quy đổi: " + ", ".join(f"{s} theo {u}" for s, u in units[:5])
                       + (f" và {len(units) - 5} chỗ khác" if len(units) > 5 else ""))
        if self.assumed_unit:
            out.append(f"{len(self.assumed_unit)} dòng không ghi đơn vị tính, em hiểu là đơn vị chuẩn của danh mục")
        if self.no_sku:
            out.append(f"{len(self.no_sku)} dòng bán hàng không có mã hàng, em gom vào 'Chưa rõ mã hàng'")
        if self.no_qty:
            out.append(f"{len(self.no_qty)} dòng bán hàng không có sản lượng")
        if self.no_cogs:
            out.append(f"{len(self.no_cogs)} dòng bán hàng không có giá vốn")
        if self.plan_no_cost:
            out.append(f"{len(self.plan_no_cost)} dòng kế hoạch không có giá vốn, em tính giá vốn kế hoạch bằng 0")
        out += self.plan_issues
        if self.plan is None:
            out.append("Không có kế hoạch theo mã hàng: em chỉ có số thực hiện")
        return out


# ── Compute ───────────────────────────────────────────────────────────────────

def latest_month(sales: list) -> Optional[str]:
    from collections import Counter
    counts = Counter(s.month for s in sales if s.month)
    if not counts:
        return None
    return max(p for p, n in counts.items() if n >= max(counts.values()) / 4)


def compute(sales: list, sku_plans: list, plans: Optional[list] = None, period: Optional[str] = None,
            version: Optional[str] = None, fy_start: int = 1, config=None, plan_file: Optional[str] = None,
            plan_tables: Optional[list] = None) -> SalesPnl:
    """sales: bod_inputs.Sale rows; sku_plans: SkuPlanRow, every version; plans: PlanRow, to check P&L lines the
    plan file states against its SKU lines; plan_tables: Bundle.sku_plan_totals, to check printed totals."""
    chosen, only = choose_plan(sku_plans, version, plan_file)
    period = period or latest_month(sales)
    if not period:
        raise ValueError("không có dòng bán hàng nào có tháng")
    year = fy_months(fiscal_year(period, fy_start), fy_start)
    months = year[:year.index(period) + 1]
    v = SalesPnl(period, months, chosen.version if chosen else "", chosen, chosen.basis(only) if chosen else "")
    names = {c: d.get("ten", "") for c, d in (getattr(config, "skus", {}) or {}).items()}
    known = set(names)
    month, ytd = {}, {}
    units_seen = {}                                   # folded unit -> as printed

    def line(book: dict, sku: str, channel: str, name: str) -> SkuLine:
        k = (sku, key(channel))
        if k not in book:
            book[k] = SkuLine(sku, channel or NO_CHANNEL, names.get(sku) or name)
        return book[k]

    def add_qty(l: SkuLine, attr: str, q: Optional[Decimal], unit: str) -> None:
        cur = getattr(l, attr)
        setattr(l, attr, None if cur is None or q is None else cur + q)
        l.unit = l.unit or unit

    for s in sales:
        if not s.month:
            v.no_month.append((s.where, s.net))
            continue
        if s.month not in months:
            v.outside.append((s.where, s.month, s.net))
            continue
        sku = s.sku or "Chưa rõ mã hàng"
        if not s.sku:
            v.no_sku.append(s.where)
        q, std, how = standard_qty(config, s.sku, s.unit, s.qty)
        if how == "no qty":
            v.no_qty.append((s.where, s.month))
        elif how in ("unknown sku", "no factor"):
            v.unconverted.append((s.where, sku, s.unit, s.month))
        elif how == "assumed":
            v.assumed_unit.append(s.where)
        elif how == "no catalogue":
            units_seen.setdefault(key(s.unit), s.unit)
        if s.cogs is None:
            v.no_cogs.append((s.where, s.month))
        for book in (ytd, month) if s.month == period else (ytd,):
            l = line(book, sku, s.channel, s.name)
            add_qty(l, "qty", q, std)
            l.revenue += s.net
            l.cogs += s.cogs or ZERO
            l.actual_from.append(s.where)
    for r in sku_plans if chosen else []:
        if key(r.version or "") != key(chosen.version) or r.file != chosen.file or r.month not in months:
            continue
        q, std, how = standard_qty(config, r.sku, r.unit, r.qty)
        if how in ("unknown sku", "no factor"):
            v.unconverted.append((r.where, r.sku, r.unit, r.month))
        elif how == "no catalogue":
            units_seen.setdefault(key(r.unit), r.unit)
        if r.no_cost:
            v.plan_no_cost.append(r.where)
        for book in (ytd, month) if r.month == period else (ytd,):
            l = line(book, r.sku, r.channel, r.name)
            add_qty(l, "plan_qty", q, std)
            l.plan_revenue += r.revenue
            l.plan_cogs += r.cogs
            l.plan_from.append(r.where)
    if known:
        v.unknown_skus = sorted({l.sku for l in ytd.values() if l.sku not in known and l.sku != "Chưa rõ mã hàng"})
    units_seen.pop("", None)
    if len(units_seen) > 1:
        v.mixed_units = [units_seen[k] for k in sorted(units_seen)]
    order = lambda l: (l.sku, key(l.channel))        # noqa: E731
    v.month = sorted(month.values(), key=order)
    v.ytd = sorted(ytd.values(), key=order)
    if chosen:
        v.plan_issues = _plan_issues(v, chosen, plans or [], plan_tables or [])
    return v


# ── The plan against itself ───────────────────────────────────────────────────

def _plan_issues(v: SalesPnl, chosen: Plan, plans: list, tables: list) -> list:
    """Where the plan file states a revenue, cost or gross profit that its SKU lines do not add up to. The SKU lines
    are what the bridge uses; the stated figure is named beside it, never absorbed into mix."""
    out = []
    stated = {}                                         # the month under review: what the bridge explains
    for r in plans:
        code = pnl_line(r)
        if code and key(r.version or "") == key(chosen.version) and r.file == chosen.file and r.month == v.period:
            got = stated.setdefault(code, [ZERO, []])
            got[0] += r.amount
            got[1].append(r.where)
    attr = {"revenue": "plan_revenue", "cogs": "plan_cogs", "gp": "plan_gp"}
    held = [h for file, t, _rows in tables if file == chosen.file for h in getattr(t, "held", [])
            if key(h[1]) == key(chosen.version) and h[2] == v.period]
    held_amount = {"revenue": sum((h[3] for h in held), ZERO), "cogs": sum((h[4] for h in held), ZERO)}
    held_amount["gp"] = held_amount["revenue"] - held_amount["cogs"]
    for code, (said, where) in sorted(stated.items(), key=lambda x: [c for c, _n in LINES].index(x[0])):
        made = sum((getattr(l, attr[code]) for l in v.month if l.plan_from), ZERO)
        if abs(made - said) > VND_TOLERANCE:
            name = dict(LINES)[code].lower()
            text = (f"File kế hoạch {chosen.file} ghi {name} tháng {_month_text(v.period)} là {fmt(said)} đ "
                    f"({', '.join(where[:2])}), nhưng cộng các dòng mã hàng được {fmt(made)} đ, lệch "
                    f"{fmt(made - said)} đ.")
            if held:
                cells = ", ".join(h[0] for h in held[:3]) + (f" và {len(held) - 3} dòng khác" if len(held) > 3 else "")
                exact = abs(made + held_amount[code] - said) <= VND_TOLERANCE
                text += (f" {'Chênh lệch này bằng đúng' if exact else 'Có'} {len(held)} dòng kế hoạch không có sản lượng "
                         f"({cells}, {name} {fmt(held_amount[code])} đ) em chưa cộng vì cầu nối cần sản lượng.")
            out.append(text + " Em dùng các dòng mã hàng cho cầu nối, chênh lệch này không đưa vào phần cơ cấu; "
                              "cần chị cho biết số nào đúng")
    for file, t, rows in tables:
        if file != chosen.file:
            continue
        out += _printed_totals(file, t, rows)
    return out


def _printed_totals(file: str, t, rows: list) -> list:
    """A printed total row of a plan-by-SKU table is never added; it is checked. It agrees when it equals the lines
    since the previous total row, or every line above it."""
    from engine.bod_inputs import num
    out = []
    cols = {"sl": "qty", "dt": "revenue", "gia_von": "cogs"}
    prev = 0
    for n, values in t.totals:
        above = [r for r in rows if int(r.where.rsplit("!", 1)[1]) < n]
        block = [r for r in above if int(r.where.rsplit("!", 1)[1]) > prev]
        for col, attr in cols.items():
            if col not in t.columns or t.columns[col] >= len(values):
                continue
            printed = num(values[t.columns[col]])
            if printed is None:
                continue
            sums = {sum((getattr(r, attr) for r in group), ZERO) for group in (block, above)}
            if all(abs(printed - s) > VND_TOLERANCE for s in sums):
                made = sum((getattr(r, attr) for r in block), ZERO)
                what = {"qty": "sản lượng", "revenue": "doanh thu", "cogs": "giá vốn"}[attr]
                out.append(f"File kế hoạch {file}, sheet {t.sheet} dòng {n}: dòng tổng in sẵn ghi {what} "
                           f"{fmt(printed)}, cộng các dòng mã hàng phía trên được {fmt(made)}. Em không dùng dòng tổng, "
                           "chỉ cộng các dòng mã hàng")
        prev = n
    return out


# ── Against the books ─────────────────────────────────────────────────────────

GOODS = ("5111", "5112")                # sale of goods and of finished products: what a sales ledger holds
OTHER_REVENUE = "doanh thu khác, không có trong sổ bán hàng"
CLOSING = "911"


@dataclass
class SalesBookLine:
    code: str                       # 'revenue' or 'cogs'
    name: str
    books: Decimal
    ledger: Optional[Decimal]       # None when the ledger has no cost of goods sold
    accounts: list = field(default_factory=list)     # the books' accounts it is made of
    rows: list = field(default_factory=list)         # trial balance rows, or ledger cells ('Sheet!row')
    how: str = ""                                    # how the books' figure is read, in plain words
    causes: list = field(default_factory=list)       # [(what, amount)] known items that make up the gap exactly
    suspect: Optional[tuple] = None                  # (what, amount, cells): one group of postings that is the gap

    @property
    def diff(self) -> Optional[Decimal]:
        return None if self.ledger is None else self.ledger - self.books

    @property
    def unexplained(self) -> Optional[Decimal]:
        return None if self.diff is None else self.diff - sum((a for _w, a in self.causes), ZERO)

    @property
    def ok(self) -> bool:
        """Matched, or the gap is exactly items the books hold and the sales ledger does not (said as a note)."""
        return self.unexplained is not None and abs(self.unexplained) <= VND_TOLERANCE

    def note(self) -> str:
        """'Lưu ý: ...' when the gap is explained, '' otherwise."""
        if not self.ok or not self.causes:
            return ""
        what = "; ".join(f"{w} {fmt(-a)} đ" for w, a in self.causes)
        more = "cao hơn" if self.diff < 0 else "thấp hơn"
        return (f"Lưu ý: {self.name.lower()} trên sổ {more} sổ bán hàng {fmt(abs(self.diff))} đ, bằng đúng {what}. "
                "Đây không phải lỗi, sổ bán hàng không ghi các khoản này")

    def warning(self) -> str:
        """A gap that is exactly one group of postings with no rule behind it: shown for the user to look at, never
        called an error and never called 'not an error' (a cut-off or a missing invoice looks just like this)."""
        if self.ok or self.suspect is None:
            return ""
        what, _amount, cells = self.suspect
        more = f" và {len(cells) - 3} dòng khác" if len(cells) > 3 else ""
        return (f"{self.name} lệch {fmt(self.diff)} đ. Chênh lệch nằm ở {what} ({', '.join(cells[:3])}{more}), "
                "không có trong sổ bán hàng; chị xem có đúng kỳ không")


@dataclass
class SalesBookMatch:
    period: str
    source: str = "sổ cân đối"                      # or 'sổ cái' when matched to the ledger's postings
    lines: list = field(default_factory=list)
    other: list = field(default_factory=list)        # [(account, amount)] revenue the sales ledger does not hold
    receipt: dict = field(default_factory=dict)      # GL match: {"total", "used", "left": [(reason, count, amount)]}

    @property
    def ok(self) -> bool:
        return bool(self.lines) and all(l.ok for l in self.lines)

    def notes(self) -> list:
        out = [l.note() for l in self.lines if l.note()]
        if self.other:
            out.append("Lưu ý: " + ", ".join(f"TK {a} {fmt(x)} đ" for a, x in self.other) + f" là {OTHER_REVENUE}, "
                       "em không so với sổ bán hàng")
        return out

    def warnings(self) -> list:
        return [l.warning() for l in self.lines if l.warning()]

    def sentence(self) -> str:
        month, src = _month_text(self.period), self.source
        parts = []
        for l in self.lines:
            if l.ledger is None:
                parts.append(f"{l.name.lower()} {src} {fmt(l.books)} đ, sổ bán hàng không có cột giá vốn nên em chưa khớp được")
            elif abs(l.diff) <= VND_TOLERANCE:
                parts.append(f"{l.name.lower()} khớp {src}, {fmt(l.books)} đ ({l.how})")
            elif l.ok:
                parts.append(f"{l.name.lower()} {src} {fmt(l.books)} đ ({l.how}), sổ bán hàng {fmt(l.ledger)} đ, "
                             "chênh lệch đã rõ nguyên nhân")
            else:
                parts.append(f"{l.name.lower()} lệch {src}: {src} {fmt(l.books)} đ ({l.how}), sổ bán hàng "
                             f"{fmt(l.ledger)} đ, lệch {fmt(l.diff)} đ")
        if not parts:
            return f"Em chưa khớp được doanh thu tháng {month} với {src}: không có tài khoản doanh thu, giá vốn."
        return f"Tháng {month}: " + "; ".join(parts) + "."


def _accounts(config, words: tuple, default: tuple) -> tuple:
    said = config.rule(*words) if config is not None and hasattr(config, "rule") else None
    return tuple(p.strip() for p in re.split(r"[;,\s]+", str(said)) if p.strip()) if said else default


def _revenue_accounts(config, accounts: list) -> tuple:
    """(accounts the sales ledger is matched to, the other revenue accounts). The catalogue can name them; else a
    chart that splits 511 matches the goods accounts (5111, 5112) and names the rest (5113 services, 5117 investment
    property, 5118 other) as revenue the ledger does not hold."""
    said = _accounts(config, ("tk", "doanh thu"), ())
    if said:
        return said, ()
    revenue = sorted({a for a in accounts if a.startswith("511")})
    goods = tuple(g for g in GOODS if any(a.startswith(g) for a in revenue))
    if not goods:
        return ("511",), ()
    return goods, tuple(sorted({a for a in revenue if not a.startswith(GOODS) and a != "511"}))


def _ledger_figures(rows: list) -> dict:
    net = sum((s.net for s in rows), ZERO)
    have = bool(rows) and all(s.cogs is not None for s in rows)
    returns = sum((s.cogs for s in rows if s.cogs is not None and s.cogs < 0), ZERO)
    return {"net": net, "gross": net + sum((s.deductions for s in rows), ZERO), "deducted": any(s.deductions for s in rows),
            "cogs": sum((s.cogs for s in rows), ZERO) if have else None, "returns": returns}


def _explain(line: SalesBookLine, known: list, others: list = ()) -> None:
    """known: [(what, amount)] items a rule says the books hold and a sales ledger does not (a provision, returns
    the ledger nets): the smallest set of them that makes the gap exactly is the cause, said as a note. others:
    [(what, amount, cells)] groups of postings with no rule behind them: only one group alone that is the gap
    exactly is named, as a warning, so a gap is never 'explained' by a chance sum of unrelated postings."""
    from engine.bod_review import explain_gap
    if line.diff is None or abs(line.diff) <= VND_TOLERANCE:
        return
    # An item that is most of the figure is the figure itself (the ledger missing it is a real gap), never a cause.
    small = lambda a: abs(a) * 2 <= abs(line.books)                     # noqa: E731
    chosen, rest = explain_gap(line.diff, [(w, a) for w, a in known if small(a)])
    if abs(rest) <= VND_TOLERANCE:
        line.causes = list(chosen)
        return
    hits = [o for o in others if small(o[1]) and abs(o[1] - line.diff) <= VND_TOLERANCE]
    if len(hits) == 1:
        line.suspect = hits[0]


def match_books(sales: list, tb, period: str, config=None) -> SalesBookMatch:
    """The month's sales ledger against the trial balance.

    Net revenue in the books is Phát sinh Có of the goods accounts (5111, 5112; all of 511 when it is not split)
    less Phát sinh Nợ of 521; the Nợ of 511 is the closing entry to 911 and the transfer of 521, so it is not read.
    Other 511 accounts (5113, 5117, 5118) are named as revenue the ledger does not hold. A chart without 521
    (TT133) books deductions on the Nợ of 511 with the closing entry, so the ledger's gross revenue is matched.

    Cost of goods sold is Phát sinh Nợ of 632, which is gross: an inventory provision (Nợ 632 / Có 2294) is in it,
    and goods returned (Nợ 156 / Có 632) are not taken out, while a sales ledger nets returns. A gap that is exactly
    the provision, the ledger's returns, or both, is said as a note, never as an error."""
    m = SalesBookMatch(period)
    rows = [s for s in sales if s.month == period]
    led = _ledger_figures(rows)
    leaves = tb.leaves
    rev_acc, other_acc = _revenue_accounts(config, [l.account for l in leaves])
    ded_acc = _accounts(config, ("tk", "giam tru"), ("521",))
    cogs_acc = _accounts(config, ("tk", "gia von"), ("632",))
    under = lambda prefixes: [l for l in leaves if prefixes and l.account.startswith(prefixes)]    # noqa: E731
    rev, ded, cogs = under(rev_acc), under(ded_acc), under(cogs_acc)
    m.other = [(l.account, l.cr) for l in under(other_acc) if l.cr]
    if rev:
        books = sum((l.cr for l in rev), ZERO) - sum((l.dr for l in ded), ZERO)
        if ded or not led["deducted"]:
            ledger, name = led["net"], LINES[0][1]
            how = f"Có TK {'/'.join(rev_acc)}" + (f" trừ Nợ TK {'/'.join(ded_acc)}" if ded else "")
        else:                                   # deductions on the Nợ of 511, with the closing entry: match gross
            ledger, name = led["gross"], "Doanh thu trước giảm trừ"
            how = (f"Có TK {'/'.join(rev_acc)}; giảm trừ ghi bên Nợ TK {'/'.join(rev_acc)} cùng bút toán kết chuyển "
                   "nên em khớp doanh thu trước giảm trừ")
        m.lines.append(SalesBookLine("revenue", name, books, ledger, [l.account for l in rev + ded],
                                     [l.row for l in rev + ded], how))
    if cogs:
        line = SalesBookLine("cogs", LINES[1][1], sum((l.dr for l in cogs), ZERO), led["cogs"],
                             [l.account for l in cogs], [l.row for l in cogs], f"Nợ TK {'/'.join(cogs_acc)}")
        provision = sum((l.cr for l in under(("2294",))), ZERO)
        _explain(line, [("trích lập dự phòng giảm giá hàng tồn kho (Có TK 2294)", -provision),
                        ("giá vốn hàng bán bị trả lại mà sổ bán hàng đã trừ", led["returns"])])
        m.lines.append(line)
    return m


def match_gl(sales: list, postings: list, period: str, config=None) -> SalesBookMatch:
    """The month's sales ledger (AR12) against the general ledger's postings on revenue, deductions and cost of
    goods sold. Closing entries (đối ứng 911) and the transfer of 521 into 511 are left out with that reason; other
    511 revenue is named; every other posting on those accounts is in a figure, so used + left out = total.

    The GL nets what the trial balance cannot: cost of goods sold is Nợ less Có of 632, so returns are netted as in
    the sales ledger. A gap that is exactly one or more groups of postings by contra account (a provision against
    2294, unearned revenue released from 3387) is said as a note."""
    m = SalesBookMatch(period, "sổ cái")
    rows = [s for s in sales if s.month == period]
    led = _ledger_figures(rows)
    month = [p for p in postings if p.period == period]
    rev_acc, other_acc = _revenue_accounts(config, [p.account for p in month])
    ded_acc = _accounts(config, ("tk", "giam tru"), ("521",))
    cogs_acc = _accounts(config, ("tk", "gia von"), ("632",))
    scope = tuple(rev_acc) + tuple(other_acc) + tuple(ded_acc) + tuple(cogs_acc) + (("511",) if other_acc else ())
    mine = [p for p in month if p.account.startswith(scope)]
    left = {}

    def leave(p, reason):
        n, amt = left.get(reason, (0, ZERO))
        left[reason] = (n + 1, amt + p.dr + p.cr)

    used = {"rev": [], "ded": [], "cogs": []}
    for p in mine:
        if p.contra.startswith(CLOSING):
            leave(p, "kết chuyển cuối kỳ (đối ứng TK 911)")
        elif p.account.startswith(rev_acc) and p.contra.startswith(ded_acc) or \
                p.account.startswith(ded_acc) and p.contra.startswith(rev_acc):
            leave(p, "kết chuyển giảm trừ doanh thu (521 sang 511)")
        elif p.account.startswith(rev_acc):
            used["rev"].append(p)
        elif other_acc and p.account.startswith(other_acc):
            leave(p, f"TK {p.account[:4]}: {OTHER_REVENUE}")
            m.other.append((p.account, p.cr - p.dr))
        elif p.account.startswith(ded_acc):
            used["ded"].append(p)
        elif p.account.startswith(cogs_acc):
            used["cogs"].append(p)
        else:
            leave(p, "tài khoản doanh thu chưa rõ loại")
    other = {}
    for a, x in m.other:
        other[a] = other.get(a, ZERO) + x
    m.other = sorted(other.items())
    m.receipt = {"total": len(mine), "used": sum(len(x) for x in used.values()),
                 "left": [(r, n, a) for r, (n, a) in sorted(left.items())]}

    def groups(ps, sign):
        """{(account, contra, side): (what the group adds to the books' figure, cells)}."""
        g = {}
        for p in ps:
            k = (p.account, p.contra or "?", "Nợ" if p.dr else "Có")
            amount, cells = g.get(k, (ZERO, []))
            g[k] = (amount + sign * (p.dr - p.cr), cells + [p.where])
        return g

    def split(g, known_rule):
        """Known causes by rule, and the other groups; a cause amount is what the ledger misses: minus the group."""
        known, others = [], []
        for (acct, contra, side), (adds, cells) in sorted(g.items()):
            what = known_rule(acct, contra, side)
            if what:
                known.append((what, -adds))
            else:
                others.append((f"bút toán {side} TK {acct} đối ứng TK {contra}", -adds, cells))
        return known, others

    unearned = bool(config is not None and hasattr(config, "rule") and (config.rule("3387") or config.rule("chua thuc hien")))
    if used["rev"] or used["ded"]:
        books = sum((p.cr - p.dr for p in used["rev"]), ZERO) - sum((p.dr - p.cr for p in used["ded"]), ZERO)
        how = f"Có trừ Nợ TK {'/'.join(rev_acc)}" + (f", trừ giảm trừ TK {'/'.join(ded_acc)}" if used["ded"] else "")
        line = SalesBookLine("revenue", LINES[0][1], books, led["net"], sorted({p.account for p in used["rev"] + used["ded"]}),
                             [p.where for p in used["rev"] + used["ded"]], how)
        g = groups(used["rev"], -1)                       # Có less Nợ of revenue adds to the figure
        g.update(groups(used["ded"], -1))                # Nợ of a deduction takes from it: -(Nợ - Có)
        rule = lambda a, c, s: ("doanh thu chưa thực hiện kết chuyển vào kỳ (Nợ TK 3387)"      # noqa: E731
                                if unearned and c.startswith("3387") and s == "Có" and a.startswith(rev_acc) else "")
        _explain(line, *split(g, rule))
        m.lines.append(line)
    if used["cogs"]:
        line = SalesBookLine("cogs", LINES[1][1], sum((p.dr - p.cr for p in used["cogs"]), ZERO), led["cogs"],
                             sorted({p.account for p in used["cogs"]}), [p.where for p in used["cogs"]],
                             f"Nợ trừ Có TK {'/'.join(cogs_acc)}")
        rule = lambda a, c, s: ("trích lập dự phòng giảm giá hàng tồn kho (Có TK 2294)" if c.startswith("2294") and s == "Nợ"  # noqa: E731
                                else "giá vốn hàng bán bị trả lại nhập kho" if c.startswith(("155", "156")) and s == "Có"
                                else "")
        _explain(line, *split(groups(used["cogs"], 1), rule))
        m.lines.append(line)
    return m
