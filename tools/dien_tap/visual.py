"""Checking with eyes: did the frame, the card and the pointer land where a person needs them?

At every scene the robot takes its own screenshot and asks Excel itself where the cell is
(never trusting Clicky's numbers), then measures:
  - the pixel error between the frame Clicky drew and the real cell;
  - whether the card or the pointer covers any figure on the sheet;
  - whether Clicky's idea of the cell matches what Excel tells the robot.

The geometry and image rules are pure functions (tested on any machine); Watcher is the
Windows-only part that grabs the screen and talks to Excel while Clicky runs.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

FRAME_PAD = 5          # logical px between the cell and the frame (ui/overlay.py _paint_spotlight)
MAX_RECT_ERR = 3.0     # px: Clicky's rect vs the robot's own reading of Excel
MAX_FRAME_ERR = 3.0    # px: frame drawn on screen vs where it should be
POINTER_R = 12.0       # logical px around the pointer tip that must stay off figures
RING_R = 37.0          # logical px: outer edge of the pulsing ring (26 + 6 pulse + 3 halo + 2 pen, ui/overlay.py)
NO_BUBBLE = "\u2063"   # ui/card_layout.NO_BUBBLE: point without bubble or ring
MAX_POINTER_GAP = 4.0  # logical px: the pointer tip touches the frame, it does not float beside it
TONE_RGB = {"bad": (0xD9, 0x38, 0x48), "ok": (0x1E, 0x9E, 0x5A), "warn": (0xD9, 0x77, 0x06),
            "info": (0x2F, 0x6F, 0xEB)}


# ── Geometry ──────────────────────────────────────────────────────────────────

def mapping(excel_rect, box):
    """Logical→physical (sx, ox, sy, oy) from one rect seen in both spaces."""
    l, t, r, b = box
    pl, pt, pr, pb = excel_rect
    sx = (pr - pl) / max(r - l, 1e-6)
    sy = (pb - pt) / max(b - t, 1e-6)
    return sx, pl - l * sx, sy, pt - t * sy


def to_physical(rect, m):
    sx, ox, sy, oy = m
    l, t, r, b = rect
    return [l * sx + ox, t * sy + oy, r * sx + ox, b * sy + oy]


def point_physical(x, y, m):
    sx, ox, sy, oy = m
    return x * sx + ox, y * sy + oy


def overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def area(a) -> float:
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def rect_error(a, b) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def gap_to_rect(x, y, rect) -> float:
    """Distance from a point to a rectangle's edge; 0 when on or inside it."""
    l, t, r, b = rect
    dx = max(l - x, 0.0, x - r)
    dy = max(t - y, 0.0, y - b)
    return (dx * dx + dy * dy) ** 0.5


def inside_screen(rect, screen) -> bool:
    return rect[0] >= screen[0] and rect[1] >= screen[1] and rect[2] <= screen[2] and rect[3] <= screen[3]


def cell_rects(cells, addrs) -> list:
    want = set(addrs)
    return [rect for addr, rect, _ in cells if addr in want]


def covered_cells(shape, cells, min_frac: float = 0.25, inside=None) -> list:
    """Addresses of cells holding a value that `shape` covers by at least min_frac of the cell.

    cells: [(address, [l, t, r, b], value)]; cells inside `inside` (the lit range) are skipped,
    they are reported by their own check."""
    hits = []
    for addr, rect, value in cells:
        if value in (None, ""):
            continue
        if inside and overlap(rect, inside) >= 0.5 * area(rect):
            continue
        if area(rect) and overlap(shape, rect) / area(rect) >= min_frac:
            hits.append(addr)
    return hits


# ── Image ─────────────────────────────────────────────────────────────────────

def _near(px, rgb, tol: int = 70) -> bool:
    return sum((a - b) ** 2 for a, b in zip(px[:3], rgb)) <= tol * tol


