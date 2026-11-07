"""Trợ lý bên cạnh inside the app: glue between present.live (logic), PowerPoint/Excel over COM, the
overlay (projector) and the presenter's box (laptop).

Started from the command line (the robot) or the tray:

    Clicky.exe --present-spec DIR --present-xlsx so_lieu.xlsx --present-deck deck.pptx

DIR holds the client's kich_ban.yaml, hoi_dap.yaml, kiem_tra_truoc.yaml (kept outside this repository).
Order: read the files, run every pre-check (any failure = no talk), attach to the presenter's slide show
(or start it), then follow it. The minutes are written when the show ends.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

import scene_log
from present import hotkeys

log = logging.getLogger(__name__)

POLL_MS = 300
START_GRACE_S = 15.0                   # PowerPoint may need seconds before the show window answers
RETRY_AFTER_S = 3.0                     # then Run again with the deck's window brought forward
COMMAND_LINE_AFTER_S = 7.0              # then PowerPoint's own POWERPNT /S
STARTED_BY = ("run", "run_again", "command_line")   # which start the show came from (present_attached)
NO_SHOW = ("Em chưa mở được PowerPoint để trình chiếu bộ slide này. File slide của anh chị vẫn nguyên. "
           "Anh chị mở PowerPoint, bấm Trình chiếu (F5), rồi gọi lại em.")
FORCE_PANEL = "CLICKY_PRESENT_FORCE_PANEL"     # robot on a one-screen PC: show the box anyway
DUPLICATE_NOTICE = ("Máy đang để chế độ Nhân bản màn hình nên khán giả sẽ thấy ô hỏi. "
                    "Anh chị bấm Win+P, chọn Mở rộng (Extend), rồi bấm Ctrl+Alt+H để hỏi.")


def present_args(argv: list) -> dict | None:
    """{'spec', 'xlsx', 'deck'} when all three --present-* flags are given, else None."""
    out = {}
    for key in ("spec", "xlsx", "deck"):
        flag = f"--present-{key}"
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                out[key] = argv[i + 1]
    return out if len(out) == 3 else None


def load_spec(folder: str) -> tuple:
    import yaml
    spec = Path(folder)
    load = lambda n: (yaml.safe_load((spec / n).read_text(encoding="utf-8")) or {}) if (spec / n).exists() else {}
    return load("kich_ban.yaml"), load("hoi_dap.yaml"), load("kiem_tra_truoc.yaml")


def open_workbook(path: str):
    """The workbook over COM: the copy already open in Excel if there is one, else open it (never saved)."""
    import win32com.client
    excel = win32com.client.Dispatch("Excel.Application")
    want = os.path.normcase(os.path.abspath(path))
    for i in range(1, excel.Workbooks.Count + 1):
        wb = excel.Workbooks(i)
        if os.path.normcase(str(wb.FullName)) == want:
            return wb
    return excel.Workbooks.Open(path)


class PresentMode(QObject):
    sig_ask = pyqtSignal()
    sig_back = pyqtSignal()
    finished = pyqtSignal(str)             # minutes path ("" when the talk never started)

    def __init__(self, manager, spec: str, xlsx: str, deck: str, parent=None):
        super().__init__(parent)
        from ui.present_panel import PresentPanel
        self.manager, self.spec_dir, self.xlsx, self.deck_path = manager, spec, xlsx, deck
        self.panel = PresentPanel()
        self.panel.asked.connect(self._on_asked)
        self.panel.picked.connect(self._on_picked)
        self.panel.dismissed.connect(self._dismiss)
        self.sig_ask.connect(self._on_ask_key)
        self.sig_back.connect(self._on_back_key)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.live = None
        self.bridge = None
        self.keys = None
        self.layout = None
        self.attached = False
        self.total = 0
        self._started = 0.0
        self._seen_running = False
        self._retried = 0

    # ---- start ----------------------------------------------------------------------------
    def start(self) -> bool:
        from present import preflight
        from present.deck import PowerPointBridge
        from present.live import LiveTalk
        from present.placeholders import ComSource, OpenpyxlSource, Resolver
        from present.talk import Minutes
        from present.whatif import WhatIf
        script, bank, checks = load_spec(self.spec_dir)
        try:
            wb = open_workbook(self.xlsx)
            source, whatif = ComSource(wb), WhatIf(wb)
        except Exception as exc:            # noqa: BLE001 - no Excel: read cached values, no what-if
            log.warning("Excel COM unavailable (%s); reading cached values", exc)
            source, whatif = OpenpyxlSource(self.xlsx), None
        resolver = Resolver(source)
        self.bridge = PowerPointBridge()
        try:
            self.attached = self.bridge.attach(self.deck_path)
        except Exception as exc:            # noqa: BLE001 - no PowerPoint: say so, never end quietly
            return self._fail("attach", type(exc).__name__)
        minutes = Minutes()
        safety = {"voice_offline": True, "ai_on": os.environ.get("CLICKY_LOCAL_ONLY") != "1"}
        report = preflight.run((checks or {}).get("kiem_tra", []), resolver, self.bridge, script, bank, safety)
        minutes.preflight = report.lines()
        scene_log.record("present_preflight", ok=report.ok, lines=report.lines())
        if not report.ok:
            self._show_panel_anywhere()
            self.panel.set_warning("Em chưa trình được. Kiểm tra trước chưa đạt:\n"
                                   + "\n".join(l for l in report.lines() if l.startswith("KHÔNG")))
            self.finished.emit("")
            return False
        if not self.attached:
            try:
                self.bridge.start(1)
            except Exception as exc:        # noqa: BLE001 - one retry from _tick before it is called failed
                log.warning("starting the slide show failed: %s", type(exc).__name__)
                self.bridge.last_error = type(exc).__name__
        self._started, self._seen_running, self._retried = time.monotonic(), False, 0
        try:
            self.total = self.bridge.count()
        except Exception as exc:            # noqa: BLE001
            return self._fail("count", type(exc).__name__)
        self.live = LiveTalk(script, bank, resolver, self.bridge, frame=self._frame, clear=self._clear,
                             slide_hint=self._hint, whatif=whatif, minutes=minutes)
        self._place()
        self.keys = hotkeys.TalkKeys({hotkeys.DEFAULT_KEYS["ask"]: self.sig_ask.emit,
                                      hotkeys.DEFAULT_KEYS["back"]: self.sig_back.emit})
        failed = self.keys.start()
        if failed:
            self.panel.set_warning(f"Phím {', '.join(failed)} đang bị chương trình khác giữ. Anh chị bấm vào ô hỏi để hỏi.")
        scene_log.record("present_start", attached=self.attached, how=self.bridge.how, slides=self.total,
                         mode=self.layout.mode,
                         keys_failed=failed)
        self.timer.start(POLL_MS)
        self._tick()
        return True

    # ---- screens ----------------------------------------------------------------------------
    def _screens(self) -> list:
        return [(s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height())
                for s in QApplication.screens()]

    def _show_window_logical(self):
        win = self.bridge.window_pixels() if self.bridge else None
        if not win:
            return None
        box = self._to_logical((win[0], win[1], win[0] + win[2], win[1] + win[3]))
        return (box[0], box[1], box[2] - box[0], box[3] - box[1]) if box else None

    def _to_logical(self, rect):
        from screen.capture import monitor_geometry, physical_to_logical
        try:
            return physical_to_logical(rect, monitor_geometry())
        except Exception:                   # noqa: BLE001
            return rect

    def _place(self) -> None:
        from present.live import layout
        screens = QApplication.screens()
        primary = screens.index(QApplication.primaryScreen()) if QApplication.primaryScreen() in screens else 0
        self.layout = layout(self._screens(), self._show_window_logical(), primary)
        if self.layout.mode == "duplicate" and os.environ.get(FORCE_PANEL) == "1":
            self._show_panel_anywhere()          # rehearsal on a one-screen PC only, never in front of people
            return
        if self.layout.mode == "duplicate":
            self.panel.hide()
            self.manager.sig_notice.emit(DUPLICATE_NOTICE)
            return
        g = screens[self.layout.laptop].availableGeometry()
        self.panel.place(QRect(g))
        self.panel.show()

    def _show_panel_anywhere(self) -> None:
        g = QApplication.primaryScreen().availableGeometry()
        self.panel.place(QRect(g))
        self.panel.show()

    # ---- projector ----------------------------------------------------------------------------
    def _frame(self, targets: list) -> None:
        from ui.card_layout import NO_BUBBLE, pointer_spot
        boxes = []
        for rect, _label in targets:
            box = self._to_logical(rect)
            if not box:
                continue
            l, t, r, b = box
            boxes.append(box)
            self.manager.sig_draw.emit({"kind": "rect", "x1": l - 6, "y1": t - 6, "x2": r + 6, "y2": b + 6,
                                        "color": "blue", "ttl": None})
        scene_log.record("present_frames", boxes=boxes)
        if boxes:
            self.manager.sig_point_hold.emit(True)
            self.manager.sig_point_at.emit(*pointer_spot(boxes[0]), NO_BUBBLE)

    def _clear(self) -> None:
        self.manager.sig_clear_drawings.emit()
        self.manager.sig_point_release.emit()

    # ---- laptop -----------------------------------------------------------------------------
    def _hint(self, info: dict) -> None:
        self.panel.set_slide(info, self.total)

    def _on_ask_key(self) -> None:
        scene_log.record("present_key", key="ask")
        if self.layout is not None and self.layout.mode == "duplicate" and os.environ.get(FORCE_PANEL) != "1":
            self.manager.sig_notice.emit(DUPLICATE_NOTICE)
            return
        self.panel.focus_ask()

    def _on_asked(self, question: str) -> None:
        if self.live is None:
            return
        r = self.live.ask(question)
        self.panel.show_answer(r)

    def _on_picked(self, entry_id: str, caption: str) -> None:
        if self.live is None:
            return
        self.panel.show_answer(self.live.pick(entry_id, caption))

    def _dismiss(self) -> None:
        self.panel.clear_answer()
        self.bridge.focus_show()

    def _on_back_key(self) -> None:
        scene_log.record("present_key", key="back")
        if self.live is None:
            return
        self.live.back()
        self._dismiss()

    # ---- end --------------------------------------------------------------------------------
    def _tick(self) -> None:
        if self.live is None:
            return
        if not self.bridge.running():
            if self._seen_running:
                self.stop()                 # the presenter ended the show
                return
            # Not on screen yet: PowerPoint may still be opening it (robot run 07/11 ended 15 ms after the
            # start). Follow a show window of this deck when it appears, start once more, then say why.
            waited = time.monotonic() - self._started
            if self.bridge.find_show(self.deck_path) and self.bridge.running():
                scene_log.record("present_attached", after=round(waited, 1), how=getattr(self.bridge, "how", ""),
                                 started_by=STARTED_BY[self._retried])
            elif waited < START_GRACE_S:
                due = 2 if waited >= COMMAND_LINE_AFTER_S else 1 if waited >= RETRY_AFTER_S else 0
                if due > self._retried:
                    self._retried = attempt = self._retried + 1           # in order, even after a slow tick
                    ok = self.bridge.restart(self.deck_path, attempt)     # ok = PowerPoint reports the show now
                    why = getattr(self.bridge, "last_error", "")
                    # sent: the call went through; the show it starts may come a second later (present_attached)
                    scene_log.record("present_retry", attempt=attempt, how=STARTED_BY[attempt], sent=ok or not why,
                                     ok=ok, why=why,
                                     shows=getattr(self.bridge, "shows_seen", 0))
                    if ok and self.bridge.running():
                        scene_log.record("present_attached", after=round(waited, 1),
                                         how=getattr(self.bridge, "how", ""), started_by=STARTED_BY[attempt])
                        self._seen_running = True
                        self.live.poll()
                return
            else:
                self._fail("show", getattr(self.bridge, "last_error", "") or "không thấy cửa sổ trình chiếu")
                return
        self._seen_running = True
        self.live.poll()

    def _fail(self, stage: str, why: str) -> bool:
        """The show could not start: a plain card for the presenter, a log line, and the robot's event."""
        log.warning("present mode: no slide show (%s: %s)", stage, why)
        b = self.bridge
        scene_log.record("present_failed", stage=stage, why=str(why)[:200],          # counts only, never deck names
                         shows=getattr(b, "shows_seen", 0), same_name=getattr(b, "same_name", 0))
        self.timer.stop()
        if self.keys is not None:
            self.keys.stop()
            self.keys = None
        self.live = None
        self._show_panel_anywhere()
        self.panel.set_warning(NO_SHOW)
        notice = getattr(self.manager, "sig_notice", None)
        if notice is not None:
            notice.emit(NO_SHOW)
        self.finished.emit("")
        return False

    def stop(self) -> str:
        self.timer.stop()
        if self.keys is not None:
            self.keys.stop()
            self.keys = None
        self._clear()
        self.panel.hide()
        path = ""
        if self.live is not None:
            from storage import data_dir
            folder = data_dir() / "bien-ban"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"thuyet-trinh-{time.strftime('%Y%m%d-%H%M')}.md"
            target.write_text(self.live.minutes.markdown(), encoding="utf-8")
            path = str(target)
            scene_log.record("present_end", minutes=path)
            self.live = None
        self.finished.emit(path)
        return path
