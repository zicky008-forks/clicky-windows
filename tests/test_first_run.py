"""First run (spec 7, 8, 12): one-click install, the welcome shown once per install, plain-words errors."""
import logging
import re
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── 7. Install: per-user, no questions; a reinstall greets again ──────────────────────────────────

def test_welcome_shows_until_started_without_installer_stamp(tmp_path):
    import first_run
    app = tmp_path / "app"
    app.mkdir()
    assert first_run.welcome_needed(app)
    first_run.mark_welcome_done(app)
    assert not first_run.welcome_needed(app)
    assert first_run.flag_path().read_text(encoding="utf-8") == "ok"


def test_reinstall_shows_welcome_again_even_with_an_old_flag(tmp_path):
    import first_run
    app = tmp_path / "app"
    app.mkdir()
    first_run.flag_path().write_text("ok")                      # left behind by an older build
    (app / first_run.STAMP_NAME).write_text("1.6.0-preview 2026-11-03 08:30:00")
    assert first_run.welcome_needed(app)
    first_run.mark_welcome_done(app)
    assert not first_run.welcome_needed(app)
    # A silent update keeps the stamp: no welcome.
    assert not first_run.welcome_needed(app)
    # The user runs Setup again: a new stamp, so the welcome comes back.
    (app / first_run.STAMP_NAME).write_text("1.6.0-preview 2026-11-20 09:00:00")
    assert first_run.welcome_needed(app)


def test_installer_stamp_with_bom_and_velopack_reset(tmp_path):
    import first_run
    app = tmp_path / "app"
    app.mkdir()
    (app / first_run.STAMP_NAME).write_bytes(b"\xef\xbb\xbfstamp-1\r\n")
    assert first_run.install_stamp(app) == "stamp-1"
    first_run.mark_welcome_done(app)
    assert not first_run.welcome_needed(app)
    first_run.forget_welcome()
    assert first_run.welcome_needed(app)


def test_running_from_source_has_no_stamp():
    import first_run
    assert first_run.app_dir() is None and first_run.install_stamp() == ""


def _installer() -> str:
    raw = (ROOT / "installer.iss").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "Vietnamese [Messages] need the UTF-8 BOM to compile"
    return raw.decode("utf-8-sig")


def test_installer_is_one_click_per_user_with_no_ai_questions():
    iss = _installer()
    setup = re.search(r"^\[Setup\](.*?)^\[", iss, re.S | re.M).group(1)
    assert re.search(r"^PrivilegesRequired=lowest\s*$", setup, re.M)
    assert "PrivilegesRequiredOverridesAllowed" not in iss
    assert re.search(r"^DefaultDirName=\{userpf\}\\", setup, re.M)
    for page in ("DisableWelcomePage", "DisableDirPage", "DisableReadyPage", "DisableFinishedPage"):
        assert re.search(rf"^{page}=yes\s*$", setup, re.M), page
    assert not re.search(r"^LicenseFile=", setup, re.M)
    assert not re.search(r"^\[Tasks\]", iss, re.M)
    live = "\n".join(line for line in iss.splitlines() if not line.lstrip().startswith(";"))
    for word in ("ollama", "api key", "openai", "msgbox", "postinstall"):
        assert word not in live.lower(), word


def test_installer_writes_the_stamp_the_app_reads():
    import first_run
    iss = _installer()
    code = iss.split("[Code]", 1)[1]
    assert first_run.STAMP_NAME in code and "WizardSilent" in code and "ssInstall" in code
    assert f'{{app}}\\{first_run.STAMP_NAME}' in iss.split("[UninstallDelete]", 1)[1].split("[", 1)[0]


def test_setup_wizard_no_longer_opens_by_itself():
    wizard = pytest.importorskip("ui.setup_wizard")
    assert wizard.maybe_show_setup_wizard() is None


# ── 8. Welcome with 3 promises ─────────────────────────────────────────────────────────────────────

