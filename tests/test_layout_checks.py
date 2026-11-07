"""Robot checks for the real-time Excel build: nothing over a figure, the 3 graphics add up, latency logged."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import layout_checks   # noqa: E402

HERE = Path(__file__).resolve().parents[1] / "tools" / "dien_tap"
SPEC = json.loads((HERE / "case01_layout_expected.json").read_text(encoding="utf-8"))
TAGS = [{"account": "6411", "amount": 800_000_000}, {"account": "6417", "amount": 380_000_000},
        {"account": "6418", "amount": 200_000_000}]
BARS = [{"label": "Trên báo cáo", "amount": 556_000_000}, {"label": "Hàng trả lại ghi Nợ 511", "amount": -120_000_000},
        {"label": "Lợi nhuận thực", "amount": 436_000_000}]
CODES = ["wrong_side", "closing_mismatch", "profit_tie", "returns_in_revenue"]


def story(**over):
    leaders = {"kind": "leaders", "figure": 1_380_000_000, "sum": 1_380_000_000, "ok": True, "tags": TAGS}
    bridge = {"kind": "bridge", "ok": True, "bars": BARS}
    notes = {"kind": "notes", "notes": [{"codes": [c], "ref": "'BC thang'!A9:G9"} for c in CODES]}
    fix = {"kind": "notes", "notes": [{"codes": ["date_flip"], "ref": "'Doi chieu'!A5:G5"},
                                      {"codes": ["duplicate"], "ref": "'Doi chieu'!A9:G9"}]}
    leaders.update(over.get("leaders", {}))
    bridge.update(over.get("bridge", {}))
    return [
        {"t": 1, "event": "turn", "text": "làm báo cáo tháng"},
        {"t": 2, "event": "card", "rect": [10, 10, 300, 90], "covers_figures": over.get("card_covers", 0),
         "covers_range": False, "title": "Lệch"},
        {"t": 3, "event": "choice_card", "rect": [400, 400, 700, 480], "covers_figures": 0},
        {"t": 4, "event": "beat", "graphic": fix},
        {"t": 5, "event": "beat", "graphic": leaders},
        {"t": 6, "event": "beat", "graphic": bridge},
        {"t": 7, "event": "beat", "graphic": notes},
        {"t": 7.1, "event": "graphic", "kind": "notes", "rects": [[0, 0, 1, 1]], "covers_figures": 0,
         "covers_range": False, "notes": ["a"] * over.get("painted", 4), "total": 4},
        {"t": 8, "event": "latency", "source": "chat", "first_feedback": 420, "first_point": 900, "done": 60000},
    ]


def by_name(rows):
    return {c.name: c for c in rows}


def test_good_story_passes_every_layout_check():
    out = layout_checks.evaluate(story(), SPEC)
    assert all(c.ok for c in out), [c for c in out if not c.ok]
    assert len(out) == 9


def test_card_over_a_figure_fails_and_names_the_rect():
    out = by_name(layout_checks.evaluate(story(card_covers=2), SPEC))
    c = out["Thẻ, nút chọn và hình không che con số (Clicky tự đo)"]
    assert not c.ok and "che 2 số" in c.detail and "[10, 10, 300, 90]" in c.detail


def test_leaders_that_do_not_add_up_or_wrong_accounts_fail():
    out = by_name(layout_checks.evaluate(story(leaders={"sum": 1_300_000_000, "ok": False}), SPEC))
    assert not out["Hình dây nối: số → các tài khoản"].ok
    out = by_name(layout_checks.evaluate(story(leaders={"tags": TAGS[:2]}), SPEC))
    assert "cần tài khoản" in out["Hình dây nối: số → các tài khoản"].detail


def test_bridge_with_wrong_bars_fails():
    bars = [dict(BARS[0]), dict(BARS[1], amount=-100_000_000), dict(BARS[2], amount=456_000_000)]
    out = by_name(layout_checks.evaluate(story(bridge={"bars": bars}), SPEC))
    assert not out["Hình cầu lợi nhuận: báo cáo → thực"].ok


def test_dropped_note_fails():
    out = by_name(layout_checks.evaluate(story(painted=3), SPEC))
    assert not out["Không tờ giấy nhớ nào bị bỏ khi vẽ"].ok


def test_missing_trap_code_fails():
    spec = json.loads(json.dumps(SPEC))
    spec["layout"]["notes"]["codes"].append("opening_mismatch")
    out = by_name(layout_checks.evaluate(story(), spec))
    assert "opening_mismatch" in out["Giấy nhớ: mỗi lỗi trên sheet Kiem tra có một tờ"].detail


def test_older_build_without_layout_events_is_not_graded_unless_asked():
    assert layout_checks.evaluate(story(), {}) == []
    out = layout_checks.evaluate([{"t": 1, "event": "turn"}], SPEC)
    assert out and not any(c.ok for c in out if c.blocking)


def lat(**kw):
    return [{"t": 1, "event": "turn"}, {"t": 2, "event": "latency", "source": "chat", **kw}]


def test_code_turn_too_slow_fails_only_on_a_build_that_tags_the_mode():
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    rows = by_name(layout_checks.evaluate(lat(mode="code", first_feedback=1800, ready=900, first_point=1200), spec))
    c = rows["Lượt chạy bằng code: phản hồi đầu ≤ 1000 ms mọi lượt"]
    assert not c.ok and c.blocking
    old = by_name(layout_checks.evaluate(lat(first_feedback=1800), spec))
    assert not old["Lượt chạy bằng code: phản hồi đầu ≤ 1000 ms mọi lượt"].blocking


def test_pointer_must_fly_within_1500_ms_of_the_answer():
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    rows = by_name(layout_checks.evaluate(lat(mode="code", first_feedback=300, ready=4000, first_point=6000), spec))
    c = rows["Có kết quả rồi thì con trỏ bay tới trong ≤ 1500 ms"]
    assert not c.ok and "2000 ms" in c.detail
    rows = by_name(layout_checks.evaluate(lat(mode="code", first_feedback=300, ready=4000, first_point=4900), spec))
    assert rows["Có kết quả rồi thì con trỏ bay tới trong ≤ 1500 ms"].ok


def test_voice_and_ai_turns_are_measured_but_never_fail():
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    ev = [{"t": 1, "event": "turn"}, {"t": 2, "event": "latency", "source": "voice", "mode": "code",
                                      "first_feedback": 5000, "transcript": 2500, "first_point": 7000},
          {"t": 3, "event": "turn"}, {"t": 4, "event": "latency", "source": "chat", "mode": "ai", "first_feedback": 9000}]
    rows = layout_checks.latency_checks(ev, spec["layout"]["latency"])
    assert all(c.ok or not c.blocking for c in rows), rows


def test_widget_over_a_figure_fails():
    ev = story() + [{"t": 9, "event": "widget", "rect": [5, 5, 50, 50], "covers_figures": 1, "covers_range": False}]
    rows = by_name(layout_checks.evaluate(ev, SPEC))
    assert not rows["Thẻ, nút chọn và hình không che con số (Clicky tự đo)"].ok


def test_untimed_button_turns_fail_only_on_a_build_that_tags_the_mode():
    """37265be: the chat turn is timed, the button presses are not. Shown, but the run is not failed for it."""
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    turns = [{"t": 1, "event": "turn"}, {"t": 2, "event": "turn"}, {"t": 3, "event": "turn"}]
    old = by_name(layout_checks.evaluate(turns + [{"t": 4, "event": "latency", "source": "typed",
                                                   "first_feedback": 16}], spec))
    c = old["Mỗi lượt có đo thời gian phản hồi"]
    assert not c.ok and not c.blocking and "1 lần đo / 3 lượt" in c.detail
    new = by_name(layout_checks.evaluate(turns + [{"t": 4, "event": "latency", "source": "chat", "mode": "code",
                                                   "first_feedback": 16}], spec))
    assert new["Mỗi lượt có đo thời gian phản hồi"].blocking


def test_robot_waits_until_every_turn_has_its_latency_line():
    assert not layout_checks.turns_measured([{"event": "turn"}, {"event": "latency"}, {"event": "turn"}])
    assert layout_checks.turns_measured([{"event": "turn"}, {"event": "latency"}])
    assert layout_checks.turns_measured([])


READY = "Lượt chạy bằng code: tính xong ≤ 5000 ms (lượt nạp Power Query đầu ≤ 8000 ms, có dòng chữ báo chờ)"


def _confirm(ready, caption=True, load=True):
    ev = [{"t": 1, "event": "turn", "label": "Đúng rồi, làm tiếp"}]
    if load:
        ev.append({"t": 2, "event": "close_step", "step": "query_load", "status": "ok"})
    e = {"t": 3, "event": "latency", "source": "typed", "mode": "code", "label": "Đúng rồi, làm tiếp",
         "first_feedback": 0, "ready": ready, "first_point": ready + 150}
    if caption:
        e["first_caption"] = 0
    return ev + [e]


def test_ready_gates_from_the_lead():
    """84603e7 on the PC: cold 6828 ms with the caption up passes; warm 3563 passes; 8.5 s or no caption fails (lead: 8 s, 07/11)."""
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    assert by_name(layout_checks.evaluate(_confirm(6828), spec))[READY].ok
    assert by_name(layout_checks.evaluate(_confirm(3563), spec))[READY].ok
    assert not by_name(layout_checks.evaluate(_confirm(8500), spec))[READY].ok
    assert by_name(layout_checks.evaluate(_confirm(7531), spec))[READY].ok          # 9ea1aa4 lap 1, clean PC
    bad = by_name(layout_checks.evaluate(_confirm(6828, caption=False), spec))[READY]
    assert not bad.ok and "báo đang chờ" in bad.detail
    # Only the first query-loading turn gets the longer limit; a turn with no Power Query must be ≤ 5 s.
    assert not by_name(layout_checks.evaluate(_confirm(6000, load=False), spec))[READY].ok
    two = _confirm(6828) + [dict(e, t=e["t"] + 10) for e in _confirm(6000)]
    assert not by_name(layout_checks.evaluate(two, spec))[READY].ok


def test_unmeasured_first_feedback_is_not_a_zero():
    spec = {"layout": {"latency": SPEC["layout"]["latency"]}}
    c = by_name(layout_checks.evaluate(lat(mode="code", ready=900, first_point=1000), spec))[
        "Lượt chạy bằng code: phản hồi đầu ≤ 1000 ms mọi lượt"]
    assert "1 lượt chưa đo" in c.detail
