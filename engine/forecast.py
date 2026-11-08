"""The forecast for head office: the fiscal year's actual months and the months still to come, against the approved
annual plan. One new workbook, marked SƠ BỘ, reopened and checked before it counts.

- Actual: the months closed so far. The current month and the year to date come from the books exactly as the FP&A
  month report has them (engine.fpa_report: the trial balance, or the ledgers with that said). Earlier months come
  from the ledgers, month by month; when the books' year to date differs from the ledgers' months, the gap is its
  own line and is said, never spread over the months.
- The months still to come: the latest forecast version in the folder (a plan version named as a forecast or a
  revised plan: LE, FC, forecast, dự báo, ước, AOPR, revised, điều chỉnh), else the annual plan. Several forecasts
  are told apart by their approval date only; with none approved, the user is asked (fpa.PickVersion). A line the
  forecast has no figure for (department x cost element, or SKU x channel, for one month) is taken from the annual
  plan, counted and said; a whole month missing is said as a month. The catalogue rule "Bản dự báo" names the
  version and wins; a named version that is not in the folder stops the forecast (NamedVersionMissing).
- Against: the annual plan (AOP), the version not named as a forecast; several are told apart by approval, else asked.

Full year = year to date + the months to come. Amounts are Decimal; every sentence is code; nothing is sent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import fpa, fpa_report, sales_pnl
from engine.bod_check import fiscal_year, fy_months
from engine.fpa import PickVersion, _month_text
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
STATUS = "SƠ BỘ"
OUT_PREFIX = "Clicky_Forecast"
PNL, COSTS, RECEIPT = "Du bao KQKD", "Chi phi theo bo phan", "Phieu nhan file"
FORECAST_WORDS = re.compile(r"\b(le|fc|rf|forecast|du bao|uoc|uoc tinh|aopr|revised|reforecast|dieu chinh|"
                            r"cap nhat|muf|rolling)\b")
GAP_LINE = "Chênh lệch lũy kế sổ cân đối và sổ chi tiết"
COST, SALES = "chi phí", "doanh thu và giá vốn"
LINE_WORDS = {COST: "dòng chi phí (bộ phận × yếu tố)", SALES: "dòng doanh thu và giá vốn (mã hàng × kênh)"}


class NamedVersionMissing(Exception):
    """The catalogue names a forecast version the folder does not hold: asked, never guessed."""

    def __init__(self, named: str, found: list):
        self.named, self.found = named, found
        super().__init__(named)


@dataclass
class Version:
    version: str
    files: list = field(default_factory=list)
    approved: str = ""

    @property
    def forecast(self) -> bool:
        return bool(FORECAST_WORDS.search(key(self.version)))

    def said(self) -> str:
        when = f", duyệt ngày {self.approved[8:10]}/{self.approved[5:7]}/{self.approved[:4]}" if self.approved else ""
        return f"bản {self.version}{when}"


def versions(plans: list, sku_plans: list) -> list:
    """Every plan version in the folder, its files together (a version may be split over a cost and a SKU file)."""
    out = {}
    for p in fpa.plan_choices(list(plans) + list(sku_plans)):
        v = out.setdefault(key(p.version), Version(p.version))
        v.files.append(p.file)
        v.approved = max(v.approved, p.approved or "")
    return [out[k] for k in sorted(out)]


def roles(plans: list, sku_plans: list, config=None) -> tuple:
    """(annual plan, forecast for the months to come or None). PickVersion when the folder does not say."""
    found = versions(plans, sku_plans)
    named = config.rule("ban du bao") if config is not None else None
    target = [v for v in found if not v.forecast]
    if len(target) > 1:
        approved = [v for v in target if v.approved]
        if len(approved) != 1:
            raise PickVersion([(v.version, ", ".join(v.files)) for v in target])
        target = approved
    if named:
        basis = [v for v in found if key(v.version) == key(named)]
        if not basis:
            raise NamedVersionMissing(str(named), [v.version for v in found])
    else:
        basis = [v for v in found if v.forecast]
        if len(basis) > 1:
            approved = sorted((v for v in basis if v.approved), key=lambda v: v.approved)
            if not approved or (len(approved) > 1 and approved[-1].approved == approved[-2].approved):
                raise PickVersion([(v.version, ", ".join(v.files)) for v in basis])
            basis = approved[-1:]
    return (target[0] if target else None), (basis[0] if basis else None)


@dataclass
class Row:
    code: str
    name: str
    months: dict = field(default_factory=dict)      # month -> Decimal: actual up to the period, then the forecast
    ytd: Decimal = ZERO                             # the year to date as the books have it
    plan: dict = field(default_factory=dict)        # month -> the annual plan
    ytd_books: bool = False

    def rest(self, months: list) -> Decimal:
        return sum((self.months.get(m, ZERO) for m in months), ZERO)

    def full(self, months: list) -> Decimal:
        return self.ytd + self.rest(months)

    @property
    def plan_full(self) -> Decimal:
        return sum(self.plan.values(), ZERO)


@dataclass
class Forecast:
    month: object                                   # engine.fpa_report.Month: the current month against the plan
    year: list                                      # the fiscal year's months
    done: list                                      # the months closed, ending with the period
    rest: list                                      # the months still to come
    target: Optional[Version] = None
    basis: Optional[Version] = None
    rows: list = field(default_factory=list)        # [Row]: revenue, cogs, gp, opex, op
    costs: list = field(default_factory=list)       # [(dept, element, label, Row)]
    fallback: list = field(default_factory=list)    # (what, month) the forecast had no figures for: annual plan used
    filled: list = field(default_factory=list)      # (what, month, line) the forecast lacked in a month it has: plan used
    missing: list = field(default_factory=list)     # (what, month) no plan at all had figures for: 0, and said
    said: list = field(default_factory=list)

    @property
    def period(self) -> str:
        return self.month.period

    def row(self, code: str) -> Optional[Row]:
        return next((r for r in self.rows if r.code == code), None)


def _cost_plan(plans: list, version: Optional[Version], month: str) -> dict:
    """{(dept, element): amount} of the version's cost lines for one month; {} when it has none that month."""
    out = {}
    if version is None:
        return out
    for r in plans:
        if key(r.version or "") == key(version.version) and r.month == month and not fpa.pnl_line(r):
            k = (r.dept, r.element)
            out[k] = out.get(k, ZERO) + r.amount
    return out


