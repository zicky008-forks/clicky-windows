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


@pytest.mark.parametrize("plant", [True, False])
def test_third_layout_parent_total_wide_last_year_sites_and_cut_rule(tmp_path, plant):
    """Style 'c': a sales export with fiscal year + month and 'Kênh phân phối'; last year as one wide table (a
    column per version, months without a year); plans per site; 'N/A' for no department; the cost total is a
    parent row 'Chi phí hoạt động'; AOPR1 has no %DTT column; month headers are real dates; the cut rule
    spares the no-cut list only the flat cut and the target to cut sits in the parameters file."""
    answers = make_case(tmp_path, plant=plant, style="c")
    review = bod_check.review(tmp_path)[0]
    found = review.findings
    assert len(found) == len(answers), [(x.group, x.where, x.message) for x in found]
    for a in answers:
        hit = [x for x in found if x.where == a["where"] and x.group == a["group"]]
        assert hit and close(a["code"], a["expected"], hit[0].expected), (a, [(x.where, x.expected) for x in found])
    assert review.unused == []
    assert all(state in ("loi", "ok") and "CHƯA" not in text.upper() for _, _, state, text in review.groups()), review.groups()
    assert "Chi phí chưa gán phòng ban" in [t for t, _, _ in review.notes]


@pytest.mark.parametrize("plant", [True, False])
def test_other_layout_row_numbers_percent_as_numbers_ytd_and_fx_sign(tmp_path, plant):
    """Layout 'b': STT column, a revenue row, % printed as 1.42, 'Chênh lệch %DTT' for points, a year-to-date
    part, FX impact printed as plan − actual, 'Tháng 10 năm 2026' as the period."""
    answers = make_case(tmp_path, plant=plant, style="b")
    found = bod_check.review(tmp_path)[0].findings
    assert len(found) == len(answers), [(x.group, x.where, x.message) for x in found]
    for a in answers:
        hit = [x for x in found if x.where == a["where"] and x.group == a["group"]]
        assert hit and close(a["code"], a["expected"], hit[0].expected), (a, [(x.where, x.expected) for x in found])


def test_division_codes_in_the_detail_and_account_lists_in_the_mapping(tmp_path):
    """The detail says 'TM', titles and sales say 'Thương mại'; the mapping writes accounts as '641*, 642*'."""
    answers = make_case(tmp_path, plant=True)
    gl = next(tmp_path.glob("GL_*.csv"))
    gl.write_text(gl.read_text(encoding="utf-8-sig").replace(",Thương mại,", ",TM,").replace(",Sản xuất,", ",SX,"),
                  encoding="utf-8-sig")
    from openpyxl import load_workbook
    cfg = tmp_path / "DanhMuc_CauHinh.xlsx"
    wb = load_workbook(cfg)
    ws = wb["Cost element"]
    for row in ws.iter_rows(min_row=2):
        row[2].value = row[2].value[:3] + "*, " + row[2].value[:3] + "8"
    wb.save(cfg)
    review = bod_check.review(tmp_path)[0]
    assert review.division in ("TM", "Thương mại")
    found = {(x.group, x.where) for x in review.findings}
    assert found == {(a["group"], a["where"]) for a in answers}


# ── never silent: a group that could not run says why ───────────────────────────

def _group(review, g):
    return next(x for x in review.groups() if x[0] == g)


@pytest.mark.parametrize("missing, group, words", [
    ("Mastersale_T07-T10.csv", 2, "doanh thu"),
    ("NamTruoc_FY2526.xlsx", 4, "năm trước"),
    ("TyGia_FY2627.xlsx", 7, "tỷ giá"),
    ("GL_ChiTiet_ChiPhi_T07-T10.csv", 1, "sổ chi tiết"),
])
def test_a_group_without_its_file_says_not_checked_and_what_it_needs(tmp_path, missing, group, words):
    make_case(tmp_path, plant=True)
    (tmp_path / missing).unlink()
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, group)
    assert state in ("chua", "mot_phan") and "CHƯA KIỂM ĐƯỢC" in text and words in text.lower() and "Cần:" in text, text
    md = bod_check.markdown([review])
    assert f"Nhóm {group}. " in md and "CHƯA KIỂM ĐƯỢC" in md
    line = next(l for l in md.splitlines() if l.startswith(f"### Nhóm {group}."))
    assert "CHƯA KIỂM ĐƯỢC" in line, line


def test_no_fx_convention_is_not_checked_not_clean(tmp_path):
    make_case(tmp_path, plant=True)
    from openpyxl import load_workbook
    cfg = tmp_path / "DanhMuc_CauHinh.xlsx"
    wb = load_workbook(cfg)
    ws = wb["Quy tac"]
    for row in ws.iter_rows():
        if row[0].value and "Tỷ giá" in str(row[0].value):
            row[0].value, row[1].value = "Ghi chú", "trống"
    wb.save(cfg)
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 7)
    assert state == "chua" and "quy ước" in text and not [x for x in review.findings if x.group == 7]


def test_a_sales_file_clicky_cannot_read_names_the_missing_columns_and_the_group(tmp_path):
    make_case(tmp_path, plant=True)
    (tmp_path / "Mastersale_T07-T10.csv").write_text("Ma_hang,So_luong\nA,1\n", encoding="utf-8")
    review = bod_check.review(tmp_path)[0]
    why = dict(review.unused)["Mastersale_T07-T10.csv"]
    assert "doanh thu" in why and "nhóm 2" in why and "không thuộc phần soát" not in why


