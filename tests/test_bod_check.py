"""BOD pack review end to end on a made-up folder (tests/bod_case.py): nine planted mistakes, each found at its
cell with the right number, nothing else reported; the same pack without them gives no finding."""
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from engine import bod_check
from engine.bod_inputs import fiscal_years, name_key
from engine.bod_pack import Sheet, _groups, Col, fy_of, month_of, version_of
from engine.tables import header_texts
from tests.bod_case import make_case

PCT_CODES = {"wrong_base", "missing_pct", "missing_pp", "wrong_pp"}


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    folder = tmp_path_factory.mktemp("case_bod")
    answers = make_case(folder, plant=True)
    return folder, answers, bod_check.review(folder)


def close(code, want, got) -> bool:
    tol = Decimal("0.0001") if code in PCT_CODES else Decimal(1)
    return got is not None and abs(Decimal(str(want)) - got) <= tol


def test_all_nine_planted_mistakes_found_at_their_cell_with_the_right_number(planted):
    _, answers, reviews = planted
    assert len(reviews) == 1
    found = reviews[0].findings
    for a in answers:
        hit = [x for x in found if x.where == a["where"] and x.group == a["group"]]
        assert hit, f"missed group {a['group']} at {a['where']}: {[(x.group, x.where) for x in found]}"
        assert close(a["code"], a["expected"], hit[0].expected), (a, hit[0].expected)
    assert len(found) == len(answers) == 9, [(x.group, x.where, x.message) for x in found]


def test_gap_is_explained_to_the_dong(planted):
    _, _, reviews = planted
    total = next(x for x in reviews[0].findings if x.group == 1)
    assert sum((Decimal(a) for _, a in total.causes), Decimal(0)) == total.diff
    assert all("chưa giải thích" not in label for label, _ in total.causes)


def test_traps_in_the_detail_are_notes_not_pack_errors(planted):
    _, _, reviews = planted
    titles = [t for t, _, _ in reviews[0].notes]
    assert "Chi phí chưa gán phòng ban" in titles
    assert "Bút toán của kỳ sau trong file xuất" in titles
    assert "Chiết khấu cho nhà phân phối đang ghi vào chi phí" in titles


def test_clean_pack_has_no_finding(tmp_path):
    make_case(tmp_path, plant=False)
    reviews = bod_check.review(tmp_path)
    assert [(x.group, x.where, x.message) for x in reviews[0].findings] == []


