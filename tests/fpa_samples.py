"""Synthetic FP&A month for tests: a cost detail, an approved plan (AOP) and the October trial balance of a made-up
company. Every figure is invented; the traps are the ones a real cost detail has (a line with no department, a
cost with no plan, a cost written down on the credit side, a line without an amount, one without a month, one
posted after the month).

Calendar fiscal year, the month under review is 10/2026. Amounts are whole đồng.
"""
import csv
from decimal import Decimal
from pathlib import Path

M = 1_000_000
PERIOD = "2026-10"
DEPTS = {"KD": "Kinh doanh", "MKT": "Marketing", "HC": "Hành chính"}
ELEMENTS = {"LUONG": "Lương", "KH": "Khấu hao", "CTP": "Công tác phí", "QC": "Quảng cáo", "THUE": "Thuê văn phòng"}
# (department, cost element, account, plan per month in millions)
PLAN = [("KD", "LUONG", "6411", 420), ("KD", "KH", "6414", 35), ("KD", "CTP", "6418", 48),
        ("MKT", "LUONG", "6411", 180), ("MKT", "QC", "6418", 260),
        ("HC", "LUONG", "6421", 310), ("HC", "THUE", "6427", 120), ("HC", "KH", "6424", 22)]
# October against plan, in millions: the stories the report should tell.
OCTOBER = {("MKT", "QC"): 95, ("KD", "CTP"): -20, ("KD", "LUONG"): 12, ("HC", "THUE"): 0}
# The traps, all in October except where said.
UNASSIGNED = ("#N/A", "CTP", "6418", Decimal("7500000"))          # no department: kept as "Chưa gán bộ phận"
UNPLANNED = ("MKT", "CTP", "6418", 12 * M)                         # spent with no plan
WRITTEN_DOWN = ("MKT", "QC", "6418", 5 * M)                        # TK Có 6418: a cost written down
LATER = ("HC", "THUE", "6427", 3 * M)                              # posted in 11/2026
NO_MONTH = ("HC", "KH", "6424", 2 * M)                             # no month, no date

GL_HEADER = ["Ngày CT", "Số CT", "Tháng", "Năm", "TK Nợ", "TK Có", "Mã phòng ban", "Mã khoản mục", "Diễn giải",
             "Số tiền"]
PLAN_HEADER = ["Phiên bản", "Năm", "Tháng", "Mã phòng ban", "Mã khoản mục", "Số tiền"]


def plan_month(dept: str, element: str, month: int) -> int:
    base = next(p for d, e, _a, p in PLAN if d == dept and e == element)
    return (base + (month * 5 if element == "QC" else 0)) * M


def actual_month(dept: str, element: str, month: int) -> int:
    """Plan plus a small, fixed wobble; October tells the stories above."""
    if month == 10:
        return plan_month(dept, element, month) + OCTOBER.get((dept, element), 3) * M
    i = [(d, e) for d, e, _a, _p in PLAN].index((dept, element))
    return plan_month(dept, element, month) + ((month * 7 + i * 3) % 11 - 5) * M


def gl_rows() -> list:
    rows, n = [GL_HEADER], 0
    for month in range(1, 11):
        for dept, element, account, _p in PLAN:
            total = actual_month(dept, element, month)
            first = total * 6 // 10
            for part in (first, total - first):
                n += 1
                rows.append([f"25/{month:02d}/2026", f"PC{n:05d}", str(month), "2026", account, "3341" if element == "LUONG"
                             else "331", dept, element, f"{ELEMENTS[element]} {DEPTS[dept]} T{month}", str(part)])
    d, e, a, amount = UNASSIGNED
    rows.append(["28/10/2026", "PC90001", "10", "2026", a, "1111", d, e, "Công tác phí chưa gán bộ phận", str(amount)])
    d, e, a, amount = UNPLANNED
    rows.append(["28/10/2026", "PC90002", "10", "2026", a, "1111", d, e, "Công tác hội chợ", str(amount)])
    d, e, a, amount = WRITTEN_DOWN
    rows.append(["30/10/2026", "PK90003", "10", "2026", "3388", a, d, e, "Ghi giảm chi phí quảng cáo", str(amount)])
    rows.append(["30/10/2026", "PK90004", "10", "2026", "6418", "331", "MKT", "QC", "Dòng thiếu số tiền", ""])
    d, e, a, amount = LATER
    rows.append(["02/11/2026", "PC90005", "11", "2026", a, "331", d, e, "Thuê văn phòng T11", str(amount)])
    d, e, a, amount = NO_MONTH
    rows.append(["", "PK90006", "", "", a, "214", d, e, "Khấu hao điều chỉnh", str(amount)])
    printed = sum(int(r[9]) for r in rows[1:] if r[9])
    rows.append(["", "", "", "", "", "", "", "", "Tổng cộng", str(printed)])     # a printed total: never added in
    return rows


def plan_rows(version: str = "AOP", approved: str = "", scale: int = 1) -> list:
    """The plan file; approved writes a 'Ngày duyệt' column, scale makes a different draft of the same plan."""
    rows = [PLAN_HEADER + (["Ngày duyệt"] if approved else [])]
    for month in range(1, 13):
        for dept, element, _a, _p in PLAN:
            rows.append([version, "2026", str(month), dept, element, str(plan_month(dept, element, month) * scale)]
                        + ([approved] if approved else []))
    rows.append(["", "", "", "", "Tổng cộng", str(sum(int(r[5]) for r in rows[1:]))])
    return rows


