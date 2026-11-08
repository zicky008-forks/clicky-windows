"""The FP&A month report: P&L month and year to date against the approved plan, operating costs by department x
cost element with each variance classified, the gross profit bridge, and the receipt of every file. One new
workbook, marked SƠ BỘ, reopened and checked before it counts.

Rule 2 (CLAUDE.md): the figures in the P&L's month column come from the official books, the trial balance
(Phát sinh of 511/521, 632, 641/642) when the folder has one, whether or not a ledger matches it. The ledgers only
split them: the sales ledger by SKU and channel (and the bridge), the cost detail by department and cost element.
Where the books and the cost detail differ, the department table carries the gap as its own line ("Chênh lệch sổ
cân đối chưa giải thích") so it still adds up to the P&L, and the receipt says not to present it yet. Without a
sales ledger, revenue and cost of goods sold still come from the books, with no split by SKU. Without a trial
balance the ledgers' figures are used and the report says so.

The year to date comes from the trial balance's "Lũy kế từ đầu năm" columns when it has them; otherwise from the
ledgers, and the column header says it is not matched to the books. A trial balance whose only movement columns
are the year to date is not used for the month.

Amounts are Decimal; every sentence is code (engine.variance_class, engine.fpa, engine.sales_pnl); nothing here is
written by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import fpa, opex_split, sales_pnl, variance_class
from engine.fpa import _month_text
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
STATUS = "SƠ BỘ"
OUT_PREFIX = "Clicky_FPA"
PNL, COSTS, VARIANCES, BRIDGE, RECEIPT = "P&L", "Chi phi theo bo phan", "Chenh lech", "Cau noi LNG", "Phieu nhan file"
GAP_LINE = "Chênh lệch sổ cân đối chưa giải thích"
YTD_LEDGER = "Lũy kế (theo sổ chi tiết, chưa khớp sổ cân đối)"
OPEX_ACCOUNTS = ("641", "642")


@dataclass
class PnlRow:
    code: str
    name: str
    actual: Decimal
    plan: Decimal
    ytd_actual: Optional[Decimal]     # None when nothing in the folder holds it
    ytd_plan: Decimal
    source: str                  # where the month's actual comes from, in plain words
    ytd_source: str = ""         # where the year to date comes from
    ytd_books: bool = False      # the year to date is the trial balance's
    has_plan: bool = True        # False: no plan in the folder holds this line; its plan cells stay blank
    plan_source: str = ""        # where the plan comes from when it is not the usual table (a P&L line total)

    @property
    def variance(self) -> Decimal:
        return self.actual - self.plan

    @property
    def ytd_variance(self) -> Optional[Decimal]:
        return None if self.ytd_actual is None else self.ytd_actual - self.ytd_plan


@dataclass
class Month:
    folder: Path
    period: str
    bundle: object
    costs: Optional[object] = None              # fpa.Variance
    sales: Optional[object] = None              # sales_pnl.SalesPnl
    tb: Optional[object] = None
    cost_books: Optional[object] = None         # fpa.BookMatch
    sales_books: Optional[object] = None        # sales_pnl.SalesBookMatch
    classified: list = field(default_factory=list)
    pnl: list = field(default_factory=list)     # [PnlRow]
    said: list = field(default_factory=list)    # the receipt's sentences, books against ledgers first
    opex_gap: tuple = (ZERO, ZERO)              # (month, year to date): books less cost detail, not explained
    tb_not_used: Optional[tuple] = None         # (file, why) for a trial balance that cannot give the month
    version: Optional[str] = None               # the plan version asked for; None: the only or the approved one
    fy: int = 1                                 # the month the fiscal year starts (engine.fiscal)
    fy_said: str = ""                           # how that was found, for the receipt
    checks: list = field(default_factory=list)  # [(code, name, result, note, stop)] by the company's check codes

    def row(self, code: str) -> Optional[PnlRow]:
        return next((r for r in self.pnl if r.code == code), None)


def fiscal_start(folder: Path, b, version: Optional[str] = None, plan_file: Optional[str] = None) -> tuple:
    """(first month of the fiscal year, the sentence that says how it was found): engine.fiscal, with the trial
    balance's year to date of operating costs when the folder has one, and the plan that will be compared against
    (the user's answer, the catalogue rule, the only or the approved one) when it is already clear."""
    from engine import fiscal
    from engine.sales_pnl import latest_month as sales_month
    period = fpa.latest_month(b.gl) or sales_month(b.sales)
    tb, books_ytd = None, None
    try:
        tb = trial_balance_in(folder, list(b.unused))
        if tb is not None and tb.has_ytd and not getattr(tb, "ytd_only", False):
            books_ytd = books_figures(tb, b.config, "ytd_")["opex"]
    except Exception:                    # noqa: BLE001 - the trial balance is read again, and named, by compute
        tb = None
    chosen = None
    try:
        plan, _only = fpa.choose_plan(b.plans, version or (str(b.config.rule("ban ke hoach") or "").strip() or None),
                                      plan_file)
        chosen = (plan.file, plan.version) if plan is not None else None
    except fpa.PickVersion:
        pass                             # asked later: until then only what every version agrees on counts
    return fiscal.start(b, tb, period, books_ytd, chosen)


def compute(folder: Path, version: Optional[str] = None, plan_file: Optional[str] = None) -> Month:
    """Read the folder and compute the month against the plan version (the user's answer; else the catalogue rule
    "Bản kế hoạch"; else the only or the approved one). plan_file tells two plans of one name apart.
    Raises fpa.PickVersion when the plan must be chosen by the user."""
    from engine import bod_inputs
    folder = Path(folder)
    b = bod_inputs.load(folder, purpose="fpa")
    fy, fy_said = fiscal_start(folder, b, version, plan_file)
    if fy != 7:
        b = bod_inputs.load(folder, fy_start=fy, purpose="fpa")
    version = version or (str(b.config.rule("ban ke hoach") or "").strip() or None)
    costs = fpa.compute(b.gl, b.plans, version=version, fy_start=fy, config=b.config,
                        plan_file=plan_file) if b.gl else None
    sales = None
    if b.sales:
        sales = sales_pnl.compute(b.sales, b.sku_plans, b.plans, period=costs.period if costs else None,
                                  version=version, fy_start=fy, config=b.config, plan_tables=b.sku_plan_totals,
                                  plan_file=plan_file if plan_file and any(r.file == plan_file for r in b.sku_plans)
                                  else None)
    period = (costs or sales).period if (costs or sales) else None
    if not period:
        raise ValueError("thư mục không có sổ chi tiết chi phí hay sổ bán hàng nào có tháng")
    m = Month(folder, period, b, costs, sales, fy=fy, fy_said=fy_said)
    m.version = version
    m.tb = trial_balance_in(folder, b.unused)
    if m.tb is not None and m.tb.ytd_only:
        m.tb_not_used = (m.tb.file, "Bảng cân đối này chỉ có số lũy kế từ đầu năm, không có số phát sinh tháng; em "
                                    "chưa dùng nó cho số tháng")
        m.tb = None
    if m.tb is not None and costs is not None:
        m.cost_books = fpa.match_books(b.gl, m.tb, period)
    if m.tb is not None and sales is not None:
        m.sales_books = sales_pnl.match_books(b.sales, m.tb, period, b.config)
    m.pnl = _pnl(m)
    if costs is not None and split_planned(m):           # no plan by department: no variance to classify
        rev = m.row("revenue")
        driver = (rev.actual, rev.plan) if rev is not None and rev.plan and rev.has_plan else None
        m.classified = variance_class.classify(costs, b.gl, b.plans, b.config, driver)
    m.checks = company_checks(m)
    m.said = _said(m)
    return m


PASS, FAIL, NOT_RUN, ASK = "ĐẠT", "KHÔNG ĐẠT", "CHƯA KIỂM", "CẦN GIẢI TRÌNH"
NO_PLAN = "chưa có kế hoạch"
OPEX_TOTAL = re.compile(r"^(tong )?(chi phi hoat dong|opex|operating (expenses?|costs?))\b")


def company_checks(m: Month) -> list:
    """The checks this report can run, by the codes of the company's kiem_tra.yaml: the operating costs against the
    books (the check on account 641/642), the cost lines left on the value printed for a missing code, the year to
    date against the books, and the variances above threshold. A check the company names that this step does not
    run is left to its own step."""
    from engine import company_config
    c = m.bundle.company
    if c is None:
        return []
    out = []

    def add(code, result, note):
        k = c.check(code)
        if k is not None:
            out.append((code, k.name, result, note, k.stop))
    month = _month_text(m.period)
    opex = m.row("opex")
    for k in c.checks.values():
        kind = k.kind
        if kind == "chi_phi_khop_so":
            if m.cost_books is None:
                add(k.code, NOT_RUN, "thư mục chưa có bảng cân đối số phát sinh")
            elif m.cost_books.ok:
                add(k.code, PASS, f"chi phí tháng {month} khớp sổ cân đối")
            else:
                add(k.code, FAIL, f"lệch {fmt(m.opex_gap[0])} đ; xem dòng {GAP_LINE}")
        elif kind == "anh_xa_du" and m.costs is not None:
            if not c.unmapped:
                add(k.code, NOT_RUN, "cấu hình chưa ghi giá trị hiện ra khi thiếu mã")
            else:
                bad = company_config.unmapped_lines(c, m.bundle.gl, m.period)
                add(k.code, PASS if not bad else FAIL,
                    f"{len(bad)} dòng chi phí còn {' / '.join(c.unmapped)}, {fmt(sum((e.amount for e in bad), ZERO))} đ"
                    if bad else "không còn dòng chi phí nào thiếu mã")
        elif kind == "luy_ke_khop_so" and opex is not None:
            if not opex.ytd_books:
                add(k.code, NOT_RUN, "bảng cân đối không có cột lũy kế")
            else:
                add(k.code, PASS if not m.opex_gap[1] else FAIL,
                    "lũy kế khớp sổ cân đối" if not m.opex_gap[1] else f"lũy kế lệch {fmt(m.opex_gap[1])} đ")
        elif kind == "chenh_lech_giai_trinh" and m.classified:
            _r, _u, alarms = variance_class.summary(m.classified)
            amount, pct = k.threshold or variance_class.thresholds(m.bundle.config)
            limit = " và ".join(x for x in ((f"{fmt(amount)} đ" if amount is not None else ""),
                                              (f"{pct}%" if pct is not None else "")) if x)
            add(k.code, PASS if not alarms else ASK,
                f"ngưỡng {limit}; {len(alarms)} dòng còn chênh thật cần phòng ban giải trình" if alarms
                else f"ngưỡng {limit}; mọi dòng trên ngưỡng đã có nguyên nhân")
    return out


def trial_balance_in(folder: Path, unused: list):
    """The trial balance in the folder: a file the readers left for the books (named by its file name), read."""
    from engine.bod_inputs import hint_of
    from engine.tables import sheets_of
    from engine.trial_balance import read_trial_balance
    from engine.comparable import basis
    for name, _why in unused:
        if hint_of(Path(name).stem) != "tb":
            continue
        try:
            sheets = sheets_of(Path(folder) / name)
            sheet, rows = max(sheets.items(), key=lambda kv: len(kv[1]))
            tb = read_trial_balance(rows, sheet)
            if tb.lines:
                tb.file = name
                tb.ytd_only = not tb.has_ytd and "movement" in basis(rows, tb).lines
                return tb
        except Exception:                    # noqa: BLE001 - not a trial balance after all
            continue
    return None


def books_figures(tb, config, prefix: str = "") -> dict:
    """Revenue (Có of the goods accounts less Nợ of 521), cost of goods sold (Nợ of 632) and operating costs (Nợ of
    641/642) in the trial balance: the month's movement (prefix "") or the year to date ("ytd_"). None for a figure
    the books have no account for; "how" says which accounts, as the receipt prints it."""
    leaves = tb.leaves
    rev_acc, _other = sales_pnl._revenue_accounts(config, [l.account for l in leaves])
    ded_acc = sales_pnl._accounts(config, ("tk", "giam tru"), ("521",))
    cogs_acc = sales_pnl._accounts(config, ("tk", "gia von"), ("632",))

    def under(prefixes):
        return [l for l in leaves if l.account.startswith(tuple(prefixes))]

    def total(lines, side):
        return sum((getattr(l, prefix + side) for l in lines), ZERO)
    rev, ded, cogs, opex = under(rev_acc), under(ded_acc), under(cogs_acc), under(OPEX_ACCOUNTS)
    return {"revenue": total(rev, "cr") - total(ded, "dr") if rev else None,
            "cogs": total(cogs, "dr") if cogs else None,
            "opex": total(opex, "dr") if opex else None,
            "deductions": bool(ded),
            "how": {"revenue": f"Có TK {'/'.join(rev_acc)}" + (f" trừ Nợ TK {'/'.join(ded_acc)}" if ded else ""),
                    "cogs": f"Nợ TK {'/'.join(cogs_acc)}", "opex": "Nợ TK 641/642 trừ phần ghi giảm bên Có"}}


def _reductions(gl: list, months: list) -> Decimal:
    """Cost detail posted on the credit side (a cost written down) in those months: in the books' Có beside the
    closing entry, so taken off the books' Nợ, as fpa.match_books does."""
    return -sum((e.amount for e in gl if e.period in months and e.account and getattr(e, "side", "") == "cr"), ZERO)


def _plan_only(m: Month) -> Optional[object]:
    """The sales plan alone, for a folder with a plan by SKU but no sales ledger."""
    b = m.bundle
    if not b.sku_plans:
        return None
    return sales_pnl.compute([], b.sku_plans, b.plans, period=m.period, version=m.version, fy_start=m.fy,
                             config=b.config, plan_tables=b.sku_plan_totals)


def _pnl(m: Month) -> list:
    """Revenue, cost of goods sold, gross profit, operating costs, operating profit: month (books) and year to date
    (books when the trial balance has the year to date)."""
    out = []
    month_book = f"sổ cân đối T{_month_text(m.period)}"
    ytd_book = f"sổ cân đối lũy kế đến T{_month_text(m.period)}"
    cfg = m.bundle.config
    bm = books_figures(m.tb, cfg) if m.tb is not None else None
    by = books_figures(m.tb, cfg, "ytd_") if m.tb is not None and m.tb.has_ytd else None
    if m.sales is not None:
        led, ytd = m.sales.lines(), m.sales.lines(ytd=True)
        books = {l.code: l for l in (m.sales_books.lines if m.sales_books else [])}
        for code, name in (("revenue", "Doanh thu thuần"), ("cogs", "Giá vốn hàng bán")):
            a = next(l for l in led if l.code == code)
            y = next(l for l in ytd if l.code == code)
            bl = books.get(code)
            if bl is not None and code == "revenue" and bl.name != a.name:
                bl = None                   # the books could only be matched gross: the net figure is the ledger's
            row = PnlRow(code, name, bl.books if bl is not None else a.actual, a.plan, y.actual, y.plan,
                         f"{month_book} ({bl.how})" if bl is not None else "sổ bán hàng", "sổ bán hàng")
            if bl is not None and by is not None and by[code] is not None:
                row.ytd_actual, row.ytd_source, row.ytd_books = by[code], f"{ytd_book} ({bl.how})", True
            out.append(row)
    elif bm is not None and bm["revenue"] is not None:
        plan = _plan_only(m)
        for code, name in (("revenue", "Doanh thu thuần"), ("cogs", "Giá vốn hàng bán")):
            p = next((l for l in plan.lines() if l.code == code), None) if plan else None
            yp = next((l for l in plan.lines(ytd=True) if l.code == code), None) if plan else None
            row = PnlRow(code, name, bm[code] if bm[code] is not None else ZERO, p.plan if p else ZERO, None,
                         yp.plan if yp else ZERO, f"{month_book} ({bm['how'][code]})", "chưa có")
            if by is not None:
                row.ytd_actual = by[code] if by[code] is not None else ZERO
                row.ytd_source, row.ytd_books = f"{ytd_book} ({by['how'][code]})", True
            out.append(row)
    _plans_by_line(m, out)
    if len(out) == 2:
        r, c = out
        ya = None if r.ytd_actual is None or c.ytd_actual is None else r.ytd_actual - c.ytd_actual
        out.append(PnlRow("gp", "Lợi nhuận gộp", r.actual - c.actual, r.plan - c.plan, ya, r.ytd_plan - c.ytd_plan,
                          "doanh thu thuần trừ giá vốn", "doanh thu thuần trừ giá vốn", r.ytd_books and c.ytd_books,
                          r.has_plan and c.has_plan))
    if m.costs is not None:
        led_a, p = fpa.Variance.totals(m.costs.month)
        led_ya, yp = fpa.Variance.totals(m.costs.ytd)
        row = PnlRow("opex", "Chi phí hoạt động", led_a, p, led_ya, yp, "sổ chi tiết chi phí", "sổ chi tiết chi phí")
        if bm is not None and bm["opex"] is not None:
            row.actual = bm["opex"] - _reductions(m.bundle.gl, [m.period])
            row.source = f"{month_book} ({bm['how']['opex']})"
        if by is not None and by["opex"] is not None:
            row.ytd_actual = by["opex"] - _reductions(m.bundle.gl, m.costs.months)
            row.ytd_source, row.ytd_books = f"{ytd_book} ({by['how']['opex']})", True
        m.opex_gap = (row.actual - led_a, row.ytd_actual - led_ya)
        out.append(row)
        _plans_by_line(m, [row])
    gp, opex = next((r for r in out if r.code == "gp"), None), next((r for r in out if r.code == "opex"), None)
    if gp is not None and opex is not None:
        ya = None if gp.ytd_actual is None else gp.ytd_actual - opex.ytd_actual
        out.append(PnlRow("op", "Lợi nhuận hoạt động", gp.actual - opex.actual, gp.plan - opex.plan, ya,
                          gp.ytd_plan - opex.ytd_plan, "lợi nhuận gộp trừ chi phí", "lợi nhuận gộp trừ chi phí",
                          gp.ytd_books and opex.ytd_books, gp.has_plan and opex.has_plan))
    return out


def plan_outside_year(m: Month) -> list:
    """Plan rows of the chosen version that fall outside this fiscal year or have no month: counted and said per
    file (rule 4), never dropped in silence. A year far from the ledger's is named: it is a cell read wrong."""
    from collections import Counter
    from engine.bod_check import fiscal_year, fy_months
    b = m.bundle
    try:
        chosen = m.costs.plan if m.costs is not None else fpa.choose_plan(b.plans, m.version)[0]
    except fpa.PickVersion:
        chosen = None
    if chosen is None:
        return []
    year = set(fy_months(fiscal_year(m.period, m.fy), m.fy))
    out = []
    rows = [r for r in b.plans if key(r.version or "") == key(chosen.version) and r.file == chosen.file]
    no_month = [r for r in rows if not r.month]
    outside = Counter(r.month[:4] for r in rows if r.month and r.month not in year)
    if no_month:
        out.append(f"File kế hoạch {chosen.file}: {len(no_month)} dòng không đọc được tháng (ô đầu tiên "
                   f"{no_month[0].where}), em chưa tính vào kế hoạch.")
    if outside:
        far = sorted(y for y in outside if abs(int(y) - int(m.period[:4])) > 1)
        out.append(f"File kế hoạch {chosen.file}: {sum(outside.values())} dòng có tháng ngoài năm tài chính này "
                   f"(năm {', '.join(sorted(outside))}), em không tính vào kế hoạch năm nay"
                   + (f"; năm {', '.join(far)} cách xa năm của sổ, có thể ô năm bị đọc sai, chị kiểm lại file" if far
                      else "") + ".")
    return out


def split_planned(m: Month) -> bool:
    """The chosen plan has lines by department x cost element: the split, and its variances, can be read."""
    return m.costs is not None and any(l.plan_from for l in m.costs.ytd)


def _plans_by_line(m: Month, rows: list) -> None:
    """A P&L row whose usual plan (by SKU for revenue and cost of goods sold, by department x cost element for the
    operating costs) has nothing takes the plan file's own line ('Doanh thu thuần', 'Chi phí bán hàng' + 'Chi phí
    quản lý', T01 .. T12), said as such. With neither, the row has no plan: its plan cells stay blank (rule 4: an
    empty plan is not a variance the size of the actual)."""
    b = m.bundle
    try:
        chosen = m.costs.plan if m.costs is not None else fpa.choose_plan(b.plans, m.version)[0]
    except fpa.PickVersion:
        chosen = None
    months = m.costs.months if m.costs is not None else (m.sales.months if m.sales is not None else [m.period])
    lines = {}
    for r in b.plans if chosen is not None else []:
        if key(r.version or "") != key(chosen.version) or r.file != chosen.file or r.month not in months:
            continue
        code = fpa.pnl_line(r) or ("opex" if fpa.opex_line(r) else "")
        if code == "opex" and OPEX_TOTAL.match(key(r.line)):
            code = "opex total"                   # 'Chi phí hoạt động' beside its parts: one or the other, not both
        if code:
            got = lines.setdefault(code, [ZERO, ZERO])
            got[1] += r.amount
            if r.month == m.period:
                got[0] += r.amount
    if "opex total" in lines:
        lines["opex"] = lines.pop("opex total")
    if m.sales is not None:
        sku_planned = any(l.plan_from for l in m.sales.ytd)
    else:
        plan_only = _plan_only(m)
        sku_planned = plan_only is not None and any(l.plan_from for l in plan_only.ytd)
    for row in rows:
        planned = split_planned(m) if row.code == "opex" else sku_planned if row.code in ("revenue", "cogs") else True
        if planned:
            continue
        if row.code in lines:
            row.plan, row.ytd_plan = lines[row.code]
            row.plan_source = f"dòng {row.name.lower()} trên file kế hoạch {chosen.file}"
        else:
            row.plan, row.ytd_plan, row.has_plan = ZERO, ZERO, False


def _said(m: Month) -> list:
    """The receipt's sentences: where the report's figures come from, the books against each ledger, what is out."""
    out = []
    month = _month_text(m.period)
    if m.tb is not None:
        split = ("Sổ bán hàng chỉ dùng để chia theo mã hàng và kênh, sổ chi tiết chi phí để chia theo bộ phận và yếu tố."
                 if m.sales is not None else
                 "Thư mục chưa có sổ bán hàng, nên doanh thu và giá vốn chỉ có tổng theo sổ cân đối, chưa chia theo mã "
                 "hàng; sổ chi tiết chi phí dùng để chia chi phí theo bộ phận và yếu tố.")
        ytd = ("Số lũy kế lấy từ cột lũy kế từ đầu năm của bảng cân đối." if m.tb.has_ytd else
               "Số lũy kế lấy từ các sổ chi tiết, chưa khớp sổ cân đối, vì bảng cân đối chỉ có số tháng này.")
        out.append(f"Số tháng {month} trên báo cáo lấy từ bảng cân đối số phát sinh (file {m.tb.file}). {split} {ytd}")
        if m.sales is None and m.row("revenue") is not None:
            if not books_figures(m.tb, m.bundle.config)["deductions"]:
                out.append("Bảng cân đối không có tài khoản giảm trừ doanh thu (521), nên doanh thu lấy bên Có TK 511; "
                           "nếu công ty ghi giảm trừ bên Nợ TK 511 thì đây là số trước giảm trừ.")
            if m.row("revenue").ytd_actual is None:
                out.append("Chưa có lũy kế doanh thu và giá vốn: thư mục không có sổ bán hàng và bảng cân đối không có "
                           "cột lũy kế.")
            if not m.bundle.sku_plans:
                out.append("Chưa có kế hoạch doanh thu theo mã hàng, nên cột kế hoạch của doanh thu và giá vốn để 0.")
    else:
        out.append(f"Thư mục chưa có bảng cân đối số phát sinh tháng {month}, nên số trên báo cáo lấy từ sổ bán hàng và "
                   "sổ chi tiết chi phí, chưa khớp với sổ. Báo cáo vẫn là bản SƠ BỘ.")
        if m.tb_not_used is not None:
            out.append(f"File {m.tb_not_used[0]}: {m.tb_not_used[1]}.")
    if m.sales_books is not None:
        out.append(m.sales_books.sentence())
        out += [n + "." for n in m.sales_books.notes()]
        out += [w + "." for w in m.sales_books.warnings()]
        rev = next((l for l in m.sales_books.lines if l.code == "revenue"), None)
        if rev is not None and m.row("revenue") is not None and m.row("revenue").source == "sổ bán hàng":
            out.append("Sổ cân đối chỉ khớp được doanh thu trước giảm trừ, nên doanh thu thuần trên báo cáo lấy từ sổ "
                       "bán hàng.")
    if m.cost_books is not None:
        out.append(m.cost_books.sentence())
        if not m.cost_books.ok:
            out.append("Vì chi phí chưa khớp sổ cân đối, dòng chi phí hoạt động trên báo cáo vẫn lấy số sổ cân đối; bảng "
                       f"chi phí theo bộ phận có thêm dòng “{GAP_LINE}” {fmt(m.opex_gap[0])} đ để cộng khớp. Chị đừng "
                       "trình bản này khi chưa rõ chênh lệch.")
    if m.costs is not None and m.opex_gap[1] != m.opex_gap[0] and m.row("opex").ytd_books:
        out.append(f"Chi phí lũy kế theo sổ cân đối khác sổ chi tiết {fmt(m.opex_gap[1])} đ; bảng chi phí theo bộ phận "
                   f"ghi phần này ở dòng “{GAP_LINE}”.")
    if m.fy_said:
        out.append(m.fy_said)
    out += [x + "." for x in m.bundle.plan_checks]
    out += plan_outside_year(m)
    none = [r.name.lower() for r in m.pnl if not r.has_plan and r.code in ("revenue", "cogs", "opex")]
    if none:
        out.append(f"Thư mục chưa có kế hoạch cho {', '.join(none)}: em để trống cột kế hoạch và chênh lệch của các "
                   "dòng đó, không coi cả số thực hiện là chênh lệch.")
    for r in m.pnl:
        if r.plan_source:
            out.append(f"Kế hoạch {r.name.lower()} lấy theo {r.plan_source}"
                       + (", chưa chia theo bộ phận, nên bảng chi phí theo bộ phận để trống cột kế hoạch."
                          if r.code == "opex" else "."))
    if m.costs is not None:
        out += [x + "." for x in m.costs.left_out()]
    if m.sales is not None:
        out += [x + "." for x in m.sales.left_out()]
        blockers = m.sales.bridge_blockers()
        if blockers:
            out.append("Em chưa tính được cầu nối lợi nhuận gộp: " + "; ".join(blockers) + ".")
        else:
            gp = m.row("gp")
            ledger_gap = sum((l.variance for l in m.sales.lines() if l.code == "gp"), ZERO)
            if gp is not None and gp.variance != ledger_gap:
                out.append(f"Cầu nối lợi nhuận gộp tính trên sổ bán hàng: chênh lệch {fmt(ledger_gap)} đ. Chênh lệch "
                           f"trên báo cáo (theo sổ cân đối) là {fmt(gp.variance)} đ; phần khác {fmt(gp.variance - ledger_gap)} "
                           "đ là chênh lệch giữa sổ cân đối và sổ bán hàng nêu ở trên.")
    stopped = [c for c in m.checks if c[2] == FAIL and c[4]]
    if stopped:
        out.append(f"Kiểm {', '.join(c[0] for c in stopped)} không đạt, mà cấu hình công ty ghi phải dừng: em chưa làm "
                   "bước sau cho đến khi chị xử lý xong.")
    if m.classified:
        reported, underlying, alarms = variance_class.summary(m.classified)
        small = sum(1 for c in m.classified if not c.material)
        out.append(f"Chi phí: {len([c for c in m.classified if c.material])} dòng chênh trên ngưỡng, số báo cáo "
                   f"{fmt(reported)} đ, số thực chất {fmt(underlying)} đ; {len(alarms)} dòng cần hỏi; "
                   f"{small} dòng dưới ngưỡng, không phải bất thường.")
    return out


# ── The workbook ────────────────────────────────────────────────────────────

def out_folder(folder: Path, period: str) -> Path:
    base = Path(folder) / f"{OUT_PREFIX}_T{period[5:7]}-{period[:4]}"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name} ({n})")
    return path


