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


def test_a_selling_and_admin_total_covers_641_and_642_even_when_the_catalogue_lists_only_641(tmp_path):
    make_case(tmp_path, plant=False)
    pack = tmp_path / "Pack_BOD_T10_ban_nhap.xlsx"
    shown = {}

    def relabel(ws):
        for row in ws.iter_rows():
            if row[0].value == "Tổng chi phí":
                row[0].value = "Chi phí BH&QL"
                shown["v"] = Decimal(str(row[1].value))
    _edit_xlsx(pack, "Tong hop", relabel)

    def only_641(ws):
        for row in ws.iter_rows(min_row=2):
            if str(row[2].value).startswith("642"):
                row[2].value = None
    _edit_xlsx(tmp_path / "DanhMuc_CauHinh.xlsx", "Cost element", only_641)
    review = bod_check.review(tmp_path)[0]
    assert not [x for x in review.findings if x.group == 1], [x.message for x in review.findings if x.group == 1]
    assert _group(review, 1)[2] == "ok"

    _edit_xlsx(pack, "Tong hop", lambda ws: [setattr(r[1], "value", int(shown["v"]) - 50_000_000)
                                             for r in ws.iter_rows() if r[0].value == "Chi phí BH&QL"])
    total = next(x for x in bod_check.review(tmp_path)[0].findings if x.group == 1)
    assert total.expected == shown["v"]


def test_account_lists_read_ranges_numbers_and_exclusions():
    assert bod_check.accounts("6421-6423") == ["6421", "6422", "6423"]
    assert bod_check.accounts(6421.0) == ["6421"]
    assert bod_check.accounts("641*, 642 (trừ 6425)") == ["641", "642"]


def test_a_sales_export_with_run_together_headers_is_read(tmp_path):
    """Accounting programs export 'NgayHoaDon', 'THANHTIEN', 'KenhBH': no spaces, no diacritics."""
    import csv
    make_case(tmp_path, plant=False)
    path = tmp_path / "Mastersale_T07-T10.csv"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    rows[0] = ["NgayHoaDon", "SoHoaDon", "Site", "KhoiKD", "KenhBH", "MaHang", "SoLuong", "THANHTIEN", "ChietKhau",
               "DoanhThuThuan"]
    for r in rows[1:]:
        if r[0][:4].isdigit():
            r[0] = f"{r[0][8:10]}/{r[0][5:7]}/{r[0][:4]}"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    review = bod_check.review(tmp_path)[0]
    assert "Mastersale_T07-T10.csv" not in dict(review.unused)
    assert _group(review, 2)[2] == "ok" and not review.findings, (_group(review, 2), review.findings)


@pytest.mark.parametrize("plant, heads, period", [
    (True, ["Chỉ tiêu", "Kỳ", "Thực tế", "Kế hoạch gốc", "Kế hoạch sửa"], "{m}/{y}"),
    (False, ["Chỉ tiêu", "Kỳ", "Thực tế", "Kế hoạch gốc", "Kế hoạch sửa"], "{m}/{y}"),
    (True, ["Khoản mục", "Kỳ", "Thực tế FY25/26", "Gốc FY25/26", "Sửa (revised) FY25/26"], "Tháng {n}/{y}"),
    (False, ["Khoản mục", "Kỳ", "Thực tế FY25/26", "Gốc FY25/26", "Sửa (revised) FY25/26"], "Tháng {n}/{y}"),
])
def test_last_year_as_an_indicator_table_one_column_per_version(tmp_path, plant, heads, period):
    """Last year as 'Chỉ tiêu | Kỳ | Thực tế | Kế hoạch gốc | Kế hoạch sửa': the label column names the
    department, the period column the month, each version has its own column."""
    from openpyxl import Workbook
    from tests.bod_case import COSTS, DEPTS, FY_MONTHS, TM, plan_amount
    answers = make_case(tmp_path, plant=plant)
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    wb = Workbook()
    ws = wb.active
    ws.title = "Chi tieu"
    ws.append(["BẢNG CHỈ TIÊU NĂM TRƯỚC (giả lập)"])
    ws.append([])
    ws.append(heads)
    keys = list(COSTS) + [(TM, "P06", "CE06")]
    for d in [d for d in DEPTS if d.startswith("P")]:
        for m in FY_MONTHS:
            mine = [k for k in keys if k[0] == TM and k[1] == d]
            ws.append([f"{d} - {DEPTS[d]}", period.format(m=m[5:], n=int(m[5:]), y=int(m[:4]) - 1)]
                      + [int(sum(plan_amount(v, *k, m) for k in mine)) for v in ("Thực tế", "AOP", "AOPR")])
    wb.save(tmp_path / "NamTruoc_FY2526.xlsx")
    review = bod_check.review(tmp_path)[0]
    got = [(x.group, x.where, x.expected) for x in review.findings if x.group == 4]
    want = [a for a in answers if a["group"] == 4]
    assert len(got) == len(want), got
    for a in want:
        assert any(w == a["where"] and close(a["code"], a["expected"], e) for _, w, e in got), (a, got)
    assert _group(review, 4)[2] == ("loi" if plant else "ok"), _group(review, 4)


