"""Read a BOD draft pack made by another tool: every sheet, its table, and what each column means.

No layout is assumed. The header row is the row (under any title rows) whose cells name the most column
roles; a group header above it ('Tháng 10', 'Lũy kế', 'So với AOPR') is read into each column under it.
Each column gets one role from its header:

  value   an amount (actual, a plan version, last year)
  pct     % of net revenue
  diff    gap in money
  pp      gap in percentage points
  month   one month of a monthly table (T7 .. T6, 07/2026, ...)
  cut / fx / text  columns of a cut table, an FX table, notes

Value columns carry the scenario they hold (version: 'thuc te', 'aop', 'aopr1', 'muf'), whether they are
last year, the fiscal year when printed, and whether they are year-to-date. Cells keep their address so
every finding points at the cell the reader will look at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine.tables import HEADER_SCAN, header_texts, is_total, key, sheets_of

PCT_RE = re.compile(r"%|\bty le\b|\btren dtt\b|\bty trong\b")
PCT_DTT_RE = re.compile(r"% ?dtt|% ?doanh thu|ty le .*(dtt|doanh thu)|tren dtt|% ?net|% ?ns\b|% ?dt\b")
PP_RE = re.compile(r"\bdiem\b|\bpp\b|\bd %|percentage point|\bbps?\b")
DIFF_RE = re.compile(r"chenh lech|\bchenh\b|bien dong|variance|\bvar\b|tang giam|\+ ?/ ?-|\bdelta\b")
YTD_RE = re.compile(r"\bluy ke\b|\bly ke\b|\bytd\b|tu dau nam|\bluy\b")
LABEL_RE = re.compile(r"^(chi tieu|khoan muc|hang muc|noi dung|dien giai|kenh|kenh ban|phong ban|bo phan|dong|ten|"
                      r"chi phi|nhom|cost element|ma|stt|muc)( .*)?$")
VALUE_WORDS = re.compile(r"\b(thuc te|actual|act|aop|aopr ?\d*|muf|nam truoc|cung ky|so tien|gia tri|ngan sach|"
                         r"de xuat|ky nay|budget|ke hoach|tt|ly|du bao|forecast|ca nam|thang \d+|t\d{1,2})\b")
CUT_WORDS = re.compile(r"buoc ?[23]|cat phan|cat deu|cat giam|so cat|duoc cat|sau cat|xin tang|de xuat|tran\b")
FX_WORDS = re.compile(r"anh huong ty gia|chenh lech ty gia|\bfx\b|ty gia|\busd\b|nguyen te")
MONTH_RE = [re.compile(r"(?:^| )(?:t|th|thang) ?(\d{1,2})$"), re.compile(r"(?:^| )(\d{1,2}) (20\d\d)$"),
            re.compile(r"(?:^| )(20\d\d) (\d{1,2})$")]
FY_RE = [re.compile(r"\bfy ?(\d\d) ?(\d\d)\b"), re.compile(r"\bnd ?(\d\d) ?(\d\d)\b"),
         re.compile(r"\b(20\d\d) (?:20)?(\d\d)\b")]


def version_of(k: str) -> str:
    """The scenario a header names, folded: 'aopr1', 'aopr', 'aop', 'muf', 'thuc te', or ''."""
    m = re.search(r"\baopr ?(\d+)\b|\baopr\b|\brevised\b|\bdieu chinh\b", k)
    if m:
        return f"aopr{m.group(1)}" if m.lastindex and m.group(1) else "aopr"
    if re.search(r"\baop\b|\bbudget\b|\bngan sach goc\b|\bke hoach goc\b|\bkh goc\b|\boriginal\b", k):
        return "aop"
    if re.search(r"\bmuf\b|\bdu bao\b|\bforecast\b|\bfc\b", k):
        return "muf"
    if re.search(r"\bthuc te\b|\bactual\b|\bact\b|\btt\b|\bthuc hien\b|\bth\b", k):
        return "thuc te"
    return ""


def fy_of(k: str) -> Optional[int]:
    """First year of a fiscal year printed in a header ('FY25/26', 'ND2526', '2025/26')."""
    for rx in FY_RE:
        m = rx.search(k)
        if m:
            a, b = m.group(1), m.group(2)
            first = int(a) if len(a) == 4 else 2000 + int(a)
            if (first + 1) % 100 == int(b) % 100:
                return first
    return None


EN_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
EN_MONTH_RE = re.compile(r"(?:^| )(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?: (?:20)?\d\d)?$")


def month_of(k: str) -> Optional[int]:
    m = EN_MONTH_RE.search(k)
    if m:
        return EN_MONTHS[m.group(1)]
    for rx in MONTH_RE:
        m = rx.search(k)
        if m:
            nums = [int(g) for g in m.groups()]
            month = nums[0] if nums[0] <= 12 else nums[-1]
            if 1 <= month <= 12:
                return month
    return None


def is_previous(k: str) -> bool:
    return bool(re.search(r"\bnam truoc\b|\bcung ky\b|\bly\b|\blast year\b|\bpy\b", k))


@dataclass
class Col:
    index: int
    text: str                      # header as printed (with the group header)
    k: str                         # folded
    role: str                      # value | pct | diff | pp | month | text | label
    version: str = ""
    previous: bool = False
    fy: Optional[int] = None
    ytd: bool = False
    month: Optional[int] = None


@dataclass
class Row:
    n: int                          # 1-based sheet row
    label: str
    section: str
    values: list
    total: bool = False


@dataclass
class Group:
    """One scenario's value column with its % of net revenue, and for a comparison its gaps."""
    value: Col
    pct: Optional[Col] = None
    diff: Optional[Col] = None
    pp: Optional[Col] = None


