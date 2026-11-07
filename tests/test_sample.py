"""The sample month: copied out of the app's files, run by itself, closing card from code figures."""
import asyncio

from office import month_close as mc, sample
from office.month_close import Beat


def test_sample_folder_is_copied_fresh_and_the_bundled_one_stays(tmp_path):
    first = sample.prepare(tmp_path)
    assert first.name == "Mau_SongHong_T10.xlsx" and first.exists()
    assert sorted(p.name for p in (first.parent / "DonHang").glob("*.csv")) == [
        "DonHang_DN_T10.csv", "DonHang_HCM_T10.csv", "DonHang_HN_T10.csv"]
    assert (first.parent / "TB_T10.xlsx").exists() and (first.parent / "TB_T09.xlsx").exists()
    (first.parent / "TB_T10.xlsx").unlink()
    assert (sample.bundled() / "TB_T10.xlsx").exists()


def test_the_sample_reads_as_case_01(tmp_path):
    from engine import intake, sources
    book = sample.prepare(tmp_path)
    folder = book.parent
    files = sources.find_order_files(folder)
    tbs, ledger = mc._ledger(folder)
    truth = sources.truth(files, sources.period_of(tbs[-1][1]))
    receipt = intake.build(folder, files, tbs, truth, mc._net_of_returns(ledger, truth), book.name)
    assert truth.unique_rows == 948 and receipt.balanced and receipt.revenue_ok
    assert sources.returns_in_revenue(tbs[-1][2]) == 120_000_000


def test_closing_card_uses_the_run_s_own_figures_and_time(tmp_path):
    book = tmp_path / "Mau_SongHong_T10.xlsx"
    book.write_bytes(b"x")
    (tmp_path / "Mau_SongHong_T10_ban-goc.xlsx").write_bytes(b"x")
    mc._LAST.clear()
    mc._LAST.update(pat=556_000_000, overstated=120_000_000, real=436_000_000)
    card = sample.closing(74.4, book)
    assert card.label == "Xong trong 74 giây" and card.choices == ("Giờ thử với file của tôi", "Xem lại")
    assert "120 triệu" in card.text and "436 triệu" in card.text and "556 triệu" in card.text
    assert "bản gốc vẫn còn" in card.text
    assert sample.closing(185, book).label == "Xong trong 3 phút 5 giây"


def test_xem_em_lam_and_file_mau_are_understood():
    import skills
    skills.load_skills() if hasattr(skills, "load_skills") else None
    import re
    assert re.search(sample.SAMPLE_TRIGGER, "Xem em làm", re.I)
    assert re.search(sample.SAMPLE_TRIGGER, "xem lại", re.I)
    assert re.search(sample.OFFER_TRIGGER, "cho tôi xem file mẫu", re.I)


def test_sample_copy_is_writable_even_when_the_installed_one_is_read_only(tmp_path, monkeypatch):
    import shutil, stat
    src = tmp_path / "installed" / sample.SAMPLE
    shutil.copytree(sample.bundled(), src)
    for f in src.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IREAD)
    monkeypatch.setattr(sample, "bundled", lambda: src)
    book = sample.prepare(tmp_path / "data")
    assert book.stat().st_mode & stat.S_IWRITE


def test_sample_stops_when_excel_shows_another_workbook(tmp_path, monkeypatch):
    # Never run the month close on whatever workbook happens to be in front: it may be the user's own.
    played = []

    class Bridge:
        def open_workbook(self, path):
            return True

        def workbook_path(self):
            return "C:\\Users\\chi\\Documents\\65302810"

    class Manager:
        _excel = Bridge()

    async def play(_m, beats):
        played.extend(beats)

    async def no_wait(_s):
        return None
    monkeypatch.setattr(sample, "prepare", lambda: tmp_path / "Mau_SongHong_T10.xlsx")
    monkeypatch.setattr(mc, "play", play)
    monkeypatch.setattr(mc, "start", lambda *a: (_ for _ in ()).throw(AssertionError("month close started")))
    monkeypatch.setattr(sample.asyncio, "sleep", no_wait)
    asyncio.run(sample.run(Manager()))
    assert len(played) == 1 and "chưa mở được file mẫu" in played[0].text


def test_same_file_needs_both_paths():
    assert sample.same_file("/tmp/A/b.xlsx", "/tmp/A/b.xlsx")
    assert not sample.same_file(None, "/tmp/A/b.xlsx")
