"""F8 and F5 (grader, Case 05 class): a change between two periods is explained only when it is like for like.

F8: the two periods' files must be on the same basis (book, method, unit, movement columns), read from their header
rows. F5: this period's opening balances must bridge to the prior period's closing balances; an account-mapping
table carries renumbered codes across. All data here is synthetic (tests/tb_samples.py)."""
import csv
from decimal import Decimal

import pytest

import tb_samples as tb
from test_month_close import folder  # noqa: F401 - a fixture
from engine import build, comparable, intake, sources
from engine.trial_balance import read_trial_balance

pytest.importorskip("openpyxl")


def _gate(prev_rows, cur_rows, mapping=None):
    return comparable.gate(prev_rows, read_trial_balance(prev_rows), cur_rows, read_trial_balance(cur_rows), mapping)


def _with_header(rows, *lines):
    """The same trial balance with other header lines above the column header (company line kept)."""
    return [rows[0]] + [[x] for x in lines] + rows[3:]


def test_the_same_report_from_two_months_is_like_for_like():
    g = _gate(tb.t9_rows(), tb.t10_rows())
    assert g.ok and g.checked and g.sentence("chị") == ""


@pytest.mark.parametrize("prev_lines,cur_lines", [
    (("BẢNG CÂN ĐỐI SỐ PHÁT SINH", "Từ ngày 01/09/2026 đến ngày 30/09/2026", "ĐVT: VND"),
     ("Bảng cân đối tài khoản", "Tháng 10 năm 2026", "Đơn vị tính: đồng")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Kỳ báo cáo: Quý 3/2026"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Kỳ báo cáo: Tháng 10/2026")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Tháng 9 năm 2026", "Đơn vị tính: đồng"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Tháng 10 năm 2026")),
])
def test_period_words_report_names_and_a_unit_said_once_are_not_a_different_basis(prev_lines, cur_lines):
    g = _gate(_with_header(tb.t9_rows(), *prev_lines), _with_header(tb.t10_rows(), *cur_lines))
    assert g.same_basis, g.basis


@pytest.mark.parametrize("prev_lines,cur_lines,what", [
    (("BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)", "Tháng 9 năm 2026"), ("BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ TÀI CHÍNH)", "Tháng 10 năm 2026"),
     "tiêu đề"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Phương pháp tính giá xuất kho: Bình quân cuối kỳ"),
     ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Phương pháp tính giá xuất kho: Nhập trước xuất trước"), "phương pháp tính giá xuất kho"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Đơn vị tính: nghìn đồng"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Đơn vị tính: đồng"), "đơn vị tính"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Loại sổ: Sổ quản trị"), ("BẢNG CÂN ĐỐI TÀI KHOẢN",), "loại sổ"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ban hành theo Thông tư 200/2014/TT-BTC"),
     ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ban hành theo Thông tư 133/2016/TT-BTC"), "chế độ kế toán"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Chế độ kế toán: Thông tư 200/2014/TT-BTC"),
     ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Chế độ kế toán: Thông tư 133/2016/TT-BTC"), "chế độ kế toán"),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Sổ tài chính"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Sổ quản trị"), "loại sổ"),
    (("TRIAL BALANCE", "Basis of preparation: management book"), ("TRIAL BALANCE", "Basis of preparation: statutory"),
     "basis of preparation"),
])
def test_a_different_book_method_or_unit_is_a_different_basis(prev_lines, cur_lines, what):
    g = _gate(_with_header(tb.t9_rows(), *prev_lines), _with_header(tb.t10_rows(), *cur_lines))
    assert not g.same_basis and g.basis[0][0] == what
    assert "xuất lại kỳ trước cùng cơ sở" in g.sentence("chị")


