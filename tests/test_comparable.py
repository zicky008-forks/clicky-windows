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
     ("BẢNG CÂN ĐỐI TÀI KHOẢN", "Ban hành theo Thông tư 99/2025/TT-BTC"), "dòng đầu file"),
])
def test_a_different_book_method_or_unit_is_a_different_basis(prev_lines, cur_lines, what):
    g = _gate(_with_header(tb.t9_rows(), *prev_lines), _with_header(tb.t10_rows(), *cur_lines))
    assert not g.same_basis and g.basis[0][0] == what
    assert "xuất lại kỳ trước cùng cơ sở" in g.sentence("chị")


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
