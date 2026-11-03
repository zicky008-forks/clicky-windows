"""Month close from one command: source files → Power Query → reconcile with the ledger → fix → report.

Excel and Power Query are faked: the fake query engine applies the same rules Power
Query would (machine date format when no culture is given, keyed Table.Distinct),
so the test proves Clicky finds the gap, names the cause and verifies the fix.
"""
import csv
from pathlib import Path
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
        self.styles, self.layouts = [], []

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
        m = re.match(r"^'?(.*?)'?!([A-Z]+)(\d+)$", ref)
        name, col, start = m.group(1).replace("''", "'"), m.group(2), int(m.group(3))
        if col == "A":                              # a block from column A replaces the sheet from that row down
            rows = self.sheets.get(name, []) if start > 1 else []
            rows = rows + [[] for _ in range(start - 1 - len(rows))]
            self.sheets[name] = rows[:start - 1] + [list(r) for r in grid]
            return True
        c0 = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(col))) - 1
        rows = self.sheets.setdefault(name, [])
        for i, r in enumerate(grid):
            while len(rows) <= start - 1 + i:
                rows.append([])
            row = rows[start - 1 + i]
            row += [""] * (c0 + len(r) - len(row))
            row[c0:c0 + len(r)] = list(r)
        return True

    def format_numbers(self, ref, number_format=None):
        self.formats.append(ref)

    def style(self, ref, **opts):
        self.styles.append((ref, opts))

    def layout_sheet(self, name, widths, gridlines=True):
        self.layouts.append((name, widths, gridlines))

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


def _begin(bridge, pq, order):
    """The run as the user drives it: the receipt first, then "Đúng rồi, làm tiếp"."""
    receipt = month_close.start(bridge, pq, order)
    if not month_close.intake_pending():
        return receipt
    assert receipt[-1].choices == month_close.INTAKE_CHOICES and receipt[-1].intake
    return month_close.confirm(bridge, pq)


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
    assert 'Table.SelectRows(GopFile, each [Ngay] <> null)' in m          # a "Tổng cộng" footer has no date
    assert 'Table.Distinct(BoDongKhongNgay, {"SoCT"})' in m and "Folder.Files(ThuMuc)" in m
    assert m_code.lint("DonHang", m) == []
    assert "IsParameterQuery = true" in m_code.parameter_query("C:\\T10")


# ── the whole flow ────────────────────────────────────────────────────────────

