"""Fill {tq:key}, {xl:Sheet!A1} and {slide:title} in a script line with figures read at run time.

The script and the cell dictionary hold no numbers. A Source reads the client's workbook (over COM
on the client's PC, or with openpyxl for rehearsals) and every placeholder must resolve; a blank or
an error cell is reported, never spoken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from present.vn_format import fmt

TOKEN = re.compile(r"\{(tq|xl|slide):([^{}|]+)(?:\|([a-z0-9]+))?\}")
OPTIONAL = re.compile(r"\[\[(.*?)\]\]")
SUMMARY_SHEET, SUMMARY_FIRST_ROW = "Tong_quan", 4


class Source(Protocol):
    def cell(self, sheet: str, address: str):  # value after calculation
        ...

    def number_format(self, sheet: str, address: str) -> str:
        ...

    def rows(self, sheet: str, first_row: int, cols: int) -> list:
        ...


def kind_from_number_format(number_format: str) -> str:
    """Excel number format -> display kind. Percent formats keep their decimals; anything else is tỷ."""
    nf = (number_format or "").lower()
    if "%" in nf:
        decimals = len(nf.split(".")[1].split("%")[0]) if "." in nf.split("%")[0] else 0
        return {0: "pct1", 1: "pct1"}.get(decimals, "pct2")
    return "bn"


@dataclass
class Resolved:
    text: str
    missing: list = field(default_factory=list)     # (token, reason)
    values: dict = field(default_factory=dict)      # token -> displayed text


class Resolver:
    def __init__(self, source: Source, slide_title: Optional[Callable[[], str]] = None):
        self.source = source
        self.slide_title = slide_title
        self.overrides: dict = {}       # "Sheet!A1" -> value captured during a what-if, before cells were put back
        self._summary: Optional[dict] = None

    def summary(self) -> dict:
        """Tong_quan: key in column A from row 4, value in B, display kind in C."""
        if self._summary is None:
            table = {}
            for row in self.source.rows(SUMMARY_SHEET, SUMMARY_FIRST_ROW, 3):
                key = str(row[0] or "").strip()
                if key:
                    table[key] = (row[1], str(row[2] or "bn").strip() or "bn")
            self._summary = table
        return self._summary

    def value(self, kind: str, ref: str, hint: Optional[str] = None):
        """Return (raw value, displayed text). Raises ValueError with a Vietnamese reason."""
        if kind == "tq":
            table = self.summary()
            if ref not in table:
                raise ValueError(f"không có khoá {ref} trong {SUMMARY_SHEET}")
            raw, k = table[ref]
            return raw, fmt(raw, hint or k)
        if kind == "xl":
            sheet, _, address = ref.partition("!")
            if not address:
                raise ValueError(f"ô {ref} thiếu tên sheet")
            raw = self.overrides[ref] if ref in self.overrides else self.source.cell(sheet, address)
            k = hint or kind_from_number_format(self.source.number_format(sheet, address))
            return raw, fmt(raw, k)
        if kind == "slide" and ref == "title":
            title = (self.slide_title() if self.slide_title else "") or ""
            if not title.strip():
                raise ValueError("slide không có tiêu đề")
            return title, title.strip().rstrip(".")
        raise ValueError(f"không hiểu {{{kind}:{ref}}}")

    def fill(self, text: str, extra: Optional[dict] = None) -> Resolved:
        """extra fills parameters such as {kenh} or {pct} from a question.

        A part written [[like this]] is optional: it is dropped when a number inside it is zero
        (e.g. "bước hai 0,0 tỷ" when nothing is left to cut), otherwise kept without the brackets.
        """
        out = Resolved(text)
        for name, val in (extra or {}).items():
            out.text = out.text.replace("{" + name + "}", str(val))

        def fill_part(part: str):
            zero = False

            def sub(m):
                nonlocal zero
                token = m.group(0)
                try:
                    raw, shown = self.value(m.group(1), m.group(2).strip(), m.group(3))
                except (ValueError, KeyError, TypeError) as exc:
                    out.missing.append((token, str(exc)))
                    return token
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) and abs(raw) < 1e-9:
                    zero = True
                out.values[token] = shown
                return shown

            return TOKEN.sub(sub, part), zero

        pieces = []
        for i, part in enumerate(OPTIONAL.split(out.text)):
            filled, zero = fill_part(part)
            if i % 2 == 1 and zero:
                continue
            pieces.append(filled)
        out.text = re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s{2,}", " ", "".join(pieces))).strip()
        return out


def tokens(text: str) -> list:
    return [m.group(0) for m in TOKEN.finditer(text or "")]


class OpenpyxlSource:
    """Rehearsal reader: a workbook saved by Excel (or recalculated by LibreOffice), values cached."""

    def __init__(self, path: str):
        import openpyxl
        self.wb = openpyxl.load_workbook(path, data_only=True)

    def cell(self, sheet, address):
        return self.wb[sheet][address].value

    def number_format(self, sheet, address):
        return self.wb[sheet][address].number_format

    def sheet_names(self):
        return list(self.wb.sheetnames)

    def rows(self, sheet, first_row, cols):
        ws = self.wb[sheet]
        return [list(r) for r in ws.iter_rows(min_row=first_row, max_col=cols, values_only=True)]


class ComSource:
    """Live reader over Excel COM on the client's PC; values are read after Excel has calculated."""

    def __init__(self, workbook):
        self.wb = workbook

    def cell(self, sheet, address):
        return self.wb.Worksheets(sheet).Range(address).Value2

    def number_format(self, sheet, address):
        return str(self.wb.Worksheets(sheet).Range(address).NumberFormat)

    def sheet_names(self):
        return [self.wb.Worksheets(i).Name for i in range(1, self.wb.Worksheets.Count + 1)]

    def rows(self, sheet, first_row, cols):
        ws = self.wb.Worksheets(sheet)
        last = ws.UsedRange.Row + ws.UsedRange.Rows.Count - 1
        if last < first_row:
            return []
        block = ws.Range(ws.Cells(first_row, 1), ws.Cells(last, cols)).Value2
        return [list(r) for r in block]