def _pct(variance: Optional[Decimal], plan: Decimal) -> Optional[Decimal]:
    return None if not plan or variance is None else (variance / abs(plan) * 100).quantize(Decimal("0.1"))



NOT_COMPARED = "đọc, không dùng: không phải bản so sánh"


def _row_of(where: str) -> str:
    """'Sheet!F12' -> 'Sheet!12': a plan printed wide by month gives one figure per cell, the file one line."""
    hit = re.match(r"^(.*)!\$?[A-Z]{1,3}\$?(\d+)$", where or "")
    return f"{hit.group(1)}!{hit.group(2)}" if hit else (where or "")


def _not_compared(m, also_used=()) -> dict:
    """{plan file: {version: lines}} of the plan versions read but not compared against. A version is compared
    when the costs or the sales are measured against it; also_used names files another step takes its figures
    from (the forecast's basis). Nothing is moved when no plan was chosen."""
    chosen = {(p.file, key(p.version or "")) for p in (getattr(m.costs, "plan", None), getattr(m.sales, "plan", None))
              if p is not None}
    if not chosen:
        return {}
    by = {}
    for r in list(m.bundle.plans) + list(getattr(m.bundle, "sku_plans", [])):
        if r.file in also_used or (r.file, key(r.version or "")) in chosen:
            continue
        by.setdefault(r.file, {}).setdefault(r.version or "không ghi phiên bản", set()).add(_row_of(r.where))
    used_lines = {}
    for r in list(m.bundle.plans) + list(getattr(m.bundle, "sku_plans", [])):
        if (r.file, key(r.version or "")) in chosen or r.file in also_used:
            used_lines.setdefault(r.file, set()).add(_row_of(r.where))
    return {f: {v: len(rows - used_lines.get(f, set())) for v, rows in vs.items()} for f, vs in by.items()}