def test_last_year_rows_clicky_cannot_find_are_not_checked_not_clean(tmp_path):
    """A last-year file that holds other lines than the pack compares: those rows are said as not checked."""
    from openpyxl import Workbook
    make_case(tmp_path, plant=False)
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    wb = Workbook()
    ws = wb.active
    ws.append(["Chỉ tiêu", "Kỳ", "Thực tế", "Kế hoạch gốc", "Kế hoạch sửa"])
    ws.append(["P01 - Kinh doanh B2B", "07/2025", 1, 2, 3])
    wb.save(tmp_path / "NamTruoc_FY2526.xlsx")
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 4)
    assert state != "ok" and "CHƯA KIỂM ĐƯỢC" in text and "năm trước" in text.lower(), text


def test_an_unreadable_last_year_file_leaves_the_last_year_part_not_checked(tmp_path):
    from openpyxl import Workbook
    make_case(tmp_path, plant=False)
    wb = Workbook()
    wb.active.append(["Ghi chú", "Nội dung"])
    wb.active.append(["Số năm trước", "xem file của phòng kế toán"])
    wb.save(tmp_path / "NamTruoc_FY2526.xlsx")
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 4)
    assert state != "ok" and "CHƯA KIỂM ĐƯỢC" in text and "năm trước" in text.lower(), text
    assert "không thấy lỗi (đã soát" not in text


def _cut_rows(ws):
    head = next(r for r in ws.iter_rows() if r[0].value == "Phòng ban")
    return head[0].row, {str(r[0].value)[:3]: r for r in ws.iter_rows(min_row=head[0].row + 1) if r[0].value}


def test_the_no_cut_list_is_spared_only_the_flat_cut_unless_a_rule_ties_it_to_step_2(tmp_path):
    """Step 2 (cut the requested increase above the ceiling) applies to every department: a no-cut department
    cut at step 2 is not an error, whether the rule sentence names step 3 only or says nothing."""
    make_case(tmp_path, plant=False)
    pack = tmp_path / "Pack_BOD_T10_ban_nhap.xlsx"

    def step2_on_p05(ws):
        _, rows = _cut_rows(ws)
        rows["P05"][3].value = 40_000_000                    # P05 asked 40m above its base
        rows["P04"][4].value = rows["P04"][4].value - 40_000_000
    _edit_xlsx(pack, "Cat giam", step2_on_p05)
    review = bod_check.review(tmp_path)[0]
    assert not [x for x in review.findings if x.code == "no_cut_touched"], [x.message for x in review.findings]
    assert bod_check.no_cut_steps(["danh sach khong cat chi duoc mien buoc 3 cat deu"]) == {"cut3"}
    assert bod_check.no_cut_steps(["phong trong danh sach khong cat duoc mien ca hai buoc"]) == {"cut2", "cut3"}
    assert bod_check.no_cut_steps(["danh sach khong cat khong bi cat phan xin tang"]) == {"cut2", "cut3"}


