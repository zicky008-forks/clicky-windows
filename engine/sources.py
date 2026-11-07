"""Read the month's source files the way they really are, so the Excel result can be checked against them.

A month-close folder holds sales exports from each branch (CSV) and the trial
balances (xlsx). This module works out, by code and without Excel:

* which columns hold the document number, date, amount and cost;
* how each file writes its dates (day/month or month/day), from the values
  themselves, because branches export from machines with different settings;
* the true totals after removing only real duplicates (same document number).

Those totals are the yardstick: whatever Power Query loads must match them, and
they must match the ledger (Có 511, Nợ 632). Amounts are Decimal.
"""
import csv
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import roles
from engine.trial_balance import ZERO, fold, parse_amount, read_trial_balance

# Header words after fold() with spaces removed.
DOC_COLS = {"soct", "sochungtu", "sohd", "sohoadon", "sophieu", "invoice", "invoiceno", "docno", "documentno", "machungtu"}
DATE_COLS = {"ngay", "ngayct", "ngaychungtu", "ngayhd", "ngayhoadon", "date", "invoicedate", "postingdate"}
AMOUNT_COLS = {"thanhtien", "doanhthu", "tienhang", "amount", "netamount", "revenue", "doanhso"}
COST_COLS = {"giavon", "tiengiavon", "cogs", "cost", "costamount"}
BRANCH_COLS = {"chinhanh", "branch", "cuahang", "store"}
CUSTOMER_COLS = {"makh", "makhachhang", "customer", "customerid", "khachhang"}
ITEM_COLS = {"mahang", "mathang", "masp", "masanpham", "sanpham", "tenhang", "tenmathang", "nhomhang", "sku", "item", "product"}

# More names, Vietnamese and English (sales platforms, POS, accounting software), each list best first. They
# rank after the names above: when a file has both "Thành tiền" and "Total", "Thành tiền" is the amount, and a
# line-item "Line total" beats an order-level "Subtotal" or "Total" repeated on every line.
MORE_COLS = {
    "doc": ("orderid", "orderno", "ordernumber", "ordercode", "madonhang", "madon", "sodonhang", "madh", "sodh",
            "invoiceid", "invoicenumber", "receiptno", "receiptnumber", "transactionid", "order"),
    "date": ("orderdate", "createdat", "createddate", "transactiondate", "saledate", "paidat", "ngayban",
             "ngaydathang", "ngaydonhang", "ngaytao", "ngaygiaodich", "ngayxuathoadon", "created"),
    "amount": ("linetotal", "lineamount", "netsales", "subtotal", "thanhtientruocthue", "doanhthuthuan",
               "tongtienhang", "sales", "totalsales", "grosssales", "totalamount", "tongtien", "total"),
    "cost": ("costofgoodssold", "costofgoods", "giavonhangban", "linecost", "totalcost"),
    "branch": ("location", "storename", "shopname", "cuahangban"),
    "customer": ("customername", "customercode", "tenkhachhang", "tenkh", "buyer"),
    "item": ("productname", "productcode", "itemname", "itemcode", "variantsku", "lineitemsku", "lineitemname",
             "tensanpham", "masku"),
    "status": ("status", "orderstatus", "trangthai", "trangthaidonhang", "tinhtrang"),
}
# A Status value that clearly means the order did not happen. Only these, whole value, case and accents ignored;
# "returned", "refunded" and the like stay in and are listed on the receipt for a person to judge.
CANCELLED = {"cancelled", "canceled", "void", "voided", "huy", "da huy"}
HEADER_SCAN = 30          # print-layout exports put a title, the company and the period above the table

DAY_FIRST, MONTH_FIRST = "vi-VN", "en-US"
_SLASHED = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})")


def _key(header) -> str:
    return re.sub(r"[\s_\-./]", "", fold(header))


