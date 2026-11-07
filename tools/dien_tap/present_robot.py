"""Diễn tập trình bày: a robot plays the presenter in Trợ lý bên cạnh mode with real PowerPoint and Excel,
then grades the run. Practice (synthetic) files only.

    python tools\\dien_tap\\present_robot.py --spec <thu_muc_kich_ban> --xlsx <gia_lap.xlsx> --deck <gia_lap.pptx>

What it does, like a presenter would:
 1. refuses to run while PowerPoint or Excel has any other file open (real company files stay untouched);
 2. copies the workbook and the deck into the output folder (the originals stay clean);
 3. turns on "Cho phép quay màn hình Clicky" for the run (Clicky hides itself from screenshots otherwise)
    and puts the old setting back at the end;
 4. starts Clicky with --present-* on the copies, scene log on, CLICKY_LOCAL_ONLY=1;
 5. presses PageDown on the slide show, slide by slide, like a clicker; after each slide it screenshots all
    screens and looks for Clicky's frame around every shape the script names;
 6. presses Ctrl+Alt+H, types each sample question of the bank into "Câu hỏi", presses "Hỏi", reads which
    answer Clicky picked, then presses Ctrl+Alt+B;
 7. ends the show, checks the minutes were written and the workbook copy was not changed;
 8. writes ket-qua.md + ket-qua.json + scene.jsonl + anh/*.png.

Pass gate (ĐẠT): pre-checks pass, every slide followed with every named shape framed (and the frame seen
on the screenshot), every bank question routed to its own entry, Ctrl+Alt+B returns to the slide before
the question, the projector never shows the ask box (two screens), the workbook copy unchanged.
Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

FRAME_RGB = (0x33, 0x80, 0xFF)          # ui/overlay.py ANNOT_COLORS["blue"]
ASK_BOX, ASK_BUTTON = "Câu hỏi", "Hỏi"
VK_NEXT, VK_CONTROL, VK_MENU = 0x22, 0x11, 0x12


def say(text: str) -> None:
    print(text, flush=True)


# ── Pure parts (tested on any PC) ─────────────────────────────────────────────

def expected_targets(script: dict) -> dict:
    """slide -> number of shapes the script points at on it (Excel targets are not on the slide)."""
    out = {}
    for item in script.get("slides", []):
        out[int(item["so"])] = sum(1 for t in item.get("chi_vao") or [] if "excel" not in t)
    return out


def sample_questions(bank: dict) -> list:
    """(entry id, sample question) for every bank entry that has one."""
    return [(e["id"], e["hoi_mau"]) for e in bank.get("cau_hoi", []) if e.get("id") and e.get("hoi_mau")]


def read_events(path: Path) -> list:
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


@dataclass
class Line:
    name: str
    ok: bool
    detail: str = ""
    unchecked: bool = False     # could not be checked on this build: shown as such, never as ĐẠT


@dataclass
class Result:
    lines: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.lines) and all(line.ok for line in self.lines)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.lines.append(Line(name, ok, detail))

    def unchecked(self, name: str, why: str) -> None:
        """A check this build cannot feed yet. It does not fail the run, but it is never silent."""
        self.lines.append(Line(name, True, why, unchecked=True))

    def markdown(self, title: str) -> str:
        out = [f"# {title}: {'ĐẠT' if self.ok else 'KHÔNG ĐẠT'}", ""]
        out += [f"- {'CHƯA KIỂM ĐƯỢC' if l.unchecked else 'ĐẠT' if l.ok else 'KHÔNG ĐẠT'}  {l.name}"
                + (f": {l.detail}" if l.detail else "") for l in self.lines]
        return "\n".join(out) + "\n"


def grade(events: list, script: dict, bank: dict, frames_seen: dict, asked: list, two_screens: bool,
          panel_on_projector: bool, unchanged: bool) -> Result:
    """events: the scene log. frames_seen: slide -> (frames found on screenshot, frames expected).
    asked: [(entry id, question, slide before, slide after back)]."""
    res = Result()
    pre = [e for e in events if e.get("event") == "present_preflight"]
    res.add("Kiểm tra trước", bool(pre) and pre[-1].get("ok") is True,
            "" if pre and pre[-1].get("ok") else "; ".join(l for l in (pre[-1].get("lines") if pre else []) if l.startswith("KHÔNG"))
            or "không thấy kết quả kiểm tra trước")
    start = [e for e in events if e.get("event") == "present_start"]
    res.add("Bắt đầu trình chiếu", bool(start), start[-1].get("mode", "") if start else "Clicky không bắt đầu")
    want = expected_targets(script)
    slides = {}
    for e in events:
        if e.get("event") == "present_slide":
            slides.setdefault(int(e["slide"]), e)          # first time the slide was shown, while following
    followed, framed, bad = 0, 0, []
    for n, count in sorted(want.items()):
        e = slides.get(n)
        if e is None:
            bad.append(f"slide {n}: Clicky không theo")
            continue
        followed += 1
        problems = list(e.get("missed") or []) + list(e.get("missing") or [])
        if len(e.get("targets") or []) != count or problems:
            bad.append(f"slide {n}: chỉ {len(e.get('targets') or [])}/{count}" + (f" ({'; '.join(problems)})" if problems else ""))
            continue
        seen, expected = frames_seen.get(n, (count, count))
        if seen < expected:
            bad.append(f"slide {n}: ảnh chụp chỉ thấy {seen}/{expected} khung")
            continue
        framed += 1
    res.add(f"Theo slide {followed}/{len(want)}, chỉ đúng {framed}/{len(want)}", framed == len(want) and len(want) > 0,
            "; ".join(bad))
    answers = [e for e in events if e.get("event") == "present_answer"]
    routed, wrong = 0, []
    for i, (entry_id, question, before, after) in enumerate(asked):
        a = next((x for x in answers if x.get("question") == question), None)
        if a is None:
            wrong.append(f"{entry_id}: không có trả lời")
            continue
        ids = [a.get("id")] + [c[0] for c in a.get("choices") or [] if isinstance(c, (list, tuple)) and c]
        if entry_id not in ids or a.get("stop"):
            wrong.append(f"{entry_id}: vào {a.get('id') or 'câu hỏi mở'}" + (f", dừng: {a['stop']}" if a.get("stop") else ""))
            continue
        if before != after:
            wrong.append(f"{entry_id}: Ctrl+Alt+B về slide {after}, không phải {before}")
            continue
        routed += 1
    res.add(f"Hỏi đáp {routed}/{len(asked)}", routed == len(asked) and len(asked) > 0, "; ".join(wrong))
    keys = [e.get("key") for e in events if e.get("event") == "present_key"]
    res.add("Phím Ctrl+Alt+H và Ctrl+Alt+B", "ask" in keys and "back" in keys,
            "" if "ask" in keys and "back" in keys else f"Clicky nhận được: {keys or 'không phím nào'}")
    if two_screens:
        res.add("Màn chiếu không có ô hỏi", not panel_on_projector, "ô hỏi hiện trên màn chiếu" if panel_on_projector else "")
    end = [e for e in events if e.get("event") == "present_end"]
    res.add("Biên bản buổi trình bày", bool(end and end[-1].get("minutes")), "" if end else "Clicky không ghi biên bản")
    res.add("File Excel không bị đổi", unchanged, "" if unchanged else "bản sao Excel đã bị thay đổi sau buổi tập")
    return res


# ── Windows parts ─────────────────────────────────────────────────────────────

def _key(*vks) -> None:
    import win32api
    import win32con
    for vk in vks:
        win32api.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)


def other_files_open() -> list:
    """Names of files PowerPoint or Excel already have open (the robot never runs next to them)."""
    import win32com.client
    names = []
    for prog, coll in (("PowerPoint.Application", "Presentations"), ("Excel.Application", "Workbooks")):
        try:
            app = win32com.client.GetActiveObject(prog)
        except Exception:                     # noqa: BLE001 - not running = nothing open
            continue
        names += [str(x.Name) for x in getattr(app, coll)]
    return names


def file_hash(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_clicky() -> str | None:
    import glob
    base = os.environ.get("LOCALAPPDATA", "")
    hits = sorted(glob.glob(os.path.join(base, "*", "current", "Clicky.exe")), key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def wait_event(log: Path, kind: str, timeout: float, where=lambda e: True) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for e in read_events(log):
            if (not kind or e.get("event") == kind) and where(e):
                return e
        time.sleep(0.25)
    return None


def show_outcome(log: Path, timeout: float = 40.0) -> str:
    """'attached' / 'failed' from Clicky's own events (BHC fb08855: two attempts, 15 s grace; BHC asks ≥ 30 s),
    or 'unknown' on an older build that logs neither, which then falls back to waiting for the window."""
    e = wait_event(log, "", timeout, where=lambda e: e.get("event") in ("present_attached", "present_failed"))
    if e is None:
        return "unknown"
    return "failed" if e.get("event") == "present_failed" else "attached"


FRAME_DRAW_MAX_S = 2.2      # ui/overlay.py SHAPE_DRAW_MAX_S: frames are drawn one after another, by hand


def draw_seconds(boxes: list, settle: float) -> float:
    """How long the frames of one slide take to finish drawing: each rect strokes in for up to 2.2 s,
    one after the other (PC 07/11: slide 3 shot with the first frame half drawn, the second not yet)."""
    return max(settle, 0.5 + FRAME_DRAW_MAX_S * len(boxes or []))


def frames_settle(slide_event, settle: float) -> float:
    """Seconds to wait before the screenshot: until this slide's frames (its rects) are fully drawn."""
    return draw_seconds((slide_event or {}).get("rects") or [], settle)