@pytest.mark.parametrize("where", ["params", "pack"])
def test_a_cut_target_labelled_muc_tieu_is_found_in_the_parameters_or_the_pack(tmp_path, where):
    from tests.bod_case import CEILING, cut_plan
    make_case(tmp_path, plant=False)
    target = int(sum(v[0] for v in cut_plan().values()) - CEILING)

    def target_not_ceiling(ws):
        for row in ws.iter_rows():
            if row[0].value and "Trần" in str(row[0].value):
                row[0].value, row[1].value = ("Mục tiêu tiết kiệm (đ)", target) if where == "params" else ("Ghi chú", "-")
    _edit_xlsx(tmp_path / "CatGiam_ThamSo.xlsx", "Tham so", target_not_ceiling)
    if where == "pack":
        _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Cat giam",
                   lambda ws: (ws.append([]), ws.append(["Mục tiêu", None, None, None, None, target])))
    review = bod_check.review(tmp_path)[0]
    assert _group(review, 6)[2] == "ok", _group(review, 6)

    def wrong_flat_cut(ws):
        _, rows = _cut_rows(ws)
        rows["P01"][4].value = rows["P01"][4].value - 7_000_000
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Cat giam", wrong_flat_cut)
    levers = [x for x in bod_check.review(tmp_path)[0].findings if x.code == "levers_vs_gap"]
    assert levers and levers[0].expected == target, levers


def test_a_row_missing_both_its_percent_and_its_point_gap_is_reported_for_both(tmp_path):
    make_case(tmp_path, plant=False)
    keep = {}

    def blank_both(ws):
        for row in ws.iter_rows():
            if str(row[0].value).startswith("P02"):
                keep.update(pct=Decimal(str(row[2].value)), pp=Decimal(str(row[6].value)), n=row[0].row)
                row[2].value = row[6].value = None
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Tong hop", blank_both)
    found = {x.where: x for x in bod_check.review(tmp_path)[0].findings}
    pct, pp = found.get(f"'Tong hop'!C{keep['n']}"), found.get(f"'Tong hop'!G{keep['n']}")
    assert pct and pct.code == "missing_pct" and close("missing_pct", keep["pct"], pct.expected), found
    assert pp and pp.code == "missing_pp" and close("missing_pp", keep["pp"], pp.expected), found
    assert len(found) == 2, found


