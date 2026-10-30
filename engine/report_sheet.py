"""Lay the monthly report out as sheet grids whose numbers are live Excel formulas.

Each report figure is a SUMIFS over a cleaned trial-balance sheet, so clicking a
cell shows exactly which accounts feed it, and a check column compares every
formula with the value the engine computed in code. Grids are plain lists, written
either into the open workbook over COM or into an .xlsx file.
"""
from engine.checks import Issue
from engine.monthly_report import PNL, Figure, Variance, fmt
from engine.trial_balance import TrialBalance

TB_HEADERS = ["Tài khoản", "Tên tài khoản", "Dư Nợ đầu kỳ", "Dư Có đầu kỳ", "Phát sinh Nợ", "Phát sinh Có",
              "Dư Nợ cuối kỳ", "Dư Có cuối kỳ", "Dòng gốc"]
TB_FIELDS = ["open_dr", "open_cr", "dr", "cr", "close_dr", "close_cr"]
SIDE_COLUMN = {"dr": "E", "cr": "F"}
FIRST_LINE_ROW = 5


def quote(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def tb_grid(tb: TrialBalance, text_prefix: str = "") -> list:
    """Leaf accounts only, so SUMIFS on a prefix never counts a parent and its children twice.
    text_prefix="'" keeps account codes as text when written over COM."""
    rows = [TB_HEADERS]
    for l in tb.leaves:
        rows.append([text_prefix + l.account, l.name] + [getattr(l, f) for f in TB_FIELDS] + [source_link(tb.sheet, l.row)])
    return rows


def source_link(sheet: str, row: int) -> str:
    return f'=HYPERLINK("#{quote(sheet)}!A{row}","{sheet} dòng {row}")'


def _sumifs(tb_sheet: str, prefixes) -> str:
    q = quote(tb_sheet)
    parts = [f'SUMIFS({q}!${SIDE_COLUMN[side]}:${SIDE_COLUMN[side]},{q}!$A:$A,"{prefix}*")' for prefix, side, _ in prefixes]
    return "=" + "+".join(parts)


def report_grid(current: list, variances: list, cur_sheet: str, prev_sheet: str, title: str) -> list:
    """Chỉ tiêu | TK | Kỳ này | Kỳ trước | Chênh lệch | % | Thuận lợi/Bất lợi | Biến động chính | Khớp với code"""
    from engine.questions import FAVOURABLE_UP
    rows = [[title], ["Số liệu lấy bằng công thức từ sheet " + cur_sheet + " và " + prev_sheet +
                      "; cột I so từng công thức với kết quả tính bằng code. Số liệu là TÍNH TOÁN; nguyên nhân xem sheet câu hỏi."], [],
            ["Chỉ tiêu", "Tài khoản", "Kỳ này", "Kỳ trước", "Chênh lệch", "%", "Thuận lợi / Bất lợi", "Biến động chính", "Khớp với code"]]
    row_of = {}
    by_key = {v.key: v for v in variances}
    for i, ((key, label, spec), fig) in enumerate(zip(PNL, current)):
        r = FIRST_LINE_ROW + i
        row_of[key] = r
        if isinstance(spec, str):
            def expr(col):
                return "=" + " ".join(f"{col}{row_of[t]}" if t not in "+-" else t for t in spec.split()).replace(" ", "")
            this, prev, accounts = expr("C"), expr("D"), ""
        else:
            this, prev = _sumifs(cur_sheet, spec), _sumifs(prev_sheet, spec)
            accounts = ", ".join(p for p, _, _ in spec)
        v = by_key[key]
        drivers = "; ".join(f"TK {a} {fmt(c, sign=True)}" for a, _n, c in v.drivers)
        check = f'=IF(ABS(C{r}-({fig.value}))<=1,"✓","≠ code {fmt(fig.value)}")'
        good, bad = ("Thuận lợi", "Bất lợi") if key in FAVOURABLE_UP else ("Bất lợi", "Thuận lợi")
        verdict = f'=IF(E{r}=0,"",IF(E{r}>0,"{good}","{bad}"))'
        rows.append([label, accounts, this, prev, f"=C{r}-D{r}", f'=IF(D{r}=0,"",E{r}/ABS(D{r}))', verdict, drivers, check])
    return rows


def checks_grid(issues: list, source_sheet: str) -> list:
    rows = [["Mức độ", "Mã", "Nội dung", "Dòng gốc"]]
    for i in issues:
        link = source_link(source_sheet, i.row) if i.row else ""
        rows.append(["Lỗi" if i.severity == "error" else "Lưu ý", i.code, i.message, link])
    if len(rows) == 1:
        rows.append(["OK", "", "Không phát hiện bẫy nào trong các phép kiểm", ""])
    return rows
