"""Open the xlsx exactly as it was saved and compare its key totals with what the code computed.

Excel stores the last calculated value of every formula in the file; openpyxl with data_only=True
reads those values, so this sees what anyone opening the file will see, not what Excel holds in
memory. "Khớp sổ cái 0 đ" is said only after this passes.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

TOLERANCE = Decimal(1)
TAMPER_ENV = "CLICKY_TEST_TAMPER"      # rehearsal only: "<Sheet>!<cell>=<delta>" is added to that cell after saving
_REF = re.compile(r"^'?(?P<sheet>.+?)'?!\$?(?P<col>[A-Z]+)\$?(?P<row>\d+)$")


def split(ref: str) -> tuple:
    m = _REF.match(ref.strip())
    if not m:
        raise ValueError(f"not a cell reference: {ref}")
    return m.group("sheet").replace("''", "'"), f"{m.group('col')}{m.group('row')}"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def tamper_from_env(path: Path) -> None:
    """Test hook for the rehearsal robot: change one saved cell so the re-read must catch it.

    Only runs when CLICKY_TEST_TAMPER is set, on practice data: the file is rewritten with values only.
    """
    spec = os.environ.get(TAMPER_ENV, "").strip()
    if not spec or "=" not in spec:
        return
    ref, delta = spec.rsplit("=", 1)
    sheet, cell = split(ref)
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet]
    ws[cell] = float((_number(ws[cell].value) or Decimal(0)) + Decimal(delta.strip()))
    wb.save(path)


def check(path: Path, expect: dict) -> tuple:
    """(ok, diffs): diffs is [{cell, want, got}] for every expected cell that is missing or off by more than 1 đ."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    names = {n.casefold(): n for n in wb.sheetnames}
    diffs = []
    for ref, want in expect.items():
        sheet, cell = split(ref)
        got = None
        if sheet.casefold() in names:
            got = _number(wb[names[sheet.casefold()]][cell].value)
        want = Decimal(str(want))
        if got is None or abs(got - want) > TOLERANCE:
            diffs.append({"cell": ref, "want": int(want), "got": None if got is None else int(got)})
    wb.close()
    return not diffs, diffs
