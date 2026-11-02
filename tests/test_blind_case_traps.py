"""Traps found by the independent grading round on cases Clicky had never seen.

The data here is made up for each trap; the graders' cases are not used.
"""
import csv
from decimal import Decimal

from engine import checks, sources
from engine.trial_balance import read_trial_balance

HEAD = [["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ", None, "Số phát sinh", None, "Số dư cuối kỳ", None],
        [None, None, "Nợ", "Có", "Nợ", "Có", "Nợ", "Có"]]


def test_dotted_detail_accounts_are_read_and_reach_the_totals():
    """A TB that exports only detail accounts (6421.01, 6421.02) and no 6421 parent line."""
    rows = HEAD + [
        ["111", "Tiền mặt", 1000, 0, 0, 300, 700, 0],
        ["6421.01", "Lương bán hàng", 0, 0, 200, 0, 200, 0],
        ["6421.02", "Thuê cửa hàng", 0, 0, 100, 0, 100, 0],
        [None, "Tổng cộng", 1000, 0, 300, 300, 1000, 0],
    ]
    tb = read_trial_balance(rows)
    assert [l.account for l in tb.lines] == ["111", "6421.01", "6421.02"]
    assert sum(l.dr for l in tb.leaves if l.account.startswith("642")) == Decimal(300)
    assert tb.skipped == []


def test_money_on_an_unreadable_account_code_is_reported_not_dropped():
    rows = HEAD + [
        ["111", "Tiền mặt", 1000, 0, 0, 300, 700, 0],
        ["6421/01", "Lương bán hàng", 0, 0, 300, 0, 300, 0],
        ["Tổng cộng 642", "", 0, 0, 300, 0, 300, 0],       # a subtotal line is not an account
    ]
    tb = read_trial_balance(rows)
    assert [(r, a) for r, a, _ in tb.skipped] == [(4, "6421/01")]
    issues = [i for i in checks.check(tb) if i.code == "unreadable_account"]
    assert len(issues) == 1 and issues[0].severity == "error" and "6421/01" in issues[0].message


def _orders(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["SoCT", "Ngay", "MaKH", "ThanhTien"])
        w.writerows(rows)


def test_a_total_line_at_the_end_of_an_export_is_not_counted_as_an_order(tmp_path):
    _orders(tmp_path / "DonHang_A.csv", [["A1", "05/10/2026", "K1", "100"], ["A2", "21/10/2026", "K2", "200"],
                                         ["", "", "", "300"], ["Tổng cộng", "", "", "300"]])
    files = sources.find_order_files(tmp_path)
    t = sources.truth(files)
    assert t.amount == Decimal(300) and t.unique_rows == 2 and t.unreadable_dates == 0
    assert len(files[0].footers) == 2


def test_return_slips_are_matched_to_521_and_sales_to_511(tmp_path):
    from office import month_close
    _orders(tmp_path / "DonHang_A.csv", [["A1", "05/10/2026", "K1", "1.000.000"], ["A2", "21/10/2026", "K2", "500.000"],
                                         ["TL1", "25/10/2026", "K1", "-200.000"]])
    t = sources.truth(sources.find_order_files(tmp_path))
    assert (t.gross, t.returns, t.amount) == (Decimal(1_500_000), Decimal(200_000), Decimal(1_300_000))
    ledger = {"name": "TB_T10", "rev_acct": "5111", "revenue": Decimal(1_500_000), "gross": Decimal(1_500_000),
              "returns": Decimal(200_000), "cost": Decimal(0)}
    net = month_close._net_of_returns(ledger, t)
    assert net["revenue"] == t.amount and net["net"]           # files and ledger agree once returns are netted on both sides
    # Without return slips the ledger is left as it was: Có 511 is the target.
    (tmp_path / "sales_only").mkdir()
    _orders(tmp_path / "sales_only" / "DonHang_B.csv", [["B1", "05/10/2026", "K1", "1.500.000"]])
    plain = sources.truth(sources.find_order_files(tmp_path / "sales_only"))
    assert month_close._net_of_returns(ledger, plain) is ledger


