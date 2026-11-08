"""Synthetic sales month for tests: a sales ledger by SKU x channel, a SKU catalogue with units, the plan by SKU and
the October trial balance of a made-up drinks company. Every figure is invented. The traps are the ones a real
export has: rows in another unit than the standard (lốc of a quarter carton), a SKU sold with no plan, a printed
total row, rows posted after the month.

Calendar fiscal year, the month under review is 10/2026. Volumes are cartons (thùng); amounts are whole đồng.
"""
import csv
from pathlib import Path

PERIOD = "2026-10"
# SKU: (name, net price per carton, cost per carton) in the plan
SKUS = {"NC01": ("Nước cam 330ml", 240_000, 150_000), "TR02": ("Trà xanh 500ml", 180_000, 120_000),
        "SU03": ("Sữa đậu nành 1L", 300_000, 210_000)}
UNPLANNED = ("DT04", "Nước dừa 330ml", "ONL", 50, 200_000, 130_000)        # sold in October, never planned
CHANNELS = ("GT", "MT", "ONL")
PLAN_QTY = {("NC01", "GT"): 1000, ("NC01", "MT"): 600, ("NC01", "ONL"): 200,
            ("TR02", "GT"): 800, ("TR02", "MT"): 500, ("TR02", "ONL"): 100,
            ("SU03", "GT"): 400, ("SU03", "MT"): 300, ("SU03", "ONL"): 100}
# October against plan: (SKU, channel) -> (cartons more or less, price change, cost change) per carton
OCTOBER = {("NC01", "GT"): (200, -5_000, 0), ("TR02", "MT"): (-150, 0, 0), ("SU03", "GT"): (0, 0, 6_000),
           ("SU03", "MT"): (0, 0, 6_000), ("SU03", "ONL"): (0, 0, 6_000)}
LOC = 4                                     # lốc per carton: NC01 MT is invoiced in lốc
DISCOUNT_MT = 2                             # percent trade discount on MT invoices, booked to 521
LATER = ("TR02", "GT", 30)                  # invoiced 02/11/2026

HEADER = ["Ngày HĐ", "Số HĐ", "Mã hàng", "Tên hàng", "Kênh", "ĐVT", "Số lượng", "Doanh thu", "Chiết khấu",
          "Doanh thu thuần", "Giá vốn"]


def plan_month(sku: str, channel: str, month: int) -> int:
    """Planned cartons: the base, a little more towards year end."""
    return PLAN_QTY[(sku, channel)] + (month - 1) * 10


def actual_month(sku: str, channel: str, month: int) -> tuple:
    """(cartons, net price, cost per carton) actually sold."""
    _n, price, cost = SKUS[sku]
    qty = plan_month(sku, channel, month)
    if month == 10:
        dq, dp, dc = OCTOBER.get((sku, channel), (0, 0, 0))
        return qty + dq, price + dp, cost + dc
    i = list(PLAN_QTY).index((sku, channel))
    return qty + ((month * 7 + i * 3) % 11 - 5) * 4, price, cost


def _row(n, day, sku, name, channel, unit, qty, net, cogs):
    ck = net * DISCOUNT_MT // (100 - DISCOUNT_MT) if channel == "MT" else 0
    return [day, f"HD{n:06d}", sku, name, channel, unit, str(qty), str(net + ck), str(ck), str(net), str(cogs)]


def ledger_rows(unplanned: bool = True, extra: list = ()) -> list:
    rows, n = [HEADER], 0
    for month in range(1, 11):
        for (sku, channel) in PLAN_QTY:
            qty, price, cost = actual_month(sku, channel, month)
            n += 1
            if (sku, channel) == ("NC01", "MT"):           # invoiced in lốc: the price per lốc is a quarter
                rows.append(_row(n, f"20/{month:02d}/2026", sku, SKUS[sku][0], channel, "lốc", qty * LOC, qty * price,
                                 qty * cost))
            else:
                rows.append(_row(n, f"20/{month:02d}/2026", sku, SKUS[sku][0], channel, "thùng", qty, qty * price,
                                 qty * cost))
    if unplanned:
        sku, name, channel, qty, price, cost = UNPLANNED
        rows.append(_row(900, "25/10/2026", sku, name, channel, "thùng", qty, qty * price, qty * cost))
    sku, channel, qty = LATER
    rows.append(_row(901, "02/11/2026", sku, SKUS[sku][0], channel, "thùng", qty, qty * SKUS[sku][1], qty * SKUS[sku][2]))
    rows += [list(r) for r in extra]
    printed = [sum(int(r[i]) for r in rows[1:] if r[i]) for i in (7, 8, 9, 10)]
    rows.append(["", "", "", "Tổng cộng", "", "", ""] + [str(x) for x in printed])     # never added in
    return rows


def catalogue_rows() -> list:
    rows = [["Mã hàng", "Tên hàng", "ĐVT chuẩn", "ĐVT", "Hệ số quy đổi"]]
    for sku, (name, _p, _c) in SKUS.items():
        rows.append([sku, name, "thùng", "", ""])
    rows.append(["NC01", "Nước cam 330ml", "thùng", "lốc", 1 / LOC])
    rows.append([UNPLANNED[0], UNPLANNED[1], "thùng", "", ""])
    return rows


PLAN_HEADER = ["Phiên bản", "Năm", "Tháng", "Mã hàng", "Kênh", "Sản lượng (thùng)", "Giá bán/thùng", "Giá vốn/thùng"]


