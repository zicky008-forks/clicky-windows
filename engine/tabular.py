"""Read the table out of an export file by what the file holds, not what it is called.

Accounting and sales software in Vietnam often saves exports with a ".xls" name that is not a real Excel 97-2003
workbook. The bytes decide how the file is read:

* OLE2 (D0 CF 11 E0): a real .xls, read with xlrd;
* a zip (PK): an .xlsx under another name, read with openpyxl;
* HTML with a <table>: what MISA and many web exports save as ".xls";
* SpreadsheetML 2003 XML (urn:schemas-microsoft-com:office:spreadsheet);
* anything else that decodes as text: comma, semicolon or tab separated.

Every cell comes back as text written the way an export writes it: a date as 2026-10-13 (ISO, so its day/month
order is never guessed), a number in plain digits (1250000 or 1250000.5), so the same rules then apply to every
format. Nothing is ever written; a file that cannot be read raises Unreadable with the reason in Vietnamese.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP = b"PK\x03\x04"
SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
MAX_BYTES = 64 * 1024 * 1024        # an order export is far smaller; a bigger file is not read at all


class Unreadable(Exception):
    """The file's content is not a table Clicky can read. str(exc) is the reason, in Vietnamese."""


def sniff(raw: bytes) -> str:
    """'xls' | 'xlsx' | 'html' | 'xml' | 'text' | 'binary', from the first bytes."""
    if raw.startswith(OLE2):
        return "xls"
    if raw.startswith(ZIP):
        return "xlsx"
    head = decode(raw[:4096]).lstrip("﻿ \t\r\n").lower()
    if head.startswith("<?xml") or head.startswith("<workbook"):
        if SS_NS in decode(raw[:65536]):
            return "xml"
        return "html" if re.search(r"<(html|table)", head) else "xml"
    if head.startswith("<") and re.search(r"<(html|table|body|head|meta|!doctype html)", head):
        return "html"
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "binary"
    return "text"


def decode(raw: bytes) -> str:
    """Text the way exports are written: UTF-16 with a byte-order mark, UTF-8, or the Vietnamese Windows code page."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp1258", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def number_text(value) -> str:
    """A number as an export writes it: 1250000, 1250000.5, -300 (never 1.25e6, never 1250000.0)."""
    try:
        d = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def date_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat() if not (value.hour or value.minute or value.second) \
            else value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def sheets(path: Path, keep_blank: bool = False) -> list:
    """[(sheet name, rows)] with rows as lists of text; fully blank rows are left out (as the CSV reader does),
    unless keep_blank (a trial balance quotes its own row numbers)."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise Unreadable("file quá lớn để là một file xuất đơn hàng, em không mở")
        raw = path.read_bytes()
    except OSError as exc:
        raise Unreadable("em chưa mở được file này (file đang bị khoá hoặc không đọc được)") from exc
    kind = sniff(raw)
    if kind == "xls":
        found = _xls(raw)
    elif kind == "xlsx":
        found = _xlsx(raw)
    elif kind == "html":
        found = _html(decode(raw))
    elif kind == "xml":
        found = _xml(decode(raw))
    elif kind == "text":
        found = [(path.stem, _text(decode(raw)))]
    else:
        raise Unreadable("chưa đọc được định dạng của file này (không phải Excel, CSV, HTML hay XML)")
    out = [(name, rows if keep_blank else [r for r in rows if any(str(c).strip() for c in r)]) for name, rows in found]
    return [(name, rows) for name, rows in out if any(any(str(c).strip() for c in r) for r in rows)]