@dataclass
class Sheet:
    name: str
    title: str                      # text of the rows above the header
    header_row: int                 # 1-based; 0 when the sheet has no table
    cols: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    kind: str = ""                  # lines | months | cut | fx | other
    loose: list = field(default_factory=list)   # [(address, label, number)] labelled numbers outside the table
    grid: list = field(default_factory=list)

    def col(self, role: str) -> list:
        return [c for c in self.cols if c.role == role]

    def addr(self, row: int, col: int) -> str:
        from openpyxl.utils import get_column_letter
        name = self.name if re.fullmatch(r"[A-Za-z0-9_]+", self.name) else "'" + self.name.replace("'", "''") + "'"
        return f"{name}!{get_column_letter(col + 1)}{row}"

    def num(self, row: Row, col: Optional[Col]) -> Optional[Decimal]:
        if col is None or col.index >= len(row.values):
            return None
        return to_num(row.values[col.index])

    def sections(self) -> list:
        """Value columns split into month and year-to-date parts; each part is [Group], first = current."""
        parts, cur, flag = [], [], None
        for c in self.cols:
            if c.role not in ("value", "pct", "diff", "pp"):
                continue
            if flag is not None and c.ytd != flag and c.role == "value":
                parts.append(cur)
                cur = []
            flag = c.ytd if c.role == "value" or flag is None else flag
            cur.append(c)
        if cur:
            parts.append(cur)
        return [_groups(p) for p in parts if any(c.role == "value" for c in p)]


def _groups(cols: list) -> list:
    groups = []
    for c in cols:
        if c.role == "value":
            groups.append(Group(c))
            continue
        if not groups:
            continue
        slot = "pct" if c.role == "pct" else c.role
        pool = groups if slot == "pct" else groups[1:] or groups
        named = [g for g in pool if c.version and g.value.version == c.version and getattr(g, slot) is None]
        free = [g for g in reversed(pool) if getattr(g, slot) is None]
        target = (named or free or [None])[0]
        if target is not None:
            setattr(target, slot, c)
    return groups