def window_handle(win) -> int:
    """SlideShowWindow.HWND. Late-bound win32com hands back HWND as a callable that fails with 'Member not
    found' when called as a method (PC, 07/11), so ask for it as a property; the show window's class
    "screenClass" is the last resort."""
    h = win.HWND
    if isinstance(h, int):
        return h
    try:
        import pythoncom
        ole = win._oleobj_
        dispid = ole.GetIDsOfNames("HWND")
        return int(ole.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, True))
    except Exception:                         # noqa: BLE001 - try the next way
        pass
    try:
        return int(h())
    except Exception:                         # noqa: BLE001
        pass
    import win32gui
    hwnd = win32gui.FindWindow("screenClass", None)
    if not hwnd:
        raise RuntimeError("không lấy được cửa sổ trình chiếu (HWND)")
    return int(hwnd)


def show_window(wait: float = 20.0, app=None):
    """The running slide show's window. Clicky logs present_start as it asks PowerPoint to start the
    show; the window can take a few seconds to exist (bhc_e2e stage 4 on the PC, 07/11)."""
    if app is None:
        import win32com.client
        app = win32com.client.GetActiveObject("PowerPoint.Application")
    end = time.monotonic() + wait
    while True:
        try:
            if int(app.SlideShowWindows.Count) >= 1:
                return app.SlideShowWindows(1)
        except Exception:                     # noqa: BLE001 - PowerPoint busy while the show opens
            pass
        if time.monotonic() >= end:
            raise RuntimeError(f"PowerPoint chưa vào trình chiếu sau {wait:.0f} giây")
        time.sleep(0.5)