def find_columns(header: list) -> dict:
    """Role → 0-based column index for the roles present in this header.

    The names in DOC_COLS…ITEM_COLS come first, leftmost column winning, as before; MORE_COLS only fill a role
    none of them filled, best-ranked name first."""
    roles = {}
    for i, h in enumerate(header):
        k = _key(h)
        for role, names in (("doc", DOC_COLS), ("date", DATE_COLS), ("amount", AMOUNT_COLS),
                            ("cost", COST_COLS), ("branch", BRANCH_COLS), ("customer", CUSTOMER_COLS),
                            ("item", ITEM_COLS)):
            if k in names and role not in roles:
                roles[role] = i
    keys = [_key(h) for h in header]
    for role, names in MORE_COLS.items():
        if role in roles:
            continue
        for name in names:
            if name in keys:
                roles[role] = keys.index(name)
                break
    return roles


def find_header(rows: list) -> Optional[int]:
    """The row that names the columns: the first of the top rows with a date and an amount column."""
    for i, row in enumerate(rows[:HEADER_SCAN]):
        cols, _told = columns_of(row)
        if "date" in cols and "amount" in cols:
            return i
    return None


def best_header(rows: list) -> tuple:
    """(row index, roles) of the top row that looks most like a header, for saying what a file lacks."""
    best, best_cols = None, {}
    for i, row in enumerate(rows[:HEADER_SCAN]):
        cols = find_columns(row)
        if len(cols) > len(best_cols):
            best, best_cols = i, cols
    return best, best_cols


# Header signatures a person has explained, the "nhớ cho tháng sau" of column names: signature → {role: column key},
# or {NOT_ORDERS: True} for "không phải file đơn hàng". Loaded and saved by the office layer; header names only.
REMEMBERED: dict = {}
NOT_ORDERS = "khong_phai_don_hang"


def header_signature(row) -> str:
    """The header row as folded column keys, blanks at either end left out: the same text m_code builds in M."""
    keys = [_key(c) for c in row]
    while keys and not keys[-1]:
        keys.pop()
    while keys and not keys[0]:
        keys.pop(0)
    return "|".join(keys)


def columns_of(row) -> tuple:
    """(roles, remembered): find_columns, and when it lacks a date or an amount, the columns a person named for
    this exact header before (REMEMBERED)."""
    cols = find_columns(row)
    if "date" in cols and "amount" in cols:
        return cols, False
    told = REMEMBERED.get(header_signature(row)) or {}
    if not told or told.get(NOT_ORDERS):
        return cols, False
    keys = [_key(c) for c in row]
    mixed = dict(cols)
    for role, key in told.items():
        if key in keys:
            mixed[role] = keys.index(key)
    return (mixed, True) if "date" in mixed and "amount" in mixed else (cols, False)


def declined(row) -> bool:
    """A person said a file with this header is not an order file."""
    return bool((REMEMBERED.get(header_signature(row)) or {}).get(NOT_ORDERS))


def is_cancelled(status) -> bool:
    return re.sub(r"\s+", " ", fold(str(status or ""))).strip(" .") in CANCELLED


def date_order(values: list) -> Optional[str]:
    """'vi-VN' when dates are day/month, 'en-US' when month/day, None when the values cannot tell.

    A first number above 12 can only be a day; a second number above 12 can only
    be a day. ISO dates (2026-10-13) are unambiguous and say nothing about order.
    """
    firsts, seconds = [], []
    for v in values:
        m = _SLASHED.match(str(v or ""))
        if m:
            firsts.append(int(m.group(1)))
            seconds.append(int(m.group(2)))
    if not firsts:
        return None
    day_first = max(firsts) > 12
    month_first = max(seconds) > 12
    if day_first and not month_first:
        return DAY_FIRST
    if month_first and not day_first:
        return MONTH_FIRST
    return None


_VI_NUMBER = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$|^-?\d+,\d{1,2}$")     # 1.250.000 / 1.250,5 / 12,5
_US_NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$|^-?\d+\.\d{1,2}$")    # 1,250,000 / 1,250.5 / 12.5


def number_style(values: list) -> Optional[str]:
    """'vi-VN' when amounts are written 1.250.000, 'en-US' when 1,250,000, None when the values cannot tell."""
    vi = us = 0
    for v in values:
        text = str(v or "").strip().replace(" ", "")
        vi += bool(_VI_NUMBER.match(text))
        us += bool(_US_NUMBER.match(text))
    if vi and not us:
        return DAY_FIRST
    if us and not vi:
        return MONTH_FIRST
    return None


