"""Excel desktop bridge over COM: read the open workbook, write ranges, and map cells to screen pixels.

Pointing at a cell is computed from Excel's own layout instead of guessed from a
screenshot. Every call attaches to the running Excel fresh, so the bridge can be
used from any thread. Needs pywin32 on Windows; elsewhere every call returns None.
"""
import ctypes
import logging
import sys
from decimal import Decimal
from typing import Optional

from office.cellref import CellRef, col_letters, parse_ref

log = logging.getLogger(__name__)

MAX_CONTEXT_ROWS = 30
MAX_CONTEXT_COLS = 12
MAX_WRITE_CELLS = 20_000
EXCEL_TITLE_HINTS = ("- excel", "excel -", "microsoft excel", "power query editor", "trình soạn thảo power query")


def is_excel_window(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in EXCEL_TITLE_HINTS)


def _as_grid(value):
    """COM returns a scalar for one cell and nested tuples for a block."""
    if isinstance(value, tuple):
        return [list(row) if isinstance(row, tuple) else [row] for row in value]
    return [[value]]


def _block_address(row: int, col: int, rows: int, cols: int) -> str:
    """'C5:H40' for a block of rows x cols whose top-left cell is (row, col).

    Built as text so no parameterised COM property (Range.Resize) is involved: early-bound
    wrappers turn those into GetResize or the default member, which fails or returns one cell.
    """
    rows, cols = max(rows, 1), max(cols, 1)
    return f"{col_letters(col)}{row}:{col_letters(col + cols - 1)}{row + rows - 1}"


def _bgr(hex_colour: str) -> int:
    """'#RRGGBB' → the BGR integer Excel's Color properties take."""
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r + (g << 8) + (b << 16)


def _window_dpi(hwnd) -> int:
    try:
        return ctypes.windll.user32.GetDpiForWindow(int(hwnd)) or 96
    except Exception:
        return 96



