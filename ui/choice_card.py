"""A small card with real buttons for the question Clicky just asked ("Sửa giúp tôi" / "Để tôi xem").

The drawing overlay is click-through, so the buttons live in their own window. A
click sends the button's words to Clicky exactly as if the user had typed them.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

_CARD = ("QFrame#card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; }"
         "QLabel#question { color: #0F172A; }"
         "QPushButton#close { background: transparent; border: none; color: #94A3B8; font-size: 16px; }"
         "QPushButton#close:hover { color: #0F172A; }")
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

    def ask(self, question: str, options: list) -> None:
        self._question.setText(question)
        for button, label in zip(self._buttons, list(options) + [None, None]):
            button.setVisible(bool(label))
            if label:
                button.setText(label)
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        # Bottom-right, above Excel's sheet tabs and status bar: the corner least likely to hold figures.
        self.move(area.right() - self.width() - 28, area.bottom() - self.height() - 72)
        self.show()
        self.raise_()

    def _pick(self, label: str) -> None:
        self.hide()
        self.chosen.emit(label)

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()