def kind_of(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            return sniff(f.read(65536))
    except OSError:
        return "binary"


# ── formats ─────────────────────────────────────────────────────────────────────

def _xls(raw: bytes) -> list:
    try:
        import xlrd
    except ImportError as exc:                # the build ships it; a dev machine may lack it
        raise Unreadable("bản Clicky này thiếu bộ đọc file .xls cũ") from exc
    try:
        book = xlrd.open_workbook(file_contents=raw, on_demand=True)
    except Exception as exc:                  # noqa: BLE001 - a broken or encrypted file
        raise Unreadable("file .xls bị hỏng hoặc có mật khẩu, em chưa đọc được") from exc
    out = []
    try:
        for i in range(book.nsheets):
            sh = book.sheet_by_index(i)
            rows = []
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            row.append(date_text(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)))
                        except Exception:     # noqa: BLE001 - an impossible serial: keep the number
                            row.append(number_text(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        row.append(number_text(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        row.append("TRUE" if cell.value else "FALSE")
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        row.append(xlrd.error_text_from_code.get(cell.value, "#ERR"))
                    else:
                        row.append(str(cell.value).strip())
                rows.append(row)
            out.append((sh.name, rows))
            book.unload_sheet(i)
    finally:
        book.release_resources()
    return out


def _xlsx(raw: bytes) -> list:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:                  # noqa: BLE001
        raise Unreadable("file Excel này bị hỏng hoặc có mật khẩu, em chưa đọc được") from exc
    out = []
    try:
        for ws in wb.worksheets:
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else date_text(v) if isinstance(v, (date, datetime))
                             else number_text(v) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
                             else str(v).strip() for v in r])
            out.append((ws.title, rows))
    finally:
        wb.close()
    return out


class _Tables(HTMLParser):
    """Every <table> as rows of cell text; colspan repeats as blank cells so columns stay aligned."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack = [], []     # stack of (rows, current row, current cell parts, colspan)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None, "span": 1})
            return
        if not self._stack:
            return
        t = self._stack[-1]
        if tag == "tr":
            self._end_row(t)
            t["row"] = []
        elif tag in ("td", "th"):
            if t["row"] is None:
                t["row"] = []
            self._end_cell(t)
            t["cell"] = []
            span = dict(attrs).get("colspan") or "1"
            t["span"] = int(span) if str(span).isdigit() and 0 < int(span) < 200 else 1
        elif tag == "br" and t["cell"] is not None:
            t["cell"].append(" ")

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ("td", "th"):
            self._end_cell(t)
        elif tag == "tr":
            self._end_row(t)
        elif tag == "table":
            self._end_row(t)
            self.tables.append(self._stack.pop()["rows"])

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)

    def _end_cell(self, t):
        if t["cell"] is not None and t["row"] is not None:
            text = re.sub(r"\s+", " ", "".join(t["cell"]).replace("\xa0", " ")).strip()
            t["row"].append(text)
            t["row"].extend([""] * (t["span"] - 1))
        t["cell"], t["span"] = None, 1

    def _end_row(self, t):
        self._end_cell(t)
        if t["row"] is not None:
            t["rows"].append(t["row"])
        t["row"] = None

    def close(self):
        super().close()
        while self._stack:                    # an export cut short: keep what was read
            t = self._stack.pop()
            self._end_row(t)
            self.tables.append(t["rows"])


def _html(text: str) -> list:
    parser = _Tables()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:                  # noqa: BLE001
        raise Unreadable("file HTML này em chưa đọc được bảng") from exc
    if not parser.tables:
        raise Unreadable("chưa đọc được định dạng: file HTML không có bảng số liệu")
    return [(f"Bảng {i + 1}", rows) for i, rows in enumerate(parser.tables)]


def _xml(text: str) -> list:
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:65536], re.IGNORECASE):
        # An export never needs entity declarations; refusing them keeps a crafted file from expanding in memory.
        raise Unreadable("file XML này có khai báo lạ, em không mở để an toàn")
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise Unreadable("file XML này bị hỏng, em chưa đọc được") from exc
    ns = f"{{{SS_NS}}}"
    out = []
    for ws in root.iter(f"{ns}Worksheet"):
        rows = []
        for table in ws.iter(f"{ns}Table"):
            for row in table.iter(f"{ns}Row"):
                at = row.get(f"{ns}Index")
                if at and at.isdigit():
                    rows.extend([] for _ in range(max(0, int(at) - 1 - len(rows))))
                cells = []
                for cell in row.iter(f"{ns}Cell"):
                    at = cell.get(f"{ns}Index")
                    if at and at.isdigit():
                        cells.extend([""] * max(0, int(at) - 1 - len(cells)))
                    data = cell.find(f"{ns}Data")
                    value = "" if data is None else re.sub(r"\s+", " ", "".join(data.itertext())).strip()
                    kind = "" if data is None else data.get(f"{ns}Type", "")
                    if kind == "DateTime" and re.match(r"\d{4}-\d{2}-\d{2}", value):
                        value = value[:10] if value[10:].strip("T0:.") == "" else value.replace("T", " ")[:19]
                    elif kind == "Number":
                        value = number_text(value)
                    cells.append(value)
                    across = cell.get(f"{ns}MergeAcross")
                    if across and across.isdigit():
                        cells.extend([""] * min(int(across), 200))
                rows.append(cells)
        out.append((ws.get(f"{ns}Name") or f"Sheet{len(out) + 1}", rows))
    if not out:
        raise Unreadable("chưa đọc được định dạng: file XML không phải bảng tính Excel")
    return out


def _text(text: str) -> list:
    # A print-layout export starts with title lines that hold no separator: judge on the line with the most.
    lines = [line for line in text.splitlines() if line.strip()][:30]
    best = {d: max((line.count(d) for line in lines), default=0) for d in ("\t", ";", ",")}
    delim = max(best, key=best.get) if max(best.values()) else ","
    return [[c.strip() for c in row] for row in csv.reader(text.splitlines(), delimiter=delim)]
