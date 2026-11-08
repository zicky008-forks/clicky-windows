"""Job 13 (v5-8): after the first report on the user's own files, one card asks how much time Clicky saved.

Four chips (one pick), an optional note the user types or dictates, "Gửi góp ý" and "Để sau". The answer is kept on
this PC by engine.feedback with the version and the run's time; nothing from the files goes with it.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ui.card_layout import BOARD, settle
from ui.choice_card import _CARD, _PRIMARY, _SECONDARY

_CHIP = ("QPushButton { background: white; color: #1E3A5F; border: 1px solid #CBD5E1; border-radius: 17px;"
         " padding: 6px 12px; } QPushButton:hover { background: #F1F5F9; }"
         " QPushButton:checked { background: #EFF6FF; color: #1D4ED8; border: 2px solid #2563EB; font-weight: 600; }")
_NOTE = ("QPlainTextEdit { background: white; border: 1.5px solid #E2E8F0; border-radius: 12px; padding: 8px 10px;"
         " color: #0F172A; } QPlainTextEdit:focus { border-color: #2563EB; }")


class FeedbackCard(QWidget):
    sent = pyqtSignal(str, str, object)     # (chosen label, note, run seconds or None)
    later = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._run_s = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(_CARD)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(15, 23, 42, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 16, 22, 18)
        body.setSpacing(12)
        self.eyebrow = QLabel()
        self.eyebrow.setObjectName("eyebrow")
        self.eyebrow.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        body.addWidget(self.eyebrow)
        self.question = QLabel()
        self.question.setObjectName("title")
        self.question.setWordWrap(True)
        self.question.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        body.addWidget(self.question)
        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(8)
        body.addLayout(self._chips_row)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonToggled.connect(lambda *_: self._refresh())
        self.note_question = QLabel()
        self.note_question.setObjectName("rowTitle")
        self.note_question.setWordWrap(True)
        self.note_question.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        body.addWidget(self.note_question)
        self.note = QPlainTextEdit()
        self.note.setStyleSheet(_NOTE)
        self.note.setFont(QFont("Segoe UI", 10))
        self.note.setFixedHeight(72)
        self.note.setTabChangesFocus(True)
        body.addWidget(self.note)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.send = QPushButton()
        self.send.setStyleSheet(_PRIMARY)
        self.send.clicked.connect(self._send)
        self.skip = QPushButton()
        self.skip.setStyleSheet(_SECONDARY)
        self.skip.clicked.connect(self._later)
        for button in (self.send, self.skip):
            button.setFont(QFont("Segoe UI", 10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch()
        body.addLayout(row)
        self.setFixedWidth(620)

    def chips(self) -> list:
        return list(self._group.buttons())

    def ask(self, info: dict) -> None:
        """Show the card for engine.feedback.card(...)."""
        for chip in self.chips():
            self._group.removeButton(chip)
        while self._chips_row.count():
            item = self._chips_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for label in info.get("choices", []):
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setStyleSheet(_CHIP)
            chip.setFont(QFont("Segoe UI", 10))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setMinimumHeight(34)
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)     # never clipped
            self._group.addButton(chip)
            self._chips_row.addWidget(chip)
        self._chips_row.addStretch()
        self.eyebrow.setText(info.get("eyebrow", ""))
        self.question.setText(info.get("question", ""))
        self.note_question.setText(info.get("note_question", ""))
        self.note.clear()
        self.note.setPlaceholderText(info.get("note_hint", ""))
        self.send.setText(info.get("send", ""))
        self.skip.setText(info.get("later", ""))
        self._run_s = info.get("run_s")
        self._refresh()
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        spot = (area.right() - self.width() - 28, area.bottom() - self.height() - 72)
        x, y, placed = settle("feedback", spot, (self.width(), self.height()),
                              (area.left(), area.top(), area.right(), area.bottom()))
        self.move(int(x), int(y))
        try:
            import scene_log
            scene_log.record("feedback_card", **placed)
        except Exception:
            pass
        self.show()
        self.raise_()
        self.activateWindow()          # the note takes typing and dictation

    def chosen(self) -> str:
        button = self._group.checkedButton()
        return button.text() if button else ""

    def _refresh(self) -> None:
        self.send.setEnabled(bool(self.chosen()))

    def _send(self) -> None:
        choice = self.chosen()
        if not choice:
            return
        note = self.note.toPlainText()
        try:
            import scene_log
            scene_log.record("feedback", choice=choice, has_note=bool(note.strip()))   # never the note itself
        except Exception:
            pass
        self.hide()
        self.sent.emit(choice, note, self._run_s)

    def _later(self) -> None:
        try:
            import scene_log
            scene_log.record("feedback", choice="", later=True)
        except Exception:
            pass
        self.hide()
        self.later.emit()

    def hideEvent(self, event):
        BOARD.release("feedback")
        super().hideEvent(event)
