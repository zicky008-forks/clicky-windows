"""The FP&A month report: P&L month and year to date against the approved plan, operating costs by department x
cost element with each variance classified, the gross profit bridge, and the receipt of every file. One new
workbook, marked SƠ BỘ, reopened and checked before it counts.

Rule 2 (CLAUDE.md): the figures in the P&L's month column come from the official books, the trial balance
(Phát sinh of 511/521, 632, 641/642) when the folder has one. The ledgers only split them: the sales ledger by SKU
and channel (and the bridge), the cost detail by department and cost element. Where the books and a ledger differ,
the receipt says by how much and why (or that it is not explained yet), in one sentence each. Without a trial
balance the ledgers' figures are used and the report says so. The year to date is the ledgers' (a month's trial
balance holds only the month), which the report says too.

Amounts are Decimal; every sentence is code (engine.variance_class, engine.fpa, engine.sales_pnl); nothing here is
written by the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import fpa, opex_split, sales_pnl, variance_class
from engine.fpa import _month_text
from engine.monthly_report import fmt

ZERO = Decimal(0)
STATUS = "SƠ BỘ"
OUT_PREFIX = "Clicky_FPA"
PNL, COSTS, VARIANCES, BRIDGE, RECEIPT = "P&L", "Chi phi theo bo phan", "Chenh lech", "Cau noi LNG", "Phieu nhan file"
OPEX_ACCOUNTS = ("641", "642")


@dataclass
class PnlRow:
    code: str
    name: str
    actual: Decimal
    plan: Decimal
    ytd_actual: Decimal
    ytd_plan: Decimal
    source: str                  # where the month's actual comes from, in plain words

    @property
    def variance(self) -> Decimal:
        return self.actual - self.plan

    @property
    def ytd_variance(self) -> Decimal:
        return self.ytd_actual - self.ytd_plan


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

    def row(self, code: str) -> Optional[PnlRow]:
        return next((r for r in self.pnl if r.code == code), None)


def _fy_start(config, default: int = 7) -> int:
    from engine.bod_inputs import num
    fy = num(config.rule("thang bat dau")) or num(config.rule("nien do"))
    return int(fy) if fy is not None and 1 <= int(fy) <= 12 else default


def compute(folder: Path) -> Month:
    """Read the folder and compute the month. Raises fpa.PickVersion when the plan must be chosen by the user."""
    from engine import bod_inputs
    folder = Path(folder)
    b = bod_inputs.load(folder)
    fy = _fy_start(b.config)
    if fy != 7:
        b = bod_inputs.load(folder, fy_start=fy)
    costs = fpa.compute(b.gl, b.plans, fy_start=fy, config=b.config) if b.gl else None
    sales = None
    if b.sales:
        sales = sales_pnl.compute(b.sales, b.sku_plans, b.plans, period=costs.period if costs else None,
                                  fy_start=fy, config=b.config, plan_tables=b.sku_plan_totals)
    period = (costs or sales).period if (costs or sales) else None
    if not period:
        raise ValueError("thư mục không có sổ chi tiết chi phí hay sổ bán hàng nào có tháng")
    m = Month(folder, period, b, costs, sales)
    m.tb = trial_balance_in(folder, b.unused)
    if m.tb is not None and costs is not None:
        m.cost_books = fpa.match_books(b.gl, m.tb, period)
    if m.tb is not None and sales is not None:
        m.sales_books = sales_pnl.match_books(b.sales, m.tb, period, b.config)
    m.pnl = _pnl(m)
    if costs is not None:
        rev = m.row("revenue")
        driver = (rev.actual, rev.plan) if rev is not None and rev.plan else None
        m.classified = variance_class.classify(costs, b.gl, b.plans, b.config, driver)
    m.said = _said(m)
    return m


def trial_balance_in(folder: Path, unused: list):
    """The trial balance in the folder: a file the readers left for the books (named by its file name), read."""
    from engine.bod_inputs import hint_of
    from engine.tables import sheets_of
    from engine.trial_balance import read_trial_balance
    for name, _why in unused:
        if hint_of(Path(name).stem) != "tb":
            continue
        try:
            sheets = sheets_of(Path(folder) / name)
            sheet, rows = max(sheets.items(), key=lambda kv: len(kv[1]))
            tb = read_trial_balance(rows, sheet)
            if tb.lines:
                tb.file = name
                return tb
        except Exception:                    # noqa: BLE001 - not a trial balance after all
            continue
    return None


def _pnl(m: Month) -> list:
    """Revenue, cost of goods sold, gross profit, operating costs, operating profit: month (books) and year to date."""
    out = []
    month_book = f"sổ cân đối T{_month_text(m.period)}"
    if m.sales is not None:
        led, ytd = m.sales.lines(), m.sales.lines(ytd=True)
        books = {l.code: l for l in (m.sales_books.lines if m.sales_books else [])}
        for code, name in (("revenue", "Doanh thu thuần"), ("cogs", "Giá vốn hàng bán")):
            a = next(l for l in led if l.code == code)
            y = next(l for l in ytd if l.code == code)
            bl = books.get(code)
            if bl is not None and code == "revenue" and bl.name != a.name:
                bl = None                   # the books could only be matched gross: the net figure is the ledger's
            out.append(PnlRow(code, name, bl.books if bl is not None else a.actual, a.plan, y.actual, y.plan,
                              f"{month_book} ({bl.how})" if bl is not None else "sổ bán hàng"))
        r, c = out[0], out[1]
        out.append(PnlRow("gp", "Lợi nhuận gộp", r.actual - c.actual, r.plan - c.plan, r.ytd_actual - c.ytd_actual,
                          r.ytd_plan - c.ytd_plan, "doanh thu thuần trừ giá vốn"))
    if m.costs is not None:
        a, p = fpa.Variance.totals(m.costs.month)
        ya, yp = fpa.Variance.totals(m.costs.ytd)
        source = "sổ chi tiết chi phí"
        if m.cost_books is not None and m.cost_books.ok:
            books, _detail, down = m.cost_books.totals
            a, source = books - down, f"{month_book} (Nợ TK 641/642 trừ phần ghi giảm bên Có)"
        out.append(PnlRow("opex", "Chi phí hoạt động", a, p, ya, yp, source))
    gp, opex = next((r for r in out if r.code == "gp"), None), next((r for r in out if r.code == "opex"), None)
    if gp is not None and opex is not None:
        out.append(PnlRow("op", "Lợi nhuận hoạt động", gp.actual - opex.actual, gp.plan - opex.plan,
                          gp.ytd_actual - opex.ytd_actual, gp.ytd_plan - opex.ytd_plan, "lợi nhuận gộp trừ chi phí"))
    return out


def _said(m: Month) -> list:
    """The receipt's sentences: where the report's figures come from, the books against each ledger, what is out."""
    out = []
    month = _month_text(m.period)
    if m.tb is not None:
        out.append(f"Số tháng {month} trên báo cáo lấy từ bảng cân đối số phát sinh (file {m.tb.file}). Sổ bán hàng "
                   "chỉ dùng để chia theo mã hàng và kênh, sổ chi tiết chi phí để chia theo bộ phận và yếu tố. Số lũy "
                   "kế lấy từ các sổ chi tiết, vì bảng cân đối chỉ có tháng này.")
    else:
        out.append(f"Thư mục chưa có bảng cân đối số phát sinh tháng {month}, nên số trên báo cáo lấy từ sổ bán hàng và "
                   "sổ chi tiết chi phí, chưa khớp với sổ. Báo cáo vẫn là bản SƠ BỘ.")
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
            out.append("Vì chi phí chưa khớp sổ cân đối, dòng chi phí hoạt động trên báo cáo lấy từ sổ chi tiết; chị "
                       "đừng trình bản này khi chưa rõ chênh lệch.")
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


def _pct(variance: Decimal, plan: Decimal) -> Optional[Decimal]:
    return None if not plan else (variance / abs(plan) * 100).quantize(Decimal("0.1"))


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
    ws.append(["Chỉ tiêu", f"Thực hiện T{month}", "Kế hoạch", "Chênh lệch", "%", "Thực hiện lũy kế", "Kế hoạch lũy kế",
               "Chênh lệch lũy kế", "%", "Nguồn số tháng"])
    for r in m.pnl:
        ws.append([r.name, r.actual, r.plan, r.variance, _pct(r.variance, r.plan), r.ytd_actual, r.ytd_plan,
                   r.ytd_variance, _pct(r.ytd_variance, r.ytd_plan), r.source])
        expect[f"'{PNL}'!B{ws.max_row}"] = r.actual
        expect[f"'{PNL}'!F{ws.max_row}"] = r.ytd_actual

    if m.costs is not None:
        cs = wb.create_sheet(COSTS)
        cs.append([f"{STATUS} · Chi phí hoạt động theo bộ phận × yếu tố, tháng {month} và lũy kế"])
        cs.append(["Sổ chi tiết chi phí chia theo bộ phận; tổng tháng khớp sổ cân đối khi có (xem Phiếu nhận file)."])
        cs.append(["Bộ phận", "Yếu tố chi phí", f"Thực hiện T{month}", "Kế hoạch", "Chênh lệch", "%", "Phân loại",
                   "Số thực chất", "Thực hiện lũy kế", "Kế hoạch lũy kế", "Chênh lệch lũy kế"])
        ytd = {(l.dept, l.element): l for l in m.costs.ytd}
        by = {(c.line.dept, c.line.element): c for c in m.classified}
        for l in m.costs.month:
            y = ytd.get((l.dept, l.element))
            c = by.get((l.dept, l.element))
            cs.append([l.dept_name or l.dept or fpa.UNASSIGNED, l.element_name or l.element, l.actual, l.plan,
                       l.variance, l.pct, c.verdict if c else "", c.underlying if c and c.material else None,
                       y.actual if y else ZERO, y.plan if y else ZERO, y.variance if y else ZERO])
        a, p = fpa.Variance.totals(m.costs.month)
        ya, yp = fpa.Variance.totals(m.costs.ytd)
        cs.append(["Tổng cộng", "", a, p, a - p, None, "", None, ya, yp, ya - yp])
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
    rs.append([])
    rs.append(["File", "Vai trò", "Dòng đã dùng", "Dòng bỏ", "Lý do bỏ"])
    from collections import Counter
    for name, role, used, skipped in m.bundle.receipt:
        why = "; ".join(f"{n} dòng: {w}" for w, n in Counter(w for _r, w in skipped).items())
        rs.append([name, role, used, len(skipped), why])
    tb_file = getattr(m.tb, "file", None) if m.tb is not None else None
    if tb_file:
        skipped = len(getattr(m.tb, "skipped", []) or [])
        rs.append([tb_file, "Bảng cân đối số phát sinh: số chính thức của tháng", len(m.tb.lines), skipped,
                   f"{skipped} dòng có số mà không đọc được số hiệu tài khoản" if skipped else ""])
    for name, why in m.bundle.unused:
        if name != tb_file:
            rs.append([name, "Không dùng", 0, 0, why])

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
