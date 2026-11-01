"""Month close from one command: source files → Power Query → reconcile with the ledger → fix → report.

Excel and Power Query are faked: the fake query engine applies the same rules Power
Query would (machine date format when no culture is given, keyed Table.Distinct),
so the test proves Clicky finds the gap, names the cause and verifies the fix.
"""
import csv
import re
from datetime import date
from decimal import Decimal

import pytest

import tb_samples as tb
from engine import m_code, sources
from office import month_close

BN = 1_000_000_000
HEADER = ["SoCT", "Ngay", "MaKH", "NhomHang", "SL", "DonGia", "ThanhTien", "GiaVon", "ChiNhanh"]
COST = 966_666_667
# (doc, date text, customer, cost); every order is 1,4 tỷ so the six orders make 8,4 tỷ = Có 5111.
HN = [("HN1", "10/05/2026", "KH01", COST), ("HN2", "10/20/2026", "KH02", COST)]             # month/day
HCM = [("HCM1", "05/10/2026", "KH09", COST), ("HCM2", "05/10/2026", "KH09", COST),        # same customer, same day
       ("HCM3", "21/10/2026", "KH10", COST), ("HCM3", "21/10/2026", "KH10", COST),        # exported twice
       ("HCM4", "30/10/2026", "KH11", 5_800_000_000 - 5 * COST)]                         # day/month

AI_QUERY = '''let
    Source = Folder.Files("C:\\Users\\ke.toan\\Desktop\\T10"),
    Csv = Table.SelectRows(Source, each [Extension] = ".csv"),
    Data = Table.AddColumn(Csv, "Bang", each Table.PromoteHeaders(Csv.Document([Content]))),
    Combined = Table.Combine(Data[Bang]),
    #"Changed Type" = Table.TransformColumnTypes(Combined, {{"Ngay", type date}, {"ThanhTien", type number}}),
    #"Removed Errors" = Table.RemoveRowsWithErrors(#"Changed Type", {"Ngay"}),
    Thang10 = Table.SelectRows(#"Removed Errors", each Date.Month([Ngay]) = 10),
    #"Removed Duplicates" = Table.Distinct(Thang10, {"Ngay", "MaKH"})
in
    #"Removed Duplicates"'''


def _write_csv(path, rows, branch):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for doc, d, kh, cost in rows:
            w.writerow([doc, d, kh, "Sữa", 1000, 1_400_000, 1_400_000_000, cost, branch])


def _write_tb(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(path)


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "DonHang").mkdir()
    _write_csv(tmp_path / "DonHang" / "DonHang_HN_T10.csv", HN, "HN")
    _write_csv(tmp_path / "DonHang" / "DonHang_HCM_T10.csv", HCM, "HCM")
    t9 = tb.month(8000, 500, 200, 5300, 5000, 8500, 5200, 20, 60, (700, 300, 150), (500, 160), 10, 5, 231)
    t10 = tb.month(8400, 450, 210, 5800, 6000, 9000, 5500, 15, 70, (760, 420, 200), (520, 170), 5, 25, 139,
                   returns_into_511=120)
    _, _, close9 = tb.balances(tb.OPENING_T9, t9)
    _write_tb(tmp_path / "TB_T09.xlsx", tb.misa_rows(tb.OPENING_T9, t9, "Tháng 9 năm 2026"))
    _write_tb(tmp_path / "TB_T10.xlsx", tb.misa_rows({a: v for a, v in close9.items() if a[0] in "1234"}, t10,
                                                     "Tháng 10 năm 2026"))
    (tmp_path / "M_do_AI_viet.txt").write_text(AI_QUERY, encoding="utf-8")
    month_close._PENDING = None
    yield tmp_path
    month_close._PENDING = None


class FakeBridge:
    def __init__(self, folder):
        self.folder, self.sheets, self.active, self.formats = folder, {"Sheet1": []}, "Sheet1", []

    def available(self):
        return True

    def workbook_folder(self):
        return str(self.folder) if self.folder else None

    def sheet_names(self):
        return list(self.sheets)

    def sheet_rows(self, name):
        return self.sheets[name]

    def context(self):
        return {"sheet": self.active}

    def add_sheet(self, name):
        final = name if name not in self.sheets else name + " 2"
        self.sheets[final] = []
        return final

    def write(self, ref, grid):
        m = re.match(r"^'?(.*?)'?!A1$", ref)
        self.sheets[m.group(1).replace("''", "'")] = [list(r) for r in grid]
        return True

    def format_numbers(self, ref, number_format=None):
        self.formats.append(ref)

    def activate(self, name):
        self.active = name


