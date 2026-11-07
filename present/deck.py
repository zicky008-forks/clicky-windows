"""Minimal PowerPoint over COM: open a deck read-only, run the slide show, go to a slide, find a shape
the script names, and turn the shape into screen pixels so the overlay can point at it.

Shape words used by scripts: "tiêu đề" (title), "biểu đồ chính" (largest chart), "biểu đồ thác nước"
(waterfall chart, else largest chart), "bảng" (largest table); or shape_chua = text the shape contains.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from present.qa import fold

PP_TITLE_TYPES = (1, 3)        # ppPlaceholderTitle, ppPlaceholderCenterTitle
MSO_PLACEHOLDER = 14
XL_WATERFALL = 119


@dataclass
class ShapeInfo:
    index: int
    name: str
    left: float                 # points on the slide
    top: float
    width: float
    height: float
    text: str = ""
    is_title: bool = False
    is_chart: bool = False
    is_table: bool = False
    chart_type: Optional[int] = None

    @property
    def area(self) -> float:
        return self.width * self.height


def describe(shape, index: int) -> ShapeInfo:
    text = ""
    try:
        if shape.HasTextFrame and shape.TextFrame.HasText:
            text = str(shape.TextFrame.TextRange.Text)
    except Exception:                       # noqa: BLE001 - some shapes refuse TextFrame
        pass
    is_title = False
    try:
        is_title = shape.Type == MSO_PLACEHOLDER and shape.PlaceholderFormat.Type in PP_TITLE_TYPES
    except Exception:                       # noqa: BLE001
        pass
    is_chart = bool(getattr(shape, "HasChart", False))
    is_table = bool(getattr(shape, "HasTable", False))
    chart_type = None
    if is_chart:
        try:
            chart_type = int(shape.Chart.ChartType)
        except Exception:                   # noqa: BLE001
            pass
    if is_table and not text:
        try:
            cells = []
            for r in range(1, shape.Table.Rows.Count + 1):
                for c in range(1, shape.Table.Columns.Count + 1):
                    cells.append(str(shape.Table.Cell(r, c).Shape.TextFrame.TextRange.Text))
            text = " | ".join(cells)
        except Exception:                   # noqa: BLE001
            pass
    return ShapeInfo(index, str(shape.Name), float(shape.Left), float(shape.Top), float(shape.Width),
                     float(shape.Height), text, is_title, is_chart, is_table, chart_type)


def find(shapes: list, kind: Optional[str] = None, contains: Optional[str] = None) -> Optional[ShapeInfo]:
    """Pick one shape. Ties go to reading order (top, then left)."""
    ordered = sorted(shapes, key=lambda s: (round(s.top), round(s.left)))
    if contains is not None:
        needle = fold(contains)
        if len(needle) <= 2:     # "1", "2": a numbered item, not any shape with a 1 in it
            pat = re.compile(r"^\s*" + re.escape(needle) + r"(?![0-9])")
            hits = [s for s in ordered if pat.search(fold(s.text))]
        else:
            hits = [s for s in ordered if needle in fold(s.text)]
        return hits[0] if hits else None
    k = fold(kind or "")
    if k in ("tieu de", "title"):
        titles = [s for s in ordered if s.is_title]
        return titles[0] if titles else (ordered[0] if ordered else None)
    if k.startswith("bieu do"):
        charts = [s for s in shapes if s.is_chart]
        if "thac nuoc" in k:
            wf = [s for s in charts if s.chart_type == XL_WATERFALL]
            charts = wf or charts
        return max(charts, key=lambda s: s.area) if charts else None
    if k in ("bang", "table"):
        tables = [s for s in shapes if s.is_table]
        return max(tables, key=lambda s: s.area) if tables else None
    return None


def to_screen(shape: ShapeInfo, slide_w: float, slide_h: float, win_left: float, win_top: float,
              win_w: float, win_h: float, dpi: int = 96) -> tuple:
    """Slide points -> screen pixels inside a slide-show window (points), letterboxed and centred."""
    scale = min(win_w / slide_w, win_h / slide_h)
    off_x = win_left + (win_w - slide_w * scale) / 2
    off_y = win_top + (win_h - slide_h * scale) / 2
    px = dpi / 72.0
    x = (off_x + shape.left * scale) * px
    y = (off_y + shape.top * scale) * px
    return (round(x), round(y), round(shape.width * scale * px), round(shape.height * scale * px))


def _full_key(path: str) -> str:
    return fold(str(path)).replace("/", "\\").rstrip("\\")


def _name_key(path: str) -> str:
    """The file name of a path or a web address ('BOD%20T10.pptx' -> 'bod t10.pptx'), folded."""
    from urllib.parse import unquote
    return fold(unquote(re.split(r"[\\/]", str(path).rstrip("/\\"))[-1]))


class PowerPointBridge:
    def __init__(self, app=None, launch=None):
        self._app = app
        self._launch = launch or (lambda argv: __import__("subprocess").Popen(argv, close_fds=True))
        self.pres = None
        self.show = None
        self.last_error = ""
        self.how = ""                       # how the show window was confirmed: path / name / presentation
        self.show_is_theirs = False          # the presenter's own show, found by attach(): never closed by Clicky
        self.shows_seen, self.same_name = 0, 0

    def app(self):
        if self._app is None:
            import win32com.client          # Windows only
            self._app = win32com.client.Dispatch("PowerPoint.Application")
        return self._app

    def open(self, path: str):
        self.pres = self.app().Presentations.Open(path, True, False, True)   # ReadOnly, Untitled, WithWindow
        return self.pres.Slides.Count

    def start(self, first: int = 1):
        try:
            self.app().Visible = -1         # msoTrue: a PowerPoint that COM started stays hidden, so would its show
        except Exception:                   # noqa: BLE001 - already visible, or this PowerPoint will not say
            pass
        settings = self.pres.SlideShowSettings
        settings.StartingSlide = first
        self.show = settings.Run()
        return self.show

    def attach(self, path: str) -> bool:
        """Follow the presenter's own slide show of this deck when one is running (True); else open the deck
        read-only without starting a show (False), so pre-checks can read it before anything is shown."""
        try:
            window, how = self._show_of(path, own=False)
            if window is not None:
                self.pres, self.show, self.how = window.Presentation, window, how
                self.show_is_theirs = True
                return True
        except Exception:                   # noqa: BLE001 - no show running is the normal case
            pass
        self.open(path)
        return False

    def find_show(self, path: str) -> bool:
        """A slide show of this deck on screen, confirmed through PowerPoint itself, never by a window class
        name (it differs between versions): follow it. self.how says which way it was found."""
        try:
            window, how = self._show_of(path)
        except Exception as exc:            # noqa: BLE001 - kept as the reason if the show never comes
            self.last_error = f"{type(exc).__name__}"
            return False
        if window is None:
            return False
        try:
            self.pres = window.Presentation
        except Exception:                   # noqa: BLE001 - keep the copy Clicky opened
            pass
        self.show, self.how = window, how
        return True

    def _show_of(self, path: str, own: bool = True) -> tuple:
        """(slide-show window, how) for this deck, or (None, ""). In order:
        'path'  a show whose deck has this full path;
        'name'  the one show whose deck has this file name: PowerPoint gives a OneDrive or SharePoint
                deck's full name as a web address, and a mapped drive or short path reads differently;
        'presentation'  the deck Clicky opened says it has a show (Presentation.SlideShowWindow)."""
        want, name = _full_key(path), _name_key(path)
        app = self.app()
        count = int(app.SlideShowWindows.Count)
        self.shows_seen, self.same_name, named = count, 0, []
        for i in range(1, count + 1):
            window = app.SlideShowWindows(i)
            deck = window.Presentation
            if _full_key(str(deck.FullName)) == want:
                return window, "path"
            if _name_key(str(getattr(deck, "Name", "") or deck.FullName)) == name:
                named.append(window)
        self.same_name = len(named)
        if len(named) == 1:
            return named[0], "name"
        if own and count and self.pres is not None:
            try:
                window = self.pres.SlideShowWindow
                _ = window.View.State
                return window, "presentation"
            except Exception:               # noqa: BLE001 - this copy has no show yet
                pass
        return None, ""

    def restart(self, path: str, attempt: int = 1) -> bool:
        """Start the show again when none came. True only when PowerPoint then reports a show of this deck
        (SlideShowWindows), not merely because the call returned; last_error says why when it refused.
        Attempt 1: bring PowerPoint and the deck's window forward, then Run again (robot 07/11 on a cold
        PowerPoint: Run returned, no show ever appeared, the deck sat in editing view).
        Attempt 2: PowerPoint's own command line, POWERPNT /S, which opens the deck straight into a show."""
        try:
            try:
                _ = self.pres.FullName
            except Exception:               # noqa: BLE001 - no copy open, or it was closed
                self.pres = None
            if attempt >= 2:
                self._command_line_show(path)
            else:
                if self.pres is None:
                    self.open(path)
                try:
                    self.pres.Windows(1).Activate()
                except Exception:           # noqa: BLE001 - no document window to bring forward
                    pass
                self.start(1)
        except Exception as exc:            # noqa: BLE001 - said to the presenter, never a traceback
            self.last_error = f"{type(exc).__name__}"
            return False
        return self.find_show(path)

    def _command_line_show(self, path: str) -> None:
        """Close the read-only copy Clicky opened (never saved: it was only read), then ask PowerPoint to
        show the deck from its command line. The show is followed through SlideShowWindows as usual."""
        import os
        if self.pres is not None and not self.show_is_theirs:
            try:
                self.pres.Close()
            except Exception:               # noqa: BLE001 - already gone
                pass
            self.pres = None
        exe = os.path.join(str(self.app().Path), "POWERPNT.EXE")
        if os.path.exists(exe):
            self._launch([exe, "/S", str(path)])
        else:
            os.startfile(str(path), "show")  # the deck's own "Show" verb runs the same command

    def count(self) -> int:
        return int(self.pres.Slides.Count)

    def running(self) -> bool:
        try:
            return self.show is not None and int(self.show.View.State) != 5     # ppSlideShowDone
        except Exception:                   # noqa: BLE001 - the show window is gone
            return False

    def window_pixels(self) -> Optional[tuple]:
        """(left, top, width, height) of the slide-show window in physical pixels, from Windows itself."""
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(int(self.show.HWND), ctypes.byref(rect)):
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:                   # noqa: BLE001 - fall back to PowerPoint's points
            pass
        return None

    def shape_pixels(self, shape: ShapeInfo) -> tuple:
        """Physical (left, top, right, bottom) of a shape on the show, ready for physical_to_logical."""
        ps = self.pres.PageSetup
        win = self.window_pixels()
        if win is None:
            x, y, w, h = self.screen_rect(shape)
        else:
            x, y, w, h = to_screen(shape, float(ps.SlideWidth), float(ps.SlideHeight), *win, dpi=72)
        return (x, y, x + w, y + h)

    def focus_show(self) -> None:
        """Give the keyboard back to the slide show, so Space and the clicker move slides again."""
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self.show.HWND))
        except Exception:                   # noqa: BLE001
            pass

    def goto(self, number: int) -> int:
        self.show.View.GotoSlide(number)
        return int(self.show.View.Slide.SlideIndex)

    def current(self) -> int:
        return int(self.show.View.Slide.SlideIndex)

    def slide_shapes(self, number: int) -> list:
        slide = self.pres.Slides(number)
        return [describe(slide.Shapes(i), i) for i in range(1, slide.Shapes.Count + 1)]

    def title(self, number: int) -> str:
        t = find(self.slide_shapes(number), kind="tiêu đề")
        return t.text.strip() if t else ""

    def screen_rect(self, shape: ShapeInfo, dpi: int = 96) -> tuple:
        ps = self.pres.PageSetup
        w = self.show
        return to_screen(shape, float(ps.SlideWidth), float(ps.SlideHeight), float(w.Left), float(w.Top),
                         float(w.Width), float(w.Height), dpi)

    def end(self):
        try:
            if self.show is not None:
                self.show.View.Exit()
        finally:
            self.show = None