@pytest.mark.parametrize("prev_lines,cur_lines", [
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ngày in: 05/10/2026 08:01"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ngày in: 05/11/2026 10:23")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "In lúc 08:01 05/10/2026"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "In lúc 10:23 05/11/2026")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Trang 1/2"), ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Trang 1/3")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Loại tiền: VND"), ("BẢNG CÂN ĐỐI TÀI KHOẢN",)),
    (("TRIAL BALANCE", "September 2026"), ("TRIAL BALANCE", "October 2026")),
    (("TRIAL BALANCE SEPTEMBER 2026", "Printed by: ketoan01"), ("TRIAL BALANCE OCTOBER 2026", "Printed by: admin")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN THÁNG CHÍN", "Người in: Lan"), ("BẢNG CÂN ĐỐI TÀI KHOẢN THÁNG MƯỜI", "Người in: Hùng")),
    (("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ngày lập: 30/09/2026", "Đơn vị tính: đồng"),
     ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ngày lập: 31/10/2026", "Loại tiền: VND")),
])
def test_print_time_pages_users_and_month_names_never_make_a_different_basis(prev_lines, cur_lines):
    """Lead's probe on #53: ordinary export header lines that changed between two months of the same report."""
    g = _gate(_with_header(tb.t9_rows(), *prev_lines), _with_header(tb.t10_rows(), *cur_lines))
    assert g.same_basis, g.basis
    assert g.notes == []


def test_the_same_regulation_written_two_ways_is_the_same_regime():
    """Lead's probe: the full ministry wording and the short form name the same Thông tư."""
    g = _gate(_with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN",
                           "Ban hành theo Thông tư số 200/2014/TT-BTC ngày 22/12/2014 của Bộ Tài chính"),
              _with_header(tb.t10_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "(Ban hành theo TT 200/2014/TT-BTC ngày 22/12/2014)"))
    assert g.same_basis and g.notes == [], g.basis
    g = _gate(_with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "Theo Quyết định 48/2006/QĐ-BTC"),
              _with_header(tb.t10_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "Chế độ kế toán: Thông tư 133/2016/TT-BTC"))
    assert not g.same_basis


def test_a_regime_line_stated_in_one_period_only_is_a_soft_note_not_a_block():
    g = _gate(_with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "Ban hành theo Thông tư 200/2014/TT-BTC"),
              _with_header(tb.t10_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN"))
    assert g.same_basis and g.notes and "Thông tư 200" in g.note()
    assert g.sentence("chị") == ""


def test_year_to_date_movement_columns_are_a_different_basis():
    prev = tb.t9_rows()
    prev[4] = ["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ", None, "Số phát sinh lũy kế", None, "Số dư cuối kỳ", None]
    g = _gate(prev, tb.t10_rows())
    assert [b[0] for b in g.basis] == ["cột phát sinh"]


def test_no_change_is_explained_and_no_cause_asked_when_the_basis_differs():
    prev = _with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)", "Tháng 9 năm 2026")
    report = build.build(tb.t10_rows(), prev, "T10", "T09")
    assert not report.gate.same_basis
    assert report.summary.startswith("Lợi nhuận sau thuế kỳ này ")
    assert "so với kỳ trước" not in report.summary.split(".")[0]          # no "tăng/giảm … so với kỳ trước"
    assert "Khoản kéo" not in report.summary and "Bù lại" not in report.summary
    assert "cơ sở khác" in report.summary and report.questions == []
    assert any(i.code == "basis_differs" for i in report.issues)
    title = report.sheets["BC thang"][0][0]
    assert "KỲ TRƯỚC KHÁC CƠ SỞ LẬP" in title
    # Same files on the same basis: the story is told as before.
    assert "Khoản kéo" in build.build(tb.t10_rows(), tb.t9_rows(), "T10", "T09").summary


def _set_opening(rows, account, debit):
    out = [list(r) for r in rows]
    for r in out:
        if r and r[0] == account:
            r[2], r[6] = debit, r[6] + debit - r[2]           # keep đầu kỳ + phát sinh = cuối kỳ
    return out


def test_an_opening_balance_that_is_not_last_closing_is_named_and_blocks_its_account():
    cur = _set_opening(tb.t10_rows(), "131", 999 * tb.M)
    g = _gate(tb.t9_rows(), cur)
    assert [x.account for x in g.gaps] == ["131"] and g.blocked == {"131"}
    report = build.build(cur, tb.t9_rows(), "T10", "T09")
    issue = next(i for i in report.issues if i.code == "opening_bridge")
    assert "TK 131" in issue.message and issue.severity == "error"


def _renumber(rows, old, new):
    return [[new if (r and r[0] == old) else r[0]] + list(r[1:]) if r else r for r in rows]


