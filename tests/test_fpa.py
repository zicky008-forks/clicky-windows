"""FP&A month on synthetic data (Case F01 shape): actual against the AOP by department x cost element, month and
year to date, after the actuals are matched to the books. Every figure is checked against the generator's own
arithmetic, and every trap a real cost detail has is named, never dropped."""
from decimal import Decimal

import pytest

import fpa_samples as fs
from engine import bod_inputs, fpa
from engine.trial_balance import read_trial_balance

M = fs.M


@pytest.fixture
def bundle(tmp_path):
    return bod_inputs.load(fs.folder(tmp_path / "F01"), fy_start=1)


def _line(lines, dept, element):
    return next(l for l in lines if (l.dept, l.element) == (dept, element))


def test_month_and_year_to_date_against_the_aop_to_the_dong(bundle):
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    assert (v.period, v.months[0], v.months[-1], v.version) == ("2026-10", "2026-01", "2026-10", "AOP")
    for dept, element, _account, _p in fs.PLAN:
        month, ytd = _line(v.month, dept, element), _line(v.ytd, dept, element)
        down = fs.WRITTEN_DOWN[3] if (dept, element) == fs.WRITTEN_DOWN[:2] else 0      # written down in October
        assert month.actual == fs.actual_month(dept, element, 10) - down
        assert month.plan == fs.plan_month(dept, element, 10)
        assert ytd.actual == sum(fs.actual_month(dept, element, m) for m in range(1, 11)) - down
        assert ytd.plan == sum(fs.plan_month(dept, element, m) for m in range(1, 11))
        assert all(isinstance(x, Decimal) for x in (month.actual, month.plan, ytd.actual, ytd.plan))
    qc = _line(v.month, "MKT", "QC")
    assert qc.variance == 90 * M and qc.pct == Decimal("29.0") and qc.over        # 95 over, 5 written down
    assert _line(v.month, "KD", "CTP").variance == -20 * M and not _line(v.month, "KD", "CTP").over
    assert [l.element for l in v.biggest(2)] == ["QC", "CTP"]


def test_every_figure_names_the_rows_it_adds_up(bundle):
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    qc = _line(v.month, "MKT", "QC")
    assert len(qc.actual_from) == 3 and all(w.startswith("SoChiTietChiPhi_2026!") for w in qc.actual_from)
    assert len(qc.plan_from) == 1 and qc.plan_from[0].startswith("AOP_2026!F")
    assert len(_line(v.ytd, "MKT", "QC").actual_from) == 21 and qc.accounts == {"6418"}


def test_nothing_is_left_out_in_silence(bundle):
    """Every detail row is in a figure, named as outside the year, without a month, or skipped with its reason."""
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    unassigned = _line(v.month, "", "CTP")
    assert unassigned.actual == fs.UNASSIGNED[3] and unassigned.label == "Chưa gán bộ phận · CTP" and unassigned.unplanned
    assert _line(v.month, "MKT", "CTP").unplanned and _line(v.month, "MKT", "CTP").actual == fs.UNPLANNED[3]
    assert v.left_out() == ["1 dòng chi phí không rõ tháng, tổng 2.000.000 đ",
                            "1 dòng chi phí sau tháng 10/2026, tổng 3.000.000 đ"]
    _file, _role, rows, skipped = next(r for r in bundle.receipt if r[1] == "Sổ chi tiết chi phí")
    blank = next(i for i, r in enumerate(fs.gl_rows(), 1) if r[9] == "")
    total = len(fs.gl_rows())
    assert sorted(skipped) == [(blank, "không có số tiền"), (total, "dòng tổng")]      # the printed total is not added
    assert rows + len(skipped) == len(fs.gl_rows()) - 1                                # used + left out = the lines
    in_figures = sum(len(l.actual_from) for l in v.ytd)
    assert in_figures + len(v.outside) + len(v.no_month) == rows                       # each used row gives a figure


def test_two_plan_versions_are_asked_never_picked(tmp_path):
    b = bod_inputs.load(fs.folder(tmp_path / "F01", versions=("AOP", "AOPR1")), fy_start=1)
    with pytest.raises(fpa.PickVersion) as asked:
        fpa.compute(b.gl, b.plans, fy_start=1)
    assert asked.value.versions == ["AOP", "AOPR1"]
    v = fpa.compute(b.gl, b.plans, version="AOPR1", fy_start=1)
    assert v.version == "AOPR1" and _line(v.month, "MKT", "QC").plan == fs.plan_month("MKT", "QC", 10)


def test_the_detail_matches_the_books_and_the_written_down_cost_is_said(bundle):
    m = fpa.match_books(bundle.gl, read_trial_balance(fs.tb_rows()), fs.PERIOD)
    assert m.ok and {l.account for l in m.lines} == {"6411", "6414", "6418", "6421", "6424", "6427"}
    books, detail, down = m.totals
    assert books == detail == sum(fs.october_debits().values()) and down == fs.WRITTEN_DOWN[3]
    assert m.sentence() == ("Chi phí theo bộ phận tháng 10/2026 khớp sổ cân đối: 1.563.500.000 đ phát sinh Nợ trên "
                            "6 tài khoản. Có thêm 5.000.000 đ ghi giảm chi phí bên Có, em đã trừ vào số thực tế.")