def file_rows(m, also_used=()) -> list:
    """The receipt's file table: every file read, the lines used and left out with the reason, and every file not
    used with why. Used + left out = the file's lines (rule 4). A plan version read but not compared against is
    never 'used': its lines are left out with that reason (also_used: files another step takes figures from)."""
    from collections import Counter
    from engine import roles
    out = [["File", "Vai trò", "Dòng đã dùng", "Dòng bỏ", "Lý do bỏ"]]
    other = _not_compared(m, set(also_used))
    compared = {p.file for p in (getattr(m.costs, "plan", None), getattr(m.sales, "plan", None)) if p is not None}
    compared |= set(also_used)
    for name, role, used, skipped in m.bundle.receipt:
        why = "; ".join(f"{n} dòng: {w}" for w, n in Counter(w for _r, w in skipped).items())
        aside = {v: n for v, n in other.get(name, {}).items() if n}
        moved = min(used, sum(aside.values())) if role.startswith((roles.PLAN, roles.FORECAST)) else 0
        if moved:
            take, left = {}, moved                   # a file read in two plan sheets: each takes its share once
            for v, n in aside.items():
                take[v] = min(n, left)
                left -= take[v]
            other[name] = {v: n - take[v] for v, n in aside.items()}
            said = "; ".join(f"{n} dòng: bản {v}, {NOT_COMPARED}" for v, n in take.items() if n)
            why = "; ".join(x for x in (why, said) if x)
            if moved == used or name not in compared:
                role = f"{role} · {NOT_COMPARED}"
        out.append([name, role, used - moved, len(skipped) + moved, why])
    tb_file = getattr(m.tb, "file", None) if m.tb is not None else None
    if tb_file:
        skipped = len(getattr(m.tb, "skipped", []) or [])
        out.append([tb_file, "Bảng cân đối số phát sinh: số chính thức của tháng", len(m.tb.lines), skipped,
                    f"{skipped} dòng có số mà không đọc được số hiệu tài khoản" if skipped else ""])
    for name, why in m.bundle.unused:
        if name != tb_file:
            if m.tb_not_used is not None and name == m.tb_not_used[0]:
                why = m.tb_not_used[1]
            role = "File làm việc của chị, không lấy số" if name in getattr(m.bundle, "working", []) else "Không dùng"
            out.append([name, role, 0, 0, why])
    return out

