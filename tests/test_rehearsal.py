"""The rehearsal robot's grading rules and the scene log Clicky writes for it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import checks    # noqa: E402
import drivers   # noqa: E402

EXPECTED = json.loads((Path(__file__).resolve().parents[1] / "tools" / "dien_tap" / "case01_expected.json")
                      .read_text(encoding="utf-8"))
BOX = [100.0, 200.0, 400.0, 220.0]


def good_run():
    spot = {"kind": "spotlight", "x1": 100.0, "y1": 200.0, "x2": 400.0, "y2": 220.0, "tone": "bad"}
    return [
        {"t": 0.0, "event": "start"},
        {"t": 1.0, "event": "turn", "text": "làm báo cáo tháng từ thư mục này"},
        {"t": 5.0, "event": "beat", "i": 0, "ref": "'Doi chieu'!A7:E7", "box": BOX, "title": "Lệch"},
        {"t": 5.1, "event": "draw", "shape": spot},
        {"t": 5.2, "event": "draw", "shape": {"kind": "card"}},
        {"t": 9.0, "event": "spoken", "i": 0, "spoke": False},
        {"t": 9.1, "event": "notice", "text": "Máy chưa có giọng tiếng Việt"},
        {"t": 10.0, "event": "beat", "i": 1, "ref": None, "box": None, "text": "Em sửa nhé?"},
        {"t": 11.0, "event": "spoken", "i": 1, "spoke": False},
        {"t": 11.1, "event": "choices", "labels": ["Sửa giúp tôi", "Để tôi xem"]},
        {"t": 15.0, "event": "chosen", "label": "Sửa giúp tôi"},
        {"t": 60.0, "event": "beat", "i": 0, "ref": "'BC thang'!A20:G22", "box": BOX,
         "title": "Lợi nhuận sau thuế thực"},
        {"t": 60.1, "event": "draw", "shape": spot},
        {"t": 70.0, "event": "spoken", "i": 0, "spoke": False},
    ]


SHEETS = {
    "Doi chieu": [["Nguồn", "Số dòng", "Doanh thu (đ)"], ["Chênh lệch Excel − sổ cái", None, 0.0, 0.0, "Phải bằng 0"]],
    "BC thang": [["Lợi nhuận sau thuế", "", 556000000.0], ["Lợi nhuận sau thuế thực", "", 436120000.0]],
}


def test_good_run_passes():
    res = checks.evaluate(good_run(), SHEETS, EXPECTED)
    assert res.passed, [(c.name, c.detail) for c in res.checks if not c.ok]


def test_dropped_box_fails_like_run_2():
    events = good_run()
    events[2]["box"] = None
    res = checks.evaluate(events, SHEETS, EXPECTED)
    failed = [c.name for c in res.checks if not c.ok]
    assert "Cảnh nào có ô thì ô đó hiện trên màn hình" in failed
    assert not res.passed


def test_spotlight_away_from_cell_fails():
    events = good_run()
    events[3] = {"t": 5.1, "event": "draw", "shape": {"kind": "spotlight", "x1": 0, "y1": 0, "x2": 50, "y2": 50}}
    res = checks.evaluate(events, SHEETS, EXPECTED)
    assert "Khung sáng nằm đúng ô Excel" in [c.name for c in res.checks if not c.ok]


def test_wrong_profit_and_open_gap_fail():
    sheets = {"Doi chieu": [["Chênh lệch Excel − sổ cái", None, -5420000000.0]],
              "BC thang": [["Lợi nhuận sau thuế thực", "", 556000000.0]]}
    res = checks.evaluate(good_run(), sheets, EXPECTED)
    failed = [c.name for c in res.checks if not c.ok]
    assert any("Chênh lệch" in n for n in failed) and any("436" in n for n in failed)


def test_silent_without_notice_fails_and_required_voice_fails():
    events = [e for e in good_run() if e["event"] != "notice"]
    assert not checks.evaluate(events, SHEETS, EXPECTED).passed
    assert not checks.evaluate(good_run(), SHEETS, dict(EXPECTED, voice="required")).passed


def test_user_facing_error_fails():
    events = good_run() + [{"t": 71, "event": "error", "text": "Skill error: boom"}]
    assert not checks.evaluate(events, SHEETS, EXPECTED).passed


def test_find_value_skips_text_and_takes_first_number_right():
    rows = [["x"], ["Lợi nhuận sau thuế thực", "", "ghi chú", 12.0, 99.0]]
    assert checks.find_value(rows, "lợi nhuận sau thuế THỰC") == 12.0
    assert checks.find_value(rows, "không có") is None


def test_report_md_says_verdict():
    res = checks.evaluate(good_run(), SHEETS, EXPECTED)
    md = checks.report_md(res, {"case": "Case 01", "steps": [("Mở Excel", 3.2)]})
    assert md.startswith("# Diễn tập Case 01: ĐẠT") and "| Mở Excel | 3.2 |" in md


def test_find_ref_in_agent_desktop_snapshot():
    tree = {"role": "window", "name": "Clicky", "children": [
        {"role": "edit", "name": "Tin nhắn cho Clicky", "ref_id": "@s1:e3"},
        {"role": "button", "name": "Gửi", "ref_id": "@s1:e4"},
        {"role": "group", "children": [{"role": "button", "name": "Sửa giúp tôi", "ref_id": "@s1:e9"}]}]}
    assert drivers.find_ref(tree, "Gửi") == "@s1:e4"
    assert drivers.find_ref(tree, "Sửa giúp tôi") == "@s1:e9"
    assert drivers.find_ref(tree, "Không có") is None


def test_window_ids_and_tasklist_parsing():
    data = {"windows": [{"id": "w-1", "title": "Clicky"}, {"id": "w-2", "title": "card"}]}
    assert drivers.window_ids(data) == ["w-1", "w-2"]
    out = '"Clicky.exe","1234","Console","1","200,000 K"\r\n"Clicky.exe","88","Console","1","9 K"\r\n'
    assert drivers.parse_tasklist(out) == {1234, 88}
    assert drivers.parse_tasklist("INFO: No tasks are running which match the specified criteria.") == set()


def test_scene_log_writes_only_when_switched_on(tmp_path, monkeypatch):
    import scene_log
    monkeypatch.delenv(scene_log.ENV, raising=False)
    scene_log.record("beat", i=0)
    target = tmp_path / "scene.jsonl"
    monkeypatch.setenv(scene_log.ENV, str(target))
    monkeypatch.setattr(scene_log, "_failed", False)
    scene_log.record("locate", ref="A1", excel_rect=(1, 2, 3, 4), box=(1.234, 2.0, 3, 4), when=object())
    lines = [json.loads(x) for x in target.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "locate" and lines[0]["excel_rect"] == [1, 2, 3, 4]
    assert lines[0]["box"][0] == 1.23 and isinstance(lines[0]["when"], str)


def test_month_close_play_logs_each_beat(tmp_path, monkeypatch):
    """play() records the beat, its box and whether it was read aloud, with no Excel needed."""
    import asyncio
    import scene_log
    from office import month_close
    target = tmp_path / "scene.jsonl"
    monkeypatch.setenv(scene_log.ENV, str(target))
    monkeypatch.setattr(scene_log, "_failed", False)
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)

    class Sig:
        def __init__(self):
            self.calls = []

        def emit(self, *a):
            self.calls.append(a)

    class Excel:
        def activate(self, _sheet):
            pass

        def screen_rect(self, _ref, _reveal=False):
            return (10, 20, 110, 40)

    class Manager:
        _excel = Excel()

        def __init__(self):
            for name in ("sig_guided", "sig_clear_drawings", "sig_draw", "sig_point_hold", "sig_point_at",
                         "sig_point_release", "sig_choices", "sig_response_chunk"):
                setattr(self, name, Sig())

        def _physical_to_logical_bbox(self, rect):
            return tuple(float(v) for v in rect)

        async def _reply_local(self, _text):
            return True

    beat = month_close.Beat("Lệch 5,42 tỷ.", "'Doi chieu'!A7:E7", "Chênh lệch", "bad", "5,42 tỷ")
    asyncio.run(month_close.play(Manager(), [beat]))
    events = [json.loads(x) for x in target.read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds == ["locate", "beat", "spoken"]
    assert events[1]["box"] == [10.0, 20.0, 110.0, 40.0] and events[2]["spoke"] is True