def plan_rows(version: str = "AOP", total: int = 0) -> list:
    """The plan by SKU x channel; total adds a printed 'Tổng cộng' row of the volume, off by that much."""
    rows = [PLAN_HEADER]
    for month in range(1, 13):
        for (sku, channel) in PLAN_QTY:
            _n, price, cost = SKUS[sku]
            rows.append([version, 2026, month, sku, channel, plan_month(sku, channel, month), price, cost])
    if total is not None:
        rows.append(["Tổng cộng", "", "", "", "", sum(r[5] for r in rows[1:]) + total, "", ""])
    return rows


def october(qty_only: bool = False) -> dict:
    """{SKU: (cartons, net revenue, cost of goods sold)} actually sold in October, all channels together."""
    out = {}
    for (sku, channel) in PLAN_QTY:
        q, p, c = actual_month(sku, channel, 10)
        a = out.get(sku, (0, 0, 0))
        out[sku] = (a[0] + q, a[1] + q * p, a[2] + q * c)
    sku, _n, _ch, q, p, c = UNPLANNED
    out[sku] = (q, q * p, q * c)
    return out


def october_plan() -> dict:
    out = {}
    for (sku, channel) in PLAN_QTY:
        _n, p, c = SKUS[sku]
        q = plan_month(sku, channel, 10)
        a = out.get(sku, (0, 0, 0))
        out[sku] = (a[0] + q, a[1] + q * p, a[2] + q * c)
    return out


def tb_rows(shift: dict = None, extra: list = ()) -> list:
    """October trial balance: 5111 Có is gross revenue, 5211 Nợ the MT discounts, 632 Nợ the cost of goods sold."""
    gross = ck = cogs = 0
    for r in ledger_rows()[1:-1]:
        if r[0].endswith("/10/2026"):
            gross, ck, cogs = gross + int(r[7]), ck + int(r[8]), cogs + int(r[10])
    by = shift or {}
    gross, ck, cogs = gross + by.get("5111", 0), ck + by.get("5211", 0), cogs + by.get("632", 0)
    return [["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ Nợ", "Số dư đầu kỳ Có", "Phát sinh Nợ", "Phát sinh Có",
             "Số dư cuối kỳ Nợ", "Số dư cuối kỳ Có"],
            ["511", "Doanh thu bán hàng", 0, 0, gross, gross, 0, 0],
            ["5111", "Doanh thu bán hàng hóa", 0, 0, gross, gross, 0, 0],      # Nợ: 521 moved in, then closed to 911
            ["521", "Các khoản giảm trừ doanh thu", 0, 0, ck, ck, 0, 0],
            ["5211", "Chiết khấu thương mại", 0, 0, ck, ck, 0, 0],
            ["632", "Giá vốn hàng bán", 0, 0, cogs, cogs, 0, 0],
            ["911", "Xác định kết quả kinh doanh", 0, 0, 0, 0, 0, 0]] + [list(r) for r in extra]


GL_HEADER = ["Ngày CT", "Số CT", "Tháng", "Năm", "TK Nợ", "TK Có", "Mã phòng ban", "Mã khoản mục", "Diễn giải",
             "Số tiền"]


def gl_rows(ledger: list = None, extra: list = (), closing: bool = True) -> list:
    """The general ledger's revenue and cost postings for October, as accounting books them from the invoices:
    Nợ 131 / Có 5111 gross, Nợ 5211 / Có 131 the discount, Nợ 632 / Có 156 the cost; then the closing entries."""
    rows, n = [GL_HEADER], 0
    gross = ck = cogs = 0
    for r in (ledger or ledger_rows())[1:-1]:
        if not r[0].endswith("/10/2026"):
            continue
        g, d, c = int(r[7]), int(r[8]), int(r[10])
        n += 1
        rows.append([r[0], r[1], "10", "2026", "131", "5111", "", "", f"Doanh thu {r[3]}", str(g)])
        if d:
            rows.append([r[0], r[1], "10", "2026", "5211", "131", "", "", f"Chiết khấu {r[3]}", str(d)])
        if c > 0:
            rows.append([r[0], f"XK{n:05d}", "10", "2026", "632", "156", "", "", f"Giá vốn {r[3]}", str(c)])
        elif c < 0:
            rows.append([r[0], f"NK{n:05d}", "10", "2026", "156", "632", "", "", f"Hàng trả lại {r[3]}", str(-c)])
        gross, ck, cogs = gross + g, ck + d, cogs + c
    rows += [list(x) for x in extra]
    if closing:
        rows += [["31/10/2026", "KC001", "10", "2026", "5111", "5211", "", "", "Kết chuyển chiết khấu", str(ck)],
                 ["31/10/2026", "KC002", "10", "2026", "5111", "911", "", "", "Kết chuyển doanh thu", str(gross - ck)],
                 ["31/10/2026", "KC003", "10", "2026", "911", "632", "", "", "Kết chuyển giá vốn", str(cogs)]]
    return rows


def write_csv(path: Path, rows: list) -> Path:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path


def write_xlsx(path: Path, sheets: dict) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def folder(path: Path, ledger: list = None, plan: dict = None, catalogue: list = None, gl: list = None) -> Path:
    """plan: {sheet: rows} of the AOP workbook, the plan by SKU by default. gl: the general ledger's rows."""
    path.mkdir(parents=True, exist_ok=True)
    if gl is not None:
        write_csv(path / "SoCai_GL_T10_2026.csv", gl)
    write_csv(path / "SoBanHang_2026.csv", ledger or ledger_rows())
    write_xlsx(path / "KeHoach_AOP_2026.xlsx", plan or {"KH theo ma hang": plan_rows()})
    write_xlsx(path / "DanhMuc.xlsx", {"Ma hang": catalogue or catalogue_rows()})
    return path
