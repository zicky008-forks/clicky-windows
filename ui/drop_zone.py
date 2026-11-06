"""The one drop place: "Chị thả 2 sổ cân đối tháng này vào đây" (screen v5-6).

Drag files or a folder from File Explorer onto the dashed box, or click it to pick files. The
work mode sits right under the box, "Làm giúp em" chosen by default. Nothing is read until
something is dropped, and the files are read where they are: never copied, moved or uploaded.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

import scene_log
from office import drop

TITLE = "Chị thả 2 sổ cân đối tháng này vào đây"
BOX_MAIN = "Kéo thả file hoặc cả thư mục, hoặc bấm để chọn file"
BOX_SUB = "Sổ cân đối và file bán hàng xuất từ MISA, Fast, Bravo hay file Excel tự làm đều được"
QUESTION = "Chị muốn em làm kiểu nào?"
FOOT = "🔒 Em đọc file ngay trên máy chị, không chép hay gửi file đi đâu."
_FILTER = "Sổ cân đối và file bán hàng (*.xlsx *.xlsm *.xls *.csv)"

_CARD = ("QFrame#card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; }"
         "QLabel#title { color: #0F172A; } QLabel#question { color: #0F172A; } QLabel#foot { color: #475569; }"
         "QPushButton#close { background: transparent; border: none; color: #94A3B8; font-size: 16px; }"
         "QPushButton#close:hover { color: #0F172A; }")
_BOX = ("QFrame#box { background: #F8FAFC; border: 2px dashed #94A3B8; border-radius: 12px; }"
        "QFrame#box[hot=\"true\"] { background: #EFF6FF; border: 2px dashed #2563EB; }"
        "QLabel { color: #334155; background: transparent; border: none; }")
_MODE = ("QFrame#mode { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; }"
         "QFrame#mode[on=\"true\"] { background: #EFF6FF; border: 2px solid #2563EB; }"
         "QRadioButton { color: #0F172A; font-weight: 600; } QRadioButton:disabled { color: #94A3B8; }"
         "QLabel { color: #475569; background: transparent; border: none; }")


class _Box(QFrame):
    """The dashed area: accepts drops from File Explorer and opens a file picker on click."""
    dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("box")
        self.setStyleSheet(_BOX)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(130)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for text, size, weight in (("⬆", 18, QFont.Weight.Normal), (BOX_MAIN, 11, QFont.Weight.DemiBold),
                                   (BOX_SUB, 9, QFont.Weight.Normal)):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setFont(QFont("Segoe UI", size, weight))
            lay.addWidget(label)

    def _hot(self, on: bool):
        self.setProperty("hot", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._hot(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hot(False)

    def dropEvent(self, event):
        self._hot(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        event.acceptProposedAction()
        self.dropped.emit(paths)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            files, _ = QFileDialog.getOpenFileNames(self, "Chọn sổ cân đối và file bán hàng", "", _FILTER)
            if files:
                self.dropped.emit(files)


class DropZone(QWidget):
    dropped = pyqtSignal(list)          # local paths, files or one folder
    dismissed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(620)
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
        body.setContentsMargins(24, 18, 16, 18)
        body.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel(TITLE)
        title.setObjectName("title")
        title.setWordWrap(True)
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        top.addWidget(title, stretch=1)
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setToolTip("Đóng")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self._close)
        top.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)

        self._box = _Box()
        self._box.dropped.connect(self._on_drop)
        body.addWidget(self._box)

        question = QLabel(QUESTION)
        question.setObjectName("question")
        question.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        body.addWidget(question)
        self._group = QButtonGroup(self)
        self._frames = {}
        for key, (label, detail) in drop.MODES.items():
            frame = QFrame()
            frame.setObjectName("mode")
            frame.setStyleSheet(_MODE)
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(2)
            radio = QRadioButton(label)
            radio.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            radio.setEnabled(key in drop.AVAILABLE)
            radio.setProperty("mode", key)
            note = QLabel(detail)
            note.setWordWrap(True)
            note.setContentsMargins(26, 0, 0, 0)
            lay.addWidget(radio)
            lay.addWidget(note)
            self._group.addButton(radio)
            self._frames[key] = (frame, radio)
            body.addWidget(frame)
        self._group.buttonToggled.connect(self._on_mode)
        foot = QLabel(FOOT)
        foot.setObjectName("foot")
        foot.setWordWrap(True)
        body.addWidget(foot)

    def _on_mode(self, button, checked):
        if checked:
            drop.set_mode(button.property("mode"))
        for key, (frame, radio) in self._frames.items():
            frame.setProperty("on", "true" if radio.isChecked() else "false")
            frame.style().unpolish(frame)
            frame.style().polish(frame)

    def _on_drop(self, paths: list):
        if not paths:
            return
        self.hide()
        scene_log.record("dropped", count=len(paths), mode=drop.mode())
        self.dropped.emit(paths)

    def _close(self):
        self.hide()
        self.dismissed.emit()

    def open(self):
        """Show on the right of the main screen, the current mode ticked."""
        self._frames[drop.mode()][1].setChecked(True)
        self._on_mode(self._frames[drop.mode()][1], True)
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 90, screen.top() + max(120, (screen.height() - self.height()) // 3))
        self.show()
        self.raise_()
        self.activateWindow()
        scene_log.record("drop_zone", title=TITLE, mode=drop.mode(), modes=list(drop.AVAILABLE))
