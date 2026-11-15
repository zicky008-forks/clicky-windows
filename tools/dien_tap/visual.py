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


# ── Is Excel really what a person sees at the cell? ───────────────────────────
# A frame Clicky draws at the right Excel coordinates proves nothing when another app sits on top of
# Excel: the frame then circles that app (seen on the user's PC, 15/11). So at every scene the robot
# asks Windows which top-level window a person sees at the cell, and runs one cover test per lap.

COVER_TITLE = "Robot dien tap - thu che Excel"
WHITE_TOL = 40          # a pixel this close to white counts as the cover's own background
MAX_LEAK = 0.03         # share of non-white pixels allowed where the card or pointer would be on the cover


def probe_points(rect, inset: float = 3.0) -> list:
    """The cell's middle and four inner corners."""
    l, t, r, b = rect
    inset = min(inset, max((r - l) / 4, 0), max((b - t) / 4, 0))
    return [((l + r) / 2, (t + b) / 2), (l + inset, t + inset), (r - inset, t + inset),
            (l + inset, b - inset), (r - inset, b - inset)]


def window_at(windows: list, x: float, y: float, skip_pids=()) -> dict | None:
    """First window, in Z-order top→bottom, that a person sees at (x, y).

    windows: [{"hwnd", "pid", "rect", "exe", "visible", "cloaked", "minimized", "click_through"}]. Clicky's own
    windows (skip_pids) and click-through overlays are skipped: they are what is being checked, not a cover."""
    for w in windows:
        if not w.get("visible", True) or w.get("cloaked") or w.get("minimized") or w.get("click_through"):
            continue
        if w.get("pid") in skip_pids:
            continue
        l, t, r, b = w["rect"]
        if r - l <= 0 or b - t <= 0:
            continue
        if l <= x < r and t <= y < b:
            return w
    return None


def excel_shown(windows: list, rect, excel_hwnd: int, skip_pids=()) -> tuple:
    """(ok, detail): at every probe point of the cell a person sees Excel's own window.

    The detail names the covering program by its file name only, never a window title (titles can carry
    the user's own file or chat names)."""
    for x, y in probe_points(rect):
        w = window_at(windows, x, y, skip_pids)
        if w is None:
            return False, "ô nằm ngoài mọi cửa sổ (Excel thu nhỏ hoặc ô ngoài màn hình)"
        if w.get("hwnd") != excel_hwnd:
            return False, f"Excel bị cửa sổ {w.get('exe') or 'khác'} che ở ô này"
    return True, "Excel hiện rõ ở ô"


def leak_share(img, rect, clip, step: int = 3) -> float | None:
    """Share of non-white pixels of `rect` inside `clip` (screenshot pixels); None when they do not meet."""
    l, t = max(rect[0], clip[0]), max(rect[1], clip[1])
    r, b = min(rect[2], clip[2]), min(rect[3], clip[3])
    w, h = img.size
    l, t, r, b = max(int(l), 0), max(int(t), 0), min(int(r), w), min(int(b), h)
    if r - l < 4 or b - t < 4:
        return None
    total = bad = 0
    for y in range(t, b, step):
        for x in range(l, r, step):
            total += 1
            if not _near(img.getpixel((x, y)), (255, 255, 255), WHITE_TOL):
                bad += 1
    return bad / total if total else None