def _sku_plan(sku_plans: list, version: Optional[Version], month: str) -> dict:
    """{(sku, channel): [revenue, cogs]} of the version's plan by SKU for one month, from the file sales_pnl would
    choose for that version; {} when it has none that month."""
    if version is None:
        return {}
    rows = [r for r in sku_plans if key(r.version or "") == key(version.version) and r.month == month]
    if not rows:
        return {}
    chosen, _only = fpa.choose_plan(sku_plans, version.version)
    out = {}
    for r in rows:
        if chosen is None or r.file == chosen.file:
            k = (r.sku, key(r.channel))
            got = out.setdefault(k, [ZERO, ZERO, r.sku + (f" · {r.channel}" if r.channel else "")])
            got[0] += r.revenue
            got[1] += r.cogs
    return out


def _fill(f: Forecast, what: str, month: str, basis: dict, plan: dict, label) -> dict:
    """The forecast's lines for the month, each line it lacks taken from the annual plan and noted. A month the
    forecast has nothing for is noted as a month; a month no plan has anything for is noted as missing."""
    if f.basis is f.target:
        got = dict(plan)
    elif not basis:
        got = dict(plan)
        if got:
            f.fallback.append((what, month))
    else:
        got = dict(basis)
        for k in sorted(set(plan) - set(basis), key=str):
            got[k] = plan[k]
            f.filled.append((what, month, label(k, plan[k])))
    if not got:
        f.missing.append((what, month))
    return got


def _sales_month(b, month: str, version: Optional[Version], fy: int, with_actual: bool) -> Optional[dict]:
    """{revenue, cogs: (actual, plan)} for one month of the sales ledger and the version's plan by SKU; None when the
    version has no SKU plan for that month."""
    if version is None or not b.sku_plans:
        return None
    if not any(key(r.version) == key(version.version) and r.month == month for r in b.sku_plans):
        return None
    s = sales_pnl.compute(b.sales if with_actual else [], b.sku_plans, b.plans, period=month, version=version.version,
                          fy_start=fy, config=b.config, plan_tables=b.sku_plan_totals)
    return {l.code: (l.actual, l.plan) for l in s.lines()}