def frame_offsets(img, rect, rgb, search: int = 10, samples: int = 9):
    """Where the frame's coloured stroke really is, edge by edge.

    For points along each edge of `rect` (screenshot pixels), look perpendicular to the edge
    up to `search` px for the accent colour. Returns (found_fraction, mean_abs_offset_px)."""
    w, h = img.size
    l, t, r, b = rect
    offsets, tried = [], 0
    for k in range(1, samples + 1):
        fx = l + (r - l) * k / (samples + 1)
        fy = t + (b - t) * k / (samples + 1)
        for (x0, y0, dx, dy) in ((fx, t, 0, 1), (fx, b, 0, 1), (l, fy, 1, 0), (r, fy, 1, 0)):
            tried += 1
            best = None
            for d in range(-search, search + 1):
                x, y = int(round(x0 + dx * d)), int(round(y0 + dy * d))
                if 0 <= x < w and 0 <= y < h and _near(img.getpixel((x, y)), rgb):
                    if best is None or abs(d) < abs(best):
                        best = d
            if best is not None:
                offsets.append(abs(best))
    if not offsets:
        return 0.0, None
    return len(offsets) / tried, sum(offsets) / len(offsets)


# ── Grading ───────────────────────────────────────────────────────────────────

@dataclass
class Capture:
    beat_no: int                       # the n-th beat event in the scene log (0-based)
    shot: str = ""                     # screenshot path
    origin: tuple = (0, 0)             # virtual-screen origin of the screenshot
    robot_rect: list = field(default_factory=list)   # physical px, from the robot's own Excel reading
    cells: list = field(default_factory=list)        # [(addr, rect, value)] physical px
    screen: list = field(default_factory=list)       # monitor holding the cell, physical px
    error: str = ""


def _after(events, start, kind):
    for e in events[start + 1:]:
        if e.get("event") == "beat":
            return None
        if e.get("event") == kind:
            return e
    return None


def grade(events: list, captures: list, open_image=None):
    """Visual checks → list of (name, ok, detail, blocking, beat_no, marks) — marks feed the annotated image."""
    beats = [i for i, e in enumerate(events) if e.get("event") == "beat"]
    rows = []
    for cap in captures:
        if cap.beat_no >= len(beats):
            continue
        idx = beats[cap.beat_no]
        beat = events[idx]
        ref = beat.get("ref") or ""
        loc = next((e for e in reversed(events[:idx]) if e.get("event") == "locate" and e.get("ref") == ref), None)
        if not beat.get("box") or not loc or not loc.get("excel_rect"):
            continue
        if cap.error or not cap.robot_rect:
            rows.append(("Đo độc lập vị trí ô", False, f"{ref}: {cap.error or 'không đọc được'}", False, cap.beat_no, {}))
            continue
        m = mapping(loc["excel_rect"], beat["box"])
        robot = cap.robot_rect
        err = rect_error(robot, loc["excel_rect"])
        rows.append(("Clicky tính đúng vị trí ô (so với robot tự hỏi Excel)", err <= MAX_RECT_ERR,
                     f"{ref}: lệch {err:.1f} px", True, cap.beat_no, {"cell": robot}))

        if cap.shot and open_image:
            img = open_image(cap.shot)
            ox, oy = cap.origin
            pad = FRAME_PAD * m[0]
            want = [robot[0] - pad - ox, robot[1] - pad - oy, robot[2] + pad - ox, robot[3] + pad - oy]
            rgb = TONE_RGB.get(_spot_tone(events, idx), TONE_RGB["info"])
            found, off = frame_offsets(img, want, rgb)
            if found < 0.1:
                rows.append(("Thấy khung vẽ trên ảnh chụp", False,
                             f"{ref}: không thấy khung (Clicky có cho quay màn hình không?)", False, cap.beat_no,
                             {"cell": robot}))
            else:
                ok = found >= 0.7 and off is not None and off <= MAX_FRAME_ERR
                rows.append(("Khung vẽ thật nằm đúng ô trên màn hình", ok,
                             f"{ref}: thấy {found:.0%} cạnh khung, lệch trung bình {off:.1f} px", True, cap.beat_no,
                             {"cell": robot}))

        card = _after(events, idx, "card")
        if card and card.get("rect"):
            crect = to_physical(card["rect"], m)
            on_range = overlap(crect, robot) > 0
            hidden = covered_cells(crect, cap.cells, inside=robot)
            ok = not on_range and not hidden
            detail = "thẻ đè lên chính vùng đang giải thích" if on_range else (
                "thẻ che " + ", ".join(hidden[:6]) + ("…" if len(hidden) > 6 else "") if hidden else "thẻ không che số nào")
            rows.append(("Thẻ không che con số", ok, f"{ref}: {detail}", True, cap.beat_no,
                         {"cell": robot, "card": crect, "hidden_rects": cell_rects(cap.cells, hidden)}))

        point = _after(events, idx, "point")
        if point:
            px, py = point_physical(point["x"], point["y"], m)
            rad = POINTER_R * m[0]
            disc = [px - rad, py - rad, px + rad, py + rad]
            on_figure = overlap(disc, robot) > 0.3 * area(disc)
            pad = FRAME_PAD * m[0]
            frame = [robot[0] - pad, robot[1] - pad, robot[2] + pad, robot[3] + pad]
            gap = gap_to_rect(px, py, frame) / m[0]
            rows.append(("Con trỏ chạm mép khung", not on_figure and gap <= MAX_POINTER_GAP,
                         f"{ref}: " + ("con trỏ đè lên chính con số" if on_figure else
                                       f"mũi con trỏ cách khung {gap:.0f} px (cần ≤ {MAX_POINTER_GAP:.0f})"),
                         True, cap.beat_no, {"cell": robot, "pointer": disc}))
            # A pointer that only marks a framed range carries no ring (label NO_BUBBLE, ui/overlay.py point_at).
            ring_r = (POINTER_R if point.get("label") == NO_BUBBLE else RING_R) * m[0]
            ring = [px - ring_r, py - ring_r, px + ring_r, py + ring_r]
            hidden = covered_cells(ring, cap.cells, min_frac=0.15, inside=robot)
            rows.append(("Con trỏ và vòng tròn không che số khác", not hidden,
                         f"{ref}: " + ("che " + ", ".join(hidden[:4]) + ("…" if len(hidden) > 4 else "")
                                       if hidden else "không che số nào"),
                         True, cap.beat_no, {"cell": robot, "pointer": ring, "hidden_rects": cell_rects(cap.cells, hidden)}))
            if cap.screen:
                ok = inside_screen(ring, cap.screen)
                rows.append(("Vòng tròn nằm trọn trong màn hình", ok,
                             f"{ref}: " + ("vòng tròn bị cắt ở mép màn hình" if not ok else "trọn trong màn hình"),
                             True, cap.beat_no, {"cell": robot, "pointer": ring}))
    return rows