def window_rect(hwnd: int) -> tuple:
    import win32gui
    return win32gui.GetWindowRect(hwnd)


def monitors() -> list:
    import win32api
    return [tuple(m[2]) for m in win32api.EnumDisplayMonitors()]


def monitor_index(rect, mons) -> int:
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    for i, (l, t, r, b) in enumerate(mons):
        if l <= cx < r and t <= cy < b:
            return i
    return -1


def screenshot(path: Path) -> tuple:
    from PIL import ImageGrab
    import win32api
    origin = (win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77))
    ImageGrab.grab(all_screens=True).save(path)
    return origin


def frames_on_shot(shot: Path, origin: tuple, rects: list, pad: int = 8) -> int:
    """How many of Clicky's frames (physical rects from the scene log) are visible on the screenshot."""
    import visual
    from PIL import Image
    img = Image.open(shot).convert("RGB")
    seen = 0
    for l, t, r, b in rects:
        box = (l - origin[0] - pad, t - origin[1] - pad, r - origin[0] + pad, b - origin[1] + pad)
        found, _ = visual.frame_offsets(img, box, FRAME_RGB, search=14)
        seen += found >= 0.5
    return seen


def panel_rect():
    import win32gui
    hwnd = win32gui.FindWindow(None, "Trợ lý trình bày")
    return win32gui.GetWindowRect(hwnd) if hwnd and win32gui.IsWindowVisible(hwnd) else None


