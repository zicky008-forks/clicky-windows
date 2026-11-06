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
        self.attached = self.bridge.attach(self.deck_path)
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
            self.bridge.start(1)
        self.total = self.bridge.count()
        self.live = LiveTalk(script, bank, resolver, self.bridge, frame=self._frame, clear=self._clear,
                             slide_hint=self._hint, whatif=whatif, minutes=minutes)
        self._place()
        self.keys = hotkeys.TalkKeys({hotkeys.DEFAULT_KEYS["ask"]: self.sig_ask.emit,
                                      hotkeys.DEFAULT_KEYS["back"]: self.sig_back.emit})
        failed = self.keys.start()
        if failed:
            self.panel.set_warning(f"Phím {', '.join(failed)} đang bị chương trình khác giữ. Anh chị bấm vào ô hỏi để hỏi.")
        scene_log.record("present_start", attached=self.attached, slides=self.total, mode=self.layout.mode,
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
            self.stop()
            return
        self.live.poll()

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
