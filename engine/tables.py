"""Find a table in a sheet without knowing its layout: title rows above, a header row somewhere, Vietnamese
headers with or without diacritics or snake_case, a total row at the end. Columns are found by synonyms.

Every row that is read is either used or reported as skipped with its reason (never skipped in silence).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from engine.trial_balance import fold

HEADER_SCAN = 40


def key(text) -> str:
    """'Phòng_ban mã' -> 'phong ban ma'."""
    return re.sub(r"[\s_./()\-:]+", " ", fold(text)).strip()


def _split_camel(text: str) -> str:
    """'NgayHoaDon' -> 'Ngay Hoa Don'; 'Khối KD', 'B2B' unchanged."""
    out = []
    for i, ch in enumerate(text):
        if i and ch.isupper() and text[i - 1].islower():
            out.append(" ")
        out.append(ch)
    return "".join(out)


def matches(cell, synonyms) -> int:
    """Strength of a header cell against a field's synonyms: 3 exact, 2 all synonym words present, 0 none.
    Headers an accounting program exports run together ('NgayHoaDon', 'THANHTIEN'): CamelCase is split into
    words and a header equal to a synonym once spaces are removed is exact too."""
    text = _split_camel(str(cell)) if cell is not None else ""
    bare = re.sub(r"\(.*?\)", " ", text)       # 'Chỉ tiêu (khối Thương mại)': the words in brackets only qualify
    k = key(bare) or key(text)
    if not k:
        return 0
    words = set(k.split())
    compact = k.replace(" ", "")
    best = 0
    for s in synonyms:
        s = key(s)
        if k == s or (s and compact == s.replace(" ", "")):
            return 3
        if s and set(s.split()) <= words:
            best = max(best, 2)
    return best


@dataclass
class Table:
    sheet: str
    header_row: int                       # 1-based row number of the header in the sheet
    columns: dict                         # field -> 0-based column index
    headers: list                         # header texts as printed
    rows: list = field(default_factory=list)      # [(row number, values)] data rows
    skipped: list = field(default_factory=list)   # [(row number, reason)]
    totals: list = field(default_factory=list)    # [(row number, values)] total rows, kept to check against

    def get(self, values, name, default=None):
        i = self.columns.get(name)
        if i is None or i >= len(values):
            return default
        v = values[i]
        return default if v is None or (isinstance(v, str) and not v.strip()) else v

    def address(self, row: int, name: str) -> str:
        from openpyxl.utils import get_column_letter
        return f"{self.sheet}!{get_column_letter(self.columns[name] + 1)}{row}"


def find_header(rows: list, spec: dict, need: int = 2) -> Optional[tuple]:
    """spec: {field: [synonyms]}. The header is the row (within the first rows) matching most fields.
    Returns (0-based row index, {field: column}) or None when fewer than `need` fields match."""
    best = None
    for r, values in enumerate(rows[:HEADER_SCAN]):
        cols = {}
        score = 0
        for name, syns in spec.items():
            hits = [(matches(v, syns), c) for c, v in enumerate(values or [])]
            hits = [h for h in hits if h[0]]
            if hits:
                strength, col = max(hits, key=lambda h: (h[0], -h[1]))
                if col not in cols.values():
                    cols[name] = col
                    score += strength
        if len(cols) >= need and (best is None or score > best[2]):
            best = (r, cols, score)
    return (best[0], best[1]) if best else None


def header_texts(rows: list, r: int) -> list:
    """Header text per column with the group header above it. A merged group cell reads as one value then
    blanks, so its text is carried right until the next one: 'Tháng 10' over 'Thực tế' -> 'Tháng 10 Thực tế'.
    A row above with a single text in the first column is a title, not a group header."""
    width = max((len(x or []) for x in rows[max(0, r - 1):r + 1]), default=0)
    pad = lambda row: (list(row or []) + [None] * width)[:width]
    out = ["" if v is None else str(v).strip() for v in pad(rows[r])]
    if r == 0:
        return out
    above = pad(rows[r - 1])
    texts = ["" if v is None or isinstance(v, (int, float)) else str(v).strip() for v in above]
    filled = [c for c, t in enumerate(texts) if t]
    if not filled or filled == [0] or any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in above):
        return out
    carry = ""
    for c in range(width):
        carry = texts[c] or carry
        if carry:
            out[c] = f"{carry} {out[c]}".strip()
    return out


def is_total(values, label_cols=()) -> bool:
    texts = [key(v) for v in values if isinstance(v, str) and v.strip()]
    if not texts:
        return False
    first = texts[0]
    return bool(re.match(r"^(tong cong|tong|cong|total|grand total|sum)\b", first))


def read_table(rows: list, sheet: str, spec: dict, need: int = 2, required=()) -> Optional[Table]:
    found = find_header(rows, spec, need)
    if not found:
        return None
    r, cols = found
    if any(f not in cols for f in required):
        return None
    t = Table(sheet, r + 1, cols, [str(v or "") for v in rows[r]])
    for i in range(r + 1, len(rows)):
        values = list(rows[i] or [])
        n = i + 1
        if not any(v is not None and str(v).strip() for v in values):
            continue
        if is_total(values):
            t.skipped.append((n, "dòng tổng"))
            t.totals.append((n, values))
            continue
        t.rows.append((n, values))
    return t


def sheets_of(path: Path) -> dict:
    """{sheet name: rows (lists of cell values)}; a CSV is one sheet named after the file. Cached values only."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        from engine.sources import read_csv
        return {path.stem: read_csv(path)}
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    finally:
        wb.close()
