"""The error card (spec 12, v5-7) and the preview of what "Báo lỗi" would send.

The card shows only catalogue words from engine.errors_vi: a plain title, "File của chị chưa
bị thay đổi gì" when the caller says so, one hint, and the two buttons "Thử lại" / "Báo lỗi".
The raw error is written to clicky.log by errors_vi.describe and never shown.
"""
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout

from engine import bug_report, errors_vi
from ui.plain_card import PRIMARY_QSS, SECONDARY_QSS, CardWindow, button, label


class ErrorCard(CardWindow):
    retry_requested = pyqtSignal(str)      # error kind
    report_requested = pyqtSignal(str)     # error kind

    def __init__(self):
        super().__init__("Clicky gặp trục trặc")
        top = QHBoxLayout()
        self.eyebrow = label(errors_vi.EYEBROW, "eyebrow", 9, QFont.Weight.Bold)
        top.addWidget(self.eyebrow, stretch=1)
        top.addWidget(self.close_button(), alignment=Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(top)
        self.title = label("", "title", 15, QFont.Weight.Bold)
        self.body.addWidget(self.title)

        self.unchanged_row = QFrame()
        line = QHBoxLayout(self.unchanged_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        tick = QLabel("✓")
        tick.setFixedSize(20, 20)
        tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tick.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        tick.setStyleSheet("background: #16A34A; color: white; border-radius: 10px;")
        line.addWidget(tick)
        self.unchanged = label(errors_vi.UNCHANGED, "ok", 10)
        line.addWidget(self.unchanged, stretch=1)
        self.body.addWidget(self.unchanged_row)

        self.hint = label("", "body", 10)
        self.body.addWidget(self.hint)

        self.retry_button = button(errors_vi.RETRY, PRIMARY_QSS)
        self.report_button = button(errors_vi.REPORT, SECONDARY_QSS)
        self.retry_button.clicked.connect(self._retry)
        self.report_button.clicked.connect(self._report)
        self.body.addLayout(self.row(self.retry_button, self.report_button, stretch_first=True))

        note = QFrame()
        note.setObjectName("note")
        box = QVBoxLayout(note)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(2)
        box.addWidget(label(errors_vi.REPORT_NOTE_TITLE.upper(), "noteTitle", 8, QFont.Weight.Bold))
        box.addWidget(label(errors_vi.REPORT_NOTE, "noteBody", 9))
        self.body.addWidget(note)
        self.kind = errors_vi.GENERIC

    def show_error(self, error, *, file_unchanged: bool = False, path=None) -> dict:
        shown = errors_vi.describe(error, file_unchanged=file_unchanged, path=path)
        self.kind = shown["kind"]
        self.title.setText(shown["title"])
        self.hint.setText(shown["hint"])
        self.unchanged_row.setVisible(bool(shown["reassurance"]))
        self.present()
        return shown

    def _retry(self) -> None:
        self.hide()
        self.retry_requested.emit(self.kind)

    def _report(self) -> None:
        self.hide()
        self.report_requested.emit(self.kind)


class ReportPreview(CardWindow):
    """Exactly what "Báo lỗi" would send, before anything is sent. Nothing leaves without "Gửi"."""

    sent = pyqtSignal(str)                 # where the stub kept the report

    WIDTH = 560

    def __init__(self):
        super().__init__("Xem trước báo lỗi")
        top = QHBoxLayout()
        self.title = label("", "title", 14, QFont.Weight.Bold)
        top.addWidget(self.title, stretch=1)
        top.addWidget(self.close_button(), alignment=Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(top)
        self.lead = label("", "body", 10)
        self.body.addWidget(self.lead)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(260)
        self.body.addWidget(self.text)
        self.send_button = button("Gửi", PRIMARY_QSS)
        self.cancel_button = button("Thôi", SECONDARY_QSS)
        self.send_button.clicked.connect(self._send)
        self.cancel_button.clicked.connect(self.hide)
        self.body.addLayout(self.row(self.send_button, self.cancel_button, stretch_first=True))
        self.report: Optional[bug_report.Report] = None

    def preview(self, kind: str = "", masker: Optional[Callable[[str], str]] = None) -> bug_report.Report:
        report = bug_report.prepare(kind, masker)
        self.report = report
        if report.ready:
            self.title.setText("Em sẽ gửi đúng nội dung này")
            self.lead.setText("Đã che tên và mã số em đọc được trong file. Không kèm file nào của chị.")
            self.text.setPlainText(report.body)
            self.text.show()
            self.send_button.show()
            self.cancel_button.setText("Thôi")
        else:
            self._message("Chưa gửi được báo lỗi", report.message)
        self.present()
        return report

    def _message(self, title: str, text: str) -> None:
        self.title.setText(title)
        self.lead.setText(text)
        self.text.hide()
        self.send_button.hide()
        self.cancel_button.setText("Đóng")
        self.adjustSize()

    def _send(self) -> None:
        path = bug_report.send(self.report) if self.report else None
        if path is None:
            self._message("Chưa gửi được báo lỗi", bug_report.NOT_READY)
            return
        self._message("Đã chuẩn bị báo lỗi", bug_report.SAVED)
        self.sent.emit(str(path))
