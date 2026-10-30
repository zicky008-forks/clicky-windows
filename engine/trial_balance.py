"""Read a trial balance (Bảng cân đối tài khoản / cân đối phát sinh) from spreadsheet rows.

Works on plain rows (lists of cell values) so the same code serves Excel over COM,
an .xlsx file and tests. Amounts are Decimal: the engine never uses floats for money.
Exports from MISA, Fast, Bravo and SAP differ in titles and header layout, so the
header is found by keywords rather than fixed positions.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

ZERO = Decimal(0)
COLUMNS = ("open_dr", "open_cr", "dr", "cr", "close_dr", "close_cr")


def fold(text) -> str:
    """Lowercase without Vietnamese accents: 'Số dư đầu kỳ' → 'so du dau ky'."""
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().lower()


def parse_amount(value):
    """Return (Decimal, was_text). Accepts 1.234.567 / 1,234,567 / (1.000) / '-' / blanks."""
    if value is None or value == "":
        return ZERO, False
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)), False
    s = str(value).strip().replace(" ", "").replace(" ", "")
    if s in ("", "-", "–"):
        return ZERO, True
    negative = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = s.strip("()-")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):        # 1.234.567,5 (Vietnamese)
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):      # 1,234,567.5
        s = s.replace(",", "")
    try:
        amount = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {value!r}") from exc
    return (-amount if negative else amount), True


@dataclass
class Line:
    account: str
    name: str
    row: int                       # 1-based row in the source sheet
    open_dr: Decimal = ZERO
    open_cr: Decimal = ZERO
    dr: Decimal = ZERO
    cr: Decimal = ZERO
    close_dr: Decimal = ZERO
    close_cr: Decimal = ZERO
    leaf: bool = True


@dataclass
class TrialBalance:
    sheet: str
    lines: list
    header_row: int
    columns: dict                  # field → 1-based source column
    text_amounts: list = field(default_factory=list)   # (row, column field) stored as text
    period: Optional[str] = None

    @property
    def leaves(self):
        return [l for l in self.lines if l.leaf]


# Section headers that sit above the Nợ/Có row in two-row headers.
_SECTIONS = (("open", ("dau ky", "dau nam", "opening", "beginning")),
             ("move", ("phat sinh", "trong ky", "movement", "during", "period")),
             ("close", ("cuoi ky", "cuoi nam", "closing", "ending")))


def _side(text: str) -> Optional[str]:
    words = set(re.findall(r"[a-z]+", fold(text)))
    if words & {"no", "debit", "dr"}:
        return "dr"
    if words & {"co", "credit", "cr"}:
        return "cr"
    return None


def _section(text: str) -> Optional[str]:
    t = fold(text)
    for key, words in _SECTIONS:
        if any(w in t for w in words):
            return key
    return None


def _find_header(rows):
    for r, row in enumerate(rows[:40]):
        cells = [fold(c) for c in row]
        acct = next((i for i, c in enumerate(cells) if c in ("so hieu tk", "so hieu tai khoan", "ma tk", "ma tai khoan",
                                                              "tai khoan", "so tk", "account", "account no", "tk")), None)
        if acct is None:
            continue
        # Sections may be on this row with Nợ/Có below (MISA), or both on one row.
        below = rows[r + 1] if r + 1 < len(rows) else []
        columns, current = {}, None
        width = max(len(row), len(below))
        for i in range(width):
            top = row[i] if i < len(row) else ""
            sub = below[i] if i < len(below) else ""
            if fold(top):                 # merged section cells leave the next cells blank
                current = _section(top)
            side = _side(sub) or _side(top)
            sec = current
            if side and sec:
                key = {"open": "open_", "move": "", "close": "close_"}[sec] + side
                columns.setdefault(key, i)
        if len(columns) >= 4 and "dr" in columns and "cr" in columns:
            name = next((i for i, c in enumerate(cells) if "ten" in c or "name" in c), acct + 1)
            data_start = r + 2 if any(_side(c) for c in below) else r + 1
            return r, data_start, acct, name, columns
    raise ValueError("Không tìm thấy dòng tiêu đề có cột Tài khoản và Phát sinh Nợ/Có")


def read_trial_balance(rows, sheet: str = "TB") -> TrialBalance:
    rows = [list(r) for r in rows]
    header, start, acct_col, name_col, columns = _find_header(rows)
    lines, text_amounts = [], []
    for r in range(start, len(rows)):
        row = rows[r]
        raw = row[acct_col] if acct_col < len(row) else None
        account = str(raw).strip() if raw is not None else ""
        if isinstance(raw, float) and raw.is_integer():
            account = str(int(raw))
        if not re.fullmatch(r"\d{3,10}", account):
            continue                      # totals, blank and title rows
        line = Line(account=account, name=str(row[name_col] or "").strip() if name_col < len(row) else "", row=r + 1)
        for key, c in columns.items():
            amount, was_text = parse_amount(row[c] if c < len(row) else None)
            if was_text and amount:
                text_amounts.append((r + 1, key))
            setattr(line, key, amount)
        lines.append(line)
    codes = {l.account for l in lines}
    for l in lines:
        l.leaf = not any(other != l.account and other.startswith(l.account) for other in codes)
    return TrialBalance(sheet=sheet, lines=lines, header_row=header + 1,
                        columns={k: v + 1 for k, v in columns.items()}, text_amounts=text_amounts)