def _spot_tone(events, idx):
    spot = _after(events, idx, "draw")
    for e in events[idx + 1:]:
        if e.get("event") == "beat":
            break
        shape = e.get("shape") or {}
        if e.get("event") == "draw" and shape.get("kind") == "spotlight":
            return shape.get("tone")
    return (spot or {}).get("shape", {}).get("tone")


def annotate(shot: str, origin, marks: dict, out: Path) -> None:
    """Draw what was expected (green cell), the card (red), hidden cells (orange) and the pointer (blue)."""
    from PIL import Image, ImageDraw
    img = Image.open(shot).convert("RGB")
    d = ImageDraw.Draw(img)
    ox, oy = origin

    def box(r, colour, width=3):
        d.rectangle([r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy], outline=colour, width=width)
    if marks.get("cell"):
        box(marks["cell"], (0, 200, 0))
    if marks.get("card"):
        box(marks["card"], (230, 0, 0))
    if marks.get("pointer"):
        box(marks["pointer"], (0, 90, 255))
    for r in marks.get("hidden_rects", []):
        box(r, (255, 140, 0), 2)
    img.save(out)


# ── Windows: grab the screen and ask Excel at every scene ─────────────────────

class Watcher(threading.Thread):
    """Follows the scene log; ~1 s after each new beat, screenshots and reads Excel itself."""

    def __init__(self, log: Path, out: Path, settle: float = 1.0):
        super().__init__(daemon=True)
        self.log, self.out, self.settle = log, out, settle
        self.captures: list = []
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            seen = 0
            while not self._halt.is_set():
                beats = [e for e in _read(self.log) if e.get("event") == "beat"]
                while seen < len(beats):
                    beat = beats[seen]
                    if beat.get("box") and beat.get("ref"):
                        time.sleep(self.settle)
                        self.captures.append(self._capture(seen, beat["ref"]))
                    seen += 1
                time.sleep(0.25)
        finally:
            pythoncom.CoUninitialize()

    def _capture(self, n: int, ref: str) -> Capture:
        cap = Capture(beat_no=n)
        try:
            from PIL import ImageGrab
            import win32api
            cap.origin = (win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77))
            path = self.out / f"canh-{n:02d}.png"
            ImageGrab.grab(all_screens=True).save(path)
            cap.shot = str(path)
        except Exception as exc:
            cap.error = f"không chụp được màn hình: {exc}"
        try:
            cap.robot_rect, cap.cells = excel_geometry(ref)
            cap.screen = monitor_of(cap.robot_rect)
        except Exception as exc:
            cap.error = (cap.error + "; " if cap.error else "") + f"không đọc được Excel: {exc}"
        return cap


