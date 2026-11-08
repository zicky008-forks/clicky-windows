"""What-if on a live scenario sheet: write inputs, let Excel calculate, read results, put every input back.

Safety rules (the workbook is the client's real file):
- Refuse while AutoSave is on. A file in OneDrive/SharePoint with AutoSave saves each edit to the
  cloud copy at once, so a what-if would change the real file for everyone.
- Never save. Old values (formulas included) are restored in a finally block and read back; if any cell
  does not come back exactly, say so loudly and leave the workbook unsaved.
- Every run is written to the minutes: inputs, results, restored = yes/no.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


class WhatIfRefused(RuntimeError):
    pass


@dataclass
class WhatIfRun:
    inputs: dict                                  # "Sheet!A1" -> new value
    results: dict = field(default_factory=dict)   # "Sheet!A1" -> value after calculation
    restored: bool = False
    problems: list = field(default_factory=list)


def _split(ref: str):
    sheet, _, address = ref.partition("!")
    if not address:
        raise ValueError(f"ô {ref} thiếu tên sheet")
    return sheet, address


def expand(ref: str) -> list:
    """'Kich_ban!F12:F14' -> ['Kich_ban!F12', 'Kich_ban!F13', 'Kich_ban!F14'] (one column or one row)."""
    from office.cellref import parse_ref, col_letters
    sheet, address = _split(ref)
    r = parse_ref(address)
    return [f"{sheet}!{col_letters(c)}{row}" for row in range(r.row1, r.row2 + 1) for c in range(r.col1, r.col2 + 1)]


class WhatIf:
    def __init__(self, workbook, calculate: Optional[Callable[[], None]] = None):
        """workbook: Excel COM Workbook. calculate: defaults to workbook.Application.Calculate."""
        self.wb = workbook
        self.calculate = calculate or (lambda: self.wb.Application.Calculate())

    def check_safe(self) -> None:
        if bool(getattr(self.wb, "AutoSaveOn", False)):
            raise WhatIfRefused("File đang bật Tự động lưu (AutoSave). Em không chạy thử kịch bản trên file này "
                                "vì mỗi lần sửa ô sẽ lưu thẳng lên OneDrive. Anh tắt AutoSave hoặc mở bản sao trên máy rồi hỏi lại.")

    def _range(self, ref):
        sheet, address = _split(ref)
        return self.wb.Worksheets(sheet).Range(address)

    def run(self, inputs: dict, outputs: list) -> WhatIfRun:
        self.check_safe()
        cells = {}
        for ref, value in inputs.items():
            for one in expand(ref):
                cells[one] = value
        run = WhatIfRun(inputs=dict(cells))
        saved_before = bool(getattr(self.wb, "Saved", True))
        old = {ref: self._range(ref).Formula for ref in cells}
        try:
            for ref, value in cells.items():
                self._range(ref).Value2 = value
            self.calculate()
            for ref in outputs:
                run.results[ref] = self._range(ref).Value2
        finally:
            for ref, formula in old.items():
                try:
                    self._range(ref).Formula = formula
                except Exception as exc:   # noqa: BLE001 - must keep restoring the other cells
                    run.problems.append(f"không trả được {ref}: {exc}")
            try:
                self.calculate()
            except Exception as exc:       # noqa: BLE001
                run.problems.append(f"Excel không tính lại được: {exc}")
            back = {ref: self._range(ref).Formula for ref in old}
            wrong = [ref for ref in old if str(back[ref]) != str(old[ref])]
            run.problems += [f"ô {ref} chưa về giá trị cũ" for ref in wrong]
            run.restored = not wrong and not run.problems
            if run.restored and saved_before:
                try:
                    self.wb.Saved = True   # nothing changed for real; do not prompt "save changes?"
                except Exception:          # noqa: BLE001
                    pass
        return run
