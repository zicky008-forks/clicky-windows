"""The presenter's box on the laptop screen during a talk: slide prompt, ask box, answer, choices.

It lives only on the screen the audience does not see (present.live.layout). It never takes the keyboard
on its own: Ctrl+Alt+H gives it focus, Enter asks, Esc (or Ctrl+Alt+B) hands the keyboard back to the show.
Accessible names ("Câu hỏi", "Hỏi", "Trả lời") are what the rehearsal robot uses.
"""
from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ui.plain_card import CARD_QSS, PRIMARY_QSS, SECONDARY_QSS, label

WIDTH = 460


class PresentPanel(QWidget):
    asked = pyqtSignal(str)
    picked = pyqtSignal(str, str)          # (entry id, label)
    dismissed = pyqtSignal()               # Esc: give the keyboard back to the show

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Trợ lý trình bày")
        self.setAccessibleName("Trợ lý trình bày")
        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(CARD_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(8)
        self.header = label("", "body", 9, wrap=False)
        self.header.setAccessibleName("Slide")
        self.prompt = label("", "title", 10.5)
        self.prompt.setAccessibleName("Lời gợi ý")
        self.warning = label("", "eyebrow", 9.5)
        self.warning.setAccessibleName("Cảnh báo")
        self.warning.hide()
        row = QHBoxLayout()
        self.ask_box = QLineEdit()
        self.ask_box.setAccessibleName("Câu hỏi")
        self.ask_box.setPlaceholderText("Gõ câu hỏi của anh chị rồi Enter")
        self.ask_box.setFont(QFont("Segoe UI", 10))
        self.ask_button = QPushButton("Hỏi")
        self.ask_button.setAccessibleName("Hỏi")
        self.ask_button.setStyleSheet(PRIMARY_QSS)
        row.addWidget(self.ask_box, 1)
        row.addWidget(self.ask_button)
        self.answer = label("", "ok", 10.5)
        self.answer.setAccessibleName("Trả lời")
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer.hide()
        self.choices = QVBoxLayout()
        self.keys_hint = label("Ctrl+Alt+H: hỏi · Esc: về slide · Ctrl+Alt+B: quay lại slide trước khi hỏi", "body", 8.5)
        for w in (self.header, self.prompt, self.warning):
            box.addWidget(w)
        box.addLayout(row)
        box.addWidget(self.answer)
        box.addLayout(self.choices)
        box.addWidget(self.keys_hint)
        self.ask_box.returnPressed.connect(self._ask)
        self.ask_button.clicked.connect(self._ask)
        self.setFixedWidth(WIDTH)

    # ---- placement ------------------------------------------------------------------------
    def place(self, screen: QRect) -> None:
        """Bottom-right of the laptop screen, clear of the taskbar."""
        self.adjustSize()
        x = screen.x() + screen.width() - self.width() - 24
        y = screen.y() + screen.height() - self.height() - 64
        self.move(max(screen.x(), x), max(screen.y(), y))

    # ---- content --------------------------------------------------------------------------
    def set_slide(self, info: dict, total: int = 0) -> None:
        n = info.get("slide", 0)
        self.header.setText(f"Slide {n}/{total}" if total else f"Slide {n}")
        self.prompt.setText(info.get("text") or "")
        problems = list(info.get("missing") or []) + list(info.get("missed") or [])
        self.set_warning("Chưa đọc được số cho slide này: " + "; ".join(problems) if problems else "")
        self._resize()

    def set_warning(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))
        self._resize()

    def show_answer(self, r: dict) -> None:
        self._clear_choices()
        text = r.get("stop") and f"Em dừng ở câu này: {r['stop']}" or r.get("text") or r.get("ask") or ""
        self.answer.setText(text)
        self.answer.setVisible(bool(text))
        for entry_id, caption in r.get("choices") or []:
            b = QPushButton(caption or entry_id)
            b.setAccessibleName(caption or entry_id)
            b.setStyleSheet(SECONDARY_QSS)
            b.clicked.connect(lambda _=False, i=entry_id, c=caption: self.picked.emit(i, c))
            self.choices.addWidget(b)
        self._resize()

    def clear_answer(self) -> None:
        self._clear_choices()
        self.answer.clear()
        self.answer.hide()
        self.ask_box.clear()
        self._resize()

    def focus_ask(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.ask_box.setFocus()
        self.ask_box.selectAll()

    # ---- internals ------------------------------------------------------------------------
    def _ask(self) -> None:
        text = self.ask_box.text().strip()
        if text:
            self.asked.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            return
        super().keyPressEvent(event)

    def _clear_choices(self) -> None:
        while self.choices.count():
            w = self.choices.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _resize(self) -> None:
        bottom = self.y() + self.height()
        self.adjustSize()
        if self.isVisible():
            self.move(self.x(), bottom - self.height())       # grow upwards, keep the bottom edge
