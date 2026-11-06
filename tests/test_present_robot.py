"""The presentation robot's grading: what counts as ĐẠT for a real-PowerPoint rehearsal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
import present_robot as robot          # noqa: E402

SCRIPT = {"slides": [{"so": 1, "chi_vao": [{"shape": "tiêu đề"}, {"excel": "Tong_quan!B4"}]},
                     {"so": 2, "chi_vao": [{"shape": "bảng"}, {"shape_chua": "1"}]}]}
BANK = {"cau_hoi": [{"id": "Q01", "hoi_mau": "Tổng?"}, {"id": "Q04", "hoi_mau": "Phòng nào?"}, {"id": "X"}]}


def good_events():
    return [{"event": "present_preflight", "ok": True, "lines": ["ĐẠT  so_slide"]},
            {"event": "present_start", "mode": "extend"},
            {"event": "present_slide", "slide": 1, "targets": ["tiêu đề"], "rects": [[0, 0, 9, 9]], "missed": [], "missing": []},
            {"event": "present_slide", "slide": 2, "targets": ["bảng", "1"], "rects": [[0, 0, 9, 9]] * 2, "missed": [], "missing": []},
            {"event": "present_key", "key": "ask"},
            {"event": "present_answer", "question": "Tổng?", "id": "Q01", "choices": [], "stop": ""},
            {"event": "present_answer", "question": "Phòng nào?", "id": "Q04", "choices": [["Q04", "P. IT"]], "stop": ""},
            {"event": "present_key", "key": "back"},
            {"event": "present_end", "minutes": "C:/x.md"}]


def test_expected_targets_and_questions():
    assert robot.expected_targets(SCRIPT) == {1: 1, 2: 2}
    assert robot.sample_questions(BANK) == [("Q01", "Tổng?"), ("Q04", "Phòng nào?")]


def test_grade_pass():
    asked = [("Q01", "Tổng?", 3, 3), ("Q04", "Phòng nào?", 3, 3)]
    res = robot.grade(good_events(), SCRIPT, BANK, {1: (1, 1), 2: (2, 2)}, asked, True, False, True)
    assert res.ok, res.markdown("x")


def test_grade_catches_each_failure():
    asked = [("Q01", "Tổng?", 3, 3), ("Q04", "Phòng nào?", 3, 5)]                  # back went elsewhere
    events = [e for e in good_events() if not (e["event"] == "present_slide" and e["slide"] == 2)]
    events[1:1] = [{"event": "present_answer", "question": "Tổng?", "id": "", "choices": [], "stop": ""}]
    res = robot.grade(events, SCRIPT, BANK, {1: (0, 1)}, asked, True, True, False)
    bad = {l.name.split(" ")[0] for l in res.lines if not l.ok}
    assert not res.ok
    assert {"Theo", "Hỏi", "Màn", "File"} <= bad
    text = res.markdown("x")
    assert "slide 2: Clicky không theo" in text and "ảnh chụp chỉ thấy 0/1" in text


def test_grade_preflight_failure_is_reported():
    events = [{"event": "present_preflight", "ok": False, "lines": ["KHÔNG ĐẠT  placeholder: {tq:d_cost_fy}"]}]
    res = robot.grade(events, SCRIPT, BANK, {}, [], False, False, True)
    assert not res.ok and "d_cost_fy" in res.lines[0].detail