def test_welcome_has_the_three_promises_and_two_actions(qt_app):
    from ui.welcome import WelcomeCard
    card = WelcomeCard()
    assert [l.text() for l in card.promise_labels] == [
        "Số tính trên máy chị", "Hỏi trước khi sửa", "Nói thật khi chưa làm được"]
    assert card.start_button.text() == "Bắt đầu"
    assert card.activation_link.text() == "Tôi có mã kích hoạt"
    text = " ".join(w.text() for w in card.findChildren(type(card.promise_labels[0])))
    assert not re.search(r"(?i)api|ollama|openai|claude|gemini|provider|key\b", text)
    card.close()


def test_activation_link_is_a_stub_until_job_9_connects(qt_app):
    from ui.welcome import WelcomeCard
    card = WelcomeCard()
    card.activation_link.click()
    assert card.later_note.isVisibleTo(card)
    other = WelcomeCard()
    asked = []
    other.activation_requested.connect(lambda: asked.append(True))
    other.activation_link.click()
    assert asked == [True] and not other.later_note.isVisibleTo(other)
    card.close(), other.close()


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Manager:
    def __init__(self):
        self.sig_choices = _Signal()


def test_pressing_bat_dau_marks_the_welcome_and_offers_the_sample(qt_app):
    import first_run
    from office import sample
    from ui.welcome import WelcomeCard, start_after_welcome
    manager = _Manager()
    card = WelcomeCard()
    started = []
    card.started.connect(lambda: started.append(True))
    card.started.connect(lambda: start_after_welcome(manager))
    assert first_run.welcome_needed()
    card.present()
    card.start_button.click()
    assert started == [True] and not card.isVisible()
    assert not first_run.welcome_needed()
    (question, choices), = manager.sig_choices.calls
    assert question == sample.offer()[-1].text and choices == list(sample.OFFER_CHOICES)
    # The "Xem em làm" button is sent as typed text; the sample skill's trigger picks it up.
    assert re.match(sample.SAMPLE_TRIGGER, choices[0], re.I)
    card.close()


def test_main_shows_welcome_instead_of_the_wizard():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "first_run.welcome_needed()" in src and "start_after_welcome(manager)" in src
    assert "maybe_show_setup_wizard" not in src


# ── 12. Errors in plain words ──────────────────────────────────────────────────────────────────────

class com_error(Exception):
    """Shaped like pywintypes.com_error: (hresult, text, excepinfo, argerror)."""

    def __init__(self, hresult, text, excepinfo=None):
        super().__init__(hresult, text, excepinfo, None)
        self.hresult = hresult


class ConnectError(Exception):          # same name as httpx.ConnectError
    pass


def test_catalogue_has_every_error_the_spec_names():
    from engine import errors_vi as ev
    titles = {e.title for e in ev.CATALOGUE.values()}
    for title in ("Excel không trả lời", "Excel đang mở hộp thoại", "File đang được người khác mở",
                  "File .xls cũ", "Mất mạng", "Hết lượt dùng thử"):
        assert title in titles
    assert "phần chỉ dùng máy" in ev.CATALOGUE[ev.OFFLINE].hint
    assert ev.GENERIC in ev.CATALOGUE


@pytest.mark.parametrize("error, kind", [
    (com_error(-2147418111, "Call was rejected by callee."), "excel_dialog"),
    (com_error(-2147352567, "Exception occurred.", (0, None, None, None, 0, -2147418111)), "excel_dialog"),
    (com_error(-2147417846, "The message filter indicated that the application is busy."), "excel_dialog"),
    (com_error(-2147023174, "The RPC server is unavailable."), "excel_not_responding"),
    (com_error(-2147417848, "The object invoked has disconnected from its clients."), "excel_not_responding"),
    ("(-2147418111, 'Call was rejected by callee.', None, None)", "excel_dialog"),
    (PermissionError(13, "Permission denied", r"C:\Ke toan\BaoCao_T10.xlsx"), "file_locked"),
    ("The process cannot access the file because it is being used by another process", "file_locked"),
    ("'BaoCao_T10.xlsx' is locked for editing by 'Lan'.", "file_locked"),
    (r"Không đọc được C:\Ke toan\DonHang_T09.xls", "old_xls"),
    (ConnectionError("Connection refused"), "offline"),
    (socket.gaierror(11001, "getaddrinfo failed"), "offline"),
    (TimeoutError("timed out"), "offline"),
    ("httpx.ConnectTimeout: connection timed out", "offline"),
    (TimeoutError("Excel did not answer within 20 s"), "excel_not_responding"),
    ("Speech recognition timed out. A local model may still be downloading.", "generic"),
    (ConnectError("All connection attempts failed"), "offline"),
    (ValueError("could not convert string to float: 'abc'"), "generic"),
    ("", "generic"),
    (None, "generic"),
])
def test_classify(error, kind):
    from engine import errors_vi as ev
    assert ev.classify(error) == kind


