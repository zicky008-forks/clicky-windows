"""Two made-up ERP general ledgers with segmented accounts, and the map file for each. Every code, name and figure is
invented; the two layouts differ on purpose (5 segments in one column split by '-', with Dr/Cr columns; 10 segments
in their own columns, with one signed amount), so the reader is shown to take its layout from the map, not the code.

Both tell the same October 2026: salaries, advertising (with 5 tr written down), rent, revenue and cost of goods
sold, plus the traps: a cost centre and an account the map does not have, a combination with too few segments,
a line without an amount, a printed total.
"""
import csv
from pathlib import Path

M = 1_000_000
# (department, cost element, VAS account, amount) of the October operating costs, as the matrix should read them
OPEX = [("KD", "LUONG", "6411", 420 * M), ("MKT", "QC", "6418", 350 * M), ("HC", "THUE", "6427", 120 * M),
        ("HC", "LUONG", "6421", 310 * M)]
WRITTEN_DOWN = ("MKT", "QC", "6418", 5 * M)       # on the credit side
UNKNOWN_DEPT = 7 * M                               # advertising on a cost centre the map does not have
UNKNOWN_ACCOUNT = 2 * M                            # a sales cost account the map does not have (641 by its code)
REVENUE, COGS = 1000 * M, 600 * M
SEPTEMBER = ("HC", "THUE", "6427", 120 * M)

# ── Layout A: 5 segments in one column, '-', Dr/Cr ─────────────────────────

A_SEGMENTS = [["Thứ tự", "Tên phân đoạn", "Vai trò"], [1, "Company", "Công ty"], [2, "Cost Center", "Bộ phận"],
              [3, "Account", "Tài khoản"], [4, "Product", "Sản phẩm"], [5, "Interco", "Khác"]]
A_MAP = [["Phân đoạn", "Mã", "Bộ phận", "Tên bộ phận", "Yếu tố chi phí", "Tên yếu tố", "TK"],
         ["Bộ phận", "2100", "KD", "Kinh doanh", "", "", ""], ["Bộ phận", "2200", "MKT", "Marketing", "", "", ""],
         ["Bộ phận", "3100", "HC", "Hành chính", "", "", ""],
         ["Tài khoản", "641100", "", "", "LUONG", "Lương", "6411"], ["Tài khoản", "641800", "", "", "QC", "Quảng cáo", "6418"],
         ["Tài khoản", "642100", "", "", "LUONG", "Lương", "6421"],
         ["Tài khoản", "642700", "", "", "THUE", "Thuê văn phòng", "6427"],
         ["Tài khoản", "5111*", "", "", "", "", "5111"], ["Tài khoản", "632000", "", "", "", "", "632"],
         ["Tài khoản", "131000", "", "", "", "", "131"], ["Tài khoản", "331000", "", "", "", "", "331"],
         ["Tài khoản", "334000", "", "", "", "", "334"], ["Tài khoản", "156*", "", "", "", "", "156"],
         ["Tài khoản", "2294*", "", "", "", "", "2294"], ["Tài khoản", "911*", "", "", "", "", "911"]]
A_CC = {"KD": "2100", "MKT": "2200", "HC": "3100"}
A_ACC = {"6411": "641100", "6418": "641800", "6421": "642100", "6427": "642700"}
A_HEADER = ["Period", "GL Date", "Journal Name", "Category", "Account", "Line Description", "Accounted Dr",
            "Accounted Cr"]


def _a(cc, acct, dr=0, cr=0, period="OCT-26", text="", journal="JE-001", category="Manual"):
    return [period, "31/10/2026" if period == "OCT-26" else "30/09/2026", journal, category,
            f"10-{cc}-{acct}-000-00", text, dr or "", cr or ""]


def ledger_a(extra: list = ()) -> list:
    rows = [A_HEADER]
    for dept, _e, vas, amount in OPEX:
        rows.append(_a(A_CC[dept], A_ACC[vas], dr=amount, text=f"Chi phí {dept}"))
        rows.append(_a("0000", "334000" if _e == "LUONG" else "331000", cr=amount, text=f"Phải trả {dept}"))
    d, _e, vas, amount = WRITTEN_DOWN
    rows.append(_a(A_CC[d], A_ACC[vas], cr=amount, text="Ghi giảm quảng cáo"))
    rows.append(_a("0000", "331000", dr=amount, text="Ghi giảm quảng cáo"))
    rows.append(_a("2999", "641800", dr=UNKNOWN_DEPT, text="Quảng cáo, cost center mới"))
    rows.append(_a("2100", "641999", dr=UNKNOWN_ACCOUNT, text="Chi phí bán hàng khác"))
    rows.append(_a("0000", "131000", dr=REVENUE, text="Doanh thu tháng 10", category="Receivables"))
    rows.append(_a("0000", "511100", cr=REVENUE, text="Doanh thu tháng 10", category="Receivables"))
    rows.append(_a("0000", "632000", dr=COGS, text="Giá vốn", category="Inventory"))
    rows.append(_a("0000", "156000", cr=COGS, text="Giá vốn", category="Inventory"))
    d, _e, vas, amount = SEPTEMBER
    rows.append(_a(A_CC[d], A_ACC[vas], dr=amount, period="SEP-26", text="Thuê văn phòng T9"))
    rows.append(["OCT-26", "31/10/2026", "JE-002", "Manual", "10-2100-641100", "Thiếu phân đoạn", 1 * M, ""])
    rows.append(["OCT-26", "31/10/2026", "JE-003", "Manual", "10-2100-641100-000-00", "Dòng không có số tiền", "", ""])
    rows += [list(r) for r in extra]
    rows.append(["Total", "", "", "", "", "", "", ""])
    return rows