def test_branches_that_reuse_document_numbers_keep_both_orders(tmp_path):
    from engine import m_code
    from office import month_close
    for name, branch in (("DonHang_HN.csv", "HN"), ("DonHang_DN.csv", "DN")):
        with open(tmp_path / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["SoCT", "Ngay", "MaKH", "ThanhTien", "ChiNhanh"])
            w.writerow(["0001", "05/10/2026", "K1" if branch == "HN" else "K7", "100", branch])
            w.writerow(["0002", "21/10/2026", "K2", "200", branch])
    # The HN file exported twice: same number and same content is a real duplicate.
    with open(tmp_path / "DonHang_HN_lan2.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["SoCT", "Ngay", "MaKH", "ThanhTien", "ChiNhanh"])
        w.writerow(["0001", "05/10/2026", "K1", "100", "HN"])
    files = sources.find_order_files(tmp_path)
    t = sources.truth(files)
    assert t.amount == Decimal(600) and t.unique_rows == 4
    assert [d for d, _, _ in t.duplicates] == ["0001"] and t.collisions == {"0001": 2, "0002": 2}
    keys = month_close._dedup_key(files, t)
    assert keys == ["ChiNhanh", "SoCT"]
    m = m_code.folder_query("C:\\T10", {f.name: "vi-VN" for f in files}, [("Ngay", "type date")], "SoCT", key_columns=keys)
    assert 'Table.Distinct(BoDongKhongNgay, {"ChiNhanh", "SoCT"})' in m


def test_rows_outside_the_ledger_month_are_set_aside_and_filtered_in_the_query(tmp_path):
    from engine import m_code
    _orders(tmp_path / "DonHang_A.csv", [["A1", "28/09/2026", "K1", "50"], ["A2", "15/10/2026", "K2", "200"],
                                         ["A3", "02/11/2026", "K3", "70"]])
    t = sources.truth(sources.find_order_files(tmp_path), (2026, 10))
    assert t.amount == Decimal(200) and [d for d, *_ in t.outside] == ["A1", "A3"]
    m = m_code.folder_query("C:\\T10", {"DonHang_A.csv": "vi-VN"}, [("Ngay", "type date")], "SoCT", period=(2026, 10))
    assert "Date.Year([Ngay]) = 2026 and Date.Month([Ngay]) = 10 otherwise true" in m


def test_a_file_whose_dates_cannot_tell_the_order_is_read_in_the_ledger_month(tmp_path):
    # 10/05 and 10/12 only: day/month would give May and December; month/day gives October.
    _orders(tmp_path / "DonHang_US.csv", [["U1", "10/05/2026", "K1", "100"], ["U2", "10/12/2026", "K2", "100"]])
    files = sources.find_order_files(tmp_path)
    assert files[0].date_order is None
    t = sources.truth(files, (2026, 10))
    assert files[0].date_order == sources.MONTH_FIRST and t.amount == Decimal(200) and not t.outside


def test_a_hardcoded_culture_is_flagged_and_used_to_explain_the_dates():
    from engine import m_code
    q = '''let
    Nguon = Csv.Document(File.Contents("a.csv")),
    Kieu = Table.TransformColumnTypes(Nguon, {{"Ngay", type date}, {"ThanhTien", type number}}, "en-US")
in
    Kieu'''
    assert [f.code for f in m_code.lint("DonHang", q)] == ["locale_fixed"]
    assert m_code.fixed_culture(q) == "en-US"
    per_file = m_code.folder_query("C:\\T10", {"a.csv": "vi-VN"}, [("Ngay", "type date")], "SoCT", folder_parameter="ThuMuc")
    assert m_code.fixed_culture(per_file) is None and m_code.lint("DonHang", per_file) == []


def test_amount_format_decides_the_culture_when_dates_are_iso_and_misread_amounts_are_named(tmp_path):
    from office import month_close
    _orders(tmp_path / "DonHang_VN.csv", [["V1", "2026-10-05", "K1", "1.250.000"], ["V2", "2026-10-21", "K2", "750.000"]])
    files = sources.find_order_files(tmp_path)
    assert files[0].date_order is None and files[0].number_style == sources.DAY_FIRST
    assert files[0].culture(sources.MONTH_FIRST) == "vi-VN"           # not the en-US machine's culture
    t = sources.truth(files)
    # Excel on an en-US machine: the row arrived but its amount failed and was replaced by 0.
    loaded = month_close.Loaded("DonHang", ["SoCT", "Ngay", "MaKH", "ThanhTien"],
                                [["V1", 46300, "K1", 0], ["V2", 46316, "K2", 750000]],
                                sources.find_columns(["SoCT", "Ngay", "MaKH", "ThanhTien"]))
    why = month_close.diagnose(t, loaded, sources.MONTH_FIRST)
    assert [(d, got) for d, _, got in why["amount"]] == [("V1", Decimal(0))]
    beats, rows = month_close._cause_beats(why, sources.MONTH_FIRST, 0)
    assert beats[0].text == "1 chứng từ vẫn có trong Excel nhưng số tiền bị đọc sai, làm lệch 1,2 triệu."
    assert rows[0][0] == "Đọc sai số tiền"


def test_shared_numbers_and_out_of_period_rows_explain_the_whole_gap(tmp_path):
    """An AI query de-duplicates on the number alone and does not filter the month: both causes are named,
    with money, and together they add up to the gap."""
    from office import month_close
    for name, branch, rows in (("DonHang_HN.csv", "HN", [["0001", "05/10/2026", "K1", "100"], ["0002", "28/09/2026", "K2", "40"]]),
                               ("DonHang_DN.csv", "DN", [["0001", "06/10/2026", "K7", "300"], ["0003", "20/10/2026", "K8", "50"]])):
        with open(tmp_path / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["SoCT", "Ngay", "MaKH", "ThanhTien", "ChiNhanh"])
            w.writerows([r + [branch] for r in rows])
    t = sources.truth(sources.find_order_files(tmp_path), (2026, 10))
    assert t.amount == Decimal(450)
    header = ["SoCT", "Ngay", "MaKH", "ThanhTien", "ChiNhanh"]
    # What the AI query loads: DN first, so DN's 0001 is kept and HN's 0001 (100) is removed; 0002 from September stays.
    loaded = month_close.Loaded("DonHang", header, [["0001", 0, "K7", 300, "DN"], ["0003", 0, "K8", 50, "DN"],
                                                    ["0002", 0, "K2", 40, "HN"]], sources.find_columns(header))
    gap = loaded.amount - t.amount
    assert gap == Decimal(-60)
    why = month_close.diagnose(t, loaded, sources.DAY_FIRST)
    assert why["shared"] == [("0001", 1, Decimal(100))] and [d for d, *_ in why["outside"]] == ["0002"]
    assert month_close.explained(why) == gap
    beats, rows = month_close._cause_beats(why, sources.DAY_FIRST, 0, gap=gap)
    assert [r[0] for r in rows] == ["Xoá nhầm đơn trùng số giữa các nơi", "Dòng ngoài kỳ còn trong query"]
    assert beats[0].text == "1 đơn thật bị xoá vì trùng số chứng từ với nơi khác, làm mất 100 đồng."


def test_a_gap_the_causes_do_not_cover_is_said_out_loud():
    from office import month_close
    why = {"date": [], "distinct": [], "other": [], "excel_dups": []}
    beats, rows = month_close._cause_beats(why, None, 0, gap=Decimal(-5_000_000))
    assert beats[-1].text == "Còn 5 triệu em chưa tìm ra nguyên nhân." and rows[-1][2] == Decimal(-5_000_000)


def test_money_outside_the_report_lines_is_said_in_the_summary():
    """6281 (a cost account the report has no line for) still carries 12 triệu: say it and its effect on profit."""
    from engine import checks, monthly_report
    tb = read_trial_balance(HEAD + [["111", "Tiền mặt", 100_000_000, 0, 0, 12_000_000, 88_000_000, 0],
                                    ["6281", "Chi phí sản xuất chung", 0, 0, 12_000_000, 0, 12_000_000, 0]])
    found = [i for i in checks.check(tb) if i.code == "unmapped"]
    assert len(found) == 1 and "12.000.000" in found[0].message and "lợi nhuận sau thuế chưa trừ khoản này" in found[0].message
    text = monthly_report.summary_vi([monthly_report.Variance("pat", "Lợi nhuận sau thuế", Decimal(100), Decimal(90), [])], found)
    assert "Lưu ý: TK 6281 Chi phí sản xuất chung còn 12.000.000" in text and "điểm cần lưu ý" not in text
