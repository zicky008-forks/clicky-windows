"""A1 cell references for the Excel bridge: parsing only, no COM, safe to test anywhere."""
import re
from dataclasses import dataclass
from typing import Optional

_SHEET = r"(?:'((?:[^']|'')+)'|([^'!\[\]:*?/\\]+))!"
_CELL = r"\$?([A-Za-z]{1,3})\$?(\d{1,7})"
REF_RE = re.compile(rf"^(?:{_SHEET})?{_CELL}(?::{_CELL})?$")

MAX_ROWS = 1_048_576
MAX_COLS = 16_384


def col_index(letters: str) -> int:
    """'A' → 1, 'Z' → 26, 'AA' → 27."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letters(index: int) -> str:
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


@dataclass(frozen=True)
class CellRef:
    sheet: Optional[str]
    col1: int
    row1: int
    col2: int
    row2: int

    @property
    def address(self) -> str:
        first = f"{col_letters(self.col1)}{self.row1}"
        if (self.col1, self.row1) == (self.col2, self.row2):
            return first
        return f"{first}:{col_letters(self.col2)}{self.row2}"

    @property
    def cells(self) -> int:
        return (self.col2 - self.col1 + 1) * (self.row2 - self.row1 + 1)


def parse_ref(text: str) -> Optional[CellRef]:
    """Parse "B12", "Sheet1!B2:D20" or "'Báo cáo T10'!$C$5". None if it is not a cell reference."""
    m = REF_RE.match(text.strip())
    if not m:
        return None
    quoted, bare, c1, r1, c2, r2 = m.groups()
    sheet = quoted.replace("''", "'") if quoted else (bare.strip() if bare else None)
    col1, row1 = col_index(c1), int(r1)
    col2, row2 = (col_index(c2), int(r2)) if c2 else (col1, row1)
    if not (1 <= col1 <= MAX_COLS and 1 <= col2 <= MAX_COLS and 1 <= row1 <= MAX_ROWS and 1 <= row2 <= MAX_ROWS):
        return None
    col1, col2 = sorted((col1, col2))
    row1, row2 = sorted((row1, row2))
    return CellRef(sheet, col1, row1, col2, row2)