def parse_date(value, order: Optional[str]) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "")
    m = _ISO.match(text)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _SLASHED.match(text)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            day, month = (b, a) if order == MONTH_FIRST else (a, b)
            return date(y, month, day)
    except ValueError:
        return None
    return None


@dataclass
class OrderFile:
    path: Path
    header: list
    rows: list                     # raw rows after the header
    columns: dict
    date_order: Optional[str]
    footers: list = field(default_factory=list)   # rows with no date, e.g. a "Tổng cộng" line: not orders
    delimiter: str = ","           # the file's own column separator; the repaired query must read it the same way
    kind: str = "csv"              # what the bytes are: csv, xls (Excel 97-2003), xlsx, html, xml, text
    preamble: list = field(default_factory=list)  # rows above the header: title, company, period (print layout)
    cancelled: list = field(default_factory=list) # rows whose Status says the order was cancelled: not sales
    sheet: str = ""                # the sheet or table the orders were read from, for a workbook-like file
    remembered: bool = False       # its date or amount column is the one a person named (REMEMBERED), not a name match

    @property
    def number_style(self) -> Optional[str]:
        i = self.columns.get("amount")
        return number_style([r[i] for r in self.rows if i is not None and i < len(r)])

    def culture(self, fallback: Optional[str]) -> str:
        """The one culture the query should read this file with: its date order, else how its amounts are written."""
        return self.date_order or self.number_style or fallback or DAY_FIRST

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class DocInfo:
    file: str
    date: Optional[date]
    customer: str
    amount: Decimal
    raw_date: str


@dataclass
class SourceTruth:
    files: list
    rows: int = 0                  # rows across all files
    unique_rows: int = 0           # after removing repeated document numbers
    amount: Decimal = ZERO         # net: sales minus return slips
    cost: Decimal = ZERO
    gross: Decimal = ZERO          # positive rows: should equal Có 511
    returns: Decimal = ZERO        # negative rows as a positive amount: should equal Nợ 521
    duplicates: list = field(default_factory=list)       # (doc number, file, amount)
    by_file: dict = field(default_factory=dict)          # file name → (rows, amount)
    unreadable_dates: int = 0
    doc_numbers: dict = field(default_factory=dict)      # doc number → DocInfo
    multi: dict = field(default_factory=dict)            # doc number → how many different rows carry it
    collisions: dict = field(default_factory=dict)       # ...of which: different places reusing the number
    invoice_lines: dict = field(default_factory=dict)    # ...of which: lines of one invoice (same date, customer, place)
    doc_amounts: dict = field(default_factory=dict)      # doc number → total of every distinct order using it
    period: Optional[tuple] = None                       # (year, month) the files were measured for
    outside: list = field(default_factory=list)          # (doc, file, date, amount) dated outside that period


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1258", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def sniff_delimiter(text: str) -> str:
    """The column separator, judged on the header line: data rows may hold decimal commas ("1234,5")
    in a file separated by semicolons, so counting over the whole sample can pick the wrong one."""
    header = next((line for line in text.splitlines() if line.strip()), "")
    counts = {d: header.count(d) for d in (",", ";", "\t")}
    if max(counts.values()):
        return max(counts, key=counts.get)
    sample = text[:4096]
    return max((",", ";", "\t"), key=sample.count)


def _parse(text: str, delim: str) -> list:
    return [row for row in csv.reader(text.splitlines(), delimiter=delim) if any(c.strip() for c in row)]


def read_csv(path: Path) -> list:
    text = _read_text(path)
    return _parse(text, sniff_delimiter(text))


ORDER_SUFFIXES = (".csv", ".xls")


def _any_date(value) -> bool:
    return parse_date(value, DAY_FIRST) is not None or parse_date(value, MONTH_FIRST) is not None


