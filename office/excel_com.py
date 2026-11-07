"""Excel desktop bridge over COM: read the open workbook, write ranges, and map cells to screen pixels.

Pointing at a cell is computed from Excel's own layout instead of guessed from a
screenshot. Every call attaches to the running Excel fresh, so the bridge can be
used from any thread. Needs pywin32 on Windows; elsewhere every call returns None.
"""
import ctypes
import logging
import os
import sys
from decimal import Decimal
from typing import Optional

from office.cellref import CellRef, col_letters, parse_ref

try:                                # the rehearsal robot checks that nothing is written before the user confirms
    import scene_log
except ImportError:                 # pragma: no cover
    scene_log = None

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


def _cell_kind(v):
    """'n' for a figure, 't' for words, else None.

    A figure is a number, or text that is mostly digits or short with a digit: "436tr", "5,42 tỷ",
    "TK 6411", "T10". A title that only mentions a month ("Báo cáo tháng 10") is words.
    """
    if v is None or isinstance(v, bool):
        return None if v is None else "t"
    if isinstance(v, (int, float, Decimal)):
        return "n"
    text = str(v).strip()
    if not text:
        return None
    digits = sum(c.isdigit() for c in text)
    if not digits:
        return "t"
    solid = sum(not c.isspace() for c in text)
    return "n" if len(text) <= 10 or digits >= 0.3 * solid else "t"


def figure_blocks(grid) -> list:
    """Rectangles of same-kind cells in a value grid: [(kind, row0, row1, col0, col1)], ends inclusive, 0-based.

    Each row is cut into runs of figures ('n') or words ('t'); a run that repeats on the next row grows down,
    so a column of numbers becomes one block and an empty gap between blocks stays free for a card.
    """
    done, open_ = [], {}
    for i, row in enumerate(grid):
        runs, j = [], 0
        while j < len(row):
            kind = _cell_kind(row[j])
            if kind is None:
                j += 1
                continue
            k = j
            while k + 1 < len(row) and _cell_kind(row[k + 1]) == kind:
                k += 1
            runs.append((kind, j, k))
            j = k + 1
        still = {}
        for run in runs:
            r0 = open_.pop(run, i)
            still[run] = r0
        for (kind, c0, c1), r0 in open_.items():
            done.append((kind, r0, i - 1, c0, c1))
        open_ = still
    for (kind, c0, c1), r0 in open_.items():
        done.append((kind, r0, len(grid) - 1, c0, c1))
    return sorted(done, key=lambda b: (b[1], b[3]))


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

XL_OPEN_XML_WORKBOOK = 51        # .xlsx


def _same_path(a: str, b: str) -> bool:
    """Two Windows paths name the same file (Excel's FullName may differ in case from ours)."""
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _shape(name, wb=None) -> str:
    """What a workbook name looks like, never the name: clicky.log must not carry a company's file names.

    Enough to tell the cases apart: no file, Excel's temporary name (no extension, as "65302810"),
    or a normal file, plus whether it sits in Clicky's own folder and whether Excel holds it read-only."""
    name = str(name or "")
    if not name:
        return "a workbook with no name"
    ext = os.path.splitext(name.replace("\\", "/").rsplit("/", 1)[-1])[1].lower()
    kind = f"a {ext} file" if ext else "a name with no extension (Excel's temporary name?)"
    has_path = bool(str(getattr(wb, "Path", "") or "")) if wb is not None else ("\\" in name or "/" in name)
    try:
        from storage import data_dir
        inside = os.path.normcase(os.path.abspath(name)).startswith(os.path.normcase(str(data_dir())))
    except Exception:
        inside = False
    where = "inside" if inside else "outside"
    flags = ("with a folder" if has_path else "with no folder") + (
        ", read-only" if wb is not None and bool(getattr(wb, "ReadOnly", False)) else "")
    return f"{kind} ({flags}, {where} the Clicky folder)"