def october_debits() -> dict:
    """Phát sinh Nợ by account in October, as the books have it: every debit posting, nothing else."""
    out = {}
    for row in gl_rows()[1:]:
        if row[2] == "10" and row[4].startswith("6") and row[9]:
            out[row[4]] = out.get(row[4], 0) + int(Decimal(row[9]))
    return out


NAMES = {"641": "Chi phí bán hàng", "6411": "Chi phí nhân viên", "6414": "Chi phí khấu hao TSCĐ",
         "6418": "Chi phí bằng tiền khác", "642": "Chi phí quản lý doanh nghiệp", "6421": "Chi phí nhân viên quản lý",
         "6424": "Chi phí khấu hao TSCĐ", "6427": "Chi phí dịch vụ mua ngoài"}


def tb_rows(shift: dict = None) -> list:
    """The October trial balance; shift adds to an account's Phát sinh Nợ (a books/detail gap)."""
    debits = october_debits()
    for account, by in (shift or {}).items():
        debits[account] = debits.get(account, 0) + by
    rows = [["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ Nợ", "Số dư đầu kỳ Có", "Phát sinh Nợ", "Phát sinh Có",
             "Số dư cuối kỳ Nợ", "Số dư cuối kỳ Có"]]
    for parent in ("641", "642"):
        kids = sorted(a for a in NAMES if a.startswith(parent) and a != parent)
        dr = sum(debits.get(a, 0) for a in kids)
        rows.append([parent, NAMES[parent], 0, 0, dr, dr, 0, 0])
        for a in kids:
            d = debits.get(a, 0)
            rows.append([a, NAMES[a], 0, 0, d, d, 0, 0])       # closed to 911 (and written down) on the credit side
    rows.append(["911", "Xác định kết quả kinh doanh", 0, 0, 0, 0, 0, 0])
    return rows


def write_csv(path: Path, rows: list) -> Path:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path


def folder(path: Path, versions=("AOP",), plans: dict = None) -> Path:
    """versions: one plan file per version. plans: {file name: rows} instead, for drafts of one version."""
    path.mkdir(parents=True, exist_ok=True)
    write_csv(path / "SoChiTietChiPhi_2026.csv", gl_rows())
    for name, rows in (plans or {f"{v}_2026.csv": plan_rows(v) for v in versions}).items():
        write_csv(path / name, rows)
    return path


# The catalogue of the opex split case: department addresses (Hành chính has none), cost elements, the rules.
DEPT_SHEET = [["Mã phòng ban", "Tên phòng ban", "Khối", "Email", "CC"],
              ["KD", "Kinh doanh", "Thương mại", "kd@example.com", "truongkd@example.com"],
              ["MKT", "Marketing", "Thương mại", "mkt@example.com", ""],
              ["HC", "Hành chính", "Hỗ trợ", "", ""]]
ELEMENT_SHEET = [["Mã khoản mục", "Tên khoản mục", "Tài khoản"]] + [[c, n, a] for c, n, a in (
    ("LUONG", "Lương", "6411"), ("KH", "Khấu hao", "6414"), ("CTP", "Công tác phí", "6418"), ("QC", "Quảng cáo", "6418"),
    ("THUE", "Thuê văn phòng", "6427"))]
RULES = [["Tham số", "Giá trị"], ["Tháng bắt đầu năm tài chính", 1], ["Email kế toán", "ketoan@example.com"],
         ["Người gửi", "Phòng FP&A"], ["Hạn phản hồi", "18/11/2026"]]
# Pay lines that name a person: they must never reach a department's file or its draft
PAYROLL = [["25/10/2026", "PL77701", "10", "2026", "6411", "3341", "KD", "LUONG", "Lương T10 – Nguyễn Văn An", "25000000"],
           ["25/10/2026", "PL77702", "10", "2026", "6411", "3341", "KD", "LUONG", "Thưởng KPI – Trần Thị Bình", "8000000"]]
PAYROLL_TOTAL = 33 * M
PERSONAL = ("Nguyễn Văn An", "Trần Thị Bình", "PL77701", "PL77702")


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


def opex_folder(path: Path, tb=True, shift=None, payroll=False) -> Path:
    """The opex split case: cost detail, AOP, catalogue and (tb) the October trial balance that matches it.
    payroll adds two pay lines that name a person; the books move with them, so the split still matches."""
    d = folder(path)
    if payroll:
        rows = gl_rows()
        rows[-1:-1] = PAYROLL
        rows[-1][9] = str(int(rows[-1][9]) + PAYROLL_TOTAL)
        write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
        shift = dict(shift or {}, **{"6411": (shift or {}).get("6411", 0) + PAYROLL_TOTAL})
    write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": DEPT_SHEET, "Khoản mục": ELEMENT_SHEET, "Tham số": RULES})
    if tb:
        write_csv(d / "TB T10 2026.csv", tb_rows(shift))
    return d