def load_order_file(path: Path) -> Optional[OrderFile]:
    """An order export: a table whose header has at least a date and an amount column.

    A .csv is read as text, as always. A .xls is read by what its bytes are (engine.tabular): a real Excel
    97-2003 file, an .xlsx under that name, an HTML or SpreadsheetML export, or delimited text. In every format
    the header is the first of the top rows that names a date and an amount, so a print-layout export with a
    title, the company and the period above the table reads the same as a plain one."""
    suffix = path.suffix.lower()
    if suffix not in ORDER_SUFFIXES or path.name.startswith("~$"):
        return None
    delim, kind, sheet = ",", "csv", ""
    if suffix == ".csv":
        try:
            text = _read_text(path)
        except OSError:
            return None
        delim = sniff_delimiter(text)
        candidates = [("", _parse(text, delim))]
    else:
        from engine import tabular
        try:
            candidates = tabular.sheets(path)
        except tabular.Unreadable:
            return None
        kind = tabular.kind_of(path)
    for sheet, rows in candidates:
        at = find_header(rows)
        if at is not None and len(rows) > at + 1:
            break
    else:
        return None
    header, body, preamble = rows[at], rows[at + 1:], rows[:at]
    cols, told = columns_of(header)
    data, footers = [], []
    for r in body:
        # A row without a date is not an order (exports often end with a "Tổng cộng" line whose
        # amount is the whole file); counting it would double the file in the yardstick. A date cell with
        # no digit at all ("Tổng", "Cộng", "Total") is that footer too; the query drops the same rows.
        blank = cols["date"] >= len(r) or not any(ch.isdigit() for ch in str(r[cols["date"]] or ""))
        (footers if blank else data).append(r)
    if kind != "csv" or at:
        # A print-layout export ends with its totals and a signature block ("Ngày 31 tháng 10 năm 2026",
        # "Người lập biểu"): after the last row with a real date, nothing is an order.
        tail = []
        while data and not _any_date(data[-1][cols["date"]] if cols["date"] < len(data[-1]) else ""):
            tail.insert(0, data.pop())
        gone = {id(r) for r in tail} | {id(r) for r in footers}
        footers = [r for r in body if id(r) in gone]
    cancelled = []
    if "status" in cols:
        s = cols["status"]
        cancelled = [r for r in data if s < len(r) and is_cancelled(r[s])]
        data = [r for r in data if not (s < len(r) and is_cancelled(r[s]))]
    order = date_order([r[cols["date"]] for r in data if cols["date"] < len(r)])
    return OrderFile(path, header, data, cols, order, footers, delim, kind, preamble, cancelled, sheet, told)


def find_order_files(folder: Path) -> list:
    """Order exports in the folder and one level down, sorted by name like Folder.Files."""
    paths = sorted([p for suffix in ORDER_SUFFIXES for p in list(folder.glob(f"*{suffix}")) + list(folder.glob(f"*/*{suffix}"))],
                   key=lambda p: p.name.lower())
    return [f for f in (load_order_file(p) for p in paths) if f]