def write(m: Month, out: Optional[Path] = None) -> dict:
    """The workbook in a new folder; reopened and every key figure checked. Returns {"xlsx", "verified", "diffs"}."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from engine import audit, saved_check
    out = Path(out) if out else out_folder(m.folder, m.period)
    out.mkdir(parents=True, exist_ok=False)
    month = _month_text(m.period)
    path = out / f"{OUT_PREFIX}_T{m.period[5:7]}-{m.period[:4]}.xlsx"
    wb = Workbook()
    expect = {}
    basis = (m.costs.basis if m.costs is not None and m.costs.basis else "") or \
        (m.sales.basis if m.sales is not None and m.sales.basis else "") or "Chưa có kế hoạch để so"

    ws = wb.active
    ws.title = PNL
    ws.append([f"{STATUS} · Báo cáo FP&A tháng {month}"])
    ws.append([basis])
    ytd_head = "Thực hiện lũy kế (sổ cân đối)" if m.pnl and all(r.ytd_books for r in m.pnl) else YTD_LEDGER
    ws.append(["Chỉ tiêu", f"Thực hiện T{month}", "Kế hoạch", "Chênh lệch", "%", ytd_head, "Kế hoạch lũy kế",
               "Chênh lệch lũy kế", "%", "Nguồn số tháng", "Nguồn số lũy kế"])
    for r in m.pnl:
        if r.has_plan:
            ws.append([r.name, r.actual, r.plan, r.variance, _pct(r.variance, r.plan), r.ytd_actual, r.ytd_plan,
                       r.ytd_variance, _pct(r.ytd_variance, r.ytd_plan), r.source, r.ytd_source]
                      + ([f"Kế hoạch: {r.plan_source}"] if r.plan_source else []))
        else:
            ws.append([r.name, r.actual, None, None, None, r.ytd_actual, None, None, None, r.source, r.ytd_source,
                       NO_PLAN])
        expect[f"'{PNL}'!B{ws.max_row}"] = r.actual
        if r.ytd_actual is not None:
            expect[f"'{PNL}'!F{ws.max_row}"] = r.ytd_actual

    if m.costs is not None:
        cs = wb.create_sheet(COSTS)
        cs.append([f"{STATUS} · Chi phí hoạt động theo bộ phận × yếu tố, tháng {month} và lũy kế"])
        cs.append(["Sổ chi tiết chi phí chia theo bộ phận; dòng tổng bằng chi phí hoạt động trên P&L (xem Phiếu nhận "
                   "file)."])
        cs.append(["Bộ phận", "Yếu tố chi phí", f"Thực hiện T{month}", "Kế hoạch", "Chênh lệch", "%", "Phân loại",
                   "Số thực chất", "Thực hiện lũy kế", "Kế hoạch lũy kế", "Chênh lệch lũy kế"])
        ytd = {(l.dept, l.element): l for l in m.costs.ytd}
        by = {(c.line.dept, c.line.element): c for c in m.classified}
        split = split_planned(m)                 # without a plan by department its plan cells stay blank
        for l in m.costs.month:
            y = ytd.get((l.dept, l.element))
            c = by.get((l.dept, l.element))
            cs.append([l.dept_name or l.dept or fpa.UNASSIGNED, l.element_name or l.element, l.actual,
                       l.plan if split else None, l.variance if split else None, l.pct if split else None,
                       c.verdict if c else "", c.underlying if c and c.material else None,
                       y.actual if y else ZERO, (y.plan if y else ZERO) if split else None,
                       (y.variance if y else ZERO) if split else None])
        gap, ygap = m.opex_gap
        if gap or ygap:                          # the books less the detail: the table still adds up to the P&L
            cs.append([GAP_LINE, "", gap, ZERO if split else None, gap if split else None, None, "", None, ygap,
                       ZERO if split else None, ygap if split else None])
            expect[f"'{COSTS}'!C{cs.max_row}"] = gap
        opex = m.row("opex")
        a, p, ya, yp = opex.actual, opex.plan, opex.ytd_actual, opex.ytd_plan
        if opex.has_plan:
            cs.append(["Tổng cộng", "", a, p, a - p, None, "", None, ya, yp, ya - yp])
        else:
            cs.append(["Tổng cộng", "", a, None, None, None, "", None, ya, None, None])
        expect[f"'{COSTS}'!C{cs.max_row}"] = a
        expect[f"'{COSTS}'!I{cs.max_row}"] = ya

        vs = wb.create_sheet(VARIANCES)
        vs.append([f"{STATUS} · Chênh lệch chi phí tháng {month}: số báo cáo và số thực chất"])
        vs.append(["Trước khi gọi là vượt hay tiết kiệm, em xét lệch thời điểm, khoản một lần, điều chỉnh bút toán và "
                   "chi phí biến đổi theo doanh thu. Dòng dưới ngưỡng không phải bất thường."])
        vs.append(["Bộ phận · yếu tố", "Số báo cáo", "Nguyên nhân", "Số thực chất", "Phân loại", "Ngưỡng", "Nguồn"])
        for c in sorted((c for c in m.classified if c.material), key=lambda c: -abs(c.underlying)):
            vs.append([c.line.label, c.reported, " ".join(x.text for x in c.causes) or "",
                       c.underlying, c.verdict, c.threshold,
                       ", ".join(w for x in c.causes for w in x.where[:3]) or ", ".join(c.line.actual_from[:3])])
            expect[f"'{VARIANCES}'!D{vs.max_row}"] = c.underlying

    if m.sales is None and m.row("revenue") is not None:
        bs = wb.create_sheet(BRIDGE)
        bs.append([f"{STATUS} · Cầu nối lợi nhuận gộp tháng {month}"])
        bs.append(["Chưa tính được: thư mục chưa có sổ bán hàng, nên em không chia được lợi nhuận gộp theo sản lượng, "
                   "cơ cấu, giá bán và giá vốn. Doanh thu và giá vốn trên P&L lấy từ sổ cân đối."])
    if m.sales is not None:
        bs = wb.create_sheet(BRIDGE)
        bs.append([f"{STATUS} · Cầu nối lợi nhuận gộp tháng {month} (theo mã hàng, gộp các kênh; tính trên sổ bán hàng)"])
        try:
            br = m.sales.bridge()
        except sales_pnl.CannotBridge as exc:
            bs.append(["Chưa tính được: " + "; ".join(exc.reasons)])
        else:
            bs.append(["Phần", "Số tiền"])
            r = br.rounded()
            bs.append(["Lợi nhuận gộp kế hoạch", r["gp_plan"]])
            for name, k in (("Sản lượng", "volume"), ("Cơ cấu", "mix"), ("Giá bán", "price"), ("Giá vốn", "cost")):
                bs.append([name, r[k]])
            bs.append(["Lợi nhuận gộp thực hiện", r["gp_actual"]])
            bs.append(["Tổng 4 phần = LNG thực hiện − LNG kế hoạch", r["gap"]])
            expect[f"'{BRIDGE}'!B{bs.max_row}"] = r["gap"]
            if br.unplanned:
                bs.append(["Mã bán ngoài kế hoạch: " + ", ".join(br.unplanned)])

    rs = wb.create_sheet(RECEIPT)
    rs.append([f"Phiếu nhận file · tháng {month}"])
    rs.append([])
    for s in m.said:
        rs.append([s])
    if m.checks:
        rs.append([])
        rs.append(["Kiểm tra theo cấu hình công ty", "Tên", "Kết quả", "Ghi chú", "Không đạt thì dừng"])
        for code, name, result, note, stop in m.checks:
            rs.append([code, name, result, note, "Có" if stop else "Không"])
    c = m.bundle.company
    if c is not None and (c.problems or c.open):
        rs.append([])
        rs.append(["Cấu hình công ty: điểm cần chị xác nhận"])
        for x in c.problems + c.open:
            rs.append([x])
    rs.append([])
    for line in file_rows(m):
        rs.append(line)

    log = audit.AuditLog(period=month)
    for name, _role, used, skipped in m.bundle.receipt:
        log.coverage.append([name, used + len(skipped), used, len(skipped), "khớp"])
    left = (m.costs.left_out() if m.costs is not None else []) + (m.sales.left_out() if m.sales is not None else [])
    log.dropped += [[x, "", None, "nêu trong Phiếu nhận file"] for x in left]
    log.add(f"'{PNL}'!A1", "Báo cáo FP&A", "", f"{len(m.pnl)} dòng P&L", "Báo cáo tháng mới, file gốc không đổi",
            source=", ".join(n for n, *_ in m.bundle.receipt))
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
        for col in "BCDEFGHIJK":
            sheet.column_dimensions[col].width = 18
    for c in ws["E"][3:] + ws["I"][3:]:
        c.number_format = "0.0"
    wb.save(path)
    ok, diffs = saved_check.check(saved_check.tamper_from_env(path), expect)
    return {"xlsx": path, "verified": ok, "diffs": diffs, "cells": len(expect)}
