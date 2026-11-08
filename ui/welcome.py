"""The welcome card with Clicky's 3 promises (spec 8, v5-2). Shown once, on the first start.

"Bắt đầu" emits ``started``; the caller marks the welcome done (first_run.mark_welcome_done)
and goes on. "Tôi có mã kích hoạt" emits ``activation_requested``; entering a code is the next
build's job (spec 9), so until something is connected to that signal the card only says so.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ui.plain_card import BLUE, LINK_QSS, PRIMARY_QSS, CardWindow, button, label

TITLE = "Chào chị, em là Clicky"
LEAD = "Em làm báo cáo tháng ngay trên Excel của chị. Em hứa 3 điều:"
PROMISES = (
    ("Số tính trên máy chị", "File không rời máy. Gửi gì lên mạng, chị xem trước."),
    ("Hỏi trước khi sửa", "Bản gốc luôn được giữ lại."),
    ("Nói thật khi chưa làm được", "Em không đoán số."),
)
START = "Bắt đầu"
ACTIVATION = "Tôi có mã kích hoạt"
ACTIVATION_LATER = "Bản sau mới nhập được mã kích hoạt. Bây giờ chị bấm Bắt đầu để dùng thử nhé."
_BADGES = (("#DCFCE7", "#15803D"), ("#DBEAFE", "#1D4ED8"), ("#FEF3C7", "#B45309"))


class WelcomeCard(CardWindow):
    started = pyqtSignal()
    activation_requested = pyqtSignal()

    def __init__(self):
        super().__init__("Chào chị")
        head = QHBoxLayout()
        head.setSpacing(10)
        try:                                           # the one Clicky symbol: the nón lá of the tray icon
            from ui.tray import non_la_pixmap
            hat = QLabel()
            hat.setPixmap(non_la_pixmap(30, QColor(BLUE)))
            hat.setFixedSize(30, 30)
            head.addWidget(hat, alignment=Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass
        head.addWidget(label(TITLE, "title", 17, QFont.Weight.Bold), stretch=1)
        self.body.addLayout(head)
        self.body.addWidget(label(LEAD, "body", 10))

        self.promise_labels = []
        for i, ((promise, detail), (fill, ink)) in enumerate(zip(PROMISES, _BADGES)):
            line = QHBoxLayout()
            line.setSpacing(12)
            badge = QLabel(str(i + 1))
            badge.setFixedSize(30, 30)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            badge.setStyleSheet(f"background: {fill}; color: {ink}; border-radius: 9px;")
            line.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            title = label(promise, "rowTitle", 11, QFont.Weight.Bold)
            self.promise_labels.append(title)
            text.addWidget(title)
            text.addWidget(label(detail, "rowDetail", 9))
            line.addLayout(text, stretch=1)
            self.body.addLayout(line)

        self.start_button = button(START, PRIMARY_QSS, 12)
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._start)
        self.body.addSpacing(4)
        self.body.addWidget(self.start_button)
        self.activation_link = button(ACTIVATION, LINK_QSS, 10)
        self.activation_link.clicked.connect(self._activation)
        self.body.addWidget(self.activation_link, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.later_note = label(ACTIVATION_LATER, "body", 9)
        self.later_note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.later_note.hide()
        self.body.addWidget(self.later_note)

    def _start(self) -> None:
        self.hide()
        self.started.emit()

    def _activation(self) -> None:
        if self.receivers(self.activation_requested) > 0:
            self.activation_requested.emit()
        else:
            self.on_activation_requested()

    def on_activation_requested(self) -> None:
        """Stub until activation codes arrive (spec 9): say so, keep the welcome up."""
        self.later_note.show()
        self.adjustSize()


def start_after_welcome(manager) -> None:
    """"Bắt đầu" pressed: the welcome is done for this install, and the sample month is offered next
    on the choice card month_close uses. "Xem em làm" there goes through the normal chat dispatch
    (office.sample.SAMPLE_TRIGGER runs the sample)."""
    import first_run
    from office import sample
    first_run.mark_welcome_done()
    beat = sample.offer()[-1]
    manager.sig_choices.emit(beat.text, list(beat.choices))