def test_a_gap_with_the_books_is_named_and_its_lines_are_held_back(bundle):
    m = fpa.match_books(bundle.gl, read_trial_balance(fs.tb_rows(shift={"6427": 4 * M})), fs.PERIOD)
    assert not m.ok and [(g.account, g.diff) for g in m.gaps] == [("6427", -4 * M)]
    assert m.sentence().startswith("Chi phí theo bộ phận tháng 10/2026 lệch sổ cân đối ở TK 6427: sổ cân đối "
                                   "124.000.000 đ, file chi tiết 120.000.000 đ.")
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    assert [(l.dept, l.element) for l in fpa.flag_unmatched(v, m)] == [("HC", "THUE")]
    assert not _line(v.month, "MKT", "QC").unmatched


def test_an_account_on_one_side_only_is_a_gap(bundle):
    rows = fs.tb_rows() + [["6428", "Chi phí bằng tiền khác", 0, 0, 4 * M, 4 * M, 0, 0]]
    m = fpa.match_books(bundle.gl, read_trial_balance(rows), fs.PERIOD)
    assert [(g.account, g.books, g.detail) for g in m.gaps] == [("6428", 4 * M, 0)]


def test_accounts_finer_or_coarser_than_the_books_are_summed_to_the_books_level(bundle):
    for e in bundle.gl:
        if e.account == "6421":
            e.account = "6421.01"                       # the detail is finer than the books
    rows = []
    for r in fs.tb_rows():                              # the books split 6411 into two sub-accounts
        if r[0] == "6411":
            half = r[4] // 2
            rows += [r, ["64111", "Lương", 0, 0, half, half, 0, 0], ["64112", "Thưởng", 0, 0, r[4] - half, r[4] - half, 0, 0]]
        else:
            rows.append(r)
    m = fpa.match_books(bundle.gl, read_trial_balance(rows), fs.PERIOD)
    assert m.ok, [(g.account, g.books, g.detail) for g in m.gaps]
    assert {l.account for l in m.lines} >= {"6411", "6421"} and "6421.01" not in {l.account for l in m.lines}


def test_the_plan_compared_against_is_named_with_why(bundle):
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    assert v.basis == "Kế hoạch: file AOP_2026.csv, phiên bản AOP, bản kế hoạch duy nhất trong thư mục"


def test_two_drafts_of_one_plan_are_asked_unless_one_says_it_was_approved(tmp_path):
    drafts = {"AOP_2026.csv": fs.plan_rows("AOP"), "AOP_2026_v2.csv": fs.plan_rows("AOP", scale=2)}
    b = bod_inputs.load(fs.folder(tmp_path / "a", plans=drafts), fy_start=1)
    with pytest.raises(fpa.PickVersion) as asked:
        fpa.compute(b.gl, b.plans, fy_start=1)                        # never by file name or date modified
    assert asked.value.versions == ["AOP (AOP_2026.csv)", "AOP (AOP_2026_v2.csv)"]
    v = fpa.compute(b.gl, b.plans, fy_start=1, plan_file="AOP_2026.csv")
    assert _line(v.month, "MKT", "QC").plan == fs.plan_month("MKT", "QC", 10)      # never both added
    drafts["AOP_2026.csv"] = fs.plan_rows("AOP", approved="15/12/2025")
    b = bod_inputs.load(fs.folder(tmp_path / "b", plans=drafts), fy_start=1)
    v = fpa.compute(b.gl, b.plans, fy_start=1)
    assert v.plan.file == "AOP_2026.csv" and v.basis.endswith("duyệt ngày 15/12/2025")
    assert _line(v.ytd, "HC", "LUONG").plan == sum(fs.plan_month("HC", "LUONG", m) for m in range(1, 11))


def test_codes_missing_from_the_catalogue_are_named_and_still_counted(bundle):
    config = bod_inputs.Config(depts={"KD": {"ten": "Kinh doanh"}, "HC": {"ten": "Hành chính"}},
                               elements={c: {"ten": n} for c, n in fs.ELEMENTS.items() if c != "QC"})
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1, config=config)
    assert v.unknown == {"bộ phận": ["MKT"], "yếu tố chi phí": ["QC"]}
    assert "Mã bộ phận chưa có trong danh mục: MKT; em vẫn tính theo mã, cần chị cho biết gán vào đâu" in v.left_out()
    assert _line(v.month, "MKT", "QC").actual == 400 * M and _line(v.month, "KD", "LUONG").label == "Kinh doanh · Lương"


def test_a_detail_line_without_a_cost_element_is_grouped_by_account_and_said(bundle):
    e = next(x for x in bundle.gl if x.period == fs.PERIOD and x.element == "THUE")
    e.element = ""
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    assert _line(v.month, "HC", "TK 6427").actual == e.amount and v.no_element == [e.where]
    assert "1 dòng chi phí không có mã yếu tố chi phí, em tạm xếp theo tài khoản" in v.left_out()


def test_a_plan_pnl_line_is_not_a_cost_line(bundle):
    """'Lợi nhuận gộp', 'Doanh thu thuần' in the plan file belong to the sales P&L, never to the cost matrix."""
    for line in ("Lợi nhuận gộp", "Doanh thu thuần", "Giá vốn hàng bán"):
        bundle.plans.append(bod_inputs.PlanRow("AOP", fs.PERIOD, Decimal(900 * M), line=line, file="AOP_2026.csv"))
    v = fpa.compute(bundle.gl, bundle.plans, fy_start=1)
    assert not any(l.dept == "" and l.element == "" for l in v.month + v.ytd)
    assert v.totals(v.month)[1] == sum(fs.plan_month(d, e, 10) for d, e, _a, _p in fs.PLAN)
