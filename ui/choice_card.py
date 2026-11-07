"""A small card with real buttons for the question Clicky just asked ("Sửa giúp tôi" / "Để tôi xem").

The drawing overlay is click-through, so the buttons live in their own window. A
click sends the button's words to Clicky exactly as if the user had typed them.

The same card shows the receipt before anything is computed ("Em đọc được những gì"):
set_details() is called just before ask(), and the card then lists each file read and
what was left out above the two buttons.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.card_layout import BOARD, settle

_CARD = ("QFrame#card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; }"
         "QLabel#question { color: #0F172A; }"
         "QPushButton#close { background: transparent; border: none; color: #94A3B8; font-size: 16px; }"
         "QPushButton#close:hover { color: #0F172A; }"
         "QLabel#eyebrow { color: #2563EB; letter-spacing: 1px; }"
         "QLabel#title { color: #0F172A; }"
         "QLabel#rowTitle { color: #0F172A; } QLabel#rowCount { color: #0F172A; } QLabel#rowDetail { color: #475569; }"
         "QFrame#line { background: #E2E8F0; }")
_PRIMARY = ("QPushButton { background: #2F6FEB; color: white; border: none; border-radius: 9px; padding: 9px 18px;"
            " font-weight: 600; } QPushButton:hover { background: #1E5AD6; }")
_SECONDARY = ("QPushButton { background: white; color: #1E3A5F; border: 1px solid #CBD5E1; border-radius: 9px;"
              " padding: 9px 18px; font-weight: 600; } QPushButton:hover { background: #F1F5F9; }")


class ChoiceCard(QWidget):
    chosen = pyqtSignal(str)
    dismissed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
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
        body.setSpacing(12)
        top = QHBoxLayout()
        self._question = QLabel()
        self._question.setObjectName("question")
        self._question.setWordWrap(True)
        self._question.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        top.addWidget(self._question, stretch=1)
        close = QPushButton("×")
        close.setObjectName("close")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAccessibleName("Đóng")
        close.clicked.connect(self._dismiss)
        top.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)
        self._details = QVBoxLayout()
        self._details.setSpacing(0)
        body.addLayout(self._details)
        self._next_details = None

        row = QHBoxLayout()
        row.setSpacing(10)
        self._buttons = []
        for style in (_PRIMARY, _SECONDARY):
            button = QPushButton()
            button.setStyleSheet(style)
            button.setFont(QFont("Segoe UI", 10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, b=button: self._pick(b.text()))
            row.addWidget(button)
            self._buttons.append(button)
        row.addStretch()
        body.addLayout(row)
        self.setFixedWidth(440)

    def set_details(self, card: dict) -> None:
        """The receipt to show with the next question (engine.intake.card)."""
        self._next_details = dict(card or {})

    def _clear_details(self) -> None:
        while self._details.count():
            item = self._details.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _line(self) -> QFrame:
        line = QFrame()
        line.setObjectName("line")
        line.setFixedHeight(1)
        return line

    def _icon(self, ok: bool) -> QLabel:
        icon = QLabel("✓" if ok else "!")
        icon.setFixedSize(26, 26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        icon.setStyleSheet(f"background: {'#16A34A' if ok else '#D97706'}; color: white; border-radius: 13px;")
        return icon

    _ROLE_STYLE = {"Số chính thức": ("#DBEAFE", "#1D4ED8"), "Chi tiết để giải thích": ("#DCFCE7", "#15803D"),
                   "Kế hoạch": ("#EDE9FE", "#6D28D9"), "Lý do": ("#FEF3C7", "#B45309"), "Ghi chú": ("#F1F5F9", "#475569")}

    def _pill(self, role: str) -> QLabel:
        fill, ink = self._ROLE_STYLE.get(role, ("#F1F5F9", "#475569"))
        pill = QLabel(role)
        pill.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        pill.setStyleSheet(f"background: {fill}; color: {ink}; border-radius: 9px; padding: 2px 8px;")
        return pill

    def _entry(self, ok: bool, title: str, count: str, detail: str, role: str = "") -> QWidget:
        box = QWidget()
        grid = QHBoxLayout(box)
        grid.setContentsMargins(0, 12, 0, 12)
        grid.setSpacing(12)
        grid.addWidget(self._icon(ok), alignment=Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        head = QHBoxLayout()
        name = QLabel(title)
        name.setObjectName("rowTitle")
        name.setWordWrap(True)
        name.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        head.addWidget(name, stretch=1)
        if count:
            n = QLabel(count)
            n.setObjectName("rowCount")
            n.setFont(QFont("Segoe UI", 10))
            head.addWidget(n, alignment=Qt.AlignmentFlag.AlignTop)
        if role:
            head.addWidget(self._pill(role), alignment=Qt.AlignmentFlag.AlignTop)
        text.addLayout(head)
        if detail:
            d = QLabel(detail)
            d.setObjectName("rowDetail")
            d.setWordWrap(True)
            d.setFont(QFont("Segoe UI", 9))
            text.addWidget(d)
        grid.addLayout(text, stretch=1)
        return box

    def _show_details(self, card: dict) -> None:
        self._clear_details()
        eyebrow = QLabel(card.get("eyebrow", ""))
        eyebrow.setObjectName("eyebrow")
        eyebrow.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title = QLabel(card.get("title", ""))
        title.setObjectName("title")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self._details.addWidget(eyebrow)
        self._details.addWidget(title)
        for r in card.get("rows", []):
            self._details.addWidget(self._line())
            self._details.addWidget(self._entry(bool(r.get("ok", True)), r.get("title", ""), r.get("count", ""),
                                                r.get("detail", ""), r.get("role", "")))
        left = card.get("left_out") or []
        self._details.addWidget(self._line())
        self._details.addWidget(self._entry(not left, card.get("left_out_title", ""), "", " · ".join(left)))

    def ask(self, question: str, options: list) -> None:
        card, self._next_details = self._next_details, None
        self._clear_details()
        if card:
            self._show_details(card)
        self._question.setVisible(not card)
        self.setFixedWidth(600 if card else 440)
        self._question.setText(question)
        for button, label in zip(self._buttons, list(options) + [None, None]):
            button.setVisible(bool(label))
            if label:
                button.setText(label)
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        if card:
            # The receipt stands on the right, under the ribbon, where a sheet's figures rarely are.
            spot = (area.right() - self.width() - 96, area.top() + max(160, int(area.height() * 0.18)))
        else:
            # Bottom-right, above Excel's sheet tabs and status bar: the corner least likely to hold figures.
            spot = (area.right() - self.width() - 28, area.bottom() - self.height() - 72)
        # ...unless figures or Clicky's explanation card are there: then the nearest free place instead.
        bounds = (area.left(), area.top(), area.right(), area.bottom())
        x, y, placed = settle("choice", spot, (self.width(), self.height()), bounds)
        self.move(int(x), int(y))
        try:
            import scene_log
            scene_log.record("choice_card", **placed)
        except Exception:
            pass
        self.show()
        self.raise_()

    def hideEvent(self, event):
        BOARD.release("choice")
        super().hideEvent(event)

    def _pick(self, label: str) -> None:
        self.hide()
        self.chosen.emit(label)

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()
