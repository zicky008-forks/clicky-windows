"""First run, right after the welcome: "0 byte rời máy chị". Shows what has left this PC so far (office.outgoing
counts it from gui-di.jsonl), and lets the user choose, once, between "Chỉ chạy trên máy" (recommended for real
company files: nothing is ever sent) and asking an AI with a preview before every send. Either choice is changed
later in the tray menu. Nothing on this card sends anything.
"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont

from ui.plain_card import LINK_QSS, PRIMARY_QSS, SECONDARY_QSS, CardWindow, button, label

TITLE = "{sent} rời máy chị"
LEAD = "Từ lúc cài đến giờ, em chưa gửi gì ra ngoài. Số trong báo cáo luôn do em tính ngay trên máy này."
LEAD_SENT = "Đây là tổng số chữ em đã gửi đi sau khi chị xem trước và đồng ý. File của chị chưa bao giờ được gửi."
FACTS = (
    "Không gửi file, không gửi ảnh màn hình nếu chị chưa đồng ý.",
    "Tên khách hàng, mã số, số tài khoản được che trước khi gửi.",
    "Mọi lần gửi đều ghi vào Nhật ký gửi đi để chị mở xem.",
)
LOCAL = "Chỉ chạy trên máy (nên chọn với file công ty)"
ASK_AI = "Cho hỏi AI, nhưng hỏi em trước mỗi lần gửi"
LOG_LINK = "Mở nhật ký gửi đi"
LATER = "Đổi lúc nào cũng được ở biểu tượng nón lá dưới khay."


def title(sent: int) -> str:
    from office.outgoing import bytes_words
    return TITLE.format(sent=bytes_words(sent))


class PrivacyCard(CardWindow):
    chosen = pyqtSignal(bool)                 # True: local only

    def __init__(self, sent: int = None):
        super().__init__("Riêng tư")
        from office import outgoing
        sent = outgoing.bytes_sent() if sent is None else sent
        self.title_label = label(title(sent), "title", 17, QFont.Weight.Bold)
        self.body.addWidget(self.title_label)
        self.body.addWidget(label(LEAD if sent == 0 else LEAD_SENT, "body", 10))
        for fact in FACTS:
            self.body.addWidget(label("✓  " + fact, "rowDetail", 9))
        self.local_button = button(LOCAL, PRIMARY_QSS, 11)
        self.local_button.setDefault(True)
        self.local_button.clicked.connect(lambda: self._choose(True))
        self.ai_button = button(ASK_AI, SECONDARY_QSS, 10)
        self.ai_button.clicked.connect(lambda: self._choose(False))
        self.body.addSpacing(4)
        self.body.addWidget(self.local_button)
        self.body.addWidget(self.ai_button)
        self.log_link = button(LOG_LINK, LINK_QSS, 9)
        self.log_link.clicked.connect(open_log)
        self.body.addWidget(self.log_link)
        self.body.addWidget(label(LATER, "body", 8))
        if outgoing.local_only_forced():          # CLICKY_LOCAL_ONLY=1: the company's setting decides
            self.ai_button.setEnabled(False)
            self.ai_button.setToolTip("Máy này đặt chế độ chỉ chạy trên máy.")

    def _choose(self, local: bool) -> None:
        choose(local)
        self.hide()
        self.chosen.emit(local)


def choose(local: bool) -> None:
    from storage import set_setting
    set_setting("local_only", bool(local))
    set_setting("privacy_chosen", True)
    try:
        import scene_log
        scene_log.record("privacy_choice", local_only=bool(local))
    except Exception:                          # noqa: BLE001 - the choice is saved either way
        pass


def open_log() -> None:
    """Notepad on gui-di.jsonl (created empty if nothing was ever sent, so the user sees it is empty)."""
    import os
    import subprocess
    from office.outgoing import log_path
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(["notepad.exe", str(path)])
    except OSError:
        pass