def truth(files: list, period: Optional[tuple] = None) -> SourceTruth:
    """The yardstick. With period (year, month), the ledger's month, rows dated in another month
    are set aside in t.outside instead of being counted: exports often run wider than the period."""
    t = SourceTruth(files=files, period=period)
    seen, seen_file = {}, {}
    for f in files:
        if period and f.date_order is None and "date" in f.columns:
            # Every date in this file has day and month ≤ 12, so the values cannot tell the order;
            # the ledger's month can. Set here so the query reads the file the same way.
            def inside(order):
                return sum(1 for r in f.rows if f.columns["date"] < len(r)
                           and (d := parse_date(r[f.columns["date"]], order)) and (d.year, d.month) == tuple(period))
            day, month = inside(DAY_FIRST), inside(MONTH_FIRST)
            if day != month:
                f.date_order = DAY_FIRST if day > month else MONTH_FIRST
        c = f.columns
        n, amt = 0, ZERO
        for row in f.rows:
            def cell(role):
                i = c.get(role)
                return row[i] if i is not None and i < len(row) else None
            amount, _ = parse_amount(cell("amount"))
            cost, _ = parse_amount(cell("cost")) if "cost" in c else (ZERO, False)
            d = parse_date(cell("date"), f.date_order)
            if d is None:
                t.unreadable_dates += 1
            t.rows += 1
            if period and d is not None and (d.year, d.month) != tuple(period):
                t.outside.append((str(cell("doc") or "").strip(), f.name, d, amount))
                continue
            doc = str(cell("doc") or "").strip() if "doc" in c else ""
            # Same number and same content is the same order exported twice. Same number with
            # different content is two real orders: branches and stores often number on their own.
            # Same number, date, customer and place but other figures: one invoice with several item lines.
            content = (d, str(cell("customer") or "").strip(), amount, cost, str(cell("branch") or "").strip(),
                       str(cell("item") or "").strip())
            if doc and doc in seen:
                if content in seen[doc]:
                    t.duplicates.append((doc, f.name, amount))
                    continue
                first, first_file = seen[doc][0], seen_file[doc]
                seen[doc].append(content)
                t.multi[doc] = len(seen[doc])
                same_invoice = (first[0], first[1], first[4]) == (content[0], content[1], content[4]) and (
                    first[4] or first_file == f.name)
                (t.invoice_lines if same_invoice and doc not in t.collisions else t.collisions)[doc] = len(seen[doc])
                if doc in t.collisions:
                    t.invoice_lines.pop(doc, None)
                t.doc_amounts[doc] += amount
            elif doc:
                seen[doc] = [content]
                seen_file[doc] = f.name
                t.doc_amounts[doc] = amount
                t.doc_numbers[doc] = DocInfo(f.name, d, str(cell("customer") or "").strip(), amount, str(cell("date") or ""))
            t.unique_rows += 1
            t.amount += amount
            if amount < 0:
                t.returns -= amount
            else:
                t.gross += amount
            t.cost += cost
            n += 1
            amt += amount
        t.by_file[f.name] = (n, amt)
    return t


def find_trial_balances(folder: Path, read_rows) -> list:
    """[(file stem, rows, trial balance)] for xlsx files that read as a trial balance, oldest period first.

    read_rows(path) returns the first sheet's rows (engine.cli.read_rows), passed in
    so this module does not import openpyxl.
    """
    found = []
    for p in sorted(list(folder.glob("*.xlsx")) + list(folder.glob("*.xls"))):
        if p.name.startswith("~$"):
            continue
        if roles.is_plan(p.name):
            continue  # an AOP/AOPR laid out like a trial balance is a plan, never "Số chính thức"
        try:
            rows = read_rows(p) if p.suffix.lower() == ".xlsx" else _first_sheet(p)
            tb = read_trial_balance(rows, p.stem[:31])
        except Exception:
            continue
        if tb.lines:
            found.append((p.stem[:31], rows, tb, _period(rows) or (0, 0)))
    found.sort(key=lambda x: (x[3], x[0]))
    return [(name, rows, tb) for name, rows, tb, _ in found]


def _first_sheet(path: Path) -> list:
    """A legacy .xls trial balance (any of the formats engine.tabular reads): its first sheet, blank rows kept so
    row numbers on the receipt are the file's own."""
    from engine import tabular
    found = tabular.sheets(path, keep_blank=True)
    return found[0][1] if found else []


def period_of(rows) -> Optional[tuple]:
    """(year, month) a trial balance covers, from its title rows."""
    return _period(rows)


def _period(rows) -> Optional[tuple]:
    """(year, month) from a title such as 'Tháng 10 năm 2026' or 'Từ ngày 01/10/2026'."""
    for row in rows[:8]:
        for c in row:
            s = fold(c)
            m = re.search(r"thang (\d{1,2}) nam (\d{4})", s)
            if m:
                return int(m.group(2)), int(m.group(1))
            m = re.search(r"\d{1,2}/(\d{1,2})/(\d{4})", s)
            if m:
                return int(m.group(2)), int(m.group(1))
    return None


def ledger_movement(tb, prefix: str, side: str) -> Decimal:
    """Sum of one side's period movement over leaf accounts starting with prefix."""
    return sum((getattr(l, side) for l in tb.leaves if l.account.startswith(prefix)), ZERO)