@pytest.mark.parametrize("how", ["catalogue_code", "name_column", "word_code"])
def test_cost_with_no_department_is_a_data_note_however_the_detail_writes_it(tmp_path, how):
    """'P99' that the catalogue calls 'Chưa phân bổ', a name column saying 'Chưa gán phòng ban', or a code
    'CHUA_GAN': each is cost with no department, said in the data notes."""
    import csv
    from openpyxl import load_workbook
    make_case(tmp_path, plant=False)
    gl = next(tmp_path.glob("GL_*.csv"))
    with open(gl, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    head = rows[0]
    pb = next(i for i, h in enumerate(head) if "Phong_ban" in h or "Phòng ban" in h or "phong_ban" in h.lower())
    if how == "name_column":
        head.insert(pb + 1, "Ten phong ban")
    for r in rows[1:]:
        if how == "name_column":
            r.insert(pb + 1, "Chưa gán phòng ban" if not r[pb].strip() else "")
            if not r[pb].strip() and r[0][:1].isdigit():
                r[pb] = "P04"            # the name column, not the code, says there is no department
        elif not r[pb].strip() and r[0][:1].isdigit():
            r[pb] = "P99" if how == "catalogue_code" else "CHUA_GAN"
    with open(gl, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    if how == "catalogue_code":
        cfg = tmp_path / "DanhMuc_CauHinh.xlsx"
        wb = load_workbook(cfg)
        ws = wb["Phong ban"]
        ws.append(["P99", "Chưa phân bổ"] + [None] * (ws.max_column - 2))
        wb.save(cfg)
    review = bod_check.review(tmp_path)[0]
    assert "Chi phí chưa gán phòng ban" in [t for t, _, _ in review.notes], review.notes
    assert not [x for x in review.findings if x.group == 1], [x.message for x in review.findings]


def test_a_whole_closed_month_left_on_plan_is_one_error_listing_every_row(tmp_path):
    """The revised plan forgot to move all of September to actuals: one error, every row listed, never '0 lỗi'."""
    from openpyxl import load_workbook
    make_case(tmp_path, plant=False)
    aop = load_workbook(tmp_path / "KeHoach_AOP_FY2627.xlsx")["Data"]
    head = [c.value for c in aop[1]]
    col = {h: i for i, h in enumerate(head)}
    plan = {(r[col["Khoi"]], r[col["Phong_ban_ma"]], r[col["Cost_element_ma"]]): r[col["So_tien_VND"]]
            for r in aop.iter_rows(min_row=2, values_only=True) if r[col["Thang"]] == 9}
    moved = []

    def september_on_plan(ws):
        for row in ws.iter_rows(min_row=2):
            if row[col["Thang"]].value == 9:
                k = (row[col["Khoi"]].value, row[col["Phong_ban_ma"]].value, row[col["Cost_element_ma"]].value)
                row[col["So_tien_VND"]].value = plan[k]
                if k[0] == "Thương mại":
                    moved.append(row[0].row)
    _edit_xlsx(tmp_path / "KeHoach_AOPR1_FY2627.xlsx", "Data", september_on_plan)
    review = bod_check.review(tmp_path)[0]
    g5 = [x for x in review.findings if x.group == 5]
    assert len(g5) == 1 and "09/2026" in g5[0].message and len(g5[0].causes) == len(moved), \
        [(x.where, x.message[:80]) for x in g5]
    assert _group(review, 5)[2] == "loi"
    assert "0 lỗi" not in bod_check.markdown([review]).splitlines()[0:6]


@pytest.mark.parametrize("codes", [["K01", "K02", "K03", "K04"], ["01", "02", "03", "04"]])
def test_channel_code_in_a_second_text_column_with_site_as_the_label(tmp_path, codes):
    """'Theo kênh' printed as Site | Mã kênh | Chi phí | %DTT, the catalogue keeps code and name apart, the
    sales file says the name: the channel's denominator rule still applies."""
    from tests.bod_case import CHANNELS
    answers = make_case(tmp_path, plant=True)
    names = list(CHANNELS)
    code_of = dict(zip(names, codes))

    def catalogue(ws):
        ws.delete_rows(1, ws.max_row)
        ws.append(["QUY TẮC MẪU SỐ %DTT THEO KÊNH"])
        ws.append(["Mã kênh", "Tên kênh", "Mẫu số %DTT"])
        for n, rule in CHANNELS.items():
            ws.append([code_of[n], n, rule])
    _edit_xlsx(tmp_path / "DanhMuc_CauHinh.xlsx", "Kenh", catalogue)

    def by_site_and_code(ws):
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        ws.delete_rows(1, ws.max_row)
        for r in rows:
            if r[0] == "Kênh":
                ws.append(["Site", "Mã kênh"] + list(r[1:]))
            elif r[0] in code_of:
                ws.append(["Toàn khối", code_of[r[0]]] + list(r[1:]))
            elif r[0] and str(r[0]).startswith("Tổng"):
                ws.append([r[0], None] + list(r[1:]))
            else:
                ws.append(list(r))
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Theo kenh", by_site_and_code)
    review = bod_check.review(tmp_path)[0]
    g2 = [x for x in review.findings if x.group == 2]
    want = next(a for a in answers if a["group"] == 2)
    assert [x.where for x in g2] == [want["where"].replace("!C", "!D")], [(x.where, x.message) for x in g2]
    assert close(want["code"], want["expected"], g2[0].expected)


def test_a_total_that_nets_an_accrual_with_its_next_period_reversal_is_explained(tmp_path):
    """October accrues 9m, November's export reverses it; the pack netted the two and left both out."""
    import csv
    make_case(tmp_path, plant=False)
    gl = next(tmp_path.glob("GL_*.csv"))
    with open(gl, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    h = {name: i for i, name in enumerate(rows[0])}
    base = next(r for r in rows[1:] if r[h["Thang"]] == "10" and r[h["Phong_ban_ma"]] == "P03")
    accrual, reversal = list(base), list(base)
    accrual[h["So_CT"]], accrual[h["So_tien_VND"]], accrual[h["Dien_giai"]] = "PK1099", "9000000", "Trích trước quảng cáo T10"
    reversal[h["So_CT"]], reversal[h["Ngay_CT"]], reversal[h["Thang"]] = "PK1199", "2026-11-01", "11"
    reversal[h["So_tien_VND"]], reversal[h["Dien_giai"]] = "-9000000", "Đảo trích trước quảng cáo T10"
    total = next(i for i, r in enumerate(rows) if r[0].startswith("Tổng"))
    rows[total:total] = [accrual, reversal]
    with open(gl, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    review = bod_check.review(tmp_path)[0]
    total_finding = next(x for x in review.findings if x.group == 1)
    labels = [label for label, _ in total_finding.causes]
    assert any("cấn trừ" in l for l in labels) and not any("chưa giải thích" in l for l in labels), total_finding.causes


@pytest.mark.parametrize("own_base", [False, True])
def test_a_channel_table_with_its_revenue_column_before_the_cost_and_percent(tmp_path, own_base):
    """'Kênh | Doanh thu kênh | Chi phí | %DTT': the current figure is the money column the %DTT stands beside;
    the channel's own revenue is a denominator the pack may have used by mistake."""
    from tests.bod_case import CHANNELS, PERIOD, revenue
    answers = make_case(tmp_path, plant=True)
    want = next(a for a in answers if a["group"] == 2)

    def with_revenue(ws):
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        ws.delete_rows(1, ws.max_row)
        for i, r in enumerate(rows, 1):
            if r[0] == "Kênh":
                ws.append([r[0], "Doanh thu kênh"] + r[1:])
            elif r[0] in CHANNELS:
                own = revenue([PERIOD], channel=r[0])
                pct = r[2]
                if own_base and i == int(want["where"][-1]):
                    pct = float(Decimal(str(r[1])) / own)          # cost / the channel's own revenue
                ws.append([r[0], int(own), r[1], pct])
            elif r[0] and str(r[0]).startswith("Tổng"):
                ws.append([r[0], None] + r[1:])
            else:
                ws.append(r)
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Theo kenh", with_revenue)
    review = bod_check.review(tmp_path)[0]
    g2 = [x for x in review.findings if x.group == 2]
    assert [x.where for x in g2] == [want["where"].replace("!C", "!D")], [(x.where, x.message) for x in g2]
    assert close(want["code"], want["expected"], g2[0].expected)


@pytest.mark.parametrize("heads", [
    ["Chỉ tiêu (khối Thương mại)", "Kỳ", "Thực tế FY25/26", "Kế hoạch FY25/26 gốc", "Kế hoạch FY25/26 sửa (revised)"],
    ["Chỉ tiêu (khối Thương mại)", "Kỳ", "Thực tế FY25/26", "Gốc FY25/26", "Sửa (revised) FY25/26"],
])
def test_last_year_version_words_before_or_after_the_fiscal_year_and_a_bracketed_label(tmp_path, heads):
    from openpyxl import Workbook
    from tests.bod_case import COSTS, DEPTS, FY_MONTHS, TM, plan_amount
    answers = make_case(tmp_path, plant=True)
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    wb = Workbook()
    ws = wb.active
    ws.append(heads)
    keys = list(COSTS) + [(TM, "P06", "CE06")]
    for d in [d for d in DEPTS if d.startswith("P")]:
        for m in FY_MONTHS:
            mine = [k for k in keys if k[0] == TM and k[1] == d]
            ws.append([f"{d} - {DEPTS[d]}", f"{m[5:]}/{int(m[:4]) - 1}"]
                      + [int(sum(plan_amount(v, *k, m) for k in mine)) for v in ("Thực tế", "AOP", "AOPR")])
    wb.save(tmp_path / "SoLieu_NamTruoc.xlsx")
    review = bod_check.review(tmp_path)[0]
    want = next(a for a in answers if a["group"] == 4)
    got = [x for x in review.findings if x.group == 4]
    assert [x.where for x in got] == [want["where"]] and close(want["code"], want["expected"], got[0].expected), \
        (_group(review, 4), review.unused)


@pytest.mark.parametrize("explained", [True, False])
def test_a_site_denominator_takes_its_division_scope_from_the_catalogue_never_a_guess(tmp_path, explained):
    """'DTT của site', explained in the catalogue as 'DTT của chính site (mọi khối)': every division of that site.
    Without the explanation Clicky does not guess the division: those rows are said as not checked."""
    from tests.bod_case import CHANNELS
    answers = make_case(tmp_path, plant=True)
    site_rule = [n for n, rule in CHANNELS.items() if "site S1" in rule]

    def catalogue(ws):
        for row in ws.iter_rows():
            if row[0].value in site_rule:
                row[1].value = "DTT của site"
        if explained:
            ws.append([])
            ws.append(["Giải thích mẫu số", None])
            ws.append(["DTT của site", "DTT của chính site (mọi khối)"])
    _edit_xlsx(tmp_path / "DanhMuc_CauHinh.xlsx", "Kenh", catalogue)

    def site_column(ws):
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        ws.delete_rows(1, ws.max_row)
        for r in rows:
            if r[0] == "Kênh":
                ws.append(["Site", "Kênh"] + r[1:])
            elif r[0] in CHANNELS:
                ws.append(["S1" if r[0] in site_rule else "Toàn khối", r[0]] + r[1:])
            elif r[0] and str(r[0]).startswith("Tổng"):
                ws.append([r[0], None] + r[1:])
            else:
                ws.append(r)
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "Theo kenh", site_column)
    review = bod_check.review(tmp_path)[0]
    want = next(a for a in answers if a["group"] == 2)
    g2 = [x for x in review.findings if x.group == 2]
    assert [x.where for x in g2] == [want["where"].replace("!C", "!D")], [(x.where, x.message) for x in g2]
    _, _, state, text = _group(review, 2)
    if explained:
        assert "CHƯA KIỂM ĐƯỢC" not in text, text
    else:
        assert "CHƯA KIỂM ĐƯỢC" in text and "DTT của site" in text, text


def _last_year_month_table(tmp_path, version_used, version_said="AOPR"):
    """'So nam truoc' as Phòng ban | Phiên bản | Thực tế T10/2025 | Kế hoạch: last year's October, the version
    the pack used written in a text cell of each row."""
    from tests.bod_case import COSTS, DEPTS, TM, plan_amount
    keys = list(COSTS) + [(TM, "P06", "CE06")]
    depts = [d for d in DEPTS if d.startswith("P")]
    ly = lambda v, d: sum(plan_amount(v, *k, "2026-10") for k in keys if k[0] == TM and k[1] == d)

    def table(ws):
        ws.delete_rows(1, ws.max_row)
        ws.append(["SO SÁNH CÙNG KỲ – KHỐI THƯƠNG MẠI"])
        ws.append([])
        ws.append(["Phòng ban", "Phiên bản", "Thực tế T10/2025", "Kế hoạch"])
        for d in depts:
            ws.append([f"{d} - {DEPTS[d]}", version_said, int(ly("Thực tế", d)), int(ly(version_used, d))])
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "So nam truoc", table)
    return ly("AOPR", "P01")


@pytest.mark.parametrize("used", ["AOP", "AOPR"])
def test_last_year_month_columns_and_the_version_written_in_the_rows(tmp_path, used):
    make_case(tmp_path, plant=False)
    right = _last_year_month_table(tmp_path, used)
    review = bod_check.review(tmp_path)[0]
    g4 = [x for x in review.findings if x.group == 4]
    if used == "AOP":
        assert len(g4) == 1 and g4[0].where == "'So nam truoc'!D3" and close("wrong_version", right, g4[0].expected), \
            [(x.where, x.message) for x in g4]
    else:
        assert not g4 and _group(review, 4)[2] == "ok", (_group(review, 4), [x.message for x in g4])


def test_a_last_year_column_that_matches_no_version_is_not_checked_not_clean(tmp_path):
    make_case(tmp_path, plant=False)
    _last_year_month_table(tmp_path, "AOP")
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 4)
    assert state != "ok" and "CHƯA KIỂM ĐƯỢC" in text and "không thấy lỗi (đã soát" not in text, text

    make_case(tmp_path / "b", plant=False)
    _last_year_month_table(tmp_path / "b", "AOP")
    _edit_xlsx(tmp_path / "b" / "Pack_BOD_T10_ban_nhap.xlsx", "So nam truoc",
               lambda ws: [setattr(r[3], "value", 123_456_789) for r in ws.iter_rows(min_row=4)])
    review = bod_check.review(tmp_path / "b")[0]
    _, _, state, text = _group(review, 4)
    assert state != "ok" and "CHƯA KIỂM ĐƯỢC" in text, text


def test_last_years_revised_plan_rows_do_not_enter_the_locked_month_check(tmp_path):
    """The last-year file carries AOPR rows with no approval date: this year's AOPR file has one, so group 5
    knows its locked months and says nothing about last year."""
    make_case(tmp_path, plant=True)

    def approval_only(ws):                    # no 'closed up to' rule: only the approval date says
        for row in ws.iter_rows():
            if row[0].value and "Khóa sổ" in str(row[0].value):
                row[0].value, row[1].value = "Ghi chú", "trống"
    _edit_xlsx(tmp_path / "DanhMuc_CauHinh.xlsx", "Quy tac", approval_only)
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 5)
    assert state == "loi" and "CHƯA KIỂM ĐƯỢC" not in text, text


