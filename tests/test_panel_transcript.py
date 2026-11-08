"""The chat must show its newest message after the panel was hidden while Clicky guided on screen."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")


def test_text_added_while_hidden_is_scrollable_and_shows_the_end():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ui.panel import CompanionPanel
    panel = CompanionPanel()
    panel.show()
    panel.begin_turn("làm báo cáo tháng từ thư mục này")
    app.processEvents()
    panel.hide()
    for i in range(12):
        panel.append_response_chunk(("\n\n" if i else "") + f"Câu số {i} đủ dài để xuống dòng trong khung chat của Clicky.")
    panel.begin_turn("Sửa giúp tôi")
    panel.append_response_chunk("Đã sửa xong. Excel khớp sổ cái đến từng đồng.")
    panel.show()
    for _ in range(5):
        app.processEvents()
    bar = panel._scroll.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    panel.close()