def _read(path: Path) -> list:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def monitor_of(rect) -> list:
    """Work area of the monitor showing the middle of `rect` (physical px)."""
    import win32api
    cx, cy = int((rect[0] + rect[2]) / 2), int((rect[1] + rect[3]) / 2)
    mon = win32api.MonitorFromPoint((cx, cy), 2)        # MONITOR_DEFAULTTONEAREST
    return [float(v) for v in win32api.GetMonitorInfo(mon)["Monitor"]]


def _dpi(hwnd) -> float:
    try:
        import ctypes
        return float(ctypes.windll.user32.GetDpiForWindow(hwnd)) or 96.0
    except Exception:
        return 96.0


def excel_geometry(ref: str, max_rows: int = 80, max_cols: int = 20):
    """The robot's own reading of Excel: the ref's screen rect and every visible non-empty cell (physical px)."""
    import win32com.client
    app = win32com.client.GetActiveObject("Excel.Application")
    win = app.ActiveWindow
    ws = app.ActiveSheet
    sheet, _, addr = ref.rpartition("!")
    target = ws.Range(addr.replace("$", ""))
    zoom = win.Zoom / 100.0
    dpi = _dpi(app.Hwnd)

    def rect_of(pane, rng):
        l = pane.PointsToScreenPixelsX(rng.Left)
        t = pane.PointsToScreenPixelsY(rng.Top)
        r = pane.PointsToScreenPixelsX(rng.Left + rng.Width)
        b = pane.PointsToScreenPixelsY(rng.Top + rng.Height)
        expect = rng.Width * zoom * dpi / 72.0           # same guard as Clicky: some builds ignore zoom here
        if expect > 4 and not 0.7 <= (r - l) / expect <= 1.3:
            k = expect / max(r - l, 1)
            r, b = l + (r - l) * k, t + (b - t) * k
        return [float(l), float(t), float(r), float(b)]

    robot_rect, cells = None, []
    for i in range(1, win.Panes.Count + 1):
        pane = win.Panes(i)
        vis = pane.VisibleRange
        if robot_rect is None and app.Intersect(vis, target) is not None:
            robot_rect = rect_of(pane, target)
        used = app.Intersect(vis, ws.UsedRange)
        if used is None:
            continue
        nr, nc = min(used.Rows.Count, max_rows), min(used.Columns.Count, max_cols)
        values = used.Value2
        if not isinstance(values, tuple):
            values = ((values,),)
        lefts = [rect_of(pane, used.Columns(c)) for c in range(1, nc + 1)]
        tops = [rect_of(pane, used.Rows(r)) for r in range(1, nr + 1)]
        for r in range(nr):
            for c in range(nc):
                v = values[r][c] if r < len(values) and c < len(values[r]) else None
                if v in (None, ""):
                    continue
                cell = used.Cells(r + 1, c + 1)
                cells.append((cell.Address.replace("$", ""),
                              [lefts[c][0], tops[r][1], lefts[c][2], tops[r][3]], v))
    if robot_rect is None:
        raise RuntimeError(f"{ref} không nằm trong vùng đang hiện")
    return robot_rect, cells