def test_start_finds_the_gap_and_names_both_causes(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    beats = _begin(bridge, pq, sources.DAY_FIRST)
    said = _text(beats)
    # One short idea per sentence, no step or file names spoken.
    assert all(len(b.text.split()) <= 16 for b in beats), [b.text for b in beats]
    assert "Bước" not in said and "M_do_AI_viet" not in said and ":\\" not in said
    assert beats[0].text == "File gốc và sổ cái khớp nhau: doanh thu 8,4 tỷ."
    assert beats[0].ref == "'Doi chieu'!C5:C6" and beats[0].mark == "ok" and beats[0].value == "8,4 tỷ"
    assert "6 chứng từ trong 2 file · đã bỏ 1 chứng từ lặp" in beats[0].note
    gap = beats[1]
    assert gap.text == "Báo cáo trong Excel đang thiếu 4,2 tỷ so với sổ cái."
    assert gap.ref == "'Doi chieu'!C7" and gap.mark == "bad" and gap.value == "−4,2 tỷ" and "3/6 chứng từ" in gap.note
    date, dedupe = beats[2], beats[3]
    assert date.text == "Nguyên nhân chính: máy đọc nhầm ngày tháng, làm mất 2,8 tỷ."
    assert date.value == "05/10 → 10/05" and "ngày 5/10 bị đọc thành ngày 10/5" in date.note
    assert date.ref == "'Doi chieu'!A10:E10" and dedupe.ref == "'Doi chieu'!A11:E11"
    assert dedupe.text == "Thêm 1 chứng từ bị xoá nhầm ở bước xoá trùng." and dedupe.value == "1,4 tỷ"
    offer = beats[-1]
    assert offer.choices == ("Sửa giúp tôi", "Để tôi xem") and "Bản cũ vẫn được giữ" in offer.text
    assert f'Folder.Files("{folder / "DonHang"}")' in pq.queries["DonHang"]
    check = bridge.sheets["Doi chieu"]
    assert check[3][1:3] == ["=COUNT('DonHang'!G:G)", "=SUM('DonHang'!G:G)"]
    assert check[4][1:4] == [6, Decimal(84 * BN // 10), Decimal(58 * BN // 10)]
    assert check[5][2] == Decimal(8400 * tb.M) and check[6][2] == "=C4-C6"
    # The full story lives in the sheet: causes on the rows the cards point at, then the query's risky steps.
    assert check[8][0] == "Chi tiết kiểm tra"
    assert check[9][:3] == ["Đọc nhầm ngày tháng", 2, Decimal(28 * BN // 10)]
    assert check[10][:3] == ["Xoá trùng nhầm", 1, Decimal(14 * BN // 10)]
    steps = [r[0] for r in check[11:]]
    assert steps.index("Bước query: Changed Type") < steps.index("Bước query: Removed Duplicates") < steps.index("Bước query: Removed Errors")
    assert any("máy này không có" in r[4] for r in check[11:])
    assert ("'Doi chieu'!A7:D7", {"color": "#B42318"}) in bridge.styles      # the gap row reads red
    assert bridge.layouts and bridge.layouts[0][2] is False                    # widths set, gridlines off
    assert month_close.pending()


def test_approve_fixes_verifies_then_builds_the_report(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    beats = month_close.approve(bridge, pq)
    said = _text(beats)
    assert month_close.stage() == "save"                      # only the save question is left
    done = beats[0]
    assert done.text == "Đã sửa xong. Excel khớp sổ cái đến từng đồng." and done.mark == "ok" and done.value == "0 đ"
    assert done.note == "6 chứng từ · doanh thu 8,4 tỷ · giá vốn khớp TK 632"
    assert ("'Doi chieu'!A7:D7", {"color": "#157347"}) in bridge.styles
    assert bridge.sheets["Doi chieu"][9][0] == "Đã sửa query"
    assert pq.queries["ThuMucDonHang"].startswith(f'"{folder}"') or str(folder).replace("\\", "\\\\") in pq.queries["ThuMucDonHang"]
    assert "BC thang" in bridge.sheets and "TB_T10" in bridge.sheets
    profit = next(b for b in beats if "Lợi nhuận thực" in b.text)
    assert profit.text == "Lợi nhuận thực là 436 triệu, không phải 556 triệu như trên báo cáo."
    assert profit.value == "436 triệu" and "TK 521" in profit.note and profit.mark == "warn"
    assert profit.was == "556 triệu" and profit.roll == (556_000_000, 436_000_000)
    real = bridge.sheets["BC thang"][-1]
    assert real[0] == "Lợi nhuận sau thuế thực" and real[2].startswith("=C20+C21")
    assert bridge.sheets["BC thang"][-3][2].startswith("=-MAX(0,SUMIFS(")
    assert bridge.sheets["BC thang"][-2][:3] == ["Doanh thu thuần thực", "", "=C7+C21"]
    assert profit.ref == "'BC thang'!A20:G23"
    report_beat = next(b for b in beats if b.text.startswith("Em đã lập báo cáo tháng"))
    assert report_beat.ref.startswith("'BC thang'!A4:G") and "công thức trỏ về sổ cái" in report_beat.note
    assert "Lợi nhuận thực" in said
    assert beats[-1].choices == month_close.SAVE_CHOICES      # the file is never saved behind the user's back
    assert "Ctrl + S" in _text(month_close.reject()) and not month_close.pending()
    assert month_close.approve(bridge, pq) == []              # nothing left to approve


def test_report_is_built_from_the_files_even_when_excel_cannot_read_the_sheets_back(folder):
    """Run 4 on the real PC: the TB sheets were written, reading them back gave nothing, and the
    report step said "0 sheet đọc được". The rows already read from the files must be enough."""
    class BlindBridge(FakeBridge):
        def sheet_rows(self, name):
            return None

    bridge = BlindBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    beats = month_close.approve(bridge, pq)
    said = _text(beats)
    assert "sheet đọc được" not in said
    assert "BC thang" in bridge.sheets
    assert any("Lợi nhuận thực là 436 triệu" in b.text for b in beats)


def test_closed_gap_reads_zero_not_a_dash(folder):
    class Recording(FakeBridge):
        def format_numbers(self, ref, number_format=None):
            self.formats.append((ref, number_format))

    bridge = Recording(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    fmt = dict(f for f in bridge.formats if isinstance(f, tuple))
    assert fmt["'Doi chieu'!B4:D7"].endswith(";0")


def test_reject_keeps_the_query_and_warns(folder):
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    before = pq.queries["DonHang"]
    assert "đừng dùng" in _text(month_close.reject())
    assert pq.queries["DonHang"] == before and not month_close.pending()


def test_clean_query_goes_straight_to_the_report(folder):
    (folder / "M_do_AI_viet.txt").unlink()
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    said = _text(_begin(bridge, pq, sources.DAY_FIRST))
    assert "Excel khớp sổ cái đến từng đồng: doanh thu 8,4 tỷ." in said
    assert any("Clicky tạo query mới" in r[4] for r in bridge.sheets["Doi chieu"][9:])
    assert "Lợi nhuận thực là 436 triệu" in said and month_close.stage() == "save"


def test_query_that_excel_cannot_load_still_gets_the_fix_offered(folder):
    class BrokenPQ(FakePQ):
        def load(self, name):
            if "XoaTrungTheoSoCT" not in self.queries[name]:
                return "DataSource.Error: Could not find a part of the path."
            return super().load(name)
    bridge = FakeBridge(folder)
    pq = BrokenPQ(bridge, folder)
    said = _text(_begin(bridge, pq, sources.DAY_FIRST))
    assert "Excel chưa tải được query DonHang" in said and month_close.pending()
    assert "DataSource.Error" not in said and "không mở được thư mục" in bridge.sheets["Doi chieu"][9][4]
    assert bridge.sheets["Doi chieu"][3][4] == "Chưa đọc được bảng query"
    assert "khớp sổ cái đến từng đồng" in _text(month_close.approve(bridge, pq))


def test_table_clicky_cannot_read_back_shows_no_gap_and_offers_no_fix(folder):
    from office.power_query import ReadFailed

    class UnreadablePQ(FakePQ):
        def load(self, name):
            return ReadFailed("No module named 'win32timezone'")
    bridge = FakeBridge(folder)
    pq = UnreadablePQ(bridge, folder)
    said = _text(_begin(bridge, pq, sources.DAY_FIRST))
    assert "lỗi của Clicky" in said and "Excel chưa tải được" not in said
    assert not month_close.pending()
    gap_row = bridge.sheets["Doi chieu"][6]
    assert gap_row[2] == "" and gap_row[3] == "" and gap_row[4].startswith("Chưa đối chiếu được")


def test_each_beat_lights_its_range_and_puts_one_card_beside_it():
    beat = month_close.Beat("x", "'Doi chieu'!C7", "Excel lệch sổ cái", "bad", "−4,2 tỷ", "Query chỉ giữ 3/6 chứng từ.")
    spot, card = month_close.shapes_for((100, 50, 200, 70), beat)
    assert spot == {"kind": "spotlight", "x1": 100, "y1": 50, "x2": 200, "y2": 70, "tone": "bad"}
    assert card["kind"] == "card" and card["anchor"] == [100, 50, 200, 70] and card["tone"] == "bad"
    assert (card["title"], card["value"], card["note"]) == ("Excel lệch sổ cái", "−4,2 tỷ", "Query chỉ giữ 3/6 chứng từ.")
    plain = month_close.shapes_for((0, 0, 1, 1), month_close.Beat("x", "A1"))
    assert [s["kind"] for s in plain] == ["spotlight"] and plain[0]["tone"] == "info"


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
    assert skills.match("làm báo cáo từ thư mục này")["name"] == "Báo cáo tháng trọn gói"   # "tháng" left out
    assert start.search("đối chiếu báo cáo trong thư mục này")
    approve = re.compile(month_close.APPROVE_TRIGGER, re.I)
    assert approve.search("đồng ý") and approve.search("ok sửa đi") and approve.search("Sửa giúp tôi")
    assert not approve.search("được không em, sao doanh thu tháng này lại giảm mạnh vậy")
    reject = re.compile(month_close.REJECT_TRIGGER, re.I)
    assert reject.search("thôi") and reject.search("Để tôi xem")
    assert not reject.search("không hiểu sao lợi nhuận lại giảm nhiều thế")

    gated = [s for s in month_close.SKILLS if s.get("when")]
    assert gated and all(not s["when"]() for s in gated)
    month_close._PENDING = {"stage": "fix_query"}
    assert all(s["when"]() for s in gated if s["when"] is month_close.pending)
    assert skills.match("đồng ý")["handler"] is month_close.handle_approve
    month_close._PENDING = {"stage": "intake"}
    assert skills.match("Đúng rồi, làm tiếp")["handler"] is month_close.handle_confirm
    assert skills.match("Có chỗ sai")["handler"] is month_close.handle_wrong
    assert skills.match("chốt chính thức")["handler"] is month_close.handle_final
    month_close._PENDING = None
    assert skills.match("Đúng rồi, làm tiếp") is None


def test_machine_order_follows_the_region_culture_not_a_custom_short_date():
    # Tester PC: region en-US, short date customised to dd/MM/yyyy; Power Query still read dates month-first.
    assert month_close.order_from_pattern("M/d/yyyy") == sources.MONTH_FIRST
    assert month_close.order_from_pattern("dd/MM/yyyy") == sources.DAY_FIRST
    assert month_close.order_from_pattern("yyyy-MM-dd") is None
    assert month_close.order_from_culture("en-US") == sources.MONTH_FIRST
    assert month_close.order_from_culture("vi-VN") == sources.DAY_FIRST
    assert month_close.machine_date_order() in (None, sources.DAY_FIRST, sources.MONTH_FIRST)


def test_play_shows_one_idea_at_a_time_then_offers_the_buttons(monkeypatch):
    import asyncio
    from ui.card_layout import NO_BUBBLE
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)

    class Sig:
        def __init__(self, log, name):
            self.log, self.name = log, name

        def emit(self, *a):
            self.log.append((self.name, a))

    class Excel:
        def activate(self, sheet):
            pass

        def screen_rect(self, ref, reveal):
            return (100.0, 50.0, 200.0, 70.0)

    class Manager:
        def __init__(self):
            self.log = []
            self._excel = Excel()
            for n in ("sig_clear_drawings", "sig_response_chunk", "sig_draw", "sig_point_hold",
                      "sig_point_at", "sig_point_release", "sig_choices", "sig_guided"):
                setattr(self, n, Sig(self.log, n))

        def _physical_to_logical_bbox(self, rect):
            return rect

        async def _reply_local(self, text):
            self.log.append(("say", (text,)))
            return True

    m = Manager()
    beats = [month_close.Beat("một", "'Doi chieu'!C7", "Lệch", "bad", "−1 tỷ"),
             month_close.Beat("hai", "'Doi chieu'!A10:E10", "Nguyên nhân", "bad", "05/10 → 10/05"),
             month_close.Beat("Em sửa giúp anh nhé?", choices=("Sửa giúp tôi", "Để tôi xem"))]
    asyncio.run(month_close.play(m, beats))
    names = [n for n, _ in m.log]
    assert ("sig_response_chunk", ("\n\n",)) in m.log
    # Each beat with a range starts from a clean screen, then lights it, then points, then speaks.
    first_say, second_say = [i for i, n in enumerate(names) if n == "say"][:2]
    between = names[first_say:second_say]
    assert between.count("sig_clear_drawings") == 1 and between.count("sig_draw") == 2
    assert all(a[2] == NO_BUBBLE for n, a in m.log if n == "sig_point_at")
    assert names.index("sig_draw") < names.index("sig_point_at") < names.index("say")
    # The question beat keeps the last card up; its buttons come with the question, not after it is read.
    ask = names.index("sig_choices")
    assert m.log[ask][1] == ("Em sửa giúp anh nhé?", ["Sửa giúp tôi", "Để tôi xem"])
    assert names[ask + 1] == "say" and m.log[ask + 1][1] == ("Em sửa giúp anh nhé?",)
    assert "sig_clear_drawings" not in names[ask:]
    # The chat panel steps aside for the whole walk-through and stays away while the buttons are up.
    assert names[0] == "sig_guided" and m.log[0][1] == (True,)
    assert ("sig_guided", (False,)) not in m.log


def test_fix_runs_behind_a_still_screen_and_ends_on_the_profit_card(folder):
    class Recording(FakeBridge):
        def __init__(self, f):
            super().__init__(f)
            self.screen = []

        def screen_updating(self, on):
            self.screen.append(on)

    bridge = Recording(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    beats = month_close.approve(bridge, pq)
    assert bridge.screen == [False, True]                    # frozen while sheets are added, released after
    assert beats[-2].text.startswith("Lợi nhuận thực là")    # the warning is the last scene before the save line
    assert any(name == "BC thang" and widths["A"] >= 30 for name, widths, _ in bridge.layouts)
    assert ("'BC thang'!A4:I4", ) == tuple(r for r, o in bridge.styles if r == "'BC thang'!A4:I4")[:1]


def test_changing_figures_roll_and_the_old_profit_is_shown_struck():
    from office.month_close import Beat, shapes_for
    beat = Beat("Lợi nhuận thực là 436 triệu.", "'BC thang'!C12", "Lợi nhuận sau thuế thực", "warn", "436 triệu",
                "note", was="556 triệu", roll=(556_000_000, 436_000_000))
    card = shapes_for((10, 10, 100, 30), beat)[1]
    assert card["was"] == "556 triệu" and card["roll"] == [556_000_000, 436_000_000]


# ── Blind-case findings: ";" exports and a reload that fails ─────────────────

def test_separator_is_judged_on_the_header_not_on_decimal_commas():
    assert sources.sniff_delimiter("Ngay;SoCT;ThanhTien\n05/10/2026;HN1;1234,5\n06/10/2026;HN2;99,25,1\n") == ";"
    assert sources.sniff_delimiter("Ngay,SoCT,ThanhTien\n05/10/2026,HN1,1234\n") == ","
    assert sources.sniff_delimiter("Ngay\tSoCT\tThanhTien\n") == "\t"


def test_fixed_query_reads_each_file_with_its_own_separator(folder):
    """Blind case: the export used ";" (UTF-8 with BOM) and the repaired query read it with ",",
    so the reload failed with "column ... wasn't found"."""
    path = folder / "DonHang" / "DonHang_HCM_T10.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(HEADER)
        for doc, d, kh, cost in HCM:
            w.writerow([doc, d, kh, "Sữa", 1000, 1_400_000, 1_400_000_000, cost, "HCM"])
    files = {f.name: f for f in sources.find_order_files(folder)}
    assert files["DonHang_HCM_T10.csv"].delimiter == ";" and files["DonHang_HN_T10.csv"].delimiter == ","
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    fixed = month_close._PENDING["formula"]
    assert 'if [Name] = "DonHang_HCM_T10.csv" then ";"' in fixed and "[Delimiter = [PhanCach]" in fixed
    assert "khớp sổ cái đến từng đồng" in _text(month_close.approve(bridge, pq))


def test_one_separator_for_all_files_is_written_as_a_literal():
    from engine import m_code
    m = m_code.folder_query("C:\\x", {"a.csv": "vi-VN"}, [("Ngay", "type date")], delimiter={"a.csv": ";", "b.csv": ";"})
    assert '[Delimiter = ";"' in m and "PhanCach" not in m
    assert '[Delimiter = "#(tab)"' in m_code.folder_query("C:\\x", {}, [], delimiter="\t")


def test_failed_reload_puts_the_old_query_back_and_says_so_in_vietnamese(folder):
    class ReloadFails(FakePQ):
        def load(self, name):
            if "XoaTrungTheoSoCT" in self.queries[name]:
                return ("(-2147352567, 'Exception occurred.', (0, None, \"[Expression.Error] The column 'Ngay' "
                        "of the table wasn't found.\", None, 0, -2146827284), None)")
            return super().load(name)
    bridge = FakeBridge(folder)
    pq = ReloadFails(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    before = pq.queries["DonHang"]
    beats = month_close.approve(bridge, pq)
    shown = " ".join(f"{b.text} {b.label} {b.value} {b.note}" for b in beats)
    assert "Exception" not in shown and "Expression.Error" not in shown and "-2147352567" not in shown
    assert beats[0].text == "Em chưa sửa được query DonHang. Query cũ vẫn giữ nguyên. Chưa lập báo cáo."
    assert beats[0].mark == "warn" and beats[0].note == "Excel không tìm thấy cột Ngay trong file đơn hàng."
    assert beats[0].ref == "'Doi chieu'!A10:E10" and bridge.sheets["Doi chieu"][9][0] == "Chưa sửa được query"
    assert pq.queries["DonHang"] == before and not month_close.pending()
    assert "BC thang" not in bridge.sheets


def test_load_errors_become_one_plain_sentence():
    vi = month_close.load_error_vi
    assert vi("DataFormat.Error: We couldn't convert to Number.") == "Excel gặp một giá trị không đổi được sang số hoặc ngày."
    assert vi("Formula.Firewall: Query 'X' references other queries") .startswith("Excel chặn query")
    assert vi("something else entirely") == "Excel báo lỗi khi tải query."


# ── Beta jobs 1, 2, 4: the receipt, nothing left out silently, the Biên bản ──

class CopyingBridge(FakeBridge):
    """Knows where the workbook is saved, and SaveCopyAs writes a file there."""
    def __init__(self, folder, name="BaoCao_T10.xlsx"):
        super().__init__(folder)
        self.path, self.copies, self.writes = folder / name, [], []
        self.path.write_bytes(b"workbook")

    def workbook_path(self):
        return str(self.path)

    def save_copy(self, path):
        self.copies.append(path)
        Path(path).write_bytes(self.path.read_bytes())
        return True

    def write(self, ref, grid):
        self.writes.append(ref)
        return super().write(ref, grid)


def _with_footer_and_stray_files(folder):
    with open(folder / "DonHang" / "DonHang_HN_T10.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["", "", "", "Tổng cộng", "", "", 2_800_000_000, "", ""])
    (folder / "anh_chup_so.png").write_bytes(b"png")
    (folder / "so_cu.xls").write_bytes(b"xls")


def test_receipt_comes_first_and_nothing_is_written_before_it_is_confirmed(folder):
    _with_footer_and_stray_files(folder)
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    beats = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert len(beats) == 1 and beats[0].choices == ("Đúng rồi, làm tiếp", "Có chỗ sai")
    assert bridge.writes == [] and bridge.copies == [] and pq.queries == {}          # not one cell touched
    card = beats[0].intake["card"]
    titles = [r["title"] for r in card["rows"]]
    assert titles[0] == "TB_T10 · Sổ cân đối T10" and titles[1] == "TB_T09 · Sổ cân đối T09"
    assert titles[2].startswith("Bán hàng") and card["rows"][2]["count"] == "6 đơn"
    assert "khớp Nợ = Có" in card["rows"][0]["detail"] and "Kỳ 01/10/2026–31/10/2026" in card["rows"][0]["detail"]
    assert "khớp Có TK 511" in card["rows"][2]["detail"]
    left = " ".join(card["left_out"])
    assert "Tổng cộng" in left and "xuất 2 lần" in left and "file không dùng" in left
    log = beats[0].intake["log"]
    assert len(log["files"]) == 5                       # 2 trial balances, 2 sales files, the query written by an AI
    for f in log["files"]:
        assert abs(f["total"] - f["used"] - f["dropped"]) <= 1, f     # used + left out = what the file holds
    listed = sum(d["amount"] for d in log["dropped"] if d["amount"] is not None)
    assert listed == sum(f["dropped"] for f in log["files"]) == 1_400_000_000
    assert {d["file"] for d in log["dropped"] if d["amount"] is None} >= {"DonHang_HN_T10.csv", "anh_chup_so.png", "so_cu.xls"}


def test_wrong_stops_before_any_change_and_opens_the_chat(folder):
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close.start(bridge, pq, sources.DAY_FIRST)
    assert "chưa sửa gì" in _text(month_close.reject())
    assert not month_close.pending() and bridge.writes == [] and bridge.copies == []


def test_confirm_keeps_the_original_and_writes_the_bien_ban(folder):
    _with_footer_and_stray_files(folder)
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close.start(bridge, pq, sources.DAY_FIRST)
    month_close.confirm(bridge, pq)
    assert (folder / "BaoCao_T10_ban-goc.xlsx").read_bytes() == b"workbook"
    month_close.approve(bridge, pq)
    rows = bridge.sheets["Bien ban Clicky"]
    lines = [" ".join(str(c) for c in r if c not in (None, "")) for r in rows]
    assert rows[0][0] == "BIÊN BẢN CLICKY · BÁO CÁO THÁNG 10/2026" and "BaoCao_T10_ban-goc.xlsx" in rows[1][0]
    assert rows[3] == ["#", "Lúc", "Ô", "Chỉ tiêu", "Trước", "Sau", "Lý do", "Ai đồng ý", "Nguồn"]
    assert any("5111" in ln and "521" in ln and "120" in ln for ln in lines)
    assert any("Doanh thu thuần thực 8640000000 8520000000" in ln for ln in lines)
    assert any("Lợi nhuận sau thuế thực 556000000 436000000" in ln for ln in lines)
    query = next(r for r in rows if len(r) > 2 and r[2] == "Query DonHang")
    assert query[4] != query[5] and query[7] != "Tự động"                   # the user agreed to this one
    status = next(r for r in rows if r and r[0] == "Trạng thái")
    assert status[1] == "SƠ BỘ" and bridge.sheets["BC thang"][0][8] == "SƠ BỘ"
    assert any(r and r[0] == "Những gì em bỏ ra ngoài" for r in rows)
    assert any(r and r[0] == "Dòng \"Tổng cộng\" cuối file" for r in rows)
    cover = [r for r in rows if r and str(r[-1]).startswith("✓")]
    assert len(cover) == 5


def test_only_the_user_makes_it_official(folder):
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    month_close.approve(bridge, pq)
    assert next(r for r in bridge.sheets["Bien ban Clicky"] if r and r[0] == "Trạng thái")[1] == "SƠ BỘ"
    said = _text(month_close.make_final(bridge))
    assert "Chính thức" in said
    rows = bridge.sheets["Bien ban Clicky"]
    assert next(r for r in rows if r and r[0] == "Trạng thái")[1] == "CHÍNH THỨC"
    assert any(r[3] == "Trạng thái báo cáo" and r[5] == "CHÍNH THỨC" for r in rows if len(r) > 5)
    assert bridge.sheets["BC thang"][0][8] == "CHÍNH THỨC"


def test_making_it_official_after_a_restart_only_changes_the_status_line(folder):
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    month_close.approve(bridge, pq)
    month_close._AUDIT = None                                   # Clicky was closed and opened again
    month_close.make_final(bridge)
    status = next(r for r in bridge.sheets["Bien ban Clicky"] if r and r[0] == "Trạng thái")
    assert status[1] == "CHÍNH THỨC" and "Chính thức" in status[3]


# ── Beta job 3: open the saved file again and compare before handing it over ──

def _saved_workbook(path, bridge, expect, tweak=None):
    """Write what the fake workbook holds to a real xlsx, as Excel would save it (values cached)."""
    from openpyxl import Workbook
    from engine import saved_check
    wb = Workbook()
    wb.remove(wb.active)
    for ref, value in expect.items():
        sheet, cell = saved_check.split(ref)
        ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
        ws[cell] = value + (tweak.get(ref, 0) if tweak else 0)
    wb.save(path)


class SavingBridge(CopyingBridge):
    def __init__(self, folder, tweak=None):
        super().__init__(folder)
        self.tweak, self.saved = tweak, 0

    def save(self):
        self.saved += 1
        _saved_workbook(self.path, self, month_close._EXPECT, self.tweak)
        return True


def _to_the_save_question(bridge, folder):
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    month_close.approve(bridge, pq)
    assert month_close.stage() == "save"
    return pq


def test_saved_file_is_read_back_and_only_then_called_matching(folder):
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)
    assert {"'Doi chieu'!C7", "'BC thang'!C23"} <= set(month_close._EXPECT)
    assert month_close._EXPECT["'BC thang'!C23"] == 436_000_000
    beats = month_close.approve(bridge, pq)                       # "Lưu và kiểm tra"
    assert bridge.saved == 1
    assert beats[0].label == "Khớp sổ cái" and beats[0].value == "0 đ" and beats[0].mark == "ok"


def test_a_cell_changed_in_the_saved_file_is_caught_and_the_file_is_not_handed_over(folder):
    bridge = SavingBridge(folder, tweak={"'BC thang'!C23": 5_000_000})
    pq = _to_the_save_question(bridge, folder)
    beats = month_close.approve(bridge, pq)
    said = " ".join(f"{b.text} {b.label} {b.note}" for b in beats)
    assert "Khớp sổ cái" not in said and beats[0].mark == "bad"
    assert beats[0].ref == "'BC thang'!C23" and "cần 436 triệu, file ghi 441 triệu" in beats[0].note
    assert "chưa giao" in beats[0].text


def test_tamper_hook_changes_the_saved_cell_only_when_asked(folder, monkeypatch):
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)
    monkeypatch.setenv("CLICKY_TEST_TAMPER", "'Doi chieu'!C7=1000")
    beats = month_close.approve(bridge, pq)
    assert beats[0].mark == "bad" and beats[0].ref == "'Doi chieu'!C7"


# ── Beta job 5: each input has a role; two of the same kind → ask, never pick ──

def test_two_trial_balances_of_one_month_are_asked_about_not_picked(folder):
    import os, shutil, time as _time
    from engine import roles
    old = folder / "TB_T10_cu.xlsx"
    shutil.copy(folder / "TB_T10.xlsx", old)
    os.utime(old, (_time.time() - 86400 * 6, _time.time() - 86400 * 6))
    (folder / "anh_so_lieu.png").write_bytes(b"png")
    (folder / "Email chi nhanh DN.eml").write_text("Doanh thu tháng 11 dự kiến 9.999.999.999", encoding="utf-8")
    bridge = CopyingBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close._CHOSEN.clear(); month_close._PICKS.clear()
    beats = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert month_close.stage() == "pick_source" and bridge.writes == []
    ask = beats[0]
    assert ask.text.startswith("Có 2 sổ cân đối T10: TB_T10.xlsx sửa ngày") and "TB_T10_cu.xlsx sửa ngày" in ask.text
    assert ask.choices == ("Dùng TB_T10", "Dùng TB_T10_cu")
    receipt = month_close.pick(bridge, pq, "Dùng TB_T10_cu")          # the user's choice, even the older one
    assert month_close.intake_pending()
    log = receipt[0].intake["log"]
    assert any(d["file"] == "TB_T10" and "anh đã chọn dùng TB_T10_cu" in d["reason"] for d in log["dropped"])
    reasons = {d["file"]: d["reason"] for d in log["dropped"]}
    assert "không bao giờ lấy số từ ảnh" in reasons["anh_so_lieu.png"]
    assert "Lý do" in reasons["Email chi nhanh DN.eml"]
    assert roles.of_file("KeHoach_2026_v3.xlsx") == roles.PLAN
    month_close.confirm(bridge, pq)
    month_close.approve(bridge, pq)
    rows = bridge.sheets["Bien ban Clicky"]
    pickrow = next(r for r in rows if len(r) > 3 and str(r[3]).startswith("Chọn nguồn"))
    assert pickrow[5].startswith("TB_T10_cu") and pickrow[4] == "TB_T10" and pickrow[7] != "Tự động"
    every = [c for r in bridge.sheets["BC thang"] for c in r]
    assert 9_999_999_999 not in every


def test_receipt_rows_carry_their_role(folder):
    bridge = CopyingBridge(folder)
    month_close._CHOSEN.clear()
    beats = month_close.start(bridge, FakePQ(bridge, folder), sources.DAY_FIRST)
    roles_shown = [r.get("role") for r in beats[0].intake["card"]["rows"]]
    assert roles_shown[:3] == ["Số chính thức", "Số chính thức", "Chi tiết để giải thích"]