def to_num(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip()
    if not s or not re.search(r"\d", s):
        return None
    from engine.trial_balance import parse_amount
    pct = s.endswith("%")
    try:
        x = parse_amount(s.rstrip("%").strip())[0]
    except ValueError:
        return None
    return x / 100 if pct else x


def _role(k: str, numeric: bool) -> str:
    if PP_RE.search(k) and (DIFF_RE.search(k) or PCT_RE.search(k) or "diem" in k):
        return "pp"
    if PCT_DTT_RE.search(k) and DIFF_RE.search(k):
        return "pp"                         # 'Chênh lệch %DTT': a gap between two % of revenue is in points
    if PCT_RE.search(k) and DIFF_RE.search(k):
        return "text"                       # a relative gap (%), not points: not checked
    if PCT_RE.search(k):
        return "pct" if PCT_DTT_RE.search(k) or not DIFF_RE.search(k) else "text"
    if DIFF_RE.search(k):
        return "diff"
    if month_of(k) is not None and not YTD_RE.search(k) and numeric:
        return "month"
    return "value" if numeric else "text"


def _score(values: list) -> int:
    s = 0
    for v in values:
        if v is None or isinstance(v, (int, float)):
            continue
        k = key(v)
        if not k:
            continue
        if PCT_RE.search(k) or DIFF_RE.search(k) or VALUE_WORDS.search(k) or CUT_WORDS.search(k) or FX_WORDS.search(k) \
                or month_of(k) is not None or LABEL_RE.match(k):
            s += 1
    return s


def _order_column(rows: list, r: int, c: int, k: str) -> bool:
    """'STT' / 'TT' / 'No.': row numbers, not amounts."""
    if re.fullmatch(r"stt|tt|so tt|no|#|thu tu", k):
        return True
    nums = [to_num((x or [None] * (c + 1))[c]) if c < len(x or []) else None for x in rows[r + 1:r + 60]]
    nums = [n for n in nums if n is not None]
    return c == 0 and len(nums) >= 2 and all(n == int(n) and 0 < n <= 300 for n in nums) and \
        sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1) >= len(nums) // 2


def _numeric_below(rows: list, r: int, c: int) -> bool:
    for x in rows[r + 1:r + 60]:
        v = (x or [None] * (c + 1))[c] if c < len(x or []) else None
        if to_num(v) is not None and not (isinstance(v, str) and not re.search(r"\d", v)):
            return True
    return False


def _dated_headers(rows: list) -> list:
    """Month headers typed as real dates (Excel shows 'Jul-26') read as '07/2026'; body cells stay as they are."""
    import datetime as dt
    out = list(rows)
    for r, values in enumerate(rows[:HEADER_SCAN]):
        dates = [v for v in values or [] if isinstance(v, (dt.date, dt.datetime))]
        if len(dates) >= 2:
            out[r] = [f"{v.month:02d}/{v.year}" if isinstance(v, (dt.date, dt.datetime)) else v for v in values]
    return out


def read_sheet(name: str, rows: list) -> Sheet:
    rows = _dated_headers(rows)
    best, best_r = 0, -1
    for r, values in enumerate(rows[:HEADER_SCAN]):
        s = _score(values or [])
        texts = sum(1 for v in values or [] if isinstance(v, str) and v.strip())
        if s >= 2 and texts >= 2 and s > best and any(_numeric_below(rows, r, c) for c in range(len(values or []))):
            best, best_r = s, r
    sheet = Sheet(name, "", 0, grid=rows)
    sheet.title = " ".join(str(v) for x in rows[:max(best_r, 0) or 6] for v in (x or []) if isinstance(v, str) and v.strip())
    if best_r < 0:
        sheet.kind = "other"
        sheet.loose = _loose(sheet, rows, 0)
        return sheet
    sheet.header_row = best_r + 1
    texts = header_texts(rows, best_r)
    label_col = None
    for c, t in enumerate(texts):
        k = key(t)
        numeric = _numeric_below(rows, best_r, c) and not _order_column(rows, best_r, c, k)
        if label_col is None and (LABEL_RE.match(k) or not numeric) and k and not PCT_RE.search(k):
            role = "label"
            label_col = c
        elif not k and not numeric:
            continue
        else:
            role = _role(k, numeric)
        sheet.cols.append(Col(c, t, k, role, version_of(k), is_previous(k), fy_of(k), bool(YTD_RE.search(k)),
                              month_of(k) if role == "month" else None))
    if label_col is None:
        label_col = 0
    text_cols = [c.index for c in sheet.cols if c.role in ("label",)] + \
                [c.index for c in sheet.cols if c.role == "text" and c.index < min((x.index for x in sheet.cols
                                                                                   if x.role in ("value", "month")), default=0)]
    section = ""
    for i in range(best_r + 1, len(rows)):
        values = list(rows[i] or [])
        if not any(v is not None and str(v).strip() for v in values):
            continue
        label = " ".join(str(values[c]).strip() for c in sorted(text_cols) if c < len(values) and values[c] is not None
                         and str(values[c]).strip() and not re.fullmatch(r"[\d.,\s]+|[IVXivx]+\.?", str(values[c]).strip()))
        numbers = [c for c in sheet.cols if c.role in ("value", "pct", "diff", "pp", "month")
                   and c.index < len(values) and to_num(values[c.index]) is not None]
        if not numbers:
            if label:
                section = label
            continue
        sheet.rows.append(Row(i + 1, label, section, values, is_total([label]) if label else False))
    sheet.kind = _kind(sheet)
    sheet.loose = _loose(sheet, rows, best_r + 1 + len(rows))
    return sheet