def test_a_renumbered_expense_with_no_mapping_never_explains_a_change():
    cur = _renumber(tb.t10_rows(), "6417", "64171")
    g = _gate(tb.t9_rows(), cur)
    assert g.renumbered == [("6417", "64171")] and {"6417", "64171"} <= g.blocked
    report = build.build(cur, tb.t9_rows(), "T10", "T09")
    assert "nhiều nhất ở TK 6417" not in report.summary and "chi phí bán hàng" not in report.summary
    assert "có thể là đổi mã" in report.summary
    assert all(q.key != "selling" for q in report.questions)


def test_a_mapping_table_carries_the_code_across_and_is_not_an_error():
    cur = _renumber(tb.t10_rows(), "6417", "64171")
    g = _gate(tb.t9_rows(), cur, {"6417": "64171"})
    assert g.ok
    report = build.build(cur, tb.t9_rows(), "T10", "T09", mapping={"6417": "64171"}, mapping_file="Bang_ghep_TK.csv")
    selling = next(v for v in report.variances if v.key == "selling")
    assert dict((a, c) for a, _n, c in selling.drivers)["64171"] == Decimal(120 * tb.M)    # 420 − 300, same account
    assert not any(i.code in ("renumbered", "opening_bridge") for i in report.issues)


def test_profit_moved_to_prior_years_at_the_start_of_a_year_bridges():
    prev, cur = tb.t9_rows(), tb.t10_rows()
    close_4212 = next(r for r in prev if r and r[0] == "4212")[7]
    moved = []
    for r in cur:
        r = list(r)
        if r and r[0] == "4212":
            r[3], r[7] = 0, r[7] - r[3]
        moved.append(r)
    moved.insert(next(i for i, r in enumerate(moved) if r and r[0] == "4212"), ["4211", "LNST năm trước", 0, close_4212, 0, 0, 0, close_4212])
    p, c = read_trial_balance(prev), read_trial_balance(moved)
    assert comparable.bridge(p, c, new_year=False)[0]
    assert comparable.bridge(p, c, new_year=True)[0] == []
    assert comparable.is_new_year((2025, 12), (2026, 1)) and not comparable.is_new_year((2026, 9), (2026, 10))


@pytest.mark.parametrize("header", [["TK cũ", "TK mới", "Tên"], ["Mã TK (TT200)", "Mã TK (TT99)", "Ghi chú"],
                                    ["Old account", "New account"]])
def test_a_mapping_table_is_read_from_its_headers(header):
    rows = [["BẢNG GHÉP TÀI KHOẢN"], header, ["6417", "64171", "Dịch vụ mua ngoài"], ["6418", "64181", "x"],
            ["Tổng", "", ""]]
    assert comparable.read_mapping(rows) == {"6417": "64171", "6418": "64181"}


def _folder(tmp_path, prev_rows, cur_rows):
    from test_month_close import _write_tb
    _write_tb(tmp_path / "TB_T09.xlsx", prev_rows)
    _write_tb(tmp_path / "TB_T10.xlsx", cur_rows)
    return tmp_path


def _receipt(folder):
    tbs, aside, _ = __import__("office.month_close", fromlist=["x"])._tb_groups(folder)
    return intake.build(folder, [], tbs, sources.truth([]), {}, "BaoCao.xlsx")


def test_the_receipt_says_in_one_line_when_the_prior_period_is_on_another_basis(tmp_path):
    folder = _folder(tmp_path, _with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)", "Tháng 9 năm 2026"),
                     tb.t10_rows())
    r = _receipt(folder)
    assert not r.same_basis and "xuất lại kỳ trước cùng cơ sở" in r.comparable
    row = next(x for x in intake.card(r)["rows"] if x["title"].startswith("So với kỳ trước"))
    assert row["ok"] is False and row["detail"] == r.comparable
    assert intake.log_fields(r, "x")["comparable"] == r.comparable