def compute(folder: Path) -> Forecast:
    from engine import bod_inputs
    folder = Path(folder)
    b = bod_inputs.load(folder)
    fy = fpa_report._fy_start(b.config)
    if fy != 7:
        b = bod_inputs.load(folder, fy_start=fy)
    target, basis = roles(b.plans, b.sku_plans, b.config)
    m = fpa_report.compute(folder, version=target.version if target else None)
    year = fy_months(fiscal_year(m.period, fy), fy)
    i = year.index(m.period)
    f = Forecast(m, year, year[:i + 1], year[i + 1:], target, basis or target)
    rest_from = basis or target

    # Operating costs by department x cost element: the ledger's months, the books' current month and year to date
    names = {(l.dept, l.element): l.label for l in (m.costs.ytd if m.costs else [])}
    lines = {}

    def line(k):
        if k not in lines:
            lines[k] = Row("opex", names.get(k) or " · ".join(x for x in k if x) or fpa.UNASSIGNED)
        return lines[k]
    if m.costs is not None:
        for e in b.gl:
            if e.period in f.done:
                r = line((e.dept, fpa._element_of(e)))
                r.months[e.period] = r.months.get(e.period, ZERO) + e.amount
        for mo in f.year:
            for k, amt in _cost_plan(b.plans, target, mo).items():
                line(k).plan[mo] = line(k).plan.get(mo, ZERO) + amt
        for mo in f.rest:
            got = _fill(f, COST, mo, _cost_plan(b.plans, rest_from, mo), _cost_plan(b.plans, target, mo),
                        lambda k, _v: names.get(k) or " · ".join(x for x in k if x) or fpa.UNASSIGNED)
            for k, amt in got.items():
                line(k).months[mo] = line(k).months.get(mo, ZERO) + amt
        for r in lines.values():
            r.ytd = sum((r.months.get(mo, ZERO) for mo in f.done), ZERO)
        order = sorted(lines, key=lambda k: (k[0] == "", k[0], k[1]))
        f.costs = [(k[0], k[1], lines[k].name, lines[k]) for k in order]
        opex = Row("opex", "Chi phí hoạt động", plan={})
        for _d, _e, _n, r in f.costs:
            for mo, v in r.months.items():
                opex.months[mo] = opex.months.get(mo, ZERO) + v
            for mo, v in r.plan.items():
                opex.plan[mo] = opex.plan.get(mo, ZERO) + v
        books = m.row("opex")
        if books is not None:
            opex.months[m.period] = books.actual          # the current month as the books have it
            ledger_ytd = sum((opex.months.get(mo, ZERO) for mo in f.done), ZERO)
            opex.ytd = books.ytd_actual if books.ytd_actual is not None else ledger_ytd
            opex.ytd_books = books.ytd_books
    else:
        opex = None

    # Revenue and cost of goods sold: the sales ledger's months, the books' current month and year to date
    rev, cogs = Row("revenue", "Doanh thu thuần"), Row("cogs", "Giá vốn hàng bán")
    has_sales = m.row("revenue") is not None
    if has_sales:
        for mo in f.year:
            got = _sales_month(b, mo, target, fy, with_actual=mo in f.done)
            if got is not None:
                for r in (rev, cogs):
                    a, p = got[r.code]
                    r.plan[mo] = p
                    if mo in f.done:
                        r.months[mo] = a
            elif mo in f.done and b.sales:
                s = sales_pnl.compute(b.sales, [], b.plans, period=mo, fy_start=fy, config=b.config)
                for l in s.lines():
                    if l.code in ("revenue", "cogs"):
                        (rev if l.code == "revenue" else cogs).months[mo] = l.actual
        for mo in f.rest:
            got = _fill(f, SALES, mo, _sku_plan(b.sku_plans, rest_from, mo), _sku_plan(b.sku_plans, target, mo),
                        lambda _k, v: v[2])
            rev.months[mo] = sum((v[0] for v in got.values()), ZERO)
            cogs.months[mo] = sum((v[1] for v in got.values()), ZERO)
        for r in (rev, cogs):
            books = m.row(r.code)
            r.months[m.period] = books.actual
            ledger_ytd = sum((r.months.get(mo, ZERO) for mo in f.done), ZERO)
            r.ytd = books.ytd_actual if books.ytd_actual is not None else ledger_ytd
            r.ytd_books = books.ytd_books
        gp = Row("gp", "Lợi nhuận gộp", {mo: rev.months.get(mo, ZERO) - cogs.months.get(mo, ZERO) for mo in f.year},
                 rev.ytd - cogs.ytd, {mo: rev.plan.get(mo, ZERO) - cogs.plan.get(mo, ZERO) for mo in f.year},
                 rev.ytd_books and cogs.ytd_books)
        f.rows += [rev, cogs, gp]
    if opex is not None:
        f.rows.append(opex)
        if has_sales:
            gp = f.row("gp")
            f.rows.append(Row("op", "Lợi nhuận hoạt động",
                              {mo: gp.months.get(mo, ZERO) - opex.months.get(mo, ZERO) for mo in f.year},
                              gp.ytd - opex.ytd, {mo: gp.plan.get(mo, ZERO) - opex.plan.get(mo, ZERO) for mo in f.year},
                              gp.ytd_books and opex.ytd_books))
    f.said = _said(f)
    return f