def _kind(s: Sheet) -> str:
    ks = " ".join(c.k for c in s.cols)
    if len(s.col("month")) >= 3:
        return "months"
    if re.search(r"buoc ?[23]|cat phan|cat deu|so cat|duoc cat|cat giam", ks):
        return "cut"
    if re.search(r"anh huong ty gia|chenh lech ty gia|\bfx\b", ks) or ("ty gia" in ks and re.search(r"\busd\b|nguyen te", ks)):
        return "fx"
    if s.col("value") and (s.col("pct") or s.col("diff") or s.col("pp")):
        return "lines"
    return "other"


def _loose(sheet: Sheet, rows: list, start: int) -> list:
    """Labelled numbers anywhere on the sheet: a text cell with a number to its right (or below)."""
    out = []
    for r, values in enumerate(rows):
        values = list(values or [])
        for c, v in enumerate(values):
            if not isinstance(v, str) or not v.strip() or to_num(v) is not None:
                continue
            for d in range(c + 1, min(c + 4, len(values))):
                x = to_num(values[d])
                if x is not None:
                    out.append((sheet.addr(r + 1, d), v.strip(), x))
                    break
                if isinstance(values[d], str) and values[d].strip():
                    break
    return out


@dataclass
class Pack:
    path: Path
    sheets: list
    title: str

    def of(self, kind: str) -> list:
        return [s for s in self.sheets if s.kind == kind]


def read_pack(path: Path) -> Pack:
    sheets = [read_sheet(n, rows) for n, rows in sheets_of(path).items()]
    return Pack(Path(path), sheets, " ".join([Path(path).stem] + [s.title for s in sheets]))


def pack_month(pack: Pack) -> Optional[int]:
    """The month named in the titles or the file name when no year is printed: 'T10', 'Tháng 10'."""
    m = re.search(r"\b(?:thang|t|th) ?(\d{1,2})\b", key(pack.title).replace("_", " "))
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 12 else None


def pack_period(pack: Pack, fy_start: int = 7) -> Optional[str]:
    """'YYYY-MM' of the pack from its titles and file name: 'Tháng 10/2026', 'T10/2026', 'T10' + a year."""
    k = key(pack.title)
    m = re.search(r"\b(?:thang|t|th|ky) ?(\d{1,2}) (?:nam )?(20\d\d)\b", k) or re.search(r"\b(\d{1,2}) (20\d\d)\b", k)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(20\d\d) (\d{1,2})\b", k)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.search(r"\b(?:thang|t|th) ?(\d{1,2})\b", k)
    fy = fy_of(k)
    if m and 1 <= int(m.group(1)) <= 12 and fy:
        month = int(m.group(1))
        return f"{fy if month >= fy_start else fy + 1:04d}-{month:02d}"
    return None
