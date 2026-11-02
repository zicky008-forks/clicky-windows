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

from engine.trial_balance import ZERO, fold, parse_amount, read_trial_balance

# Header words after fold() with spaces removed.
DOC_COLS = {"soct", "sochungtu", "sohd", "sohoadon", "sophieu", "invoice", "invoiceno", "docno", "documentno", "machungtu"}
DATE_COLS = {"ngay", "ngayct", "ngaychungtu", "ngayhd", "ngayhoadon", "date", "invoicedate", "postingdate"}
AMOUNT_COLS = {"thanhtien", "doanhthu", "tienhang", "amount", "netamount", "revenue", "doanhso"}
COST_COLS = {"giavon", "tiengiavon", "cogs", "cost", "costamount"}
BRANCH_COLS = {"chinhanh", "branch", "cuahang", "store"}
CUSTOMER_COLS = {"makh", "makhachhang", "customer", "customerid", "khachhang"}
ITEM_COLS = {"mahang", "mathang", "masp", "masanpham", "sanpham", "tenhang", "tenmathang", "nhomhang", "sku", "item", "product"}

DAY_FIRST, MONTH_FIRST = "vi-VN", "en-US"
_SLASHED = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})")


def _key(header) -> str:
    return re.sub(r"[\s_\-./]", "", fold(header))


def find_columns(header: list) -> dict:
    """Role → 0-based column index for the roles present in this header."""
    roles = {}
    for i, h in enumerate(header):
        k = _key(h)
        for role, names in (("doc", DOC_COLS), ("date", DATE_COLS), ("amount", AMOUNT_COLS),
                            ("cost", COST_COLS), ("branch", BRANCH_COLS), ("customer", CUSTOMER_COLS),
                            ("item", ITEM_COLS)):
            if k in names and role not in roles:
                roles[role] = i
    return roles


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


def read_csv(path: Path) -> list:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1258", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    delim = max((",", ";", "\t"), key=sample.count)
    return [row for row in csv.reader(text.splitlines(), delimiter=delim) if any(c.strip() for c in row)]


def load_order_file(path: Path) -> Optional[OrderFile]:
    """An order export: a CSV whose header has at least a date and an amount column."""
    if path.suffix.lower() != ".csv":
        return None
    try:
        rows = read_csv(path)
    except OSError:
        return None
    if len(rows) < 2:
        return None
    cols = find_columns(rows[0])
    if "amount" not in cols or "date" not in cols:
        return None
    data, footers = [], []
    for r in rows[1:]:
        # A row without a date is not an order (exports often end with a "Tổng cộng" line whose
        # amount is the whole file); counting it would double the file in the yardstick.
        blank = cols["date"] >= len(r) or not str(r[cols["date"]] or "").strip()
        (footers if blank else data).append(r)
    order = date_order([r[cols["date"]] for r in data if cols["date"] < len(r)])
    return OrderFile(path, rows[0], data, cols, order, footers)


def find_order_files(folder: Path) -> list:
    """Order exports in the folder and one level down, sorted by name like Folder.Files."""
    paths = sorted(list(folder.glob("*.csv")) + list(folder.glob("*/*.csv")), key=lambda p: p.name.lower())
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
    for p in sorted(folder.glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        try:
            rows = read_rows(p)
            tb = read_trial_balance(rows, p.stem[:31])
        except Exception:
            continue
        if tb.lines:
            found.append((p.stem[:31], rows, tb, _period(rows) or (0, 0)))
    found.sort(key=lambda x: (x[3], x[0]))
    return [(name, rows, tb) for name, rows, tb, _ in found]


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
