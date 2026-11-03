"""What leaves the machine, shown before it leaves: the approval card and the "Nhật ký gửi đi" window.

The card shows the masked lines exactly as they will be sent, with one line saying what was
covered. It appears the first time file content would go to the AI; after "Gửi đi" each send
is only written to the log, which the window lists, newest first.
"""
import os
import subprocess

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.choice_card import _CARD, _PRIMARY, _SECONDARY

VISIBLE_LINES = 12
TITLE = "Những dòng này sẽ gửi cho AI"
NOTE = "Tên và mã số đã được che. Lần sau em gửi luôn và ghi vào Nhật ký gửi đi."
SEND, DONT_SEND = "Gửi đi", "Không gửi"
_LINES = ("QPlainTextEdit { background: #F8FAFC; color: #0F172A; border: 1px solid #E2E8F0;"
          " border-radius: 8px; padding: 6px; }")


def _mono() -> QFont:
    font = QFont("Consolas", 9)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class OutgoingCard(QWidget):
    answered = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)          # room for the shadow
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
        body.setContentsMargins(20, 14, 12, 16)
        body.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel(TITLE)
        title.setObjectName("question")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        top.addWidget(title, stretch=1)
        close = QPushButton("×")
        close.setObjectName("close")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAccessibleName(DONT_SEND)
        close.clicked.connect(lambda: self._answer(False))
        top.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: #1E3A5F;")
        self._summary.setFont(QFont("Segoe UI", 10))
        body.addWidget(self._summary)

        self._lines = QPlainTextEdit()
        self._lines.setReadOnly(True)
        self._lines.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._lines.setFont(_mono())
        self._lines.setStyleSheet(_LINES)
        body.addWidget(self._lines)

        note = QLabel(NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748B;")
        note.setFont(QFont("Segoe UI", 9))
        body.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.send_button = QPushButton(SEND)
        self.keep_button = QPushButton(DONT_SEND)
        for button, style, value in ((self.send_button, _PRIMARY, True), (self.keep_button, _SECONDARY, False)):
            button.setStyleSheet(style)
            button.setFont(QFont("Segoe UI", 10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self._answer(v))
            row.addWidget(button)
        row.addStretch()
        body.addLayout(row)
        self.setFixedWidth(600)
        self._open = False

    def show_preview(self, preview: dict) -> None:
        """An empty preview hides the card (the request was answered or cancelled elsewhere)."""
        if not preview:
            self._open = False
            self.hide()
            return
        self._summary.setText(preview.get("summary", ""))
        lines = list(preview.get("lines") or [])
        self._lines.setPlainText("\n".join(lines))
        # Up to twelve lines in view; the rest scroll.
        line_h = QFontMetrics(self._lines.font()).lineSpacing()
        self._lines.setFixedHeight(line_h * max(3, min(VISIBLE_LINES, len(lines))) + 22)
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 28, area.bottom() - self.height() - 72)
        self._open = True
        self.show()
        self.raise_()
        self.activateWindow()

    def lines_text(self) -> str:
        return self._lines.toPlainText()

    def summary_text(self) -> str:
        return self._summary.text()

    def _answer(self, value: bool) -> None:
        if not self._open:
            return
        self._open = False
        self.hide()
        self.answered.emit(value)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._answer(False)
        else:
            super().keyPressEvent(event)


def format_entry(entry: dict) -> str:
    when = str(entry.get("time", "")).replace("T", " ")
    head = f"{when} · {entry.get('summary', '')}"
    if not entry.get("sent", True):
        head += " · KHÔNG GỬI"
    lines = entry.get("preview") or []
    return head + ("\n" + "\n".join("    " + line for line in lines) if lines else "")


class OutgoingLogWindow(QWidget):
    """Every request that went (or was stopped), newest first; the full text is in the file."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nhật ký gửi đi")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        intro = QLabel("Mỗi lần Clicky gửi cho AI, em ghi lại ở đây đúng những gì đã gửi, sau khi che tên và mã số.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(_mono())
        layout.addWidget(self.view)
        row = QHBoxLayout()
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(self.refresh)
        open_file = QPushButton("Mở file nhật ký")
        open_file.clicked.connect(self.open_file)
        row.addWidget(refresh)
        row.addWidget(open_file)
        row.addStretch()
        layout.addLayout(row)

    def refresh(self) -> None:
        from office.outgoing import read_log
        entries = read_log()
        self.view.setPlainText("\n\n".join(format_entry(e) for e in entries) if entries
                               else "Chưa gửi gì cho AI.")

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def open_file(self) -> None:
        from office.outgoing import log_path
        path = log_path()
        if not path.exists():
            path.touch()
        try:
            os.startfile(str(path))                  # Windows: opens in Notepad or the user's editor
        except (AttributeError, OSError):
            subprocess.Popen(["notepad", str(path)] if os.name == "nt" else ["xdg-open", str(path)])