def gap(r: Row, done: list) -> Decimal:
    """The books' year to date less the months shown: not spread over the months, its own line."""
    return r.ytd - sum((r.months.get(mo, ZERO) for mo in done), ZERO)


def _span(months: list) -> str:
    if not months:
        return ""
    return _month_text(months[0]) if len(months) == 1 else f"{_month_text(months[0])}–{_month_text(months[-1])}"


def _said(f: Forecast) -> list:
    out = []
    month = _month_text(f.period)
    if f.target is not None:
        out.append(f"So với kế hoạch năm: {f.target.said()}.")
    if f.rest:
        if f.basis is not None and f.basis is not f.target:
            out.append(f"Các tháng {_span(f.rest)} lấy theo {f.basis.said()}, bản dự báo mới nhất trong thư mục.")
        else:
            out.append(f"Thư mục chưa có bản dự báo nào, nên các tháng {_span(f.rest)} lấy theo kế hoạch năm.")
    else:
        out.append(f"Tháng {month} là tháng cuối năm tài chính: cả năm là số thực hiện.")
    by_what = {}
    for what, mo in f.fallback:
        by_what.setdefault(what, []).append(mo)
    for what, months in by_what.items():
        out.append(f"{f.basis.version} không có số {what} tháng {', '.join(_month_text(m) for m in months)}; em lấy "
                   "theo kế hoạch năm cho các tháng đó.")
    by_what = {}
    for what, mo, _line in f.filled:
        by_what.setdefault(what, []).append(mo)
    for what, months in by_what.items():
        span = sorted(set(months))
        out.append(f"Bản {f.basis.version} không có {len(months)} {LINE_WORDS[what]} của "
                   f"{'tháng' if len(span) == 1 else 'các tháng'} {', '.join(_month_text(m) for m in span)}; em lấy "
                   "theo kế hoạch năm cho các dòng đó (từng dòng ghi trong Phiếu nhận file).")
    by_what = {}
    for what, mo in f.missing:
        by_what.setdefault(what, []).append(mo)
    for what, months in by_what.items():
        out.append(f"Không bản kế hoạch nào có số {what} tháng {', '.join(_month_text(m) for m in months)}; em để 0 "
                   "cho các tháng đó, chị bổ sung kế hoạch rồi làm lại.")
    earlier = _span(f.done[:-1])
    books = "bảng cân đối" if f.month.tb is not None else "sổ chi tiết (thư mục chưa có bảng cân đối)"
    out.append(f"Thực hiện tháng {month} và lũy kế lấy theo {books}, như báo cáo FP&A tháng"
               + (f"; các tháng {earlier} theo sổ chi tiết từng tháng." if earlier else "."))
    for r in f.rows:
        g = gap(r, f.done)
        if g and r.code in ("revenue", "cogs", "opex"):
            out.append(f"{r.name}: lũy kế theo sổ cân đối khác cộng các tháng theo sổ chi tiết {fmt(g)} đ; em ghi ở dòng "
                       f"“{GAP_LINE}”, không chia vào tháng nào.")
    for code in ("op", "gp", "opex", "revenue"):
        r = f.row(code)
        if r is not None and r.plan_full:
            full, plan = r.full(f.rest), r.plan_full
            pct = (full - plan) / abs(plan) * 100
            out.append(f"{r.name} cả năm ước {fmt(full)} đ, kế hoạch năm {fmt(plan)} đ, chênh {fmt(full - plan)} đ "
                       f"({pct.quantize(Decimal('0.1'))}%).")
            break
    out += f.month.said
    return out


# ── The workbook ────────────────────────────────────────────────────────────

def out_folder(folder: Path, period: str) -> Path:
    base = Path(folder) / f"{OUT_PREFIX}_T{period[5:7]}-{period[:4]}"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name} ({n})")
    return path


def _pct(variance: Decimal, plan: Decimal) -> Optional[Decimal]:
    return None if not plan else (variance / abs(plan) * 100).quantize(Decimal("0.1"))


