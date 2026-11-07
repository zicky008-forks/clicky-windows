"""The robot answers Clicky's unknown-column question only from the answer key, and each run starts with no
remembered answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import columns   # noqa: E402

KEY = [{"file": "Shopee_T10.csv", "date": "Order date", "amount": "Total"}]


def question(need, options, file="Shopee_T10.csv"):
    return {"t": 1.0, "event": "close_step", "step": "column_question", "file": file, "need": need,
            "options": options}


def test_confirm_is_pressed_only_when_clicky_guessed_the_key_columns():
    step = question("confirm", [columns.COLUMN_YES, columns.COLUMN_NO])
    said = ('File Shopee_T10.csv có vẻ là file đơn hàng nhưng tên cột lạ, em chưa lấy số từ file này. '
            'Em hiểu cột "Order date" là ngày bán, cột "Total" là số tiền. Đúng không chị?')
    assert columns.answer(step, said, KEY) == (columns.COLUMN_YES, None)
    wrong = said.replace('"Total"', '"Qty"')
    hit, problem = columns.answer(step, wrong, KEY)
    assert hit is None and '"Qty"' in problem and '"Total"' in problem


def test_picks_the_key_column_and_never_guesses():
    hit, problem = columns.answer(question("date", ["Cột Order date", "Cột Ship date", columns.COLUMN_NO]), "", KEY)
    assert (hit, problem) == ("Cột Order date", None)
    hit, problem = columns.answer(question("amount", ["Cột Qty", "Cột Price", columns.COLUMN_NO]), "", KEY)
    assert hit is None and "Total" in problem and "Cột Qty" in problem
    hit, problem = columns.answer(question("date", ["Cột A", "Cột B"], file="Lazada.csv"), "", KEY)
    assert hit is None and "không có mục \"columns\"" in problem and "không đoán" in problem
    hit, problem = columns.answer(question("date", ["Cột A", "Cột B"]), "", None)
    assert hit is None and problem


def test_not_orders_and_file_named_without_extension():
    key = [{"file": "ghi_chu", "not_orders": True}]
    step = question("confirm", [columns.COLUMN_YES, columns.COLUMN_NO], file="ghi_chu.csv")
    assert columns.answer(step, "", key) == (columns.COLUMN_NO, None)


def test_answerer_answers_each_question_once_and_reports_what_it_could_not():
    a = columns.Answerer(KEY)
    q = question("date", ["Cột Order date", "Cột Ship date", columns.COLUMN_NO])
    beat = {"t": 1.1, "event": "beat", "text": "Cột nào là ngày bán hả chị?"}
    card = {"t": 1.2, "event": "choices", "labels": q["options"]}
    receipt = {"t": 0.5, "event": "choices", "labels": ["Đúng rồi, làm tiếp", "Để tôi xem"]}
    assert a(receipt, before=[]) is None                       # not a column question
    assert a(card, before=[q, beat]) == "Cột Order date"
    assert a(card, before=[q, beat]) is None                   # already answered
    other = columns.Answerer([])
    assert other(card, before=[q, beat]) is None and other.problems
    rows = columns.check_lines([q], [], other)
    assert rows[0][1] is False and rows[0][3] is True and "không đoán" in rows[0][2]
    ok = columns.check_lines([q], KEY, a)
    assert ok == [("Câu hỏi tên cột lạ được trả lời theo đáp án", True,
                   "1 câu hỏi, đã trả lời: Shopee_T10.csv: Cột Order date", True)]
    assert columns.check_lines([], [], columns.Answerer([])) == []
    not_asked = columns.check_lines([], KEY, columns.Answerer(KEY))
    assert not_asked[0][1] is False and not_asked[0][3] is False


def test_memory_is_set_aside_for_the_run_and_put_back(tmp_path):
    mem = tmp_path / columns.MEMORY_FILE
    mem.write_text('{"tester": {"date": "ngay"}}', encoding="utf-8")
    restore = columns.isolate_memory(tmp_path)
    assert not mem.exists()
    mem.write_text('{"run": {}}', encoding="utf-8")            # what Clicky remembered during the run
    restore()
    assert mem.read_text(encoding="utf-8") == '{"tester": {"date": "ngay"}}'
    assert not (tmp_path / (columns.MEMORY_FILE + columns.BACKUP_SUFFIX)).exists()
    # A crashed run left its backup: the backup is the tester's file, the leftover is the run's.
    (tmp_path / (columns.MEMORY_FILE + columns.BACKUP_SUFFIX)).write_text("tester", encoding="utf-8")
    mem.write_text("run", encoding="utf-8")
    restore = columns.isolate_memory(tmp_path)
    assert not mem.exists()
    restore()
    assert mem.read_text(encoding="utf-8") == "tester"
    empty = tmp_path / "trong"
    empty.mkdir()
    columns.isolate_memory(empty)()                            # nothing remembered: nothing to do
    assert not (empty / columns.MEMORY_FILE).exists()


def test_auto_press_gives_the_answerer_the_events_before_the_card():
    import rehearse

    class D:
        def __init__(self):
            self.pressed = []

        def wait_for(self, name, timeout):
            return True

        def press(self, name):
            self.pressed.append(name)

    d = D()
    q = question("amount", ["Cột Total", "Cột Qty", columns.COLUMN_NO])
    events = [q, {"event": "beat", "text": "Còn số tiền bán là cột nào hả chị?"},
              {"event": "choices", "labels": q["options"]}]
    rehearse.AUTO_PRESS.clear()
    rehearse.AUTO_PRESS.append((d, columns.Answerer(KEY), set()))
    try:
        rehearse._auto_press(events)
        rehearse._auto_press(events)
    finally:
        rehearse.AUTO_PRESS.clear()
    assert d.pressed == ["Cột Total"]
