"""The robot closes the "bớt được bao lâu" card with "Để sau", fails the sample if it asks, and leaves the tester's
feedback counts as they were."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import first_feedback as fb   # noqa: E402
import rehearse               # noqa: E402

VERIFY = {"event": "verify", "ok": True, "path": "x", "diffs": []}
ASKED = {"event": "close_step", "step": "feedback_card", "asked": True}
SHOWN = {"event": "feedback_card", "x": 1, "y": 2}
LATER = {"event": "feedback", "choice": "", "later": True}
END = {"event": "story_end", "beats": 2, "last": "", "choices": []}


def test_settled_waits_for_the_card_to_close():
    assert not fb.settled([{"event": "turn"}])
    assert not fb.settled([VERIFY])
    assert fb.settled([VERIFY, END])                      # nothing asked: the re-check story is the end
    assert not fb.settled([VERIFY, END, ASKED])           # asked, card not shown yet
    assert not fb.settled([VERIFY, END, ASKED, SHOWN])
    assert fb.settled([VERIFY, END, ASKED, SHOWN, LATER])


def test_check_lines_own_file():
    rows = fb.check_lines([VERIFY, END, ASKED, SHOWN, LATER], sample=False)
    assert [(ok, blocking) for _, ok, _, blocking in rows] == [(True, False), (True, True)]
    rows = fb.check_lines([VERIFY, END, ASKED, SHOWN], sample=False)
    assert rows[1][1] is False and rows[1][2] == "thẻ vẫn mở" and rows[1][3]
    rows = fb.check_lines([VERIFY, END], sample=False)
    assert rows == [("Hỏi \"bớt được bao lâu\" sau báo cáo đã lưu và khớp", False, "không hỏi", False)]
    assert fb.check_lines([dict(VERIFY, ok=False), END], sample=False) == []     # not delivered: not asked
    rows = fb.check_lines([VERIFY, ASKED], sample=False)
    assert rows[-1][1] is False and rows[-1][3]


def test_sample_must_not_ask():
    assert fb.check_lines([VERIFY, END], sample=True)[0][1] is True
    name, ok, detail, blocking = fb.check_lines([VERIFY, END, ASKED, SHOWN, LATER], sample=True)[0]
    assert not ok and blocking


def test_auto_press_answers_the_feedback_card_once(monkeypatch):
    pressed = []

    class Driver:
        def wait_for(self, name, timeout):
            return True

        def press(self, name):
            pressed.append(name)

    monkeypatch.setattr(rehearse, "say", lambda *a, **k: None)
    rehearse.AUTO_PRESS.clear()
    rehearse.AUTO_PRESS.append((Driver(), fb.Later(), set()))
    rehearse.AUTO_PRESS.append((Driver(), {"Lưu và kiểm tra"}, set()))
    events = [{"event": "choices", "labels": ["Để sau", "Lưu và kiểm tra"]}, SHOWN]
    rehearse._auto_press(events)
    rehearse._auto_press(events)
    rehearse.AUTO_PRESS.clear()
    assert pressed == ["Để sau", "Lưu và kiểm tra"]      # "Để sau" only for the card, and only once


def test_isolate_settings_keeps_other_keys_and_restores_counts(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"feedback_asked": 2, "feedback_done": True, "tts_provider": "auto"}), encoding="utf-8")
    restore = fb.isolate_settings(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"tts_provider": "auto"}
    # Clicky during the run: asks once and changes another setting.
    path.write_text(json.dumps({"feedback_asked": 1, "tts_provider": "edge"}), encoding="utf-8")
    restore()
    assert json.loads(path.read_text(encoding="utf-8")) == {"feedback_asked": 2, "feedback_done": True,
                                                             "tts_provider": "edge"}


def test_isolate_settings_removes_keys_the_tester_never_had(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"x": 1}), encoding="utf-8")
    restore = fb.isolate_settings(tmp_path)
    path.write_text(json.dumps({"x": 1, "feedback_asked": 1}), encoding="utf-8")
    restore()
    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 1}
    fb.isolate_settings(tmp_path / "none")()              # no settings file: nothing to do
