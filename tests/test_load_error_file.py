"""Grader F6 (Case 05): a failed Power Query load says which file, which column when Excel says, and one next step.
The per-file query names the file in its error ("Tệp <name>: …"); clicky.log keeps only the name's shape."""
import logging

from engine import m_code
from engine.log_safe import scrub
from office import month_close
from test_month_close import FakeBridge, FakePQ, folder  # noqa: F401 - folder is a fixture
from engine import sources

KIND = "[Expression.Error] Tệp Doanh thu T10 (MISA).xls: The field 'Kind' of the record wasn't found."


def test_the_per_file_query_names_the_file_a_read_fails_on():
    m = m_code.order_files_query("ThuMuc", {"a.xls": {"order": "vi-VN", "encoding": 65001}}, ["amount", "date", "doc"],
                                 None)
    reader = m[m.index("    DonHang = (b as binary"):m.index("    ThamSo =")]
    assert "try List.Buffer(List.Transform(DocDon(b, ten, ext), List.Buffer))" in reader
    assert '"Tệp " & ten & ": " & kq[Error][Message]' in reader and "Record.HasFields(ThamSo, ten)" in reader
    assert "otherwise {}" not in m[m.index("    DocDon ="):m.index("    DonHang = (b as binary")]


def test_each_failure_names_the_file_and_one_next_step():
    kind = month_close.load_error_vi(KIND)
    assert "file Doanh thu T10 (MISA).xls" in kind and "Save As" in kind and "CSV UTF-8" in kind
    col = month_close.load_error_vi("[Expression.Error] Tệp POS.csv: The column 'Giá trị' of the table wasn't found.")
    assert "file POS.csv" in col and "cột \"Giá trị\"" in col and "còn cột đó không" in col
    cell = month_close.load_error_vi("[DataFormat.Error] Tệp POS.csv: We couldn't convert to Number. Details: Công ty ABC")
    assert "file POS.csv" in cell and "sửa cho đúng" in cell and "ABC" not in cell       # never a cell's value
    assert month_close.load_error_file(KIND) == "Doanh thu T10 (MISA).xls"
    assert month_close.load_error_file("[Expression.Error] something else") is None


def test_the_scene_log_keeps_the_shape_of_the_name_and_clicky_log_the_category_only():
    assert scrub(KIND) == "[Expression.Error] Tệp <20 ký tự>.xls: The field 'Kind' of the record wasn't found."
    from engine.log_safe import kind_only
    raw = ("(-2147352567, 'Exception occurred.', (0, 'Microsoft Excel', \"[DataFormat.Error] Tệp POS.csv: We couldn't "
           "convert to Number. Details: Công ty ABC\", None, 0, -2146827284), None)")
    assert kind_only(raw) == "DataFormat.Error -2147352567 (một file đơn hàng)"


def test_the_card_names_the_file_and_the_log_does_not(folder, caplog):  # noqa: F811
    class FailingPQ(FakePQ):
        def load(self, name):
            return KIND.replace("Doanh thu T10 (MISA).xls", "DonHang_HN_T10.csv")
    bridge = FakeBridge(folder)
    pq = FailingPQ(bridge, folder)
    month_close._PENDING = None
    caplog.set_level(logging.INFO)
    try:
        month_close.start(bridge, pq, sources.DAY_FIRST)
        beats = month_close.confirm(bridge, pq)
    finally:
        month_close._PENDING = None
    bad = next(b for b in beats if b.label == "Query chưa tải được")
    assert "lỗi ở file DonHang_HN_T10.csv" in bad.text and "Save As" in bad.note
    assert "DonHang_HN_T10" not in caplog.text and "Kind" not in caplog.text       # the category only
    assert "Expression.Error" in caplog.text