def test_classify_sharing_violation_by_winerror_and_xls_by_path():
    from engine import errors_vi as ev

    class WinOSError(OSError):
        winerror = 32
    assert ev.classify(WinOSError("x")) == ev.FILE_LOCKED
    assert ev.classify(ValueError("x"), path=r"C:\a\TB_T10.XLS") == ev.OLD_XLS
    assert ev.classify(ValueError("x"), path=r"C:\a\TB_T10.xlsx") == ev.GENERIC
    assert ev.classify("opened BaoCao.xlsx and Sales.xlsm") == ev.GENERIC
    assert ev.classify(ev.TrialUsedUp()) == ev.TRIAL_USED_UP
    try:
        try:
            raise com_error(-2147023174, "The RPC server is unavailable.")
        except Exception as inner:
            raise RuntimeError("reading the sheet failed") from inner
    except RuntimeError as outer:
        assert ev.classify(outer) == ev.EXCEL_NOT_RESPONDING


def test_describe_never_shows_raw_text_and_says_unchanged_only_when_told(caplog):
    from engine import errors_vi as ev
    raw = com_error(-2147418111, "Call was rejected by callee.")
    with caplog.at_level(logging.WARNING, logger="clicky.errors"):
        shown = ev.describe(raw)
    assert shown["title"] == "Excel đang mở hộp thoại" and shown["reassurance"] == ""
    assert shown["buttons"] == ["Thử lại", "Báo lỗi"]
    on_screen = " ".join(str(v) for v in shown.values())
    assert "rejected" not in on_screen and "2147" not in on_screen
    assert "Call was rejected" in caplog.text                    # the raw text is in the log
    assert ev.describe(raw, file_unchanged=True)["reassurance"] == "File của chị chưa bị thay đổi gì"


def test_generic_card_keeps_the_apps_own_vietnamese_but_never_english():
    from engine import errors_vi as ev
    own = "câu này cần AI, mà máy chưa bật AI. Không cần AI, em vẫn làm được: \"làm báo cáo tháng từ thư mục này\"."
    assert ev.describe(own)["hint"].startswith("Câu này cần AI")
    english = ev.describe("'NoneType' object has no attribute 'write'")
    assert english["hint"] == ev.CATALOGUE[ev.GENERIC].hint
    assert ev.describe("Traceback (most recent call last): lỗi")["hint"] == ev.CATALOGUE[ev.GENERIC].hint


def test_month_close_load_errors_use_the_shared_words():
    from office.month_close import load_error_vi
    assert load_error_vi("(-2147418111, 'Call was rejected by callee.', None, None)").startswith(
        "Excel đang mở hộp thoại.")
    assert load_error_vi("Expression.Error: The column 'Ngay' of the table wasn't found.") == \
        "Excel không tìm thấy cột Ngay trong file đơn hàng."
    assert load_error_vi("something else") == "Excel báo lỗi khi tải query."


# ── 12. "Báo lỗi": masked log, previewed, never a file ─────────────────────────────────────────────

def _write_log(text: str) -> Path:
    from storage import data_dir
    path = data_dir() / "clicky.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_report_is_masked_previewed_and_kept_locally():
    from engine import bug_report
    from storage import data_dir
    _write_log("2026-11-03 INFO opened workbook for Công ty Minh Anh\n2026-11-03 WARNING Call was rejected\n")
    report = bug_report.prepare("excel_dialog", masker=lambda t: t.replace("Minh Anh", "Khách #01"), with_log=True)
    assert report.ready
    assert "Khách #01" in report.body and "Minh Anh" not in report.body
    assert "Lỗi: Excel đang mở hộp thoại" in report.body
    path = bug_report.send(report)
    assert path.parent == data_dir() / "bao-loi" and path.read_text(encoding="utf-8") == report.body
    assert bug_report.send(report) != path                       # a second report never overwrites the first