def reveal_scroll(row: int, rows: int, col: int, seen_rows: int, seen_cols: int) -> tuple:
    """(ScrollRow, ScrollColumn) that bring a range into view with its context.

    Keeps the sheet's top-left (title, row labels in column A) on screen when the range fits
    under it; otherwise puts the range about a third of the way down, never at the very top.
    """
    seen_rows, seen_cols = max(seen_rows, 1), max(seen_cols, 1)
    top = 1 if row + rows - 1 <= seen_rows - 1 else max(1, row - seen_rows // 3)
    left = 1 if col < seen_cols - 1 else col
    return top, left

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
                block = ws.Range(_block_address(sel.Row, sel.Column, rows, cols))
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
        def cell(v):
            return float(v) if isinstance(v, Decimal) else v
        grid = tuple(tuple(cell(r[i]) if i < len(r) else "" for i in range(width)) for r in rows)
        try:
            target = self._sheet(app, ref).Range(
                f"{col_letters(ref.col1)}{ref.row1}:{col_letters(ref.col1 + width - 1)}{ref.row1 + height - 1}")
            target.Formula = grid
            return True
        except Exception as exc:
            log.warning("Excel write failed: %s", exc)
            return False

    def add_sheet(self, name: str) -> Optional[str]:
        """Add a sheet at the end; returns the name used (suffixed when taken), or None."""
        app = self._excel()
        if app is None:
            return None
        try:
            wb = app.ActiveWorkbook
            taken = {wb.Worksheets(i + 1).Name.lower() for i in range(wb.Worksheets.Count)}
            base, n, final = name[:28], 2, name[:31]
            while final.lower() in taken:
                final, n = f"{base} {n}", n + 1
            ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
            ws.Name = final
            return final
        except Exception:
            return None

    def sheet_rows(self, name: str, max_rows: int = 5000, max_cols: int = 40) -> Optional[list]:
        """A sheet's values as rows anchored at A1, so row numbers match what the user sees."""
        app = self._excel()
        if app is None:
            return None
        try:
            ws = app.ActiveWorkbook.Worksheets(name)
            used = ws.UsedRange
            block = ws.Range(_block_address(used.Row, used.Column, min(used.Rows.Count, max_rows),
                                            min(used.Columns.Count, max_cols)))
            # Value2: plain numbers and text, no date or currency conversion that can fail.
            grid = _as_grid(block.Value2)
            pad = [None] * (used.Column - 1)
            return [[] for _ in range(used.Row - 1)] + [pad + row for row in grid]
        except Exception as exc:
            log.warning("Reading sheet %s failed: %s", name, exc)
            return None

    def workbook_folder(self) -> Optional[str]:
        """Folder of the saved active workbook, or None for a new, unsaved one."""
        app = self._excel()
        try:
            path = str(app.ActiveWorkbook.Path or "")
            return path or None
        except Exception:
            return None

    def sheet_names(self) -> list:
        app = self._excel()
        try:
            wb = app.ActiveWorkbook
            return [wb.Worksheets(i + 1).Name for i in range(wb.Worksheets.Count)]
        except Exception:
            return []

    def activate(self, name: str) -> None:
        app = self._excel()
        try:
            app.ActiveWorkbook.Worksheets(name).Activate()
        except Exception:
            pass

    def content_rect(self):
        """Physical screen pixels of the filled part of the sheet on screen (UsedRange ∩ visible), or None."""
        app = self._excel()
        try:
            ws, win = app.ActiveSheet, app.ActiveWindow
            filled = app.Intersect(ws.UsedRange, win.VisibleRange)
            if filled is None:
                return None
            name = ws.Name.replace("'", "''")
            return self.screen_rect(f"'{name}'!{filled.Address.replace('$', '')}")
        except Exception as exc:
            log.debug("content_rect failed: %s", exc)
            return None

    def scroll_home(self, name: str) -> None:
        """Show a sheet from its top-left corner, so a report is seen from its title down."""
        app = self._excel()
        try:
            app.ActiveWorkbook.Worksheets(name).Activate()
            app.ActiveWindow.ScrollRow, app.ActiveWindow.ScrollColumn = 1, 1
        except Exception as exc:
            log.debug("scroll_home %s failed: %s", name, exc)

    def fit_rows(self, name: str, address: str, low: int = 70, high: int = 100) -> None:
        """Zoom so a whole block (a report from its title to its last line) fits the window, within low–high %,
        then show it from the top-left. Only used on sheets Clicky made."""
        app = self._excel()
        try:
            ws = app.ActiveWorkbook.Worksheets(name)
            ws.Activate()
            win = app.ActiveWindow
            ws.Range(address).Select()
            win.Zoom = True                         # Excel's "zoom to selection"
            win.Zoom = max(low, min(high, int(win.Zoom)))
            ws.Range("A1").Select()
            win.ScrollRow, win.ScrollColumn = 1, 1
        except Exception as exc:
            log.debug("fit_rows %s %s failed: %s", name, address, exc)

    def screen_updating(self, on: bool) -> None:
        """Freeze or release Excel's screen, so sheets added and filled in a batch do not flash past the user."""
        app = self._excel()
        try:
            app.ScreenUpdating = bool(on)
        except Exception:
            pass

    def ignore_text_numbers(self, text: str) -> None:
        """Hide Excel's green 'number stored as text' triangle on cells that are text on purpose (account codes)."""
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None:
            return
        try:
            for cell in self._sheet(app, ref).Range(ref.address).Cells:
                cell.Errors(3).Ignore = True          # xlNumberAsText
        except Exception:
            pass

    def format_numbers(self, text: str, number_format: str = '#,##0;(#,##0);"-"') -> None:
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None:
            return
        try:
            self._sheet(app, ref).Range(ref.address).NumberFormat = number_format
        except Exception:
            pass

    def clear(self, text: str) -> None:
        """Clear values and formatting of a range, so a sheet Clicky rewrites keeps no stale rows or colours."""
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None:
            return
        self._sheet(app, ref).Range(ref.address).Clear()

    def style(self, text: str, bold=None, italic=None, size=None, color=None, fill=None, wrap=None,
              halign=None, border_bottom=None, height=None) -> None:
        """Format a range; colours are '#RRGGBB'. Raises on COM errors (callers decide whether to care)."""
        ref = parse_ref(text)
        app = self._excel()
        if ref is None or app is None:
            return
        rng = self._sheet(app, ref).Range(ref.address)
        font = rng.Font
        if bold is not None:
            font.Bold = bold
        if italic is not None:
            font.Italic = italic
        if size is not None:
            font.Size = size
        if color is not None:
            font.Color = _bgr(color)
        if fill is not None:
            rng.Interior.Color = _bgr(fill)
        if wrap is not None:
            rng.WrapText = wrap
        if halign is not None:
            rng.HorizontalAlignment = {"left": -4131, "center": -4108, "right": -4152}[halign]
        if border_bottom is not None:
            edge = rng.Borders(9)            # xlEdgeBottom
            edge.LineStyle = 1               # xlContinuous
            edge.Weight = 2                  # xlThin
            edge.Color = _bgr(border_bottom)
            if rng.Rows.Count > 1:
                inside = rng.Borders(12)     # xlInsideHorizontal
                inside.LineStyle = 1
                inside.Weight = 2
                inside.Color = _bgr(border_bottom)
        if height is not None:
            rng.RowHeight = height
        rng.VerticalAlignment = -4108        # xlCenter: text sits in the middle of taller rows

    def layout_sheet(self, name: str, widths: dict, gridlines: bool = True) -> None:
        """Column widths (in Excel characters) and gridlines for one sheet; gridlines are a window setting."""
        app = self._excel()
        if app is None:
            return
        ws = app.ActiveWorkbook.Worksheets(name)
        for col, width in widths.items():
            ws.Columns(col).ColumnWidth = width
        if not gridlines:
            ws.Activate()
            app.ActiveWindow.DisplayGridlines = False

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
                seen = win.Panes(1).VisibleRange
                win.ScrollRow, win.ScrollColumn = reveal_scroll(rng.Row, rng.Rows.Count, rng.Column,
                                                                seen.Rows.Count, seen.Columns.Count)
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
