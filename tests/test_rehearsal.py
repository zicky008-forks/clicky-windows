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
    assert kinds == ["locate", "beat", "spoken", "story_end"]
    assert events[1]["box"] == [10.0, 20.0, 110.0, 40.0] and events[2]["spoke"] is True


def test_find_value_skips_a_title_that_only_mentions_the_label():
    rows = [["Đối chiếu doanh thu tháng: Power Query, file gốc và sổ cái", "", ""],
            ["Sổ cái TB_T10", "", 8400000000.0]]
    assert checks.find_value(rows, "sổ cái") == 8400000000.0


def test_text_expectations_catch_missing_warning_and_false_alarm():
    sheets = dict(SHEETS, **{"Kiem tra": [["Hàng trả lại ghi Nợ 511", "120 triệu"]]})
    exp = dict(EXPECTED, texts=[
        {"name": "Báo hàng trả lại", "text": "trả lại", "where": "sheets", "sheet": "Kiem tra", "must": True},
        {"name": "Không báo động sai về hoá đơn", "text": "hoá đơn trùng", "where": "any", "must": False},
        {"name": "Nói đến chỗ lệch", "text": "lệch", "where": "said", "must": True}])
    assert checks.evaluate(good_run(), sheets, exp).passed
    bad = good_run() + [{"t": 80, "event": "said", "text": "Có hoá đơn trùng"}]
    res = checks.evaluate(bad, {k: v for k, v in sheets.items() if k != "Kiem tra"}, exp)
    failed = [c.name for c in res.checks if not c.ok]
    assert failed == ["Báo hàng trả lại", "Không báo động sai về hoá đơn"]


class _FakeAuto:
    class PatternId:
        ValuePattern = 10002
        InvokePattern = 10000

    def __init__(self):
        self.calls = []


class _GenericControl:
    """Like uiautomation's Control returned by Control(Name=...): no GetValuePattern / GetInvokePattern."""

    def __init__(self, log):
        self.log = log

    def GetPattern(self, pid):
        log = self.log

        class P:
            def SetValue(self, text):
                log.append(("set", pid, text))

            def Invoke(self):
                log.append(("invoke", pid))
        return P()


def test_uia_driver_uses_patterns_by_id_on_generic_controls():
    d = drivers.Uia.__new__(drivers.Uia)
    d.auto = _FakeAuto()
    log = []
    d._find = lambda name: _GenericControl(log)
    d.set_text("Tin nhắn cho Clicky", "làm báo cáo")
    d.press("Gửi")
    assert log == [("set", 10002, "làm báo cáo"), ("invoke", 10000)]


def test_robot_crash_exits_2_not_1(monkeypatch):
    import rehearse

    def boom(argv=None):
        raise AttributeError("x")
    monkeypatch.setattr(rehearse, "main", boom)
    assert rehearse.run() == 2


def test_raw_com_error_inside_a_card_fails_the_error_check():
    run = good_run()
    run.append({"t": 61.0, "event": "beat", "i": 2, "ref": None, "box": None,
                "text": "Không đọc được: (-2147352567, 'Exception occurred.', [Expression.Error] ...)"})
    res = checks.evaluate(run, SHEETS, EXPECTED)
    bad = [c for c in res.checks if c.name == "Không có lỗi hiện ra cho người dùng"][0]
    assert not bad.ok and not res.passed
    assert checks.leaked_errors(good_run()) == []


def test_stalled_stage_is_its_own_failure():
    assert checks.stage_check([]).ok
    c = checks.stage_check([("Chặng 2–3", 302.4)])
    assert not c.ok and c.blocking and "302" in c.detail


def test_robot_presses_the_intake_confirm_when_the_case_does_not_grade_it(tmp_path):
    import rehearse

    class D:
        pressed = []

        def wait_for(self, name, timeout):
            return True

        def press(self, name):
            self.pressed.append(name)
    log = tmp_path / "scene.jsonl"
    log.write_text(json.dumps({"t": 1, "event": "choices", "labels": ["Đúng rồi, làm tiếp", "Có chỗ sai"]}) + "\n",
                   encoding="utf-8")
    d = D()
    rehearse.AUTO_PRESS[:] = [(d, {"Đúng rồi, làm tiếp"}, set())]
    try:
        rehearse.wait_log(log, lambda ev: True, 2)
        rehearse.wait_log(log, lambda ev: True, 2)
    finally:
        rehearse.AUTO_PRESS.clear()
    assert d.pressed == ["Đúng rồi, làm tiếp"]


def test_source_choice_matches_the_named_book_not_the_old_copy():
    import rehearse
    labels = ["Dùng TB_T10", "Dùng TB_T10_cu"]
    assert rehearse.match_label("TB_T10.xlsx", labels) == "Dùng TB_T10"
    assert rehearse.match_label("TB_T10_cu.xlsx", labels) == "Dùng TB_T10_cu"
    assert rehearse.match_label("KeHoach_2026.xlsx", labels) is None


def test_source_choice_never_matches_a_longer_name_sharing_the_prefix():
    import rehearse
    for labels in (["Dùng TB_T10 (1)", "Dùng TB_T10"], ["Dùng TB_T10", "Dùng TB_T10 (1)"]):
        assert rehearse.match_label("TB_T10.xlsx", labels) == "Dùng TB_T10"
        assert rehearse.match_label("TB_T10 (1).xlsx", labels) == "Dùng TB_T10 (1)"
    assert rehearse.match_label("TB_T10.xlsx", ["Dùng TB_T10 (1)", "Dùng TB_T10_cu"]) is None