def test_no_group_with_an_unchecked_part_ever_reads_as_clean():
    """The lock: whatever the counts, a group with a part not run never says 'không thấy lỗi' alone, and the
    summary lists it."""
    for state_maker in (lambda c: c.cannot(4, "x", "y"), lambda c: c.partly(4, "z")):
        cov = bod_check.Coverage()
        cov.ran(4, 12)
        state_maker(cov)
        state, text = cov.status(4, [])
        assert state != "ok" and "CHƯA KIỂM ĐƯỢC" in text, text
        from engine.bod_review import Finding
        state, text = cov.status(4, [Finding(4, "wrong_version", "'A'!B1", "m", Decimal(1), Decimal(2))])
        assert state == "loi" and "CHƯA KIỂM ĐƯỢC" in text, text


@pytest.mark.parametrize("plant", [True, False])
@pytest.mark.parametrize("where_division", ["label_header", "title_row", "nowhere"])
def test_last_year_indicator_rows_take_the_division_the_table_names_once(tmp_path, plant, where_division):
    """Grader V4: last year's file holds one indicator in all 3 versions, under 'Chỉ tiêu (khối Thương mại)'
    (or a title row naming the division, or no division at all). The pack's line reads 'Chi phí bán hàng khối
    Thương mại'. The division is given to every row below, the label is matched without its 'khối …' part, and
    the plan version error is caught, not said as not checked."""
    from openpyxl import Workbook
    from tests.bod_case import COSTS, ELEMENTS, FY_MONTHS, LOCKED, TM, plan_amount
    make_case(tmp_path, plant=False)
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    sell = [k for k in COSTS if k[0] == TM and ELEMENTS[k[2]][3] == "Chi phí bán hàng"]
    amount = {v: {m: int(sum(plan_amount(v, *k, m) for k in sell)) for m in FY_MONTHS} for v in ("Thực tế", "AOP", "AOPR")}
    wb = Workbook()
    ws = wb.active
    ws.title = "Chi tieu"
    ws.append(["CHỈ TIÊU NĂM TRƯỚC – KHỐI THƯƠNG MẠI (giả lập)" if where_division == "title_row" else "CHỈ TIÊU NĂM TRƯỚC"])
    ws.append([])
    ws.append(["Chỉ tiêu (khối Thương mại)" if where_division == "label_header" else "Chỉ tiêu", "Kỳ", "Thực tế",
               "Kế hoạch gốc", "Kế hoạch sửa"])
    for m in FY_MONTHS:
        ws.append(["Chi phí bán hàng", f"{m[5:]}/{int(m[:4]) - 1}"] + [amount[v][m] for v in ("Thực tế", "AOP", "AOPR")])
    wb.save(tmp_path / "NamTruoc_FY2526.xlsx")
    used = "AOP" if plant else "AOPR"
    facts = bod_check.Facts(bod_check.load(tmp_path, 7), TM, 7)
    actual = int(facts.actual(lambda r: r["dong"] == "Chi phí bán hàng", LOCKED))
    assert actual

    def one_line(ws):
        ws.delete_rows(5, ws.max_row)
        ws["C4"] = f"Năm trước {used} FY25/26"
        last = sum(amount[used][m] for m in LOCKED)
        ws.append(["Chi phí bán hàng khối Thương mại", actual, last, actual - last])
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "So nam truoc", one_line)
    review = bod_check.review(tmp_path)[0]
    got = [x for x in review.findings if x.group == 4]
    _, _, state, text = _group(review, 4)
    if plant:
        assert [x.where for x in got] == ["'So nam truoc'!C4"], (text, got)
        assert got[0].expected == sum(amount["AOPR"][m] for m in LOCKED), got[0].message
    else:
        assert not got and state == "ok", text
    assert "CHƯA KIỂM ĐƯỢC" not in text, text


