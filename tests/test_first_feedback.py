"""Job 13: after the first report on the user's own files Clicky asks once how much time it saved, keeps the answer on
this PC with the version and the run's time, and never anything from the files."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from engine import feedback
from office import month_close as mc


def test_four_choices_and_one_answer_kept_on_this_pc():
    assert feedback.due()
    info = feedback.card(83.6)
    assert info["eyebrow"] == "XONG BÁO CÁO ĐẦU TIÊN" and info["question"] == "Lần này em giúp chị bớt được bao lâu?"
    assert info["choices"] == ["Chưa bớt được", "Dưới 1 giờ", "1 đến 4 giờ", "Nửa ngày trở lên"]
    assert info["run_s"] == 83 and info["send"] == "Gửi góp ý" and info["later"] == "Để sau"
    entry = feedback.record("1 đến 4 giờ", "  Cột 521 khó hiểu  ", 83, now=0)
    assert entry["saved"] == "1to4h" and entry["note"] == "Cột 521 khó hiểu" and entry["run_s"] == 83
    assert entry["asked"] == 1 and entry["sent"] is False and entry["version"]
    assert set(entry) == {"time", "version", "saved", "note", "run_s", "asked", "sent"}     # nothing from the files
    assert feedback.answers() == [json.loads(json.dumps(entry))]
    assert not feedback.due()                                   # answered: never asked again


def test_later_asks_again_at_most_three_times():
    for _ in range(feedback.ASK_LIMIT):
        assert feedback.due()
        feedback.card(None)
    assert not feedback.due()
    assert feedback.card(None)["eyebrow"] == "XONG BÁO CÁO"


def test_only_a_choice_on_the_card_is_kept():
    with pytest.raises(ValueError):
        feedback.record("2 giờ")
    assert feedback.answers() == [] and feedback.due()
    assert len(feedback.record("Dưới 1 giờ", "x" * 5000)["note"]) == feedback.NOTE_LIMIT


def _manager():
    return SimpleNamespace(sig_feedback=Mock())


DELIVERED = mc.Beat("Em đã mở lại file đã lưu: khớp sổ cái 0 đ.", None, "Khớp sổ cái", "ok", "0 đ")


def test_asked_after_the_users_own_report_is_saved_and_rechecked(monkeypatch):
    monkeypatch.setattr(mc, "_RUN_T0", 100.0)
    monkeypatch.setattr(mc.time, "monotonic", lambda: 412.0)
    manager = _manager()
    mc._ask_feedback(manager, [DELIVERED])
    (info,), _ = manager.sig_feedback.emit.call_args
    assert info["run_s"] == 312 and info["choices"][0] == "Chưa bớt được"


def test_not_asked_when_the_saved_file_did_not_match_or_after_an_answer():
    manager = _manager()
    mc._ask_feedback(manager, [mc.Beat("File đã lưu không giống số em tính.", "C7", "File đã lưu bị lệch", "bad")])
    mc._ask_feedback(manager, [])
    manager.sig_feedback.emit.assert_not_called()
    feedback.record("Dưới 1 giờ")
    mc._ask_feedback(manager, [DELIVERED])
    manager.sig_feedback.emit.assert_not_called()
    mc._ask_feedback(SimpleNamespace(), [DELIVERED])            # a manager without the card (robot tools) is fine


def test_the_sample_never_asks():
    """The sample saves through save_and_verify itself; only the buttons a user presses ask."""
    import inspect
    from office import sample
    assert "_ask_feedback" not in inspect.getsource(sample)
    for handler in (mc.handle_save, mc.handle_verify):
        assert "_ask_feedback(manager, beats)" in inspect.getsource(handler)


def test_the_card_sends_the_pick_and_note_and_logs_neither_note_nor_file(qt_app, monkeypatch):
    from ui.feedback_card import FeedbackCard
    import scene_log
    events = []
    monkeypatch.setattr(scene_log, "record", lambda event, **data: events.append((event, data)))
    card = FeedbackCard()
    card.ask(feedback.card(42))
    assert [c.text() for c in card.chips()] == [label for _, label in feedback.CHOICES]
    assert not card.send.isEnabled()                            # a pick first
    sent = []
    card.sent.connect(lambda *a: sent.append(a))
    card.chips()[2].click()
    assert card.send.isEnabled()
    card.note.setPlainText("Khách hàng Công ty ABC bị đếm hai lần")
    card.send.click()
    assert sent == [("1 đến 4 giờ", "Khách hàng Công ty ABC bị đếm hai lần", 42)]
    assert ("feedback", {"choice": "1 đến 4 giờ", "has_note": True}) in events
    assert not any("ABC" in json.dumps(d, ensure_ascii=False) for _, d in events)
    assert any(e == "feedback_card" for e, _ in events) and not card.isVisible()


def test_later_closes_the_card_without_an_answer(qt_app):
    from ui.feedback_card import FeedbackCard
    card = FeedbackCard()
    card.ask(feedback.card(None))
    later = []
    card.later.connect(lambda: later.append(True))
    card.skip.click()
    assert later == [True] and not card.isVisible() and feedback.answers() == []


def test_the_card_says_nothing_technical(qt_app):
    import re
    import tech_mode
    from ui.feedback_card import FeedbackCard
    card = FeedbackCard()
    card.ask(feedback.card(None))
    words = re.compile(r"\b(" + "|".join(tech_mode.HIDDEN_WORDS) + r")\b", re.I)
    texts = [card.eyebrow.text(), card.question.text(), card.note_question.text(), card.note.placeholderText(),
             card.send.text(), card.skip.text()] + [c.text() for c in card.chips()]
    assert all(texts) and not any(words.search(t) for t in texts)
    card.hide()