def main(argv=None) -> int:
    import yaml
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True, help="thư mục có kich_ban.yaml, hoi_dap.yaml, kiem_tra_truoc.yaml")
    ap.add_argument("--xlsx", required=True, help="file Excel GIẢ LẬP")
    ap.add_argument("--deck", required=True, help="file PowerPoint GIẢ LẬP")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-trinh-bay-<giờ> cạnh file Excel)")
    ap.add_argument("--settle", type=float, default=1.2, help="giây chờ sau mỗi slide trước khi chụp")
    a = ap.parse_args(argv)

    spec = Path(a.spec)
    load = lambda n: yaml.safe_load((spec / n).read_text(encoding="utf-8")) or {}
    script, bank = load("kich_ban.yaml"), load("hoi_dap.yaml")
    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    others = other_files_open()
    if others:
        say(f"Robot không chạy vì PowerPoint/Excel đang mở {len(others)} file khác (robot không đọc tên). Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path(a.xlsx).resolve().parent / f"dien-tap-trinh-bay-{time.strftime('%Y%m%d-%H%M')}"
    shots = out / "anh"
    shots.mkdir(parents=True, exist_ok=True)
    xlsx, deck = out / Path(a.xlsx).name, out / Path(a.deck).name
    shutil.copy(a.xlsx, xlsx)
    shutil.copy(a.deck, deck)
    hash_before = file_hash(xlsx)
    log = out / "scene.jsonl"

    from storage import set_setting, setting
    old_recording = bool(setting("allow_screen_recording", False))
    set_setting("allow_screen_recording", True)
    mons = monitors()
    two_screens = len(mons) >= 2
    env = dict(os.environ, CLICKY_SCENE_LOG=str(log), CLICKY_LOCAL_ONLY="1")
    if not two_screens:
        env["CLICKY_PRESENT_FORCE_PANEL"] = "1"
        say("Máy chỉ có 1 màn hình: robot vẫn tập hỏi đáp; buổi thật cần 2 màn hình (Extend).")
    asked, frames_seen, panel_on_projector = [], {}, False
    try:
        say(f"1/4 Mở Clicky ở chế độ trình bày: {exe}")
        subprocess.Popen([exe, "--present-spec", str(spec), "--present-xlsx", str(xlsx), "--present-deck", str(deck)],
                         env=env, cwd=str(Path(exe).parent))
        pre = wait_event(log, "present_preflight", 120)
        if pre is None or not pre.get("ok"):
            say("Kiểm tra trước chưa đạt hoặc Clicky không trả lời; robot dừng đúng chỗ.")
        elif wait_event(log, "present_start", 60) is None:
            say("Clicky không bắt đầu trình chiếu sau 60 giây.")
        elif show_outcome(log) == "failed":
            say("Clicky báo không mở được trình chiếu (present_failed); robot dừng đúng chỗ.")
            for e in read_events(log):          # what Clicky tried, for the BHC thread (no file names in these)
                if e.get("event") in ("present_retry", "present_failed"):
                    say("   " + json.dumps({k: v for k, v in e.items() if k != "ts"}, ensure_ascii=False))
        else:
            win = show_window()
            hwnd = window_handle(win)
            projector = monitor_index(window_rect(hwnd), mons)
            say("2/4 Bấm PageDown qua từng slide, chụp màn hình, tìm khung")
            want = expected_targets(script)
            total = int(win.Presentation.Slides.Count)
            for n in range(1, total + 1):
                if n > 1:
                    import win32gui
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:            # noqa: BLE001
                        pass
                    _key(VK_NEXT)
                e = wait_event(log, "present_slide", 8, where=lambda ev, n=n: int(ev.get("slide", 0)) == n)
                time.sleep(frames_settle(e, a.settle))
                if e is not None and n in want:
                    shot = shots / f"slide-{n:02d}.png"
                    origin = screenshot(shot)
                    rects = e.get("rects") or []
                    frames_seen[n] = (frames_on_shot(shot, origin, rects), len(rects))
                    p = panel_rect()
                    if two_screens and p and monitor_index(p, mons) == projector:
                        panel_on_projector = True
            say("3/4 Hỏi từng câu mẫu trong ngân hàng câu hỏi")
            import drivers
            driver = drivers.Uia("Clicky.exe")
            for entry_id, question in sample_questions(bank):
                before = int(show_window().View.Slide.SlideIndex)
                _key(VK_CONTROL, VK_MENU, ord("H"))
                if not driver.wait_for(ASK_BOX, 10):
                    asked.append((entry_id, question, before, -1))
                    continue
                driver.set_text(ASK_BOX, question)
                driver.press(ASK_BUTTON)
                wait_event(log, "present_answer", 30, where=lambda ev, q=question: ev.get("question") == question)
                time.sleep(0.5)
                _key(VK_CONTROL, VK_MENU, ord("B"))
                time.sleep(0.8)
                after = int(show_window().View.Slide.SlideIndex)
                asked.append((entry_id, question, before, after))
            say("4/4 Kết thúc trình chiếu, chờ biên bản")
            show_window().View.Exit()
            wait_event(log, "present_end", 20)
    finally:
        set_setting("allow_screen_recording", old_recording)
    unchanged = file_hash(xlsx) == hash_before
    res = grade(read_events(log), script, bank, frames_seen, asked, two_screens, panel_on_projector, unchanged)
    (out / "ket-qua.md").write_text(res.markdown("Diễn tập trình bày"), encoding="utf-8")
    (out / "ket-qua.json").write_text(json.dumps({"ok": res.ok, "lines": [asdict(l) for l in res.lines]},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown("Diễn tập trình bày"))
    say(f"Kết quả: {out}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