def test_the_receipt_lists_the_mapping_table_as_read_and_raises_nothing_on_case_01_files(tmp_path):
    folder = _folder(tmp_path, tb.t9_rows(), _renumber(tb.t10_rows(), "6417", "64171"))
    with open(folder / "Bang_ghep_TK.csv", "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows([["TK cũ", "TK mới"], ["6417", "64171"]])
    r = _receipt(folder)
    assert r.comparable == ""
    row = next(x for x in intake.card(r)["rows"] if "Bảng ghép tài khoản" in x["title"])
    assert row["count"] == "1 cặp mã" and r.roles["Bang_ghep_TK.csv"]
    assert not any(d.file == "Bang_ghep_TK.csv" for d in r.dropped)
    assert sources.find_order_files(folder) == [] and sources.unexplained_order_files(folder, set()) == []
    (folder / "Bang_ghep_TK.csv").unlink()
    assert "đổi mã" in _receipt(folder).comparable


def test_the_bien_ban_records_what_was_not_compared(monkeypatch):
    from engine import audit
    from office import month_close
    monkeypatch.setattr(month_close, "_AUDIT", audit.AuditLog(period="10/2026"))
    prev = _with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)", "Tháng 9 năm 2026")
    cur = _set_opening(_renumber(tb.t10_rows(), "6417", "64171"), "131", 999 * tb.M)
    g = _gate(prev, cur)
    g.mapping_file, g.mapped = "Bang_ghep_TK.csv", 3
    month_close._audit_gate(g)
    items = [c.item for c in month_close._AUDIT.changes]
    assert items[0] == "Bảng ghép tài khoản" and "Cơ sở lập kỳ trước (tiêu đề)" in items
    assert "Số dư đầu kỳ so với cuối kỳ trước" in items and "Có thể đổi mã tài khoản" in items
    gap = next(c for c in month_close._AUDIT.changes if c.cell == "TK 131")
    assert "không dùng tài khoản này để giải thích" in gap.reason


def test_month_close_says_it_on_the_receipt_explains_nothing_and_writes_the_bien_ban(folder):  # noqa: F811
    """End to end on the Case 01 folder with the prior month exported from the management book."""
    from openpyxl import load_workbook
    from office import month_close
    from test_month_close import FakeBridge, FakePQ, _begin, _text
    book = load_workbook(folder / "TB_T09.xlsx")
    book.active["A2"] = "BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)"
    book.save(folder / "TB_T09.xlsx")
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    receipt = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert "cơ sở khác" in receipt[-1].text and "xuất lại kỳ trước cùng cơ sở" in receipt[-1].text
    month_close._PENDING = None
    _begin(bridge, pq, sources.DAY_FIRST)
    said = _text(month_close.approve(bridge, pq))
    assert "Khoản kéo lợi nhuận" not in said and "cơ sở khác" in said and "so với kỳ trước." not in said
    assert "KỲ TRƯỚC KHÁC CƠ SỞ LẬP" in bridge.sheets["BC thang"][0][0]
    assert any(c.item == "Cơ sở lập kỳ trước (tiêu đề)" for c in month_close._AUDIT.changes)
    month_close._PENDING = None


def test_month_close_on_case_01_files_says_nothing_about_the_basis(folder):  # noqa: F811
    from office import month_close
    from test_month_close import FakeBridge, FakePQ, _begin, _text
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    assert "cơ sở" not in month_close.start(bridge, pq, sources.DAY_FIRST)[-1].text
    month_close._PENDING = None
    _begin(bridge, pq, sources.DAY_FIRST)
    said = _text(month_close.approve(bridge, pq))
    assert "cơ sở khác" not in said and "không khớp số dư" not in said and "đổi mã" not in said
    assert not any("Cơ sở lập" in c.item or "Số dư đầu kỳ" in c.item for c in month_close._AUDIT.changes)
    month_close._PENDING = None