def grade_cover(events: list, cap: "Capture", beat_idx: int, m, open_image) -> list:
    """Rows for the cover test: while the robot's plain window sits on top of Excel, nothing of Clicky shows."""
    rows = []
    cov = cap.cover
    ref = events[beat_idx].get("ref") or ""
    if cov.get("error") or not cov.get("shot"):
        rows.append(("Che Excel bằng cửa sổ khác: Clicky ẩn thẻ, khung, con trỏ", False,
                     f"{ref}: robot không làm được bài che ({cov.get('error') or 'không có ảnh'})", True,
                     cap.beat_no, {}))
        return rows
    img = open_image(cov["shot"])
    ox, oy = cov.get("origin", (0, 0))
    clip = [cov["rect"][0] - ox, cov["rect"][1] - oy, cov["rect"][2] - ox, cov["rect"][3] - oy]
    robot = cap.robot_rect
    leaks = []
    pad = FRAME_PAD * m[0]
    want = [robot[0] - pad - ox, robot[1] - pad - oy, robot[2] + pad - ox, robot[3] + pad - oy]
    for tone in TONE_RGB.values():
        found, _ = frame_offsets(img, want, tone)
        if found >= 0.3:
            leaks.append(f"khung vẫn hiện ({found:.0%} cạnh)")
            break
    card = _after(events, beat_idx, "card")
    if card and card.get("rect"):
        c = to_physical(card["rect"], m)
        share = leak_share(img, [c[0] - ox, c[1] - oy, c[2] - ox, c[3] - oy], clip)
        if share is not None and share > MAX_LEAK:
            leaks.append(f"thẻ vẫn hiện ({share:.0%} điểm ảnh)")
    point = _after(events, beat_idx, "point")
    if point:
        px, py = point_physical(point["x"], point["y"], m)
        rad = RING_R * m[0]
        share = leak_share(img, [px - rad - ox, py - rad - oy, px + rad - ox, py + rad - oy], clip)
        if share is not None and share > MAX_LEAK:
            leaks.append(f"con trỏ vẫn hiện ({share:.0%} điểm ảnh)")
    rows.append(("Che Excel bằng cửa sổ khác: Clicky ẩn thẻ, khung, con trỏ", not leaks,
                 f"{ref}: " + ("; ".join(leaks) if leaks else "không còn gì của Clicky trên cửa sổ che"),
                 True, cap.beat_no, {"cell": robot}))
    hid = any(e.get("event") == "overlay_hidden" for e in events)
    rows.append(("Clicky ghi nhận bị che (overlay_hidden)", hid,
                 "có" if hid else "nhật ký cảnh không có overlay_hidden", False, cap.beat_no, {}))
    if cov.get("after_shot"):
        after = open_image(cov["after_shot"])
        found = max(frame_offsets(after, want, tone)[0] for tone in TONE_RGB.values())
        stamp = {e.get("event") for e in events}
        back = found >= 0.5 or "overlay_shown" in stamp
        rows.append(("Mở lại Excel: Clicky hiện lại và chạy tiếp", back,
                     f"{ref}: " + ("hiện lại" if back else "không thấy khung hay overlay_shown sau khi bỏ che"),
                     False, cap.beat_no, {"cell": robot}))
    return rows


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
    shown: bool | None = None          # Excel's own window is the one a person sees at the cell (None = not read)
    shown_detail: str = ""
    cover: dict = field(default_factory=dict)        # the robot's cover test: {shot, origin, rect, after_shot, error}


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
        if cap.cover and open_image:
            rows += grade_cover(events, cap, idx, m, open_image)
        if cap.shown is None:
            rows.append(("Excel thật sự hiện ở ô đang chỉ (không bị cửa sổ khác che)", False,
                         f"{ref}: robot không đọc được cửa sổ nào đang hiện ({cap.shown_detail or 'không rõ'})",
                         True, cap.beat_no, {"cell": robot}))
            continue
        rows.append(("Excel thật sự hiện ở ô đang chỉ (không bị cửa sổ khác che)", cap.shown,
                     f"{ref}: {cap.shown_detail}", True, cap.beat_no, {"cell": robot}))
        if not cap.shown:
            continue    # a frame "on the cell" over another app proves nothing: the scene is not graded further

        # A sticky-notes scene lights no range on purpose (plain graphic): there is no frame to find.
        plain = (beat.get("graphic") or {}).get("kind") == "notes"
        if cap.shot and open_image and not plain:
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

    def __init__(self, log: Path, out: Path, settle: float = 1.0, cover_beat: int | None = 1,
                 cover_seconds: float = 2.5):
        super().__init__(daemon=True)
        self.log, self.out, self.settle = log, out, settle
        self.cover_beat, self.cover_seconds = cover_beat, cover_seconds
        self.captures: list = []
        self._framed = 0
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
                        cap = self._capture(seen, beat["ref"])
                        if self.cover_beat is not None and self._framed == self.cover_beat and cap.shown:
                            cap.cover = self._cover(seen)
                        self._framed += 1
                        self.captures.append(cap)
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
        if cap.robot_rect:
            try:
                cap.shown, cap.shown_detail = excel_shown(zorder_windows(), cap.robot_rect, excel_hwnd(),
                                                          skip_pids=clicky_pids())
            except Exception as exc:
                cap.shown, cap.shown_detail = None, f"{exc}"
        return cap

    def _cover(self, n: int) -> dict:
        """Put a plain window of the robot's own over Excel, as a person switching apps would; shoot; take it away."""
        import subprocess
        import sys
        from PIL import ImageGrab
        import win32api
        res: dict = {}
        proc = None
        try:
            rect = window_rect(excel_hwnd())
            res["rect"] = rect
            l, t, r, b = (int(v) for v in rect)
            proc = subprocess.Popen([sys.executable, "-c", COVER_SCRIPT, str(l), str(t), str(r - l), str(b - t)])
            time.sleep(self.cover_seconds)
            res["origin"] = (win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77))
            path = self.out / f"che-{n:02d}.png"
            ImageGrab.grab(all_screens=True).save(path)
            res["shot"] = str(path)
            fg = foreground_exe()
            if fg and fg.lower() not in ("python.exe", "pythonw.exe", "py.exe"):
                res["error"] = f"cửa sổ che không lên trước được (đang trước: {fg})"
        except Exception as exc:
            res["error"] = f"{exc}"
        finally:
            if proc is not None:
                proc.kill()
                proc.wait(timeout=10)
        try:
            bring_to_front(excel_hwnd())
            time.sleep(2.0)
            path = self.out / f"che-{n:02d}-sau.png"
            ImageGrab.grab(all_screens=True).save(path)
            res["after_shot"] = str(path)
        except Exception as exc:
            res.setdefault("error", f"không đưa Excel lên lại được: {exc}")
        return res