def test_report_sends_nothing_without_a_working_masker(monkeypatch):
    from engine import bug_report
    from storage import data_dir
    _write_log("Công ty Minh Anh MST 0101234567\n")
    monkeypatch.setattr(bug_report, "find_masker", lambda: None)
    report = bug_report.prepare("generic", with_log=True)
    assert not report.ready and report.body == "" and "Bản sau" in report.message
    assert bug_report.send(report) is None and not (data_dir() / "bao-loi").exists()

    def broken(_text):
        raise RuntimeError("boom")
    assert not bug_report.prepare("generic", masker=broken, with_log=True).ready


def test_report_uses_the_job_6_masker_by_default_and_hides_the_windows_user(monkeypatch):
    pytest.importorskip("engine.redact")
    from engine import bug_report
    home = str(Path.home())
    _write_log(f"opened {home}/BaoCao_T10.xlsx; MST: 0101234567-001\n")
    report = bug_report.prepare("generic", with_log=True)
    assert report.ready
    assert "0101234567" not in report.body and home not in report.body


def test_error_card_and_preview(qt_app, monkeypatch):
    from ui.error_card import ErrorCard, ReportPreview
    from engine import bug_report
    card = ErrorCard()
    shown = card.show_error("The RPC server is unavailable.")
    assert card.title.text() == "Excel không trả lời" and not card.unchanged_row.isVisibleTo(card)
    assert card.retry_button.text() == "Thử lại" and card.report_button.text() == "Báo lỗi"
    card.show_error("The RPC server is unavailable.", file_unchanged=True)
    assert card.unchanged_row.isVisibleTo(card)
    kinds = []
    card.report_requested.connect(kinds.append)
    card.retry_requested.connect(kinds.append)
    card.report_button.click()
    card.retry_button.click()
    assert kinds == [shown["kind"]] * 2

    _write_log("2026-11-03 INFO opened Công ty Minh Anh\n2026-11-03 WARNING com_error -2147418111\n")
    preview = ReportPreview()
    preview.preview("excel_not_responding", masker=lambda t: t.replace("Minh Anh", "Khách #01"))
    # Counts only by default: the consent lines, and not one word of the log.
    body = preview.report.body
    assert "Chế độ: chỉ gửi số đếm, không gửi nội dung" in body and "Nhật ký: 2 dòng · 1 cảnh báo · 0 lỗi" in body
    assert "com_error ×1" in body and "Minh Anh" not in body and "Khách #01" not in body
    assert preview.consent_values["to"].text() == bug_report.RECIPIENT
    assert preview.consent_values["where"].text() == bug_report.DESTINATION
    assert preview.consent_values["what"].text() == bug_report.COUNTS_ONLY
    assert not preview.with_log.isChecked()
    preview.with_log.setChecked(True)                                  # the user chooses to add the log
    assert "Khách #01" in preview.report.body and "Minh Anh" not in preview.report.body
    assert preview.consent_values["what"].text() == bug_report.WITH_LOG
    assert preview.text.toPlainText() == preview.report.body and preview.send_button.isVisibleTo(preview)
    sent = []
    preview.sent.connect(sent.append)
    preview.send_button.click()
    assert sent and Path(sent[0]).read_text(encoding="utf-8") == preview.report.body
    assert preview.lead.text() == bug_report.SAVED

    monkeypatch.setattr(bug_report, "find_masker", lambda: None)
    empty = ReportPreview()
    assert empty.preview("generic").ready                              # counts need no masker
    empty.with_log.setChecked(True)                                    # no masker: the log never goes
    assert not empty.with_log.isChecked() and empty.lead.text() == bug_report.LOG_NOT_READY
    assert "Chế độ: chỉ gửi số đếm" in empty.report.body and empty.send_button.isVisibleTo(empty)
    card.close(), preview.close(), empty.close()
