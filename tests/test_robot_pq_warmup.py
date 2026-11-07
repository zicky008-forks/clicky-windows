"""The robot reads the receipt for as long as asked before pressing, reports Clicky's Power Query warm-up timings,
and fails a run that leaves Clicky's hidden warm-up workbook open."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import rehearse   # noqa: E402


def timing(part, ms):
    return {"event": "close_step", "step": "timing", "part": part, "ms": ms}


def test_warm_up_timings_and_closed_workbook():
    events = [timing("pq_warm_open", 900), timing("pq_warm_add", 5100), timing("pq_warm_load", 3600),
              timing("pq_warm_total", 9700), timing("formula_write", 15), timing("pq_warm_wait", 1200)]
    info, closed = rehearse.warmup_checks(events, still_open=False)
    assert not info.blocking and info.ok
    assert info.detail == "open 900 ms, add 5100 ms, load 3600 ms, total 9700 ms, wait 1200 ms"
    assert closed.ok and closed.blocking


def test_warm_up_workbook_left_open_blocks():
    out = rehearse.warmup_checks([timing("pq_warm_total", 9700)], still_open=True)
    assert out[-1].blocking and not out[-1].ok and rehearse.WARM_BOOK in out[-1].detail
    assert rehearse.warmup_checks([], still_open=False) == []      # warm-up off (CLICKY_PQ_WARMUP=0): no lines
    assert rehearse.warmup_checks([], still_open=None) == []       # Excel could not be asked: no claim


def test_warm_book_open_asks_by_name_only():
    class Books:
        def __init__(self, open_):
            self.open_ = open_

        def __call__(self, name):
            assert name == rehearse.WARM_BOOK
            if not self.open_:
                raise Exception("(-2147352565, 'Invalid index.', None, None)")
            return object()

    class Excel:
        def __init__(self, open_):
            self.Workbooks = Books(open_)

    assert rehearse.warm_book_open(Excel(True)) is True
    assert rehearse.warm_book_open(Excel(False)) is False
    assert rehearse.warm_book_open(None) is None


def test_receipt_press_waits_the_reading_time(monkeypatch):
    slept, pressed = [], []

    class Driver:
        def wait_for(self, name, timeout):
            return True

        def press(self, name):
            pressed.append(name)

    monkeypatch.setattr(rehearse, "say", lambda *a, **k: None)
    monkeypatch.setattr(rehearse.time, "sleep", slept.append)
    rehearse.AUTO_PRESS.clear()
    rehearse.PRESS_DELAY.clear()
    rehearse.AUTO_PRESS.append((Driver(), {"Đúng rồi, làm tiếp", "Lưu và kiểm tra"}, set()))
    rehearse.PRESS_DELAY["Đúng rồi, làm tiếp"] = 8.0
    rehearse._auto_press([{"event": "choices", "labels": ["Đúng rồi, làm tiếp"]},
                          {"event": "choices", "labels": ["Lưu và kiểm tra"]}])
    rehearse.AUTO_PRESS.clear()
    rehearse.PRESS_DELAY.clear()
    assert pressed == ["Đúng rồi, làm tiếp", "Lưu và kiểm tra"] and slept == [8.0]


def test_state_line_counts_the_warm_up_workbook():
    line = rehearse.state_line("sau lượt 1", {"excel": 1, "workbooks": 2, "warm_books": 1})
    assert "sổ khởi động PQ 1" in line
    assert "sổ khởi động" not in rehearse.state_line("x", {"excel": 1, "workbooks": 1})


def test_skip_reason_and_what_the_press_waited_for():
    events = [timing("pq_warm_skip", 3) | {"why": "no_excel"}]
    (info,) = rehearse.warmup_checks(events, still_open=False)
    assert not info.ok and not info.blocking and "bỏ qua: no_excel" in info.detail
    events = [timing("pq_warm_total", 4800), timing("pq_warm_wait", 900) | {"by": "read_files", "timed_out": False}]
    info, closed = rehearse.warmup_checks(events, still_open=False)
    assert info.detail == "total 4800 ms, wait 900 ms (chờ read_files)" and closed.ok
