"""The white floating card the first-run screens share (welcome, error, bug-report preview).

Same look as ui/choice_card.py: white card, soft shadow, Segoe UI, #2563EB primary button.
One idea per card; it stands on the right of the screen, under Excel's ribbon.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.card_layout import BOARD, record_widget, settle

BLUE = "#2563EB"
INK = "#0F172A"
MUTED = "#475569"

CARD_QSS = ("QFrame#card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; }"
            "QLabel { background: transparent; }"
            f"QLabel#title {{ color: {INK}; }} QLabel#body {{ color: {MUTED}; }}"
            f"QLabel#rowTitle {{ color: {INK}; }} QLabel#rowDetail {{ color: {MUTED}; }}"
            "QLabel#eyebrow { color: #DC2626; }"
            "QLabel#ok { color: #0F172A; }"
            "QFrame#note { background: #F1F5F9; border: none; border-radius: 10px; }"
            "QLabel#noteTitle { color: #475569; } QLabel#noteBody { color: #0F172A; }"
            "QPushButton#close { background: transparent; border: none; color: #94A3B8; font-size: 16px; }"
            "QPushButton#close:hover { color: #0F172A; }"
            "QPlainTextEdit { background: #F8FAFC; color: #0F172A; border: 1px solid #E2E8F0; border-radius: 8px;"
            " padding: 6px; font-family: Consolas, 'Segoe UI'; font-size: 9pt; }")
PRIMARY_QSS = (f"QPushButton {{ background: {BLUE}; color: white; border: none; border-radius: 10px; padding: 11px 18px;"
               " font-weight: 600; } QPushButton:hover { background: #1D4ED8; }")
SECONDARY_QSS = ("QPushButton { background: white; color: #0F172A; border: 1px solid #CBD5E1; border-radius: 10px;"
                 " padding: 11px 18px; font-weight: 600; } QPushButton:hover { background: #F1F5F9; }")
LINK_QSS = (f"QPushButton {{ background: transparent; color: {BLUE}; border: none; padding: 4px; font-weight: 600; }}"
            "QPushButton:hover { text-decoration: underline; }")


def label(text: str, name: str, size: float = 10, weight=QFont.Weight.Normal, wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setFont(QFont("Segoe UI", int(size), weight))
    return widget


def button(text: str, style: str, size: int = 11) -> QPushButton:
    widget = QPushButton(text)
    widget.setStyleSheet(style)
    widget.setFont(QFont("Segoe UI", size))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


class CardWindow(QWidget):
    """A frameless, always-on-top window holding one white card; subclasses fill ``self.body``."""

    WIDTH = 460

    def hideEvent(self, event):
        BOARD.release(self.__class__.__name__)       # its place on screen is free again for cards
        super().hideEvent(event)

    def __init__(self, title: str = "Clicky"):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)          # room for the shadow
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(CARD_QSS)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(15, 23, 42, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        self.card = card
        self.body = QVBoxLayout(card)
        self.body.setContentsMargins(26, 22, 26, 22)
        self.body.setSpacing(12)
        self.setFixedWidth(self.WIDTH)

    def close_button(self) -> QPushButton:
        close = QPushButton("×")
        close.setObjectName("close")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAccessibleName("Đóng")
        close.clicked.connect(self.hide)
        return close

    def row(self, *widgets, stretch_first: bool = False) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(10)
        for i, widget in enumerate(widgets):
            line.addWidget(widget, stretch=1 if (stretch_first and i == 0) else 0)
        return line

    def present(self) -> None:
        """Right side of the screen, under the ribbon, where a sheet's figures rarely are."""
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            spot = (max(area.left(), area.right() - self.width() - 96), area.top() + max(120, int(area.height() * 0.16)))
            x, y, placed = settle(self.__class__.__name__, spot, (self.width(), self.height()),
                                  (area.left(), area.top(), area.right(), area.bottom()))
            self.move(int(x), int(y))
            record_widget(self.__class__.__name__, placed)
        self.show()
        self.raise_()
        self.activateWindow()
