"""The BOD rehearsal robot's grading, on a scene log made by the real review (no Windows needed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import bod_robot  # noqa: E402
from engine import bod_check  # noqa: E402
from office import bod_skill  # noqa: E402
from tests.bod_case import make_case  # noqa: E402


def _run(tmp_path, monkeypatch, plant=True, drop_box=None):
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    answers = make_case(tmp_path / "case", plant=plant)
    reviews = bod_check.review(tmp_path / "case")
    out = bod_check.write_outputs(reviews, tmp_path / "case" / "Clicky_soat_BOD")
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    for i, b in enumerate(bod_skill.beats_for(reviews, out)):      # what play() logs, cell found on screen
        if b.ref:
            events.append({"event": "locate", "ref": b.ref, "box": None if b.ref == drop_box else [1, 2, 3, 4]})
        events.append({"event": "beat", "i": i, "ref": b.ref})
        events.append({"event": "spoken", "i": i})
    return events, answers


def test_planted_case_passes(tmp_path, monkeypatch):
    events, answers = _run(tmp_path, monkeypatch)
    res = bod_robot.grade(events, True, answers)
    assert res.ok, res.markdown("x")


def test_a_card_off_its_cell_or_a_changed_pack_fails(tmp_path, monkeypatch):
    events, answers = _run(tmp_path, monkeypatch, drop_box="'Ty gia'!F7")
    res = bod_robot.grade(events, False, answers)
    bad = {l.name for l in res.lines if not l.ok}
    assert bad == {"Thẻ nằm đúng ô trên màn hình", "Bộ BOD không bị sửa"}


def test_missing_mistakes_and_no_finish_fail(tmp_path, monkeypatch):
    events, answers = _run(tmp_path, monkeypatch)
    events = [e for e in events if not (e.get("event") == "bod_finding" and e.get("group") == 7)]
    res = bod_robot.grade(events, True, answers)
    assert not res.ok
    assert not bod_robot.grade([], True, answers).ok


def test_a_group_clicky_could_not_check_fails_the_run_and_is_said(tmp_path, monkeypatch):
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    make_case(tmp_path / "case", plant=True)
    (tmp_path / "case" / "Mastersale_T07-T10.csv").unlink()
    reviews = bod_check.review(tmp_path / "case")
    out = bod_check.write_outputs(reviews, tmp_path / "case" / "Clicky_soat_BOD")
    beats = bod_skill.beats_for(reviews, out)
    assert any("chưa kiểm được nhóm 2" in b.text.lower() for b in beats)
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    res = bod_robot.grade(events, True)
    line = next(l for l in res.lines if l.name == "Đủ 7 nhóm đều kiểm được")
    assert not line.ok and "2" in line.detail
    from openpyxl import load_workbook
    rows = list(load_workbook(out["xlsx"])["Cac nhom kiem"].iter_rows(values_only=True))
    assert any(r[1] == 2 and "CHƯA KIỂM ĐƯỢC" in str(r[3]) for r in rows)


def test_results_inside_the_case_folder_are_refused(tmp_path, monkeypatch):
    """Case 07 on the PC, 07/11: --out inside the case folder copied the case into itself, level after level."""
    case = tmp_path / "Case07"
    case.mkdir()
    (case / "BOD_nhap.xlsx").write_bytes(b"x")
    monkeypatch.setattr(bod_robot, "find_clicky", lambda: str(tmp_path / "Clicky.exe"))
    (tmp_path / "Clicky.exe").write_bytes(b"")
    monkeypatch.setattr(bod_robot, "other_files_open", lambda: [])
    said = []
    monkeypatch.setattr(bod_robot, "say", said.append)
    assert bod_robot.main(["--thu-muc", str(case), "--out", str(case / "ket-qua-robot")]) == 2
    assert not (case / "ket-qua-robot").exists() and sorted(p.name for p in case.iterdir()) == ["BOD_nhap.xlsx"]
    assert "nằm trong thư mục case" in said[-1]
    assert bod_robot.inside(case, case) and not bod_robot.inside(tmp_path / "ket-qua-Case07", case)


def test_open_files_are_counted_not_named(monkeypatch):
    monkeypatch.setattr(bod_robot, "other_files_open", lambda: ["So_cai_that.xlsx", "AOP.pptx"])
    said = []
    monkeypatch.setattr(bod_robot, "say", said.append)
    monkeypatch.setattr(bod_robot, "find_clicky", lambda: __file__)
    assert bod_robot.main(["--tu-tao"]) == 2
    assert "2 file khác" in said[-1] and "So_cai" not in said[-1]