# A plain white window, DPI-aware so its geometry is in physical pixels like Excel's. Brought to the front
# once (topmost for a moment, as Windows lets a new window take the front), then an ordinary window.
COVER_SCRIPT = (
    "import ctypes,sys,tkinter as tk\n"
    "try: ctypes.windll.shcore.SetProcessDpiAwareness(2)\n"
    "except Exception: pass\n"
    "l,t,w,h=(int(v) for v in sys.argv[1:5])\n"
    "root=tk.Tk(); root.title(%r); root.configure(bg='white')\n"
    "root.geometry(f'{w}x{h}+{l}+{t}')\n"
    "root.attributes('-topmost',True); root.lift(); root.focus_force()\n"
    "root.after(400,lambda: root.attributes('-topmost',False))\n"
    "root.after(600,root.focus_force)\n"
    "root.mainloop()\n" % COVER_TITLE)


def _per_monitor_dpi():
    """Make this thread DPI-aware so window rects come in physical pixels, like Excel's cell rects."""
    import ctypes
    try:
        return ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:
        return None


def _exe_of(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        k32.CloseHandle(h)


def window_rect(hwnd: int) -> list:
    """Visible bounds of a top-level window in physical pixels (DWM frame bounds, no drop shadow)."""
    import ctypes
    from ctypes import wintypes
    _per_monitor_dpi()
    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
        return [float(r.left), float(r.top), float(r.right), float(r.bottom)]
    import win32gui
    return [float(v) for v in win32gui.GetWindowRect(hwnd)]


def zorder_windows() -> list:
    """Every top-level window, top of the Z-order first, as window_at() reads them."""
    import ctypes
    from ctypes import wintypes
    import win32con
    import win32gui
    import win32process
    _per_monitor_dpi()
    out = []

    def one(h, _):
        if not win32gui.IsWindowVisible(h):
            return True
        cloaked = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(h), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        _, pid = win32process.GetWindowThreadProcessId(h)
        out.append({"hwnd": h, "pid": pid, "rect": window_rect(h), "exe": _exe_of(pid), "visible": True,
                    "cloaked": bool(cloaked.value), "minimized": bool(win32gui.IsIconic(h)),
                    "click_through": bool(ex & win32con.WS_EX_TRANSPARENT and ex & win32con.WS_EX_LAYERED)})
        return True
    win32gui.EnumWindows(one, None)
    return out


def excel_hwnd() -> int:
    import win32com.client
    return int(win32com.client.GetActiveObject("Excel.Application").Hwnd)


def clicky_pids() -> set:
    from drivers import pids_of
    return set(pids_of("Clicky.exe"))


def foreground_exe() -> str:
    import win32gui
    import win32process
    h = win32gui.GetForegroundWindow()
    if not h:
        return ""
    _, pid = win32process.GetWindowThreadProcessId(h)
    return _exe_of(pid)


def bring_to_front(hwnd: int) -> bool:
    """Put a window in front the way a person's click would. Windows only lets the foreground process hand
    the front over, so a tap on Alt first (the documented way a background process may take it)."""
    import win32api
    import win32con
    import win32gui
    if win32gui.GetForegroundWindow() == hwnd and not win32gui.IsIconic(hwnd):
        return True                     # never tap Alt on Excel itself: it would light the ribbon key tips
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)
    return win32gui.GetForegroundWindow() == hwnd


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