class FakePQ:
    """Loads the folder the way Power Query would run the given M on a vi-VN machine."""

    def __init__(self, bridge, folder, machine=sources.DAY_FIRST):
        self.bridge, self.folder, self.machine, self.queries = bridge, folder, machine, {}

    def list(self):
        return list(self.queries.items())

    def add(self, name, formula):
        self.queries[name] = formula
        return True

    def load(self, name):
        formula = self.queries[name]
        fixed = "XoaTrungTheoSoCT" in formula
        rows, seen = [], set()
        for f in sources.find_order_files(self.folder):
            order = f.date_order if fixed else self.machine
            for r in f.rows:
                d = sources.parse_date(r[1], order)
                if not fixed and (d is None or d.month != 10):
                    continue                                   # RemoveRowsWithErrors, then the month filter
                key = r[0] if fixed else (d, r[2])
                if key in seen:
                    continue
                seen.add(key)
                rows.append([r[0], d, r[2], r[3], int(r[4]), int(r[5]), int(r[6]), int(r[7]), r[8]])
        sheet = name
        self.bridge.sheets[sheet] = [HEADER] + rows
        return sheet, HEADER, rows


def _text(beats):
    return " ".join(b.text for b in beats)


# ── sources ───────────────────────────────────────────────────────────────────

def test_date_order_reads_the_values_not_the_machine():
    assert sources.date_order(["10/05/2026", "10/20/2026"]) == sources.MONTH_FIRST
    assert sources.date_order(["05/10/2026", "30/10/2026"]) == sources.DAY_FIRST
    assert sources.date_order(["01/02/2026", "2026-10-13"]) is None
    assert sources.parse_date("10/20/2026", sources.MONTH_FIRST) == date(2026, 10, 20)
    assert sources.parse_date("10/20/2026", sources.DAY_FIRST) is None


