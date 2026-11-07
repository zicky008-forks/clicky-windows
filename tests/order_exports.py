"""Synthetic order exports the way accounting and sales software writes them (made-up company, made-up numbers).

Each writer lays the same five October 2026 orders out in print layout: a title block above the table (company,
report name, period, unit) and, below it, a "Tổng cộng" line and a signature block. Formats:

* write_ole2_xls     a real Excel 97-2003 file (xlwt), dates as date cells, amounts as numbers;
* write_html_xls     an HTML table saved as .xls, as MISA and many web exports do: 1.250.000, 21/10/2026, colspan;
* write_spreadsheetml  SpreadsheetML 2003 XML saved as .xls (ss:Type DateTime / Number, ss:Index, MergeAcross);
* write_english_csv  a sales-platform CSV: Order ID, Created at, SKU, Quantity, Line total, COGS, Status;
* write_datetime_xlsx  an .xlsx saved as .xls, date cells carrying a time (serial with a fraction);
* write_datetime_csv   a Vietnamese CSV whose dates carry a time as text: 03/10/2026 14:35:20;
* write_pos_csv        a POS export whose column names Clicky does not know (POS_HEADER): chị names them once
                       (POS_REMEMBERED), and every later month is read with the columns she named.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path

COMPANY = "CÔNG TY TNHH DỮ LIỆU GIẢ LẬP"
VI_HEADER = ["Số chứng từ", "Ngày chứng từ", "Mã khách hàng", "Mã hàng", "Số lượng", "Thành tiền", "Giá vốn"]
EN_HEADER = ["Order ID", "Created at", "Customer", "SKU", "Quantity", "Line total", "COGS", "Status"]
# (doc, date, customer, item, qty, amount, cost, status)
ORDERS = [
    ("DH001", date(2026, 10, 3), "KH01", "SP01", 2, Decimal("1250000"), Decimal("800000"), "Completed"),
    ("DH002", date(2026, 10, 9), "KH02", "SP02", 1, Decimal("2500000"), Decimal("1700000"), "Completed"),
    ("DH003", date(2026, 10, 15), "KH01", "SP03", 3, Decimal("3750500"), Decimal("2400000"), "Completed"),
    ("DH004", date(2026, 10, 21), "KH03", "SP01", 4, Decimal("4000000"), Decimal("2600000"), "Cancelled"),
    ("DH005", date(2026, 10, 28), "KH04", "SP02", 1, Decimal("980000"), Decimal("640000"), "Refunded"),
]
TOTAL = sum(o[5] for o in ORDERS)                                   # 12.480.500: every order, VN formats
SALES_EN = sum(o[5] for o in ORDERS if o[7] != "Cancelled")         # 8.480.500: the cancelled one set aside
TIMES = [(9, 15, 0), (14, 35, 20), (8, 0, 5), (17, 45, 0), (23, 59, 59)]      # one per order, for dates with a time
POS_HEADER = ["Mã phiếu", "Thời điểm", "Khách", "Giá trị", "Phí"]
POS_REMEMBERED = {"date": "thoidiem", "amount": "giatri"}           # what chị named: column keys (sources._key)
TITLE = [[COMPANY], ["SỔ CHI TIẾT BÁN HÀNG"], ["Từ ngày 01/10/2026 đến ngày 31/10/2026"], ["Đơn vị tính: đồng"]]
SIGN = [["", "", "", "Ngày 31 tháng 10 năm 2026"], ["Người lập biểu", "", "", "Kế toán trưởng", "", "Giám đốc"]]


def vi_dmy(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def vi_number(x: Decimal) -> str:
    whole = f"{int(x):,}".replace(",", ".")
    frac = x - int(x)
    return whole + ("," + str(frac)[2:] if frac else "")


def write_ole2_xls(path: Path) -> Path:
    import xlwt
    book = xlwt.Workbook(encoding="utf-8")
    ws = book.add_sheet("So chi tiet ban hang")
    day = xlwt.easyxf(num_format_str="dd/mm/yyyy")
    money = xlwt.easyxf(num_format_str="#,##0")
    r = 0
    for line in TITLE:
        ws.write(r, 0, line[0])
        r += 1
    r += 1                                                    # a blank row before the table
    for c, h in enumerate(VI_HEADER):
        ws.write(r, c, h)
    r += 1
    for doc, d, kh, item, qty, amt, cost, _ in ORDERS:
        ws.write(r, 0, doc)
        ws.write(r, 1, d, day)
        ws.write(r, 2, kh)
        ws.write(r, 3, item)
        ws.write(r, 4, qty)
        ws.write(r, 5, float(amt), money)
        ws.write(r, 6, float(cost), money)
        r += 1
    ws.write(r, 0, "Tổng cộng")
    ws.write(r, 5, float(TOTAL), money)
    r += 2
    for line in SIGN:
        for c, v in enumerate(line):
            if v:
                ws.write(r, c, v)
        r += 1
    book.save(str(path))
    return path


def write_html_xls(path: Path, header=None, date_in_column=True) -> Path:
    """MISA-style: one HTML table, title rows spanning the width, Vietnamese number and date text."""
    header = header or VI_HEADER
    span = len(header)
    rows = [f'<tr><td colspan="{span}"><b>{escape(t[0])}</b></td></tr>' for t in TITLE]
    rows.append(f'<tr><td colspan="{span}">&nbsp;</td></tr>')
    rows.append("<tr>" + "".join(f"<th>{escape(h)}</th>" for h in header) + "</tr>")
    for doc, d, kh, item, qty, amt, cost, _ in ORDERS:
        cells = [doc, vi_dmy(d), kh, item, str(qty), vi_number(amt), vi_number(cost)][:span]
        rows.append("<tr>" + "".join(f'<td style="mso-number-format:\'@\'">{escape(c)}</td>' for c in cells) + "</tr>")
    rows.append(f'<tr><td>Tổng cộng</td><td colspan="4"></td><td>{vi_number(TOTAL)}</td><td></td></tr>')
    # The signature date sits in the date column here, the way some layouts place it.
    signed = ["", "Ngày 31 tháng 10 năm 2026"] if date_in_column else ["", "", "", "Ngày 31 tháng 10 năm 2026"]
    rows.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in signed) + "</tr>")
    rows.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in SIGN[1]) + "</tr>")
    html = ('<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta http-equiv="Content-Type" '
            'content="text/html; charset=utf-8"></head><body><table border="1">' + "\n".join(rows) + "</table></body></html>")
    path.write_bytes(html.encode("utf-8"))
    return path


def write_spreadsheetml(path: Path) -> Path:
    ss = "urn:schemas-microsoft-com:office:spreadsheet"
    width = len(VI_HEADER)

    def cell(value, kind="String", index=None, merge=None):
        extra = (f' ss:Index="{index}"' if index else "") + (f' ss:MergeAcross="{merge}"' if merge else "")
        return f'<Cell{extra}><Data ss:Type="{kind}">{escape(str(value))}</Data></Cell>'

    rows = [f"<Row>{cell(t[0], merge=width - 1)}</Row>" for t in TITLE]
    rows.append('<Row ss:Index="6">' + "".join(cell(h) for h in VI_HEADER) + "</Row>")  # row 5 left blank
    for doc, d, kh, item, qty, amt, cost, _ in ORDERS:
        rows.append("<Row>" + cell(doc) + cell(f"{d.isoformat()}T00:00:00.000", "DateTime") + cell(kh) + cell(item)
                    + cell(qty, "Number") + cell(amt, "Number") + cell(cost, "Number") + "</Row>")
    rows.append("<Row>" + cell("Tổng cộng") + cell(TOTAL, "Number", index=6) + "</Row>")   # columns 2-5 skipped
    rows.append("<Row>" + cell("Ngày 31 tháng 10 năm 2026", index=4) + "</Row>")
    rows.append("<Row>" + "".join(cell(v) for v in SIGN[1] if v) + "</Row>")
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<?mso-application progid="Excel.Sheet"?>\n'
           f'<Workbook xmlns="{ss}" xmlns:ss="{ss}"><Worksheet ss:Name="BanHang"><Table>'
           + "".join(rows) + "</Table></Worksheet></Workbook>")
    path.write_text(xml, encoding="utf-8")
    return path


def write_english_csv(path: Path, header=None) -> Path:
    """A sales platform's export: a title line above the header, "Total" below, dates with a time."""
    header = header or EN_HEADER
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Sales export", "October 2026"])
        w.writerow(header)
        for doc, d, kh, item, qty, amt, cost, status in ORDERS:
            row = {"Order ID": doc, "Created at": f"{d.isoformat()} 09:15:00", "Customer": kh, "SKU": item,
                   "Quantity": qty, "Line total": f"{amt:.2f}", "COGS": f"{cost:.2f}", "Status": status}
            w.writerow([row.get(h, "") for h in header])
        w.writerow(["Total", "", "", "", "", f"{SALES_EN:.2f}", "", ""][:len(header)])
    return path


