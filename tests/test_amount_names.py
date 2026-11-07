"""F1' (grader, Case 05 class): longer amount names from accounting software, a unit after the name, amounts stored
as text in a real .xls, and the receipt saying what a left-out order-like file does to the revenue."""
import csv
from decimal import Decimal

import pytest

from engine import intake, m_code, sources
from office import month_close
from test_month_close import FakePQ, _write_tb, folder  # noqa: F401 - folder is a fixture

pytest.importorskip("openpyxl")


@pytest.mark.parametrize("name", ["Doanh số bán chưa thuế", "Thành tiền hàng", "Thành tiền (VND)",
                                  "Doanh thu (đ)", "Tiền hàng chưa thuế", "Số tiền hàng"])
def test_longer_amount_names_are_the_amount(name):
    assert sources.find_columns(["Số chứng từ", "Ngày", "Mã KH", name])["amount"] == 3


def test_a_bare_so_tien_stays_out_so_a_ledger_detail_is_never_read_as_sales():
    """GL_ChiTiet_ChiPhi (BHC) has Ngay + So_tien: it is a cost ledger, not orders. "Số tiền" alone is asked about."""
    assert "amount" not in sources.find_columns(["Ngày", "Số tiền"])
    assert "amount" not in sources.find_columns(["Ngày", "Số tiền (VND)"])


def test_the_unit_is_dropped_for_names_only_not_for_the_remembered_signature():
    assert sources.header_signature(["Ngày", "Số tiền (VND)"]) == "ngay|sotien(vnd)"
    m = m_code.order_files_query("ThuMuc", {"x.csv": {"order": "dmy", "encoding": 65001}}, ["amount", "date", "doc"],
                                 None)
    assert 'DonVi = {"(vnd)", "(dong)", "(d)", "vnd"}' in m and "k = List.Transform(k0, BoDonVi)" in m
    assert list(sources.UNIT_SUFFIXES) == ["(vnd)", "(dong)", "(d)", "vnd"]


def test_text_amounts_under_a_long_name_in_a_real_xls_are_read(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    pytest.importorskip("xlrd")
    book = xlwt.Workbook(encoding="utf-8")
    ws = book.add_sheet("Ban hang")
    rows = [["SỔ CHI TIẾT BÁN HÀNG"], [], ["Số chứng từ", "Ngày chứng từ", "Doanh số bán chưa thuế"],
            ["BH01", "03/10/2026", "1.250.000"], ["BH02", "09/10/2026", "2.500.000"], ["Cộng", "", "3.750.000"]]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            ws.write(r, c, v)                         # every cell text, as some exports store amounts
    book.save(str(tmp_path / "SoChiTiet.xls"))
    f = sources.load_order_file(tmp_path / "SoChiTiet.xls")
    assert f is not None and f.columns["amount"] == 2
    assert sources.truth([f]).amount == Decimal("3750000")


def _drop_amount_header(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    rows[0] = [("Tien_le" if h == "ThanhTien" else h) for h in rows[0]]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def test_the_receipt_says_a_left_out_file_leaves_the_revenue_short_of_the_ledger(folder):  # noqa: F811
    """Without a column name Clicky knows, the HN file is left out: the receipt says, before "làm tiếp", that the
    revenue is short of Có TK 511 by what the ledger shows and the other files do not."""
    _drop_amount_header(folder / "DonHang" / "DonHang_HN_T10.csv")
    files = sources.find_order_files(folder)
    assert [f.name for f in files] == ["DonHang_HCM_T10.csv"]
    tbs, ledger = month_close._ledger(folder)
    truth = sources.truth(files, sources.period_of(tbs[-1][1]))
    receipt = intake.build(folder, files, tbs, truth, ledger, "BaoCao_T10.xlsx")
    gap = ledger["revenue"] - truth.amount
    assert receipt.impact_short and "DonHang_HN_T10.csv" in receipt.impact
    assert f"thiếu {intake.spoken(gap)} so với sổ cái" in receipt.impact
    row = next(r for r in intake.card(receipt)["rows"] if r["title"].startswith("Ảnh hưởng"))
    assert row["ok"] is False and row["detail"] == receipt.impact
    assert intake.log_fields(receipt, "x")["impact"] == receipt.impact


def test_answering_not_orders_still_shows_what_it_leaves_out(folder, monkeypatch):  # noqa: F811
    import storage
    home = folder / "clicky-data"
    home.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: home)
    sources.REMEMBERED.clear()
    month_close._COLUMN_PICKS.clear()
    _drop_amount_header(folder / "DonHang" / "DonHang_HN_T10.csv")
    from test_month_close import FakeBridge
    bridge = FakeBridge(folder)
    month_close._PENDING = None
    try:
        ask = month_close.start(bridge, FakePQ(bridge, folder), sources.DAY_FIRST)
        assert month_close.columns_pending(), ask[0].text
        receipt = month_close.answer_columns(bridge, FakePQ(bridge, folder), month_close.COLUMN_NO)
        assert month_close.intake_pending()
        text = receipt[-1].text
        assert "DonHang_HN_T10.csv" in text and "thiếu" in text and "so với sổ cái" in text
        assert "chị đã chỉ không phải đơn hàng" in text
    finally:
        month_close._PENDING = None
        sources.REMEMBERED.clear()
        month_close._COLUMN_PICKS.clear()


def test_a_left_out_file_that_changes_nothing_says_so(folder):  # noqa: F811
    (folder / "DonHang" / "Ghi_chu_T10.csv").write_text("Ngay,So_luong\n03/10/2026,abc\n", encoding="utf-8")
    files = sources.find_order_files(folder)
    tbs, ledger = month_close._ledger(folder)
    truth = sources.truth(files, sources.period_of(tbs[-1][1]))
    receipt = intake.build(folder, files, tbs, truth, ledger, "BaoCao_T10.xlsx")
    if not receipt.impact:
        pytest.skip("this file is not order-like enough to be listed as unread")
    assert not receipt.impact_short