def test_receipt_counts_the_pack_rows_read_and_the_log_has_every_group(tmp_path, monkeypatch):
    import json
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    make_case(tmp_path / "case", plant=True)
    review = bod_check.review(tmp_path / "case")[0]
    pack = next(r for r in review.receipt if r[0].startswith("Pack_BOD"))
    assert pack[2] > 20
    groups = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if '"bod_group"' in l]
    assert sorted(e["group"] for e in groups) == [1, 2, 3, 4, 5, 6, 7] and all(e["state"] == "loi" for e in groups)


def test_parent_rows_are_found_by_their_children_nested_or_below():
    from engine.bod_check import _spans
    D = Decimal
    # Chi phí hoạt động = (BH = a + b) + (QL = c + d); then a block whose total sits under its children
    values = [D(100), D(60), D(25), D(35), D(40), D(10), D(30), D(7), D(9), D(16)]
    spans = _spans(values)
    assert spans[0] == (1, 6) and spans[1] == (2, 3) and spans[4] == (5, 6)
    assert spans[9] == (7, 8)


# ── round 2 roots: locked months only when a file says so; plan lines only against a source of their kind ──

def _edit_xlsx(path, sheet, fn):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    fn(wb[sheet])
    wb.save(path)


def test_the_month_under_review_is_never_locked_and_nothing_is_locked_unless_a_file_says_so(tmp_path):
    """Any layout: the review month (and later ones) of a revised plan may hold plan numbers; with no approval
    date and no 'closed up to' rule, group 5 is not checked rather than guessed."""
    from tests.bod_case import plan, DEPTS
    make_case(tmp_path, plant=False)
    pack = tmp_path / "Pack_BOD_T10_ban_nhap.xlsx"

    def plan_in_review_month(ws):
        for row in ws.iter_rows(min_row=4):
            code = str(row[0].value or "")[:3]
            if code in DEPTS:
                row[4].value = int(plan("AOP", code, ["2026-10"]))       # October holds the plan, as it should
    _edit_xlsx(pack, "AOPR theo thang", plan_in_review_month)
    _edit_xlsx(tmp_path / "KeHoach_AOPR1_FY2627.xlsx", "Data",
               lambda ws: [setattr(r[1], "value", "2026-12-20") for r in ws.iter_rows(min_row=2)])
    review = bod_check.review(tmp_path)[0]
    assert not [x for x in review.findings if x.group == 5], [x.message for x in review.findings if x.group == 5]
    assert _group(review, 5)[2] in ("ok", "mot_phan")

    _edit_xlsx(tmp_path / "KeHoach_AOPR1_FY2627.xlsx", "Data",
               lambda ws: [setattr(r[1], "value", None) for r in ws.iter_rows(min_row=2)])

    def no_lock_rule(ws):
        for row in ws.iter_rows():
            if row[0].value and "Khóa sổ" in str(row[0].value):
                row[0].value, row[1].value = "Ghi chú", "trống"
    _edit_xlsx(tmp_path / "DanhMuc_CauHinh.xlsx", "Quy tac", no_lock_rule)
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 5)
    assert not [x for x in review.findings if x.group == 5]
    assert state == "chua" and "CHƯA KIỂM ĐƯỢC" in text and "ngày duyệt" in text.lower(), text


def test_plan_lines_are_only_compared_with_a_ledger_that_carries_their_kind(tmp_path):
    """Revenue lines and other divisions' lines in the revised plan, with a cost-only detail filtered to the
    division under review: none is compared against a 0 actual; group 5 says which lines and what file it needs."""
    from openpyxl import load_workbook
    make_case(tmp_path, plant=False)
    extra = []
    for m in (7, 8, 9):
        extra.append(["Doanh thu thuần", "", "", "Doanh thu thuần", 9_000_000_000, m])            # revenue line
        extra.append(["", "Q01", "CE01", "Chi phí bán hàng", 55_000_000, m, "Sản xuất"])         # other division
    for name, version, approved in (("KeHoach_AOP_FY2627.xlsx", "AOP", "2026-06-20"),
                                    ("KeHoach_AOPR1_FY2627.xlsx", "AOPR1", "2026-11-03")):
        path = tmp_path / name
        wb = load_workbook(path)
        ws = wb["Data"]
        head = [c.value for c in ws[1]]
        for dong_or_blank, dept, ce, dong, amount, m, *div in extra:
            row = dict(zip(head, [None] * len(head)))
            row.update({"Phien_ban": version, "Ngay_duyet": approved, "Nam": "FY26/27", "Thang": m,
                        "Khoi": div[0] if div else "Thương mại", "Phong_ban_ma": dept, "Cost_element_ma": ce,
                        "Dong_BOD": dong, "So_tien_VND": amount})
            ws.append([row[h] for h in head])
        wb.save(path)

    def revenue_row(ws):
        ws.append(["Doanh thu thuần"] + [9_000_000_000] * 12 + [108_000_000_000])
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "AOPR theo thang", revenue_row)
    review = bod_check.review(tmp_path)[0]
    assert not [x for x in review.findings if x.group == 5], [x.message for x in review.findings if x.group == 5]
    _, _, state, text = _group(review, 5)
    assert state == "mot_phan" and "CHƯA KIỂM ĐƯỢC" in text and "doanh thu" in text.lower() and "Cần:" in text, text