def test_returns_line_may_be_negative_on_the_report():
    exp = {"numbers": [{"name": "Hàng trả lại 120 triệu", "sheet": "BC", "label": "hàng trả lại",
                        "value": 120000000, "tolerance": 1000000, "abs": True}]}
    sheets = {"BC thang": [["Trừ hàng trả lại", None, -120000000.0]]}
    res = checks.evaluate([], sheets, exp)
    assert [c for c in res.checks if c.name == "Hàng trả lại 120 triệu"][0].ok


def test_laps_pass_only_when_every_lap_passes(tmp_path):
    """--lan N is pass^k: one KHÔNG ĐẠT lap fails the whole run, and each lap has its own folder."""
    import rehearse
    seen, cleaned = [], []

    def runner(argv):
        seen.append(argv)
        return [0, 1, 0][len(seen) - 1]

    code = rehearse.run_laps(["--workbook", "x.xlsx", "--lan", "3", "--out", str(tmp_path)],
                             runner=runner, cleanup=cleaned.append, before=set)
    assert code == 1
    assert [a[-1] for a in seen] == [str(tmp_path / f"lan-{i}") for i in (1, 2, 3)]
    assert all("--lan" not in a for a in seen)
    assert len(cleaned) == 2                       # between laps only; the last lap stays open to inspect
    summary = (tmp_path / "tong-hop.md").read_text(encoding="utf-8")
    assert "KHÔNG ĐẠT (2/3 lượt đạt)" in summary


def test_laps_all_pass_and_could_not_run():
    import rehearse
    assert rehearse.run_laps(["--lan", "2"], runner=lambda a: 0, cleanup=lambda k: None, before=set) == 0
    assert rehearse.run_laps(["--lan", "2"], runner=lambda a: 2, cleanup=lambda k: None, before=set) == 2


def test_single_lap_is_unchanged():
    import rehearse
    seen = []
    assert rehearse.run_laps(["--workbook", "x.xlsx", "--out", "o"], runner=lambda a: seen.append(a) or 0) == 0
    assert seen == [["--workbook", "x.xlsx", "--out", "o"]]


def test_notes_scene_shows_sticky_notes_instead_of_a_lit_range():
    events = good_run() + [
        {"t": 80.0, "event": "beat", "i": 2, "ref": "'BC thang'!A1:G23", "box": BOX, "graphic": {"kind": "notes"}},
        {"t": 80.1, "event": "draw", "shape": {"kind": "notes", "notes": []}},
    ]
    res = checks.evaluate(events, SHEETS, EXPECTED)
    assert "Khung sáng nằm đúng ô Excel" not in [c.name for c in res.checks if not c.ok]
    events[-1] = {"t": 80.1, "event": "draw", "shape": {"kind": "card"}}
    res = checks.evaluate(events, SHEETS, EXPECTED)
    assert "Khung sáng nằm đúng ô Excel" in [c.name for c in res.checks if not c.ok]


def test_old_robot_copies_leave_the_case_folder_before_a_run(tmp_path):
    import rehearse
    case = tmp_path / "case"
    case.mkdir()
    src = case / "BaoCao_T10_Beta.xlsx"
    keep = [src, case / "NhatKy_Ban_Hang.xlsx", case / "BaoCao_T10_Beta_ban-goc.xlsx"]
    old = [case / "BaoCao_T10_Beta_dientap_1107_123225.xlsx",
           case / "BaoCao_T10_Beta_dientap_1107_123225_ban-goc.xlsx"]
    for f in keep + old + [case / "~$BaoCao_T10_Beta_dientap_1107_130000.xlsx"]:
        f.write_bytes(b"x")
    dest = tmp_path / "out" / "ban-sao-cu"
    moved = rehearse.park_old_copies(src, dest)
    assert sorted(moved) == sorted(f.name for f in old)
    assert all(f.exists() for f in keep)
    assert not any(f.exists() for f in old)
    assert sorted(p.name for p in dest.iterdir()) == sorted(f.name for f in old)
    assert rehearse.park_old_copies(src, dest) == []


def test_story_is_not_over_at_the_final_card_while_scenes_remain():
    """lan3-v2, 07/11: the robot graded 6 s after the profit card, while Clicky was still showing it
    (no voice, long reading time), and the bridge and sticky notes never came on screen."""
    import rehearse
    t = "Lợi nhuận sau thuế"
    ev = [{"event": "turn"}, {"event": "beat", "title": "Báo cáo tháng đã lập"},
          {"event": "beat", "title": "Lợi nhuận sau thuế thực"}, {"event": "spoken", "i": 3}]
    assert not rehearse.story_ended(ev, t)
    more = ev + [{"event": "beat", "title": "", "graphic": {"kind": "bridge"}},
                 {"event": "beat", "title": "", "graphic": {"kind": "notes"}}]
    assert not rehearse.story_ended(more, t)
    assert rehearse.story_ended(more + [{"event": "beat", "title": "", "choices": ["Lưu và kiểm tra"]}], t)
    assert rehearse.story_ended(ev + [{"event": "guided", "active": False}], t)
    assert not rehearse.story_ended([{"event": "beat", "title": "Lệch", "choices": ["Sửa giúp tôi"]}], t)