def test_truth_counts_each_document_once(folder):
    files = sources.find_order_files(folder)
    assert [f.name for f in files] == ["DonHang_HCM_T10.csv", "DonHang_HN_T10.csv"]
    t = sources.truth(files)
    assert (t.rows, t.unique_rows, t.amount, t.cost) == (7, 6, Decimal(84 * BN // 10), Decimal(58 * BN // 10))
    assert [d for d, _, _ in t.duplicates] == ["HCM3"]
    assert t.doc_numbers["HN2"].date == date(2026, 10, 20)


def test_ledger_reads_revenue_and_returns(folder):
    from engine.cli import read_rows
    tbs = sources.find_trial_balances(folder, read_rows)
    assert [n for n, _, _ in tbs] == ["TB_T09", "TB_T10"]
    cur = tbs[-1][2]
    assert sources.revenue_account(cur) == "5111"
    assert sources.ledger_movement(cur, "5111", "cr") == Decimal(8400 * tb.M)
    assert sources.returns_in_revenue(cur) == Decimal(120 * tb.M)
    assert sources.returns_in_revenue(tbs[0][2]) == 0


# ── m_code ────────────────────────────────────────────────────────────────────

def test_lint_flags_dedup_on_columns_other_than_the_document_number():
    codes = {(f.code, f.step) for f in m_code.lint("DonHang", AI_QUERY)}
    assert ("distinct_keys", "Removed Duplicates") in codes
    assert ("locale_types", "Changed Type") in codes and ("remove_errors", "Removed Errors") in codes
    ok = AI_QUERY.replace('{"Ngay", "MaKH"}', '{"SoCT"}')
    assert "distinct_keys" not in {f.code for f in m_code.lint("DonHang", ok)}


def test_folder_query_reads_each_file_in_its_own_format_and_lints_clean():
    m = m_code.folder_query("C:\\T10", {"a.csv": "en-US", "b.csv": "vi-VN", "c.csv": "vi-VN"},
                            [("Ngay", "type date"), ("ThanhTien", "type number")], "SoCT", folder_parameter="ThuMuc")
    assert 'if [Name] = "a.csv" then "en-US"' in m and 'else "vi-VN"' in m
    assert 'Table.Distinct(GopFile, {"SoCT"})' in m and "Folder.Files(ThuMuc)" in m
    assert m_code.lint("DonHang", m) == []
    assert "IsParameterQuery = true" in m_code.parameter_query("C:\\T10")


# ── the whole flow ────────────────────────────────────────────────────────────

def test_start_finds_the_gap_and_names_both_causes(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    beats = month_close.start(bridge, pq, sources.DAY_FIRST)
    said = _text(beats)
    assert "2 file đơn hàng: 7 dòng" in said and "6 chứng từ, doanh thu 8,4 tỷ" in said
    assert "khớp file gốc đến từng đồng" in said
    lint = next(b.text for b in beats if b.text.startswith("Query DonHang có"))
    assert lint.index("Bước Changed Type") < lint.index("Bước Removed Duplicates") < lint.index("Bước Removed Errors")
    assert "M_do_AI_viet.txt" in said and "máy này không có" in said
    assert f'Folder.Files("{folder / "DonHang"}")' in pq.queries["DonHang"]
    assert "chỉ giữ 3 dòng, doanh thu 4,2 tỷ, thiếu 4,2 tỷ so với sổ cái" in said
    assert "2 chứng từ, 2,8 tỷ, mất vì máy này đọc ngày kiểu ngày/tháng" in said
    assert "1 chứng từ, 1,4 tỷ, bị xoá nhầm ở bước xoá trùng" in said
    gap = next(b for b in beats if b.text.startswith("Query trong Excel"))
    assert gap.ref == "'Doi chieu'!C7"
    check = bridge.sheets["Doi chieu"]
    assert check[3][1:3] == ["=COUNT('DonHang'!G:G)", "=SUM('DonHang'!G:G)"]
    assert check[4][1:4] == [6, Decimal(84 * BN // 10), Decimal(58 * BN // 10)]
    assert check[5][2] == Decimal(8400 * tb.M) and check[6][2] == "=C4-C6"
    assert month_close.pending()


def test_approve_fixes_verifies_then_builds_the_report(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close.start(bridge, pq, sources.DAY_FIRST)
    beats = month_close.approve(bridge, pq)
    said = _text(beats)
    assert not month_close.pending()
    assert "query giữ 6 chứng từ, doanh thu 8,4 tỷ, giá vốn khớp Nợ TK 632. Khớp sổ cái" in said
    assert pq.queries["ThuMucDonHang"].startswith(f'"{folder}"') or str(folder).replace("\\", "\\\\") in pq.queries["ThuMucDonHang"]
    assert "BC thang" in bridge.sheets and "TB_T10" in bridge.sheets
    profit = next(b for b in beats if "Lợi nhuận thực" in b.text)
    assert "Lợi nhuận thực là 436 triệu" in profit.text and "556 triệu" in profit.text
    assert profit.ref.startswith("'BC thang'!C")
    assert month_close.approve(bridge, pq) == []              # nothing left to approve


def test_reject_keeps_the_query_and_warns(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close.start(bridge, pq, sources.DAY_FIRST)
    before = pq.queries["DonHang"]
    assert "đừng dùng" in _text(month_close.reject())
    assert pq.queries["DonHang"] == before and not month_close.pending()


def test_clean_query_goes_straight_to_the_report(folder):
    (folder / "M_do_AI_viet.txt").unlink()
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    said = _text(month_close.start(bridge, pq, sources.DAY_FIRST))
    assert "em tạo query mới" in said and "Query giữ 6 dòng, doanh thu 8,4 tỷ, khớp sổ cái" in said
    assert "Lợi nhuận thực là 436 triệu" in said and not month_close.pending()


def test_query_that_excel_cannot_load_still_gets_the_fix_offered(folder):
    class BrokenPQ(FakePQ):
        def load(self, name):
            if "XoaTrungTheoSoCT" not in self.queries[name]:
                return "DataSource.Error: Could not find a part of the path."
            return super().load(name)
    bridge = FakeBridge(folder)
    pq = BrokenPQ(bridge, folder)
    said = _text(month_close.start(bridge, pq, sources.DAY_FIRST))
    assert "Excel báo lỗi khi tải query DonHang" in said and month_close.pending()
    assert bridge.sheets["Doi chieu"][3][4] == "Chưa đọc được bảng query"
    assert "Khớp sổ cái đến từng đồng" in _text(month_close.approve(bridge, pq))


def test_unsaved_workbook_and_empty_folder(tmp_path):
    bridge = FakeBridge(None)
    assert "chưa được lưu" in _text(month_close.start(bridge, FakePQ(bridge, tmp_path), None))
    bridge = FakeBridge(tmp_path)
    assert "không thấy file đơn hàng" in _text(month_close.start(bridge, FakePQ(bridge, tmp_path), None))


def test_voice_triggers_and_pending_gate(folder):
    import skills
    skills.load_all()
    assert skills.match("làm báo cáo tháng từ thư mục này")["name"] == "Báo cáo tháng trọn gói"
    assert skills.match("đồng ý") is None or skills.match("đồng ý")["handler"] is not month_close.handle_approve
    start = re.compile(month_close.START_TRIGGER, re.I)
    assert start.search("làm báo cáo tháng từ thư mục này")
    assert start.search("chạy case")
    assert not start.search("làm báo cáo tháng")
    approve = re.compile(month_close.APPROVE_TRIGGER, re.I)
    assert approve.search("đồng ý") and approve.search("ok sửa đi")
    assert not approve.search("được không em, sao doanh thu tháng này lại giảm mạnh vậy")
    reject = re.compile(month_close.REJECT_TRIGGER, re.I)
    assert reject.search("thôi") and not reject.search("không hiểu sao lợi nhuận lại giảm nhiều thế")

    gated = [s for s in month_close.SKILLS if s.get("when")]
    assert gated and all(not s["when"]() for s in gated)
    month_close._PENDING = {"stage": "fix_query"}
    assert all(s["when"]() for s in gated)
    assert skills.match("đồng ý")["handler"] is month_close.handle_approve