def revenue_account(tb) -> str:
    """5111 (goods) when the ledger splits 511, else 511."""
    return "5111" if any(l.account.startswith("5111") for l in tb.leaves) else "511"


def revenue_scope(tb, source_gross: Decimal, tol: Decimal = Decimal(1)) -> str:
    """The 511 scope the order files cover: 5111 alone, or every 511 sub-account.

    A sales export may hold goods only (5111) or goods and finished products together
    (5111 + 5112 ...). Picking 5111 by default reports a false gap equal to the other
    sub-accounts. Take the scope whose credit movement matches the files; when neither
    matches, take the closer one; the gap is still reported, never hidden.
    """
    default = revenue_account(tb)
    scopes = list(dict.fromkeys((default, "511")))
    # Exact match first; otherwise the closest scope, so a real gap in a goods+products file is not
    # inflated by the whole of 5112. The receipt names the scope used.
    return min(scopes, key=lambda a: (abs(ledger_movement(tb, a, "cr") - source_gross) > tol,
                                      abs(ledger_movement(tb, a, "cr") - source_gross)))


def returns_in_revenue(tb) -> Decimal:
    """Amount by which income closed to 911 falls short of income from 511/521/515/711.

    That gap is almost always returns or discounts booked straight to Nợ 511, which
    leaves gross revenue and profit overstated by the same amount (engine.checks rule 7).
    """
    closing = [l for l in tb.leaves if l.account.startswith("911")]
    if not closing:
        return ZERO
    income = (ledger_movement(tb, "511", "cr") - ledger_movement(tb, "521", "dr")
              + ledger_movement(tb, "515", "cr") + ledger_movement(tb, "711", "cr"))
    gap = income - sum((l.cr for l in closing), ZERO)
    return gap if gap > 0 else ZERO


def query_rows(files: list, roles: list, period: Optional[tuple] = None, dedup: Optional[list] = None) -> list:
    """The rows m_code.order_files_query must load for these files, from the receipt's own reading: one list per
    order in the roles' order (engine.m_code.CANON) plus the file name. A blank amount or cost is None, as the
    query leaves it. The yardstick tools/dien_tap/m_agree.py compares Excel's result with, row for row."""
    order = [r for r in ("doc", "date", "amount", "cost", "branch", "customer", "item", "status") if r in roles]
    out = []
    for f in sorted(files, key=lambda f: f.name.lower()):
        for row in f.rows:
            def cell(role):
                i = f.columns.get(role)
                return row[i] if i is not None and i < len(row) else None
            values = []
            for r in order:
                v = cell(r)
                if r == "date":
                    values.append(parse_date(v, f.date_order))
                elif r in ("amount", "cost"):
                    values.append(None if v is None or str(v).strip() == "" else parse_amount(v)[0])
                else:
                    values.append("" if v is None else str(v).strip())
            out.append(values + [f.name])
    if period and "date" in order:
        d = order.index("date")
        out = [r for r in out if r[d] is None or (r[d].year, r[d].month) == tuple(period)]
    if "doc" in order:
        i = order.index("doc")
        keys = [j for j, r in enumerate(order) if r != "status"] if dedup == [] else \
            [order.index(r) for r in dedup] if dedup else [i]
        seen, kept = set(), []
        for r in out:
            if not r[i]:
                continue
            k = tuple(r[j] for j in keys)
            if k not in seen:
                seen.add(k)
                kept.append(r)
        out = kept + [r for r in out if not r[i]]
    return out


def file_encoding(path: Path) -> int:
    """The code page the receipt read a text file with, so the query decodes it the same way."""
    raw = path.read_bytes()[:1 << 20]
    if path.suffix.lower() != ".csv" and raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return 1200 if raw.startswith(b"\xff\xfe") else 1201
    for enc, code in (("utf-8", 65001), ("cp1258", 1258)):
        try:
            raw.decode(enc)
            return code
        except UnicodeDecodeError as exc:
            if enc == "utf-8" and exc.start >= len(raw) - 4 and len(raw) == 1 << 20:
                return code                       # a character cut by the 1 MB sample, not a wrong encoding
    return 28591


