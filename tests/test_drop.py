"""The one drop place: dropped files or a folder become the month close's source, read where they are."""
import asyncio

import pytest

from engine import sources
from office import drop, month_close as mc, sample
from tests.test_month_close import CopyingBridge, FakePQ


@pytest.fixture(autouse=True)
def _fresh():
    mc.use_folder(None)
    mc._CHOSEN.clear()
    mc._PICKS.clear()
    yield
    mc.use_folder(None)


def _sample(tmp_path):
    return sample.prepare(tmp_path / "data")


def test_a_folder_or_files_from_one_folder_give_that_folder(tmp_path):
    book = _sample(tmp_path)
    folder = book.parent
    assert drop.source_folder([str(folder)]) == (folder, None)
    got, problem = drop.source_folder([str(folder / "TB_T10.xlsx"), str(folder / "TB_T09.xlsx")])
    assert problem is None and got == folder.resolve()


@pytest.mark.parametrize("make, words", [
    (lambda d: [], "chưa nhận được file"),
    (lambda d: [str(d / "a"), str(d / "b")], "một thư mục thôi"),
    (lambda d: [str(d / "anh.png")], "chỉ đọc được file Excel"),
    (lambda d: [str(d / "a" / "x.xlsx"), str(d / "b" / "y.csv")], "nhiều thư mục"),
    (lambda d: [str(d / "mat.xlsx")], "không mở được mat.xlsx"),
])
def test_what_cannot_be_used_is_said_plainly(tmp_path, make, words):
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
    (tmp_path / "anh.png").write_bytes(b"x")
    (tmp_path / "a" / "x.xlsx").write_bytes(b"x")
    (tmp_path / "b" / "y.csv").write_text("x")
    folder, problem = drop.source_folder(make(tmp_path))
    assert folder is None and words in problem
    assert "Traceback" not in problem and "Error" not in problem


def test_the_report_workbook_is_the_one_that_is_not_a_trial_balance(tmp_path):
    book = _sample(tmp_path)
    assert drop.report_workbook(book.parent) == book
    (book.parent / "Mau_SongHong_T10_ban-goc.xlsx").write_bytes(b"x")       # a kept original is never the report
    (book.parent / "~$Mau_SongHong_T10.xlsx").write_bytes(b"x")             # nor Excel's lock file
    assert drop.report_workbook(book.parent) == book
    (book.parent / "Khac.xlsx").write_bytes(b"x")                          # two candidates: Clicky does not guess
    assert drop.report_workbook(book.parent) is None


class _Excel:
    def __init__(self, folder=None, path=None):
        self.folder, self.path, self.opened = folder, path, []

    def workbook_folder(self):
        return self.folder

    def workbook_path(self):
        return self.path

    def open_workbook(self, path):
        self.opened.append(path)
        self.folder, self.path = str(_parent(path)), path
        return True


def _parent(path):
    from pathlib import Path
    return Path(path).parent


def test_nothing_open_opens_the_report_from_the_dropped_folder(tmp_path):
    book = _sample(tmp_path)
    excel = _Excel()
    folder, beats = drop.prepare(excel, [str(book.parent)])
    assert beats == [] and folder == book.parent and excel.opened == [str(book)]


def test_an_open_saved_report_elsewhere_reads_the_dropped_folder(tmp_path):
    book = _sample(tmp_path)
    (book.parent / "Khac.xlsx").write_bytes(b"x")                          # no single report to open
    excel = _Excel(str(tmp_path), str(tmp_path / "BaoCao.xlsx"))
    folder, beats = drop.prepare(excel, [str(book.parent)])
    assert beats == [] and folder == book.parent and excel.opened == []


def test_nothing_open_and_no_report_asks_to_open_it_and_changes_nothing(tmp_path):
    book = _sample(tmp_path)
    (book.parent / "Khac.xlsx").write_bytes(b"x")
    folder, beats = drop.prepare(_Excel(), [str(book.parent)])
    assert folder is None and "mở file báo cáo tháng" in beats[0].text and "chưa sửa gì" in beats[0].text


def test_the_receipt_reads_the_dropped_folder_not_the_workbook_s(tmp_path):
    book = _sample(tmp_path)
    elsewhere = tmp_path / "BaoCao"
    elsewhere.mkdir()
    bridge = CopyingBridge(elsewhere)                                       # its own folder holds no data
    pq = FakePQ(bridge, elsewhere)
    assert "không thấy file đơn hàng" in mc.start(bridge, pq, sources.DAY_FIRST)[0].text
    mc.use_folder(book.parent)
    beats = mc.start(bridge, pq, sources.DAY_FIRST)
    assert beats[-1].choices == mc.INTAKE_CHOICES
    assert "948 đơn" in beats[-1].text and bridge.writes == []


def test_typing_the_command_goes_back_to_the_workbook_s_own_folder(tmp_path):
    book = _sample(tmp_path)
    mc.use_folder(book.parent)

    class _M:
        _excel = CopyingBridge(tmp_path)

    played = []

    async def fake_play(manager, beats):
        played.extend(beats)

    original = mc.play
    mc.play = fake_play
    try:
        asyncio.run(mc.handle_start(_M(), "làm báo cáo tháng từ thư mục này"))
    finally:
        mc.play = original
    assert mc._SOURCE is None and "không thấy file đơn hàng" in played[0].text


def test_only_ready_modes_can_be_chosen(monkeypatch):
    store = {}
    monkeypatch.setattr(drop, "setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(drop, "set_setting", lambda k, v: store.__setitem__(k, v))
    assert drop.mode() == drop.DO_IT                                        # "Làm giúp em" by default
    assert drop.set_mode(drop.SHOW_ME) == drop.DO_IT                        # not ready in beta: never pretended
    assert drop.MODES[drop.SHOW_ME][1].startswith("Có ở bản sau")


def test_drop_words_open_the_zone():
    import re
    assert re.search(drop.DROP_TRIGGER, "thả file", re.I)
    assert re.search(sample.OWN_TRIGGER, "Giờ thử với file của tôi", re.I)