def test_a_last_year_table_of_another_division_is_not_compared_with_this_one(tmp_path):
    """The table names 'khối Sản xuất' once: its rows are that division's, never matched to Thương mại's line."""
    from openpyxl import Workbook
    from tests.bod_case import FY_MONTHS
    make_case(tmp_path, plant=False)
    (tmp_path / "NamTruoc_FY2526.xlsx").unlink()
    wb = Workbook()
    ws = wb.active
    ws.append(["Chỉ tiêu (khối Sản xuất)", "Kỳ", "Thực tế", "Kế hoạch gốc", "Kế hoạch sửa"])
    for m in FY_MONTHS:
        ws.append(["Chi phí bán hàng", f"{m[5:]}/{int(m[:4]) - 1}", 1, 2, 3])
    wb.save(tmp_path / "NamTruoc_FY2526.xlsx")

    def one_line(ws):
        ws.delete_rows(5, ws.max_row)
        ws.append(["Chi phí bán hàng khối Thương mại", 8, 8, 0])
    _edit_xlsx(tmp_path / "Pack_BOD_T10_ban_nhap.xlsx", "So nam truoc", one_line)
    review = bod_check.review(tmp_path)[0]
    _, _, state, text = _group(review, 4)
    assert not [x for x in review.findings if x.group == 4] and "CHƯA KIỂM ĐƯỢC" in text, text