# ── A table that looks like orders under names Clicky does not know ─────────────────────────────────────────────

@dataclass
class Unexplained:
    """An order-like file whose header names no date or amount Clicky knows: a column of dates and a column of
    amounts by content. Clicky asks which is which (at most 3 likeliest of each) instead of leaving it out."""
    path: Path
    header: list
    dates: list                    # column names, likeliest first
    amounts: list

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def signature(self) -> str:
        return header_signature(self.header)


def _is_amount(value) -> bool:
    text = str(value or "").strip()
    if not any(ch.isdigit() for ch in text) or _any_date(text):
        return False
    try:
        parse_amount(text)
        return True
    except ValueError:
        return False


def guess_columns(rows: list) -> Optional[tuple]:
    """(header row, date column indexes, amount column indexes) judged by the values, best first, at most 3 each.

    The header is the first of the top rows whose next row already holds a date and an amount under named
    columns; a column counts when at least 4 in 5 of its cells below that hold a digit read as a date (or an
    amount).
    Amounts rank by how often they read, then by size, so a line total comes before a quantity."""
    for i, row in enumerate(rows[:HEADER_SCAN]):
        body = rows[i + 1:i + 201]
        if not body:
            break
        width = max(len(r) for r in [row] + body)
        dates, amounts = [], []
        for j in range(width):
            if j >= len(row) or not str(row[j] or "").strip() or _any_date(row[j]) or _is_amount(row[j]):
                continue                           # a column needs a name that is not itself a figure
            first = body[0][j] if j < len(body[0]) else ""
            # Cells without a digit ("Cộng", "Người lập biểu") are footers, as the order reader treats them.
            values = [r[j] for r in body if j < len(r) and any(ch.isdigit() for ch in str(r[j] or ""))]
            if not values:
                continue
            if _any_date(first):
                share = sum(1 for v in values if _any_date(v)) / len(values)
                if share >= 0.8:
                    dates.append((-share, j))
            elif _is_amount(first):
                read = [v for v in values if _is_amount(v)]
                share = len(read) / len(values)
                if share >= 0.8:
                    size = sum(abs(parse_amount(v)[0]) for v in read) / len(read)
                    amounts.append((-share, -size, j))
        if dates and amounts:
            return i, [j for *_, j in sorted(dates)][:3], [j for *_, j in sorted(amounts)][:3]
    return None


def _candidate_tables(path: Path) -> list:
    """Every table in a .csv or .xls, as the order reader would see it."""
    if path.suffix.lower() == ".csv":
        try:
            text = _read_text(path)
        except OSError:
            return []
        return [_parse(text, sniff_delimiter(text))]
    from engine import tabular
    try:
        return [rows for _, rows in tabular.sheets(path)]
    except tabular.Unreadable:
        return []


def unexplained_order_files(folder: Path, used: set) -> list:
    """Order-like .csv/.xls files in the folder (and one level down) that are not read as orders, have not been
    declined, and have a column of dates and a column of amounts under names Clicky does not know."""
    out = []
    paths = sorted([p for suffix in ORDER_SUFFIXES for p in list(folder.glob(f"*{suffix}")) + list(folder.glob(f"*/*{suffix}"))],
                   key=lambda p: p.name.lower())
    for p in paths:
        if p.name in used or p.name.startswith("~$"):
            continue
        for rows in _candidate_tables(p):
            if find_header(rows) is not None:
                break                              # read as orders after all (one sheet among several)
            guess = guess_columns(rows)
            if guess is None:
                continue
            at, dates, amounts = guess
            header = rows[at]
            if declined(header):
                break
            name = lambda j: str(header[j]).strip()
            out.append(Unexplained(p, header, [name(j) for j in dates], [name(j) for j in amounts]))
            break
    return out


def declined_file(path: Path) -> bool:
    """A person said this file (its header) is not an order file."""
    for rows in _candidate_tables(path):
        guess = guess_columns(rows)
        if guess is not None and declined(rows[guess[0]]):
            return True
    return False