# ── Layout B: 10 segments in their own columns, one signed amount ──────────

B_NAMES = ["Entity", "Region", "Department", "Natural Account", "Sub Account", "Product", "Channel", "Project",
           "Intercompany", "Future"]
B_ROLES = ["Pháp nhân", "Khác", "Phòng ban", "Tài khoản", "Khác", "Sản phẩm", "Kênh", "Khác", "Khác", "Khác"]
B_SEGMENTS = [["STT", "Tên phân đoạn", "Vai trò"]] + [[i + 1, n, r] for i, (n, r) in enumerate(zip(B_NAMES, B_ROLES))]
B_MAP = [["Phân đoạn", "Mã", "Bộ phận", "Tên bộ phận", "Yếu tố chi phí", "Tên yếu tố", "TK", "Tên"],
         ["Department", "D10", "KD", "Kinh doanh", "", "", "", ""], ["Department", "D20", "MKT", "Marketing", "", "", "", ""],
         ["Department", "D30", "HC", "Hành chính", "", "", "", ""],
         ["Natural Account", "60110", "", "", "LUONG", "Lương", "6411", ""],
         ["Natural Account", "60180", "", "", "QC", "Quảng cáo", "6418", ""],
         ["Natural Account", "60210", "", "", "LUONG", "Lương", "6421", ""],
         ["Natural Account", "60270", "", "", "THUE", "Thuê văn phòng", "6427", ""],
         ["Natural Account", "40*", "", "", "", "", "5111", ""], ["Natural Account", "50100", "", "", "", "", "632", ""],
         ["Natural Account", "1*", "", "", "", "", "131", ""], ["Natural Account", "2*", "", "", "", "", "331", ""],
         ["Channel", "GT1", "", "", "", "", "", "Kênh truyền thống"]]
B_PARAMS = [["Tham số", "Giá trị"], ["Dấu phân cách", "."], ["TK chi phí hoạt động", "641, 642"]]
B_DEPT = {"KD": "D10", "MKT": "D20", "HC": "D30"}
B_ACC = {"6411": "60110", "6418": "60180", "6421": "60210", "6427": "60270"}
B_HEADER = ["GL Date", "Journal Name", "Journal Category", "Line Description"] + B_NAMES + ["Accounted Amount"]


def _b(dept, acct, amount, date="31/10/2026", text="", category="Manual"):
    return [date, "JE-B", category, text, "E1", "R0", dept, acct, "000", "P0", "GT1", "000", "00", "0000", amount]


def ledger_b() -> list:
    rows = [B_HEADER]
    for dept, _e, vas, amount in OPEX:
        rows.append(_b(B_DEPT[dept], B_ACC[vas], amount, text=f"Chi phí {dept}"))
        rows.append(_b("D00", "20100", -amount, text=f"Phải trả {dept}"))
    d, _e, vas, amount = WRITTEN_DOWN
    rows.append(_b(B_DEPT[d], B_ACC[vas], -amount, text="Ghi giảm quảng cáo"))
    rows.append(_b("D00", "20100", amount, text="Ghi giảm quảng cáo"))
    rows.append(_b("D99", "60180", UNKNOWN_DEPT, text="Quảng cáo, phòng mới"))
    rows.append(_b("D10", "64199", UNKNOWN_ACCOUNT, text="Chi phí bán hàng khác"))
    rows.append(_b("D00", "10100", REVENUE, text="Doanh thu"))
    rows.append(_b("D00", "40100", -REVENUE, text="Doanh thu"))
    rows.append(_b("D00", "50100", COGS, text="Giá vốn"))
    rows.append(_b("D00", "10200", -COGS, text="Giá vốn"))
    d, _e, vas, amount = SEPTEMBER
    rows.append(_b(B_DEPT[d], B_ACC[vas], amount, date="30/09/2026", text="Thuê văn phòng T9"))
    rows.append(_b("D10", "60110", "", text="Dòng không có số tiền"))
    return rows


def october_debits() -> dict:
    """Phát sinh Nợ of each operating cost account in October, as the books have it."""
    out = {}
    for _d, _e, vas, amount in OPEX:
        out[vas] = out.get(vas, 0) + amount
    out["6418"] += UNKNOWN_DEPT
    return out


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


def folder(path: Path, layout: str = "A", extra: list = ()) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if layout == "A":
        write_xlsx(path / "AnhXa_PhanDoan.xlsx", {"Phân đoạn": A_SEGMENTS, "Ánh xạ": A_MAP})
        write_csv(path / "GL_T10_2026.csv", ledger_a(extra))
    else:
        write_xlsx(path / "Segment mapping.xlsx", {"Segments": B_SEGMENTS, "Map": B_MAP, "Tham số": B_PARAMS})
        write_xlsx(path / "GL export Oct 2026.xlsx", {"GL": ledger_b()})
    return path