def write(f: Forecast, out: Optional[Path] = None) -> dict:
    """The pack in a new folder; reopened and every key figure checked. Returns {"xlsx", "verified", "diffs", "cells"}."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from engine import audit, saved_check
    out = Path(out) if out else out_folder(f.month.folder, f.period)
    out.mkdir(parents=True, exist_ok=False)
    month = _month_text(f.period)
    path = out / f"{OUT_PREFIX}_T{f.period[5:7]}-{f.period[:4]}.xlsx"
    wb = Workbook()
    expect = {}
    head = (["Chỉ tiêu"] + [("TH " if mo in f.done else "DB ") + _month_text(mo) for mo in f.year]
            + ["Lũy kế thực hiện", "Các tháng còn lại", "Cả năm ước", "Kế hoạch năm", "Chênh lệch", "%"])
    basis = (f"Thực hiện đến T{month}; " + (f"dự báo theo {f.basis.said()}" if f.rest else "cả năm là thực hiện")
             + (f"; so với {f.target.said()}" if f.target else ""))

    def table(ws, rows, first_data):
        for r in rows:
            label, row = r
            full = row.full(f.rest)
            ws.append([label] + [row.months.get(mo) for mo in f.year]
                      + [row.ytd, row.rest(f.rest), full, row.plan_full, full - row.plan_full,
                         _pct(full - row.plan_full, row.plan_full)])
            n = ws.max_row
            col = len(f.year) + 2
            expect[f"'{ws.title}'!{_col(col)}{n}"] = row.ytd
            expect[f"'{ws.title}'!{_col(col + 2)}{n}"] = full

    ws = wb.active
    ws.title = PNL
    ws.append([f"{STATUS} · Dự báo kết quả kinh doanh năm tài chính, cập nhật T{month}"])
    ws.append([basis])
    ws.append(head)
    table(ws, [(r.name, r) for r in f.rows], 4)
    gaps = [(r.name, gap(r, f.done)) for r in f.rows if r.code in ("revenue", "cogs", "opex") and gap(r, f.done)]
    if gaps:
        ws.append([])
        for name, g in gaps:
            ws.append([f"{GAP_LINE}: {name}"] + [None] * len(f.year) + [g])

    if f.costs:
        cs = wb.create_sheet(COSTS)
        cs.append([f"{STATUS} · Chi phí hoạt động theo bộ phận × yếu tố, cả năm ước"])
        cs.append([basis])
        cs.append(head)
        table(cs, [(name, r) for _d, _e, name, r in f.costs], 4)
        opex = f.row("opex")
        shown = Row("opex", "")
        for _d, _e, _n, r in f.costs:
            for mo, v in r.months.items():
                shown.months[mo] = shown.months.get(mo, ZERO) + v
            shown.ytd += r.ytd
        g_month = opex.months.get(f.period, ZERO) - shown.months.get(f.period, ZERO)
        g_ytd = opex.ytd - shown.ytd
        if g_month or g_ytd:
            cs.append([fpa_report.GAP_LINE] + [g_month if mo == f.period else None for mo in f.year]
                      + [g_ytd, ZERO, g_ytd, ZERO, g_ytd, None])
        table(cs, [("Tổng cộng", opex)], cs.max_row + 1)

    rs = wb.create_sheet(RECEIPT)
    rs.append([f"Phiếu nhận file · dự báo cập nhật T{month}"])
    rs.append([])
    for s in f.said:
        rs.append([s])
    rs.append([])
    if f.filled:
        rs.append([f"Dòng {f.basis.version} không có, em lấy theo {f.target.version}", "Tháng", "Loại"])
        for what, mo, label in f.filled:
            rs.append([label, _month_text(mo), what])
        rs.append([])
    for line in fpa_report.file_rows(f.month):
        rs.append(line)

    log = audit.AuditLog(period=month)
    for name, _role, used, skipped in f.month.bundle.receipt:
        log.coverage.append([name, used + len(skipped), used, len(skipped), "khớp"])
    log.add(f"'{PNL}'!A1", "Dự báo năm", "", f"{len(f.rows)} dòng", "Dự báo mới, file gốc không đổi",
            source=", ".join(n for n, *_ in f.month.bundle.receipt))
    bb = wb.create_sheet(audit.SHEET)
    for line in audit.grid(log)[0]:
        bb.append(line)

    for sheet in wb.worksheets:
        sheet["A1"].font = Font(bold=True, size=13)
        for row in sheet.iter_rows():
            for c in row:
                if isinstance(c.value, Decimal):
                    c.number_format = "#,##0;(#,##0);\"-\""
        sheet.column_dimensions["A"].width = 44
    wb.save(path)
    ok, diffs = saved_check.check(saved_check.tamper_from_env(path), expect)
    return {"xlsx": path, "verified": ok, "diffs": diffs, "cells": len(expect)}


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