def write_datetime_xlsx(path: Path) -> Path:
    """Print layout in a real .xlsx saved under a .xls name: dates are datetime cells with a time of day."""
    from openpyxl import Workbook
    book = Workbook()
    ws = book.active
    ws.title = "Ban hang"
    for line in TITLE:
        ws.append(line)
    ws.append([])
    ws.append(VI_HEADER)
    for (doc, d, kh, item, qty, amt, cost, _), (h, m, sec) in zip(ORDERS, TIMES):
        ws.append([doc, datetime(d.year, d.month, d.day, h, m, sec), kh, item, qty, float(amt), float(cost)])
        ws.cell(ws.max_row, 2).number_format = "dd/mm/yyyy hh:mm:ss"
    ws.append(["Tổng cộng", None, None, None, None, float(TOTAL)])
    ws.append([])
    for line in SIGN:
        ws.append(line)
    book.save(str(path))
    return path


def write_datetime_csv(path: Path) -> Path:
    """A Vietnamese CSV with the time after the date, as text, and Vietnamese amounts."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(VI_HEADER)
        for (doc, d, kh, item, qty, amt, cost, _), (h, m, sec) in zip(ORDERS, TIMES):
            w.writerow([doc, f"{vi_dmy(d)} {h:02d}:{m:02d}:{sec:02d}", kh, item, qty, vi_number(amt), vi_number(cost)])
    return path


def write_pos_csv(path: Path) -> Path:
    """A POS export with column names no list knows (POS_HEADER); dates with a time, Vietnamese amounts."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(POS_HEADER)
        for (doc, d, kh, item, qty, amt, cost, _), (h, m, _s) in zip(ORDERS, TIMES):
            w.writerow([doc, f"{vi_dmy(d)} {h:02d}:{m:02d}", kh, vi_number(amt), vi_number(amt / 100)])
    return path
