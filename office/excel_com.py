"""Excel desktop bridge over COM: read the open workbook, write ranges, and map cells to screen pixels.

Pointing at a cell is computed from Excel's own layout instead of guessed from a
screenshot. Every call attaches to the running Excel fresh, so the bridge can be
used from any thread. Needs pywin32 on Windows; elsewhere every call returns None.
"""
import ctypes
import logging
import sys
from typing import Optional

from office.cellref import CellRef, col_letters, parse_ref

log = logging.getLogger(__name__)

MAX_CONTEXT_ROWS = 30
MAX_CONTEXT_COLS = 12
MAX_WRITE_CELLS = 20_000
EXCEL_TITLE_HINTS = ("- excel", "excel -", "microsoft excel")


def is_excel_window(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in EXCEL_TITLE_HINTS)


def _as_grid(value):
    """COM returns a scalar for one cell and nested tuples for a block."""
    if isinstance(value, tuple):
        return [list(row) if isinstance(row, tuple) else [row] for row in value]
    return [[value]]


def _window_dpi(hwnd) -> int:
    try:
        return ctypes.windll.user32.GetDpiForWindow(int(hwnd)) or 96
    except Exception:
        return 96


class ExcelBridge:
    def __init__(self, app=None, dpi: Optional[int] = None):
        self._app = app        # injected in tests; otherwise attach to the running Excel
        self._dpi = dpi

    def _excel(self):
        if self._app is not None:
            return self._app
        if sys.platform != "win32":
            return None
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            return win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            return None   # Excel not running, or pywin32 missing

    def available(self) -> bool:
        app = self._excel()
        try:
            return app is not None and app.ActiveWorkbook is not None
        except Exception:
            return False

    def _sheet(self, app, ref: CellRef):
        return app.ActiveWorkbook.Worksheets(ref.sheet) if ref.sheet else app.ActiveSheet

    # ── Reading ────────────────────────────────────────────────────────────

    def context(self) -> Optional[dict]:
        """What the user is looking at: workbook, sheet, selection and a bounded sample of its values."""
        app = self._excel()
        if app is None:
            return None
        try:
            wb, ws = app.ActiveWorkbook, app.ActiveSheet
            if wb is None or ws is None:
                return None
            sel = app.Selection
            ctx = {
                "workbook": wb.Name,
                "sheet": ws.Name,
                "sheets": [wb.Worksheets(i + 1).Name for i in range(min(wb.Worksheets.Count, 30))],
                "used_range": ws.UsedRange.Address.replace("$", ""),
                "selection": None,
                "values": [],
            }
            try:
                ctx["selection"] = sel.Address.replace("$", "")
                rows = min(sel.Rows.Count, MAX_CONTEXT_ROWS)
                cols = min(sel.Columns.Count, MAX_CONTEXT_COLS)
                block = sel.Resize(rows, cols)
                ctx["values"] = _as_grid(block.Formula)
            except Exception:
                pass   # a chart or shape is selected, not cells
            return ctx
        except Exception as exc:
            log.debug("Excel context unavailable: %s", exc)
            return None

    def read(self, text: str) -> Optional[list]:
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None or ref.cells > MAX_WRITE_CELLS:
            return None
        try:
            return _as_grid(self._sheet(app, ref).Range(ref.address).Value)
        except Exception:
            return None

    # ── Writing ────────────────────────────────────────────────────────────

    def write(self, text: str, rows: list) -> bool:
        """Write a 2-D block starting at a cell; strings starting with '=' become formulas."""
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None or not rows or not all(isinstance(r, list) for r in rows):
            return False
        height, width = len(rows), max(len(r) for r in rows)
        if height * width > MAX_WRITE_CELLS:
            return False
        grid = tuple(tuple(r[i] if i < len(r) else "" for i in range(width)) for r in rows)
        try:
            target = self._sheet(app, ref).Range(
                f"{col_letters(ref.col1)}{ref.row1}:{col_letters(ref.col1 + width - 1)}{ref.row1 + height - 1}")
            target.Formula = grid
            return True
        except Exception as exc:
            log.warning("Excel write failed: %s", exc)
            return False

    def add_sheet(self, name: str) -> bool:
        app = self._excel()
        if app is None:
            return False
        try:
            wb = app.ActiveWorkbook
            ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
            ws.Name = name[:31]
            return True
        except Exception:
            return False

    # ── Pointing ───────────────────────────────────────────────────────────

    def screen_rect(self, text: str, reveal: bool = False):
        """Physical screen pixels (left, top, right, bottom) of a cell or range, or None when not on screen."""
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None:
            return None
        try:
            ws = self._sheet(app, ref)
            if ws.Name != app.ActiveSheet.Name:
                return None   # the model named a sheet the user is not looking at
            rng = ws.Range(ref.address)
            win = app.ActiveWindow
            pane = self._pane_showing(app, win, rng)
            if pane is None and reveal and win.Panes.Count == 1:
                win.ScrollRow, win.ScrollColumn = rng.Row, rng.Column
                pane = self._pane_showing(app, win, rng)
            if pane is None:
                return None
            left = pane.PointsToScreenPixelsX(rng.Left)
            top = pane.PointsToScreenPixelsY(rng.Top)
            right = pane.PointsToScreenPixelsX(rng.Left + rng.Width)
            bottom = pane.PointsToScreenPixelsY(rng.Top + rng.Height)
            # Guard against builds that ignore zoom in PointsToScreenPixels: the
            # rendered width must roughly match points × zoom × dpi/72.
            dpi = self._dpi or _window_dpi(app.Hwnd)
            expect_w = rng.Width * (win.Zoom / 100.0) * dpi / 72.0
            if expect_w > 4 and not 0.7 <= (right - left) / expect_w <= 1.3:
                scale = expect_w / max(right - left, 1)
                right = left + (right - left) * scale
                bottom = top + (bottom - top) * scale
            return float(left), float(top), float(right), float(bottom)
        except Exception as exc:
            log.debug("Excel screen_rect failed for %s: %s", text, exc)
            return None

    @staticmethod
    def _pane_showing(app, win, rng):
        for i in range(1, win.Panes.Count + 1):
            pane = win.Panes(i)
            try:
                if app.Intersect(pane.VisibleRange, rng) is not None:
                    return pane
            except Exception:
                continue
        return None


def format_for_prompt(ctx: Optional[dict]) -> str:
    if not ctx:
        return ""
    lines = [
        "\n\nEXCEL CONTEXT (read from Excel itself, exact):",
        f"  workbook: {ctx['workbook']} · active sheet: {ctx['sheet']} · used range: {ctx['used_range']}",
        f"  sheets: {', '.join(ctx['sheets'])}",
    ]
    if ctx.get("selection"):
        lines.append(f"  selection: {ctx['selection']}")
        ref = parse_ref(ctx["selection"].split(",")[0])
        if ref and ctx.get("values"):
            for r, row in enumerate(ctx["values"]):
                cells = [f"{col_letters(ref.col1 + c)}{ref.row1 + r}={v!r}" for c, v in enumerate(row) if v not in (None, "")]
                if cells:
                    lines.append("    " + "  ".join(cells))
    lines.append(
        "  To point at a cell or range use [CELL:Sheet!B12|label]; to box a range use "
        "[RANGE:Sheet!B2:D20|label]. The app computes the exact position from Excel, so "
        "never guess pixel coordinates for cells. Only reference cells on the active sheet."
    )
    return "\n".join(lines)