def _why(exc) -> str:
    """An Excel error without its text: COM messages quote the file's name and folder."""
    code = next((a for a in getattr(exc, "args", ()) if isinstance(a, int)), None)
    if code is None and isinstance(exc, OSError):
        code = exc.errno
    return type(exc).__name__ + (f" 0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else "")


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
        if scene_log is not None:
            scene_log.record("write", ref=text, rows=height, cols=width)
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
            if scene_log is not None:
                scene_log.record("write", ref=f"{final}!", add_sheet=True)
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

    def workbook_path(self) -> Optional[str]:
        """Full path of the saved active workbook, or None for a new, unsaved one."""
        app = self._excel()
        try:
            wb = app.ActiveWorkbook
            return str(wb.FullName) if str(wb.Path or "") else None
        except Exception:
            return None

    def open_workbook(self, path: str) -> bool:
        """Open a workbook in Excel (starting Excel when it is not running), maximised and in front."""
        if self._app is None and sys.platform != "win32":
            return False
        try:
            app = self._excel()
            if app is None:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                app = win32com.client.Dispatch("Excel.Application")
            app.Visible = True
            app.Workbooks.Open(path)
            app.WindowState = -4137            # xlMaximized
            return True
        except Exception as exc:
            log.warning("Opening %s in Excel failed: %s", _shape(path), _why(exc))
            return False

    def show_workbook(self, path: str) -> bool:
        """Bring a workbook to the front so a cell in it can be lit: the open one with that file, else the file
        opened read-only (a source file such as the AOPR plan is never changed). Files opened here are
        remembered, so close_opened() puts Excel back as it was."""
        app = self._excel()
        if app is None:
            return False
        try:
            for i in range(1, app.Workbooks.Count + 1):
                wb = app.Workbooks(i)
                if _same_path(str(wb.FullName or ""), path):
                    wb.Activate()
                    return True
            alerts = app.DisplayAlerts
            app.DisplayAlerts = False           # no "update links?" or "read-only?" window over the story
            try:
                wb = app.Workbooks.Open(path, UpdateLinks=0, ReadOnly=True, AddToMru=False)
            finally:
                app.DisplayAlerts = alerts
            wb.Activate()
            self.__dict__.setdefault("_opened", []).append(str(wb.FullName))
            return True
        except Exception as exc:
            log.warning("Showing %s in Excel failed: %s", _shape(path), _why(exc))
            return False

    def close_opened(self, back_to: Optional[str] = None) -> int:
        """Close, without saving, the read-only files show_workbook opened; then show back_to again."""
        app, closed = self._excel(), 0
        opened, self._opened = list(getattr(self, "_opened", [])), []
        if app is None:
            return 0
        for name in opened:
            try:
                for i in range(app.Workbooks.Count, 0, -1):
                    wb = app.Workbooks(i)
                    if _same_path(str(wb.FullName or ""), name):
                        wb.Close(SaveChanges=False)
                        closed += 1
            except Exception as exc:
                log.warning("Closing a source file failed: %s", _why(exc))
        if back_to:
            self.show_workbook(back_to)
        return closed

    def save(self, path: Optional[str] = None) -> bool:
        """Save the active workbook (only when the user asked Clicky to); never shows a window.

        Workbook.Save on a workbook with no file behind it (new, or renamed by Excel to a temporary
        name like "65302810" after a save it could not finish) opens a modal Save As dialog. That
        dialog shows the user's own folders and blocks every later COM call. So:
        - with `path` (a file Clicky made, e.g. the sample in Clicky's folder), any workbook that is not
          already that saved file goes through SaveAs with that exact name;
        - without it (the user's own file), Clicky refuses and says it could not save.
        Alerts stay off while saving, so a failure comes back as an error rather than a window, and the
        save counts only if the workbook ends up at the file it should be.
        """
        app = self._excel()
        try:
            wb = app.ActiveWorkbook
            before = str(wb.FullName or "")
            has_file = bool(str(wb.Path or "")) and not bool(getattr(wb, "ReadOnly", False))
            if path is None and not has_file:
                log.warning("Not saving %s: no writable file behind it (Excel would open Save As)", _shape(before, wb))
                return False
            target = str(path) if path else before
            alerts = app.DisplayAlerts
            app.DisplayAlerts = False
            try:
                if has_file and _same_path(before, target):
                    wb.Save()
                else:
                    wb.SaveAs(Filename=target, FileFormat=XL_OPEN_XML_WORKBOOK)
            finally:
                app.DisplayAlerts = alerts
            after = str(wb.FullName or "")
            if not _same_path(after, target):
                log.warning("Saving %s did not finish: Excel now holds %s", _shape(target), _shape(after, wb))
                return False
            return True
        except Exception as exc:
            log.warning("Saving the workbook failed: %s", _why(exc))
            return False

    def save_copy(self, path: str) -> bool:
        """Save a copy of the workbook as it is now under another name; the open workbook is not renamed or saved.

        When Excel says nothing changed since the last save, the file on disk is the workbook as it is now,
        so it is copied straight from disk: a file copy takes milliseconds, while SaveCopyAs makes Excel
        write the whole workbook again (seconds on the first save after Excel starts)."""
        app = self._excel()
        try:
            wb = app.ActiveWorkbook
            disk = str(wb.FullName or "") if str(wb.Path or "") else ""
            if disk and bool(wb.Saved) and os.path.isfile(disk):
                import shutil
                try:
                    shutil.copyfile(disk, path)
                    return True
                except OSError as exc:
                    log.info("Copying the saved file failed (%s); asking Excel for the copy", _why(exc))
        except Exception:
            pass
        try:
            alerts = app.DisplayAlerts
            app.DisplayAlerts = False           # a failure comes back as an error, never as a window
            try:
                app.ActiveWorkbook.SaveCopyAs(path)
            finally:
                app.DisplayAlerts = alerts
            return True
        except Exception as exc:
            log.warning("Saving a copy to %s failed: %s", _shape(path), _why(exc))
            return False

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

    def figure_boxes(self):
        """Physical screen boxes of the visible cells holding figures and of those holding words, or None.

        {"numbers": [(l, t, r, b), ...], "text": [...]}. One read of the visible values per pane, then each
        block is mapped with that pane's own geometry, so a card can sit in a gap between blocks of figures
        instead of over them (one box for the whole used range left no room on a full sheet).
        """
        app = self._excel()
        if app is None:
            return None
        try:
            ws, win = app.ActiveSheet, app.ActiveWindow
            dpi = self._dpi or _window_dpi(app.Hwnd)
            out = {"numbers": [], "text": []}
            for p in range(1, win.Panes.Count + 1):
                pane = win.Panes(p)
                filled = app.Intersect(ws.UsedRange, pane.VisibleRange)
                if filled is None:
                    continue
                grid = _as_grid(filled.Value2)
                rows, cols = len(grid), len(grid[0]) if grid else 0
                if not rows or not cols or rows * cols > MAX_WRITE_CELLS:
                    continue
                x0, y0 = float(filled.Left), float(filled.Top)
                xs = [float(filled.Columns(j + 1).Left) for j in range(cols)] + [x0 + float(filled.Width)]
                ys = [float(filled.Rows(i + 1).Top) for i in range(rows)] + [y0 + float(filled.Height)]
                left, top = pane.PointsToScreenPixelsX(x0), pane.PointsToScreenPixelsY(y0)
                right, bottom = pane.PointsToScreenPixelsX(xs[-1]), pane.PointsToScreenPixelsY(ys[-1])
                # Same guard as screen_rect: some builds ignore zoom in PointsToScreenPixels.
                expect_w = (xs[-1] - x0) * (win.Zoom / 100.0) * dpi / 72.0
                sx = (right - left) / max(xs[-1] - x0, 1e-6)
                sy = (bottom - top) / max(ys[-1] - y0, 1e-6)
                if expect_w > 4 and not 0.7 <= (right - left) / expect_w <= 1.3:
                    scale = expect_w / max(right - left, 1)
                    sx, sy = sx * scale, sy * scale
                for kind, r0, r1, c0, c1 in figure_blocks(grid):
                    box = (left + (xs[c0] - x0) * sx, top + (ys[r0] - y0) * sy,
                           left + (xs[c1 + 1] - x0) * sx, top + (ys[r1 + 1] - y0) * sy)
                    if box[2] > box[0] and box[3] > box[1]:
                        out["numbers" if kind == "n" else "text"].append(tuple(float(v) for v in box))
            return out
        except Exception as exc:
            log.debug("figure_boxes failed: %s", exc)
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
        if scene_log is not None:
            scene_log.record("write", ref=text, clear=True)
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