def test_the_soft_note_shows_on_the_receipt_as_a_note_not_a_warning(tmp_path):
    folder = _folder(tmp_path, _with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "Tháng 9 năm 2026",
                                            "Chế độ kế toán: Thông tư 200/2014/TT-BTC"),
                     _with_header(tb.t10_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN", "Tháng 10 năm 2026"))
    r = _receipt(folder)
    assert r.same_basis and r.comparable == "" and "Thông tư 200" in r.basis_note
    row = next(x for x in intake.card(r)["rows"] if x["title"].startswith("So với kỳ trước"))
    assert row["ok"] is True and row["title"].endswith("lưu ý")


def _bhc():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import bhc_case
    return bhc_case


def test_the_bhc_shaped_month_close_bridges_and_keeps_its_basis(tmp_path):
    """BHC-shaped books (tools/dien_tap/bhc_case.py, synthetic): Sep → Oct carries every balance across."""
    from engine.cli import read_rows
    _bhc().make(tmp_path)
    prev, cur = read_rows(tmp_path / "TB_T09.xlsx"), read_rows(tmp_path / "TB_T10.xlsx")
    g = _gate(prev, cur)
    assert g.ok and g.checked, (g.basis, [(x.account, x.diff) for x in g.gaps], g.renumbered)


def test_a_year_end_421_split_in_bhc_shaped_books_bridges_only_as_a_new_year(tmp_path):
    """December's profit (4212) opens January as prior years' profit (4211): the same money, under 421."""
    from decimal import Decimal as D
    bhc = _bhc()
    bhc.make(tmp_path)
    fig = bhc.month_figures(tmp_path, bhc.PERIOD)
    opening = {"111": D(350_000_000), "112": D(9_000_000_000), "131": D(6_500_000_000), "155": D(8_200_000_000),
               "331": D(-5_100_000_000), "3334": D(-300_000_000), "411": D(-12_000_000_000)}
    opening["4212"] = -sum(opening.values(), D(0))
    rows12, closing12 = bhc._tb_rows((2026, 12), opening, bhc._movements(fig))
    jan = dict(closing12)
    jan["4211"], jan["4212"] = jan.get("4211", D(0)) + jan.pop("4212"), D(0)
    mv = bhc._movements(fig)
    mv.setdefault("4211", (D(0), D(0)))
    rows01, _ = bhc._tb_rows((2027, 1), jan, mv)
    assert sources.period_of(rows12) == (2026, 12) and sources.period_of(rows01) == (2027, 1)
    g = comparable.gate(rows12, read_trial_balance(rows12), rows01, read_trial_balance(rows01), new_year=True)
    assert g.ok, [(x.account, x.diff) for x in g.gaps]
    g = comparable.gate(rows12, read_trial_balance(rows12), rows01, read_trial_balance(rows01), new_year=False)
    assert {x.account for x in g.gaps} == {"4211", "4212"}
    # The month close decides "new year" from the two titles: December → January is one.
    assert comparable.is_new_year(sources.period_of(rows12), sources.period_of(rows01))
    report = build.build(rows01, rows12, "T01", "T12")
    assert not any(i.code == "opening_bridge" for i in report.issues)


def test_the_case_01_sample_bridges_and_keeps_its_basis():
    from pathlib import Path
    from engine.cli import read_rows
    sample = Path(__file__).resolve().parents[1] / "assets" / "mau" / "Mau_SongHong_T10"
    g = _gate(read_rows(sample / "TB_T09.xlsx"), read_rows(sample / "TB_T10.xlsx"))
    assert g.ok and g.checked and g.notes == []


# ── Grader, Case 05 class (F5): a code change is not a broken bridge ──────────────────────────────────────────────

def _split(rows, account, parts):
    """The same balance under several leaf codes of one parent: [(code, share)] with shares adding up to 1."""
    out = []
    for r in rows:
        if r and r[0] == account:
            out.append(list(r))
            nums = r[2:8]
            done = [0] * 6
            for k, (code, share) in enumerate(parts):
                last = k == len(parts) - 1
                part = [n - d if last else int(n * share) for n, d in zip(nums, done)]
                done = [d + p for d, p in zip(done, part)]
                out.append([code, f"{r[1]} {k + 1}"] + part)
        else:
            out.append(r)
    return out


def test_a_code_moved_to_another_parent_with_its_balance_is_a_warning_not_an_error():
    cur = _renumber(tb.t10_rows(), "156", "158")
    g = _gate(tb.t9_rows(), cur)
    assert {x.account for x in g.moved} == {"156", "158"} and g.broken == []
    report = build.build(cur, tb.t9_rows(), "T10", "T09")
    assert not any(i.code == "opening_bridge" for i in report.issues)
    note = next(i for i in report.issues if i.code == "renumbered_balance")
    assert note.severity == "warning" and "156" in note.message and "158" in note.message


def test_several_old_codes_merged_into_one_new_code_bridge_by_their_sum():
    prev = _split(tb.t9_rows(), "112", [("1121", 0.6), ("1122", 0.4)])
    cur = _renumber(tb.t10_rows(), "112", "1128")
    g = _gate(prev, cur)
    assert g.broken == [], [(x.account, x.diff) for x in g.broken]
    assert {x.account for x in g.moved} == {"1121", "1122", "1128"}


def test_a_real_gap_next_to_a_code_change_is_still_the_only_error():
    cur = _set_opening(_renumber(tb.t10_rows(), "156", "158"), "131", 999 * tb.M)
    g = _gate(tb.t9_rows(), cur)
    assert [x.account for x in g.broken] == ["131"]
    report = build.build(cur, tb.t9_rows(), "T10", "T09")
    issue = next(i for i in report.issues if i.code == "opening_bridge")
    assert "TK 131" in issue.message and "158" not in issue.message and "1 tài khoản" in issue.message


def test_a_mapping_table_is_found_on_any_sheet_of_any_file_with_a_merged_header(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from engine.cli import read_rows
    wb = openpyxl.Workbook()
    wb.active.title = "Ghi chu"
    wb.active.append(["Danh mục dùng nội bộ"])
    ws = wb.create_sheet("Ma TK")
    ws.append(["Số hiệu tài khoản", None, "Diễn giải"])
    ws.append(["Cũ", "Mới", None])
    ws.append(["1561 - Giá mua hàng hóa", "TK 1568", "x"])
    ws.append([6417.0, "64171", "y"])
    ws.append(["Cộng", None, None])
    wb.save(tmp_path / "Danh muc TT200 sang TT99.xlsx")
    name, mapping = comparable.find_mapping(tmp_path, read_rows)
    assert name == "Danh muc TT200 sang TT99.xlsx" and mapping == {"1561": "1568", "6417": "64171"}


def test_a_two_column_list_in_a_file_not_named_as_a_mapping_needs_two_pairs(tmp_path):
    with open(tmp_path / "ghi chu.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["TK cũ", "TK mới"], ["6417", "64171"]])
    from engine.cli import read_rows
    assert comparable.find_mapping(tmp_path, read_rows) == ("", {})


def test_change_columns_stay_empty_when_the_prior_period_is_on_another_basis():
    prev = _with_header(tb.t9_rows(), "BẢNG CÂN ĐỐI TÀI KHOẢN (SỔ QUẢN TRỊ)", "Tháng 9 năm 2026")
    grid = build.build(tb.t10_rows(), prev, "T10", "T09").sheets["BC thang"]
    lines = grid[4:4 + 5]
    assert all(r[4:8] == ["", "", "", ""] and str(r[2]).startswith("=") and str(r[3]).startswith("=") for r in lines)
    same = build.build(tb.t10_rows(), tb.t9_rows(), "T10", "T09").sheets["BC thang"]
    assert str(same[4][4]).startswith("=C")


@pytest.mark.parametrize("header", [["Mã TK cũ", "Tên TK cũ", "Ghép vào TK", "Tên TK mới"],
                                    ["Tên tài khoản cũ", "Số hiệu cũ", "Chuyển sang", "Tên tài khoản mới"]])
def test_the_code_column_is_the_one_holding_codes_not_the_name_beside_it(header):
    code_first = header[0].startswith("Mã")
    data = [("4111", "Vốn góp", "4118", "Vốn khác"), ("4112", "Thặng dư vốn", "4118", "Vốn khác"),
            ("1561", "Giá mua", "1568", "Hàng hóa")]
    rows = [header] + [[o, n_old, new, n_new] if code_first else [n_old, o, new, n_new] for o, n_old, new, n_new in data]
    assert comparable.read_mapping(rows) == {"4111": "4118", "4112": "4118", "1561": "1568"}


def test_what_is_still_off_after_the_mapping_stays_an_error():
    cur = _set_opening(_renumber(tb.t10_rows(), "156", "158"), "158", 6705 * tb.M)    # 5 triệu more than T9 closed
    g = _gate(tb.t9_rows(), cur, {"156": "158"})
    assert [x.account for x in g.broken] == ["158"] and g.broken[0].diff == 5 * tb.M and not g.ok


def test_several_equity_codes_mapped_into_one_bridge_by_their_sum():
    prev = _split(tb.t9_rows(), "411", [("4111", 0.7), ("4112", 0.3)])
    cur = _renumber(tb.t10_rows(), "411", "4118")
    g = _gate(prev, cur, {"4111": "4118", "4112": "4118"})
    assert g.broken == [] and g.moved == [], [(x.account, x.diff) for x in g.gaps]