def test_every_file_is_in_the_receipt(planted):
    folder, _, reviews = planted
    named = {n for n, *_ in reviews[0].receipt} | {n for n, _ in reviews[0].unused}
    data = {p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx")}
    assert data <= named
    gl = next(r for r in reviews[0].receipt if r[0].startswith("GL_"))
    assert gl[2] == 63 and [why for _, why in gl[3]] == ["dòng tổng"]


def test_outputs_are_written_reopened_and_the_pack_is_untouched(planted, tmp_path):
    folder, answers, reviews = planted
    pack = folder / "Pack_BOD_T10_ban_nhap.xlsx"
    before = hashlib.sha256(pack.read_bytes()).hexdigest()
    res = bod_check.write_outputs(reviews, tmp_path / "out")
    assert res["verified"] and res["findings"] == 9
    md = res["md"].read_text(encoding="utf-8")
    for a in answers:
        assert a["where"] in md
    assert "SƠ BỘ" in md and "Phiếu nhận file" in md
    from openpyxl import load_workbook
    wb = load_workbook(res["xlsx"], data_only=True)
    assert {"Ket qua soat", "Bien ban Clicky"} <= set(wb.sheetnames)
    assert hashlib.sha256(pack.read_bytes()).hexdigest() == before


def test_cli_writes_results(tmp_path, capsys):
    make_case(tmp_path, plant=True)
    assert bod_check.main([str(tmp_path), "-o", str(tmp_path / "kq")]) == 0
    assert "9 lỗi" in capsys.readouterr().out
    assert (tmp_path / "kq" / "ket-qua.md").exists()


def test_scene_log_records_each_finding(tmp_path, monkeypatch):
    import json
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    make_case(tmp_path / "case", plant=True)
    bod_check.write_outputs(bod_check.review(tmp_path / "case"), tmp_path / "out")
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds.count("bod_finding") == 9 and "bod_receipt" in kinds and kinds[-1] == "bod_done"


# ── readers ─────────────────────────────────────────────────────────────────────

def test_group_header_is_read_into_each_column():
    rows = [["Báo cáo T10"], [None, "Tháng 10", None, "Lũy kế", None], ["Chỉ tiêu", "Thực tế", "%DTT", "Thực tế", "%DTT"]]
    assert header_texts(rows, 2) == ["Chỉ tiêu", "Tháng 10 Thực tế", "Tháng 10 %DTT", "Lũy kế Thực tế", "Lũy kế %DTT"]
    assert header_texts(rows[:1] + rows[2:], 1)[1] == "Thực tế"


def test_names_years_versions_months():
    assert name_key("DanhMuc_CauHinh") == "danh muc cau hinh"
    assert fiscal_years("FY26/27") == (2026, 2027) and fiscal_years("2026/27") == (2026, 2027)
    assert fy_of("nam truoc aop fy25 26") == 2025
    assert version_of("aopr1 fy26 27") == "aopr1" and version_of("thuc te") == "thuc te" and version_of("aop") == "aop"
    assert month_of("t10") == 10 and month_of("10 2026") == 10 and month_of("ca nam") is None


def test_percent_columns_attach_by_name_when_not_beside_their_value():
    cols = [Col(1, "", "thuc te", "value", "thuc te"), Col(2, "", "aopr", "value", "aopr"),
            Col(3, "", "%dtt thuc te", "pct", "thuc te"), Col(4, "", "%dtt aopr", "pct", "aopr"),
            Col(5, "", "chenh lech", "diff"), Col(6, "", "diem %", "pp")]
    cur, cmp_ = _groups(cols)
    assert cur.pct.index == 3 and cmp_.pct.index == 4 and cmp_.diff.index == 5 and cmp_.pp.index == 6


# ── in the app ──────────────────────────────────────────────────────────────────

def test_chat_words_start_the_review():
    import re
    from office import bod_skill
    for text in ("soát bộ BOD", "Soat bo BOD giúp em", "kiểm tra bộ BOD trước khi trình", "rà soát pack tháng 10"):
        assert re.search(bod_skill.TRIGGER, text, re.I), text
    assert not re.search(bod_skill.TRIGGER, "làm báo cáo tháng từ thư mục này", re.I)
    assert bod_skill.args(["Clicky.exe", "--bod-review", "D:/case"]) == "D:/case"


def test_one_beat_per_finding_pointing_at_its_cell(planted):
    from office import bod_skill
    _, answers, reviews = planted
    beats = bod_skill.beats_for(reviews, {"verified": True})
    refs = [b.ref for b in beats if b.ref]
    assert sorted(refs) == sorted(a["where"] for a in answers)
    assert "9 chỗ" in beats[0].text and "không sửa gì" in beats[-1].text
    fx = next(b for b in beats if b.ref == "'Ty gia'!F7")
    assert fx.value == "759.000 đ" and fx.was == "908.500 đ" and fx.mark == "bad"


def test_run_writes_results_and_plays(tmp_path):
    import asyncio
    from office import bod_skill, month_close as mc
    make_case(tmp_path, plant=True)
    played = []

    class Bridge:
        def workbook_folder(self): return None
        def workbook_path(self): return None
        def open_workbook(self, p): played.append(("open", p)); return True

    class M:
        _excel = Bridge()

    async def fake_play(manager, beats):
        played.append(("play", len(beats)))

    orig = mc.play
    mc.play = fake_play
    try:
        asyncio.run(bod_skill.run(M(), tmp_path))
    finally:
        mc.play = orig
    assert (tmp_path / "Clicky_soat_BOD" / "ket-qua.md").exists()
    assert played[0][0] == "open" and played[0][1].endswith("Pack_BOD_T10_ban_nhap.xlsx")
    assert played[-1] == ("play", 12)
