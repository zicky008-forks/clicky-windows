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
    assert 'Table.SelectRows(GopFile, each try [Ngay] <> null otherwise true)' in m     # an unconvertible date stays, counted
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


def test_play_shows_one_idea_at_a_time_then_offers_the_buttons(monkeypatch, tmp_path):
    import asyncio, json
    scene = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(scene))
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
    # The pointer leaves as soon as the range is found; the frame and card are drawn while it flies.
    assert names.index("sig_point_at") < names.index("sig_draw") < names.index("say")
    # The question beat keeps the last card up; its buttons come with the question, not after it is read.
    ask = names.index("sig_choices")
    assert m.log[ask][1] == ("Em sửa giúp anh nhé?", ["Sửa giúp tôi", "Để tôi xem"])
    assert names[ask + 1] == "say" and m.log[ask + 1][1] == ("Em sửa giúp anh nhé?",)
    assert "sig_clear_drawings" not in names[ask:]
    # The chat panel steps aside for the whole walk-through and stays away while the buttons are up.
    assert names[0] == "sig_guided" and m.log[0][1] == (True,)
    assert ("sig_guided", (False,)) not in m.log
    # The robot waits for the end of the story, not for a quiet spell: a card read without a voice sits
    # still for ten seconds while scenes are still to come.
    events = [json.loads(l) for l in scene.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "story_end" and events[-1]["beats"] == 3
    assert events[-1]["choices"] == ["Sửa giúp tôi", "Để tôi xem"]
    assert [e["i"] for e in events if e["event"] == "spoken"] == [0, 1, 2]


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
    assert bridge.screen == [False, True]                    # the check after "Đúng rồi, làm tiếp" runs frozen too
    beats = month_close.approve(bridge, pq)
    assert bridge.screen == [False, True] * 2                # frozen while sheets are added, released after
    # The warning, then how 556 becomes 436, then what to check before the boss sees it, then the save line.
    assert beats[-4].text.startswith("Lợi nhuận thực là")
    assert beats[-3].graphic["kind"] == "bridge" and beats[-2].graphic["kind"] == "notes"
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
    assert titles[0] == "TB_T10.xlsx · Sổ cân đối T10" and titles[1] == "TB_T09.xlsx · Sổ cân đối T09"
    assert titles[2].startswith("Bán hàng") and card["rows"][2]["count"] == "6 đơn"
    assert "khớp Nợ = Có" in card["rows"][0]["detail"] and "Kỳ 01/10/2026–31/10/2026" in card["rows"][0]["detail"]
    assert "khớp Có TK 511" in card["rows"][2]["detail"]
    left = " ".join(card["left_out"])
    assert "Tổng cộng" in left and "trùng" in left and "file không dùng" in left
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


def _lock_like_excel(monkeypatch, path):
    """Excel holds the saved workbook open: on Windows any write to it fails with Errno 13."""
    from openpyxl.workbook.workbook import Workbook
    real = Workbook.save

    def save(self, filename):
        if Path(filename).resolve() == Path(path).resolve():
            raise PermissionError(13, "Permission denied", str(filename))
        return real(self, filename)
    monkeypatch.setattr(Workbook, "save", save)


def test_tamper_hook_works_while_excel_holds_the_saved_file(folder, monkeypatch):
    # PC run 37265be: the hook rewrote the open workbook, got Errno 13, and the card said only
    # "không đọc lại được"; it must name the cell and the gap, and leave the saved file as it was.
    import hashlib
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)
    real_save = bridge.save

    def save_then_lock():
        done = real_save()
        _lock_like_excel(monkeypatch, bridge.path)
        bridge.digest = hashlib.sha256(Path(bridge.path).read_bytes()).hexdigest()
        return done
    bridge.save = save_then_lock
    monkeypatch.setenv("CLICKY_TEST_TAMPER", "'BC thang'!C23=5000000")
    beats = month_close.approve(bridge, pq)
    assert beats[0].mark == "bad" and beats[0].ref == "'BC thang'!C23"
    assert "cần 436 triệu, file ghi 441 triệu" in beats[0].note and beats[0].value.endswith("5 triệu")
    assert hashlib.sha256(Path(bridge.path).read_bytes()).hexdigest() == bridge.digest


def test_a_saved_file_that_cannot_be_read_back_is_never_called_matching(folder, monkeypatch):
    from engine import saved_check
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)

    def locked(path, expect):
        raise PermissionError(13, "Permission denied", str(path))
    monkeypatch.setattr(saved_check, "check", locked)
    beats = month_close.approve(bridge, pq)
    said = " ".join(f"{b.text} {b.label} {b.note}" for b in beats)
    assert "Khớp sổ cái" not in said and "không đọc lại được" in said and beats[0].mark == "bad"


def test_a_failed_re_read_logs_no_path(folder, monkeypatch, caplog):
    # PC run 37265be: clicky.log carried "Permission denied: '...\\Thu_Clicky_Case01_beta\\BaoCao….xlsx'".
    import logging
    from engine import saved_check
    caplog.set_level(logging.DEBUG)
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)

    def locked(path, expect):
        raise PermissionError(13, "Permission denied", "C:\\Users\\chi\\AgriS\\BCTC_Q3.xlsx")
    monkeypatch.setattr(saved_check, "check", locked)
    month_close.approve(bridge, pq)
    assert "AgriS" not in caplog.text and "BCTC" not in caplog.text
    assert "re-reading the saved workbook failed: PermissionError 0x0000000D" in caplog.text


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
    # Grader G1: people see the file's own name, extension included, on the receipt and in the Biên bản.
    assert any(d["file"] == "TB_T10.xlsx" and "chị đã chọn dùng TB_T10_cu.xlsx" in d["reason"] for d in log["dropped"])
    assert {f["name"] for f in log["files"] if f["kind"].startswith("Sổ cân đối")} == {"TB_T10_cu.xlsx", "TB_T09.xlsx"}
    assert not [n for n in receipt[0].intake["card"]["rows"] if n["title"].split(" · ")[0] in ("TB_T10", "TB_T10_cu")]
    reasons = {d["file"]: d["reason"] for d in log["dropped"]}
    assert "không bao giờ lấy số từ ảnh" in reasons["anh_so_lieu.png"]
    assert "Lý do" in reasons["Email chi nhanh DN.eml"]
    assert roles.of_file("KeHoach_2026_v3.xlsx") == roles.PLAN
    month_close.confirm(bridge, pq)
    month_close.approve(bridge, pq)
    rows = bridge.sheets["Bien ban Clicky"]
    pickrow = next(r for r in rows if len(r) > 3 and str(r[3]).startswith("Chọn nguồn"))
    assert pickrow[5].startswith("TB_T10_cu.xlsx (sửa") and pickrow[4] == "TB_T10.xlsx" and pickrow[7] != "Tự động"
    # Every row naming the file says TB_T10.xlsx; only "Thêm sheet" lists sheets, which keep the short name.
    left_out = [r for r in rows if "TB_T10" in " ".join(str(c) for c in r) and "Thêm sheet" not in r]
    bad = [r for r in left_out if not re.search(r"TB_T10(_cu)?\.xlsx", " ".join(str(c) for c in r))]
    assert left_out and not bad, bad
    every = [c for r in bridge.sheets["BC thang"] for c in r]
    assert 9_999_999_999 not in every


def test_receipt_rows_carry_their_role(folder):
    bridge = CopyingBridge(folder)
    month_close._CHOSEN.clear()
    beats = month_close.start(bridge, FakePQ(bridge, folder), sources.DAY_FIRST)
    roles_shown = [r.get("role") for r in beats[0].intake["card"]["rows"]]
    assert roles_shown[:3] == ["Số chính thức", "Số chính thức", "Chi tiết để giải thích"]


def test_revenue_scope_follows_what_the_order_files_cover():
    """Case 07 class: a sales file with goods and finished products must be compared with all of 511,
    not 5111 alone (that reported a false gap equal to 5112); goods-only files still use 5111."""
    from types import SimpleNamespace as L
    ledger = L(leaves=[L(account="5111", cr=Decimal(700), dr=0), L(account="5112", cr=Decimal(300), dr=0)])
    assert sources.revenue_scope(ledger, Decimal(1000)) == "511"
    assert sources.revenue_scope(ledger, Decimal(700)) == "5111"
    # Neither matches: the closer scope, so a real 100 gap is not reported as 300 + 100.
    assert sources.revenue_scope(ledger, Decimal(900)) == "511"
    assert sources.revenue_scope(ledger, Decimal(650)) == "5111"


def test_plan_versions_are_not_duplicates_and_never_official(tmp_path):
    """AOP and AOPR1 side by side are versions used together: no 'which one?' question, and a plan
    laid out like a trial balance is never read as Số chính thức. Only the same scenario twice is a copy."""
    from engine import roles
    assert roles.plan_scenario("AOP_FY2627_v6.xlsx") == "AOP"
    assert roles.plan_scenario("AOPR1 FY26-27.xlsx") == "AOPR1"
    assert roles.plan_scenario("TB_T10.xlsx") == ""
    assert roles.is_plan("AOPR1 FY26-27.xlsx") and roles.is_plan("MUF_T10.xlsx")
    assert not roles.is_plan("TB_T10.xlsx") and not roles.is_plan("DonHang_HN_T10.csv")
    _write_tb(tmp_path / "AOP_FY2627.xlsx", [["Bảng cân đối số phát sinh tháng 10 năm 2026"], ["511", "Doanh thu", 0, 0, 0, 9]])
    from engine.cli import read_rows
    names = [n for n, _, _ in sources.find_trial_balances(tmp_path, read_rows)]
    assert "AOP_FY2627" not in names


def test_unread_cost_ledger_csv_is_named_truthfully(tmp_path):
    from engine import intake
    p = tmp_path / "SoChiTiet_ChiPhi_T10.csv"
    p.write_text("SoCT,Ngay,TK,ThanhTien\nPC1,05/10/2026,6421,1000\n", encoding="utf-8")
    why = intake._csv_why(p)
    assert "sổ chi tiết chi phí" in why and "không có cột ngày" not in why
    r = tmp_path / "SCT_T10.csv"
    r.write_text("Ngay_CT,So_CT,TK_No,TK_Co,So_tien_VND\n05/10/2026,PC1,6421,1111,1000\n", encoding="utf-8")
    assert "sổ chi tiết chi phí" in intake._csv_why(r)
    q = tmp_path / "ghi_chu.csv"
    q.write_text("a,b\n1,2\n", encoding="utf-8")
    assert "không có cột ngày" in intake._csv_why(q)
def test_cards_keep_off_each_block_of_figures_not_just_the_used_range():
    """The card is told where every block of figures is; without that read it keeps off the whole filled area."""
    import asyncio
    from office import month_close

    class Excel:
        def __init__(self, boxes):
            self.boxes = boxes

        def figure_boxes(self):
            return self.boxes

        def content_rect(self):
            return (0, 0, 800, 600)

    class M:
        def __init__(self, boxes):
            self._excel = Excel(boxes)

        def _physical_to_logical_bbox(self, rect):
            return tuple(v / 2 for v in rect)

    figures, words = asyncio.run(month_close._figures(M({"numbers": [(200, 100, 800, 600)],
                                                          "text": [(0, 100, 200, 600)]})))
    assert figures == [(100, 50, 400, 300)] and words == [(0, 50, 100, 300)]
    figures, words = asyncio.run(month_close._figures(M(None)))
    assert figures == [(0, 0, 400, 300)] and words == []
    beat = month_close.Beat("Lợi nhuận thật", "C9", label="LNST thực", value="436tr")
    card = month_close.shapes_for((10, 10, 100, 30), beat, figures, words)[1]
    assert card["avoid"] == [[0, 0, 400, 300]] and card["soft"] is None


def _timed_out(dialog=""):
    from office.power_query import LoadTimeout
    out = LoadTimeout("Power Query refresh did not finish in 180s")
    out.seconds, out.dialog = 180.0, dialog
    return out


def test_a_query_excel_does_not_finish_loading_stops_with_the_reason_and_no_fix_offer(folder, tmp_path, monkeypatch):
    """Excel stuck behind a credentials or privacy dialog: Clicky stops waiting, says which dialog, offers no
    rewritten query (it would not load either), builds no report, and logs every step it reached."""
    import json
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))

    class StuckPQ(FakePQ):
        def load(self, name):
            return _timed_out("Truy cập Nội dung Web")
    bridge = FakeBridge(folder)
    said = _text(_begin(bridge, StuckPQ(bridge, folder), sources.DAY_FIRST))
    assert "Excel chưa tải được query DonHang" in said and not month_close.pending()
    note = bridge.sheets["Doi chieu"][9][4]
    assert "Truy cập Nội dung Web" in note and "trả lời hoặc đóng hộp thoại" in note and "Power Query refresh" not in note
    assert "Báo cáo tháng đã lập" not in said
    steps = [(e["step"], e.get("state")) for e in map(json.loads, log.read_text(encoding="utf-8").splitlines())
             if e.get("event") == "close_step"]
    assert ("query_load", "start") in steps and ("query_load", "timeout") in steps
    assert [s for s, _ in steps].index("read_folder") < [s for s, _ in steps].index("query_load")


def test_load_errors_name_a_timeout_in_plain_words():
    with_dialog = month_close.load_error_vi(_timed_out("Privacy levels"))
    assert "Privacy levels" in with_dialog and "quyền riêng tư" not in with_dialog
    plain = month_close.load_error_vi(_timed_out())
    assert "180 giây" in plain and "gọi lại em" in plain and "Power Query refresh" not in plain


def test_a_save_excel_refuses_gives_a_clear_card_and_no_check(folder):
    # The bridge refuses to save a workbook with no file behind it (it would open Save As): the user
    # gets "chưa lưu được", never "khớp", and no delivery.
    bridge = SavingBridge(folder)
    pq = _to_the_save_question(bridge, folder)
    bridge.save = lambda: False
    beats = month_close.approve(bridge, pq)
    said = " ".join(f"{b.text} {b.label} {b.note}" for b in beats)
    assert beats[0].label == "Chưa lưu được" and beats[0].mark == "warn" and "Khớp sổ cái" not in said


def test_folder_query_combines_only_the_order_files_when_a_cost_ledger_csv_sits_beside_them(tmp_path):
    """Robot run 07/11 (bhc_e2e): Folder.Files also picked GL_ChiTiet_ChiPhi.csv, typing its missing Ngay_HD
    column failed the whole load. Each CSV must carry the order columns before its columns are typed."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import bhc_case
    bhc_case.make(tmp_path)
    order = sources.load_order_file(tmp_path / "Mastersale_T07-T10.csv")
    m = m_code.folder_query(str(tmp_path), {order.name: "vi-VN"}, month_close._typed_columns([order]),
                            order.header[order.columns["doc"]], folder_parameter="ThuMuc")
    keep = re.search(r'FileDonHang = Table\.SelectRows\(DocFile, each Table\.HasColumns\(\[Tho\], \{(.*?)\}\)\)', m)
    assert keep, m
    needed = re.findall(r'"((?:[^"]|"")*)"', keep.group(1))
    typed = [c for c, _ in month_close._typed_columns([order])]
    assert set(typed) <= set(needed) and m.index("FileDonHang") < m.index("Table.TransformColumnTypes(")
    assert "Table.Combine(DoiKieu[Bang])" in m and m_code.lint("DonHang", m) == []
    kept = []
    for p in sorted(tmp_path.glob("*.csv")):
        with open(p, encoding="utf-8-sig", newline="") as fh:
            head = next(csv.reader(fh))
        if set(needed) <= set(head):
            kept.append(p.name)
    assert kept == ["Mastersale_T07-T10.csv"]          # the cost ledger CSV is left out, not typed


@pytest.mark.parametrize("variant", ["bom_comma", "no_bom_semicolon", "spaced_header", "crlf_footer"])
def test_the_query_names_the_columns_exactly_as_power_query_reads_the_header(tmp_path, variant):
    """Power Query (Encoding 65001) drops a UTF-8 BOM, splits on the query's own delimiter and keeps header
    text as written. The query must ask for those exact names, or the load fails with 'column not found'."""
    sep = ";" if variant == "no_bom_semicolon" else ","
    head = ["Ngay HD ", "So HD", " Doanh thu"] if variant == "spaced_header" else ["Ngay_HD", "So_HD", "Doanh_thu"]
    rows = [["20/10/2026", "HD001", "1200000"], ["21/10/2026", "HD002", "3400000"]]
    if variant == "crlf_footer":
        rows.append(["", "Tổng", "4600000"])
    nl = "\r\n" if variant == "crlf_footer" else "\n"
    text = nl.join(sep.join(r) for r in [head] + rows) + nl
    raw = ("﻿" if variant in ("bom_comma", "crlf_footer", "spaced_header") else "") + text
    p = tmp_path / "Mastersale_T10.csv"
    p.write_bytes(raw.encode("utf-8"))
    (tmp_path / "GL_ChiTiet_ChiPhi.csv").write_bytes("﻿Ngay_CT,So_CT,So_tien_VND\n2026-10-01,PK1,5\n".encode("utf-8"))
    order = sources.load_order_file(p)
    assert order is not None and order.delimiter == sep
    m = m_code.folder_query(str(tmp_path), {order.name: order.culture(None)}, month_close._typed_columns([order]),
                            order.header[order.columns["doc"]], folder_parameter="ThuMuc",
                            delimiter={order.name: order.delimiter})
    assert f"[Delimiter = {m_code._m_sep(sep)}, Encoding = 65001]" in m
    keep = re.search(r"Table\.HasColumns\(\[Tho\], \{(.*?)\}\)\)", m)
    needed = [n.replace('""', '"') for n in re.findall(r'"((?:[^"]|"")*)"', keep.group(1))]
    seen_by_pq = p.read_bytes().decode("utf-8").lstrip("﻿").splitlines()[0].split(sep)
    assert needed and set(needed) <= set(seen_by_pq), (needed, seen_by_pq)
    gl_head = (tmp_path / "GL_ChiTiet_ChiPhi.csv").read_bytes().decode("utf-8").lstrip("﻿").splitlines()[0].split(",")
    assert not set(needed) <= set(gl_head)                     # the cost ledger is left out of the combine
    if variant == "crlf_footer":
        assert "each [Ngay HD ] <> null" not in m and "<> null" in m and len(order.footers) == 1


class _PlaySig:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def emit(self, *a):
        self.log.append((self.name, a))


class _PlayExcel:
    """Two workbooks: the pack (shown) and a source file Clicky has to bring forward to light a cell in it."""
    def __init__(self, on_screen=True):
        self.front, self.shown, self.on_screen = "pack", [], on_screen

    def show_workbook(self, path):
        self.shown.append(path)
        self.front = path
        return True

    def activate(self, sheet):
        pass

    def screen_rect(self, ref, reveal):
        if ref.startswith("Data!") and (self.front != "C:/case/KeHoach_AOPR1.xlsx" or not self.on_screen):
            return None                        # that cell is in the source file, not on the pack
        return (100.0, 50.0, 200.0, 70.0)


class _PlayManager:
    def __init__(self, excel):
        self.log, self._excel = [], excel
        for n in ("sig_clear_drawings", "sig_response_chunk", "sig_draw", "sig_point_hold",
                  "sig_point_at", "sig_point_release", "sig_choices", "sig_guided"):
            setattr(self, n, _PlaySig(self.log, n))

    def _physical_to_logical_bbox(self, rect):
        return rect

    async def _reply_local(self, text):
        self.log.append(("say", (text,)))
        return True


def _between_says(log):
    """The screen events of each beat: [[names before say 1], [between say 1 and 2], ...]."""
    out, cur = [], []
    for n, a in log:
        if n == "say":
            out.append(cur)
            cur = []
        else:
            cur.append((n, a))
    return out


def test_a_cell_in_another_file_is_lit_in_that_file_and_no_card_outlives_its_mistake(monkeypatch, tmp_path):
    """Grading round 8 (AA): a mistake in the AOPR file had no box; the previous mistake's card stayed up for
    14 s while the new one was read. Now the source file comes forward (read-only) and its cell is lit."""
    import asyncio
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(tmp_path / "scene.jsonl"))
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)
    excel = _PlayExcel()
    m = _PlayManager(excel)
    beats = [month_close.Beat("Có 2 chỗ cần sửa."),
             month_close.Beat("Tổng lệch sổ.", "'Tong hop'!B13", "Tổng chi phí lệch sổ", "bad", "10 đ", book="C:/case/pack.xlsx",
                              book_pack=True),
             month_close.Beat("Tháng 7 còn số AOP.", "Data!I174", "File AOPR: tháng khoá còn số kế hoạch", "bad", "5 đ",
                              note="Nhóm 5 · file KeHoach_AOPR1.xlsx, ô Data!I174", book="C:/case/KeHoach_AOPR1.xlsx"),
             month_close.Beat("Em đã ghi kết quả.", mark="ok")]
    asyncio.run(month_close.play(m, beats, keep_card=False))
    assert excel.shown == ["C:/case/pack.xlsx", "C:/case/KeHoach_AOPR1.xlsx"]
    import json
    from office.excel_com import book_id
    shows = [json.loads(l) for l in (tmp_path / "scene.jsonl").read_text(encoding="utf-8").splitlines() if '"show_book"' in l]
    assert [(e["book"], e["pack"]) for e in shows] == [(book_id("C:/case/pack.xlsx"), True),
                                                      (book_id("C:/case/KeHoach_AOPR1.xlsx"), False)]
    raw = (tmp_path / "scene.jsonl").read_text(encoding="utf-8")
    assert "KeHoach" not in raw and "pack.xlsx" not in raw          # the card says the name; the log never does
    assert f"[file {book_id('C:/case/KeHoach_AOPR1.xlsx')}]" in raw
    per = _between_says(m.log)
    third = [n for n, _ in per[2]]
    assert third.index("sig_clear_drawings") < third.index("sig_draw")          # old card gone, then the new frame
    spot = next(a[0] for n, a in per[2] if n == "sig_draw")
    assert spot["kind"] == "spotlight" and (spot["x1"], spot["y1"]) == (100.0, 50.0)
    assert "sig_clear_drawings" in [n for n, _ in per[3]]                        # the closing line keeps no card


def test_a_cell_that_cannot_be_shown_gets_its_own_card_without_a_pointer(monkeypatch, tmp_path):
    import asyncio
    import json
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)
    m = _PlayManager(_PlayExcel(on_screen=False))
    beats = [month_close.Beat("Tổng lệch sổ.", "'Tong hop'!B13", "Tổng chi phí lệch sổ", "bad", "10 đ"),
             month_close.Beat("Tháng 7 còn số AOP.", "Data!I174", "File AOPR: tháng khoá còn số kế hoạch", "bad", "5 đ",
                              was="7 đ", note="Nhóm 5 · file KeHoach_AOPR1.xlsx, ô Data!I174")]
    asyncio.run(month_close.play(m, beats))                  # even when a story keeps cards up, not this one
    second = _between_says(m.log)[1]
    names = [n for n, _ in second]
    assert names.index("sig_clear_drawings") < names.index("sig_draw") and "sig_point_at" not in names
    card = next(a[0] for n, a in second if n == "sig_draw")
    assert card["kind"] == "card" and card["free"] and card["value"] == "5 đ" and card["was"] == "7 đ"
    assert "KeHoach_AOPR1.xlsx" in card["note"] and "Data!I174" in card["note"]
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert {"event": "clear", "i": 1, "ref": "Data!I174", "free_card": True}.items() <= \
        next(e for e in events if e.get("event") == "clear").items()


def test_a_beat_without_a_range_still_keeps_the_card_in_a_story_that_wants_it(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(tmp_path / "scene.jsonl"))
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)
    m = _PlayManager(_PlayExcel())
    asyncio.run(month_close.play(m, [month_close.Beat("Lệch.", "'Doi chieu'!C7", "Lệch", "bad", "−1 tỷ"),
                                     month_close.Beat("Em sửa giúp anh nhé?", choices=("Sửa giúp tôi", "Để tôi xem"))]))
    assert "sig_clear_drawings" not in [n for n, _ in _between_says(m.log)[1]]


def test_confirm_logs_where_its_time_goes(folder, tmp_path, monkeypatch):
    # PC gate on 0b88545: the first "Đúng rồi, làm tiếp" after install took 11.8 s to its first card.
    # Each part of the step logs its own time, so the slow one on the PC is named, not guessed.
    import json
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    bridge = FakeBridge(folder)
    _begin(bridge, FakePQ(bridge, folder), sources.DAY_FIRST)
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    parts = {e["part"]: e["ms"] for e in events if e.get("event") == "close_step" and e.get("step") == "timing"}
    assert {"keep_original", "read_files", "find_query", "build_query", "query_add", "lint", "check_sheet",
            "reconcile", "audit_sheet"} <= set(parts)
    assert all(isinstance(ms, int) and ms >= 0 for ms in parts.values())
    assert parts["reconcile"] >= parts["check_sheet"]


def test_a_failing_check_still_releases_the_screen(folder):
    class Recording(FakeBridge):
        def __init__(self, f):
            super().__init__(f)
            self.screen = []

        def screen_updating(self, on):
            self.screen.append(on)

    class Broken(FakePQ):
        def load(self, name):
            raise RuntimeError("COM went away")
    bridge = Recording(folder)
    with pytest.raises(RuntimeError):
        _begin(bridge, Broken(bridge, folder), sources.DAY_FIRST)
    assert bridge.screen == [False, True]


def _recording(folder):
    class Recording(FakeBridge):
        def __init__(self, f):
            super().__init__(f)
            self.screen = []

        def screen_updating(self, on):
            self.screen.append(on)
    return Recording(folder)


def test_a_failing_bien_ban_never_leaves_the_screen_frozen(folder, monkeypatch):
    # Excel rejects a call (the user clicked into a cell) while the Biên bản is rewritten: the screen must
    # still be released, in the check after "Đúng rồi, làm tiếp" and in the fix after "Sửa giúp tôi".
    bridge = _recording(folder)
    pq = FakePQ(bridge, folder)
    real = month_close._write_audit

    def rejected(_bridge):
        raise RuntimeError("Call was rejected by callee")
    monkeypatch.setattr(month_close, "_write_audit", rejected)
    with pytest.raises(RuntimeError, match="rejected"):
        _begin(bridge, pq, sources.DAY_FIRST)
    assert bridge.screen == [False, True]


def test_the_step_s_own_error_is_not_hidden_by_a_failing_bien_ban(folder, monkeypatch):
    bridge = _recording(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)
    bridge.screen.clear()

    def broken(*_a):
        raise ValueError("the fix itself failed")

    def rejected(_bridge):
        raise RuntimeError("Call was rejected by callee")
    monkeypatch.setattr(month_close, "_approve", broken)
    monkeypatch.setattr(month_close, "_write_audit", rejected)
    with pytest.raises(ValueError, match="fix itself"):
        month_close.approve(bridge, pq)
    assert bridge.screen == [False, True]


def test_a_failing_bien_ban_never_logs_the_workbook_name(folder, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    bridge = _recording(folder)
    pq = FakePQ(bridge, folder)
    _begin(bridge, pq, sources.DAY_FIRST)

    def broken(*_a):
        raise ValueError("the fix itself failed")

    def rejected(_bridge):
        raise RuntimeError(-2147352567, "Cannot access 'BCTC_AgriS_Q3.xlsx' in C:\\Users\\chi\\AgriS")
    monkeypatch.setattr(month_close, "_approve", broken)
    monkeypatch.setattr(month_close, "_write_audit", rejected)
    with pytest.raises(ValueError):
        month_close.approve(bridge, pq)
    assert "AgriS" not in caplog.text and "BCTC" not in caplog.text
    assert "Bien ban sheet failed" in caplog.text and "RuntimeError 0x80020009" in caplog.text


def test_the_line_under_the_cursor_says_when_excel_starts_loading(folder):
    # The first Power Query work of an Excel session takes seconds: the caption says what is happening.
    shown = []
    bridge = FakeBridge(folder)
    pq = FakePQ(bridge, folder)
    month_close.start(bridge, pq, sources.DAY_FIRST)
    month_close._PROGRESS = shown.append
    try:
        month_close.confirm(bridge, pq)
    finally:
        month_close._PROGRESS = None
    assert shown == ["load"] and month_close.WORKING["load"] == "Em đang nạp đơn hàng vào Excel…"


def test_a_total_row_at_the_end_of_the_export_is_dropped_before_dates_are_typed_and_counted(tmp_path):
    """Robot run 07/11 (bhc_e2e, dcf6bca): the synthetic Mastersale ends with a 'Tổng' row. Typing 'Tổng' as a
    date failed the whole load ('couldn't parse the input provided as a Date value'). The query drops rows whose
    date has no digit before typing; the receipt counts the same row as dropped, so used + dropped = total."""
    import csv
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import bhc_case
    from engine import intake
    bhc_case.make(tmp_path)
    path = tmp_path / "Mastersale_T07-T10.csv"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        lines = list(csv.reader(fh))[1:]
    assert lines[-1][0] == "Tổng"                                   # the writer's footer, like a real export
    order = sources.load_order_file(path)
    assert len(order.footers) == 1 and order.footers[0][0] == "Tổng"
    assert len(order.rows) + len(order.footers) == len(lines)
    truth = sources.truth([order], (2026, 10))
    assert truth.unreadable_dates == 0
    m = m_code.folder_query(str(tmp_path), {order.name: order.culture(None)}, month_close._typed_columns([order]),
                            order.header[order.columns["doc"]], folder_parameter="ThuMuc", period=(2026, 10))
    date = order.header[order.columns["date"]]
    step = next(l for l in m.splitlines() if l.strip().startswith("DoiKieu ="))
    keep = f'Text.Select(Text.From(Record.FieldOrDefault(_, "{date}", "") ?? ""), {{"0".."9"}}) <> ""'
    assert keep in step and step.index(keep) < step.index("{{"), step       # filtered on the raw text, then typed
    kept = [r for r in lines if any(ch.isdigit() for ch in r[0])]          # what that filter keeps
    assert kept == order.rows
    assert f"try [{date}] <> null otherwise true" in m and m_code.lint("DonHang", m) == []
    _, items = intake._order_lines([order], truth, (2026, 10))
    footer = [d for d in items if d.what.startswith('Dòng "Tổng"')]
    assert len(footer) == 1 and footer[0].reason.startswith("dòng tổng cuối file") and footer[0].file == order.name


def test_the_receipt_counts_rows_used_and_left_out_for_every_file(tmp_path):
    """bhc_e2e 84603e7, stages 1–2: the intake event gave amounts per file but not rows. Rule 4 is in rows too:
    rows used + rows left out = rows the file holds, for the order export and each trial balance."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import bhc_case
    from engine import intake
    bhc_case.make(tmp_path)
    files = sources.find_order_files(tmp_path)
    tbs, ledger = month_close._ledger(tmp_path)
    truth = sources.truth(files, sources.period_of(tbs[-1][1]))
    receipt = intake.build(tmp_path, files, tbs, truth, month_close._net_of_returns(ledger, truth),
                           "BaoCao_QuanTri_T10.xlsx")
    logged = intake.log_fields(receipt, "")["files"]
    sales = next(f for f in logged if f["kind"] == "Bán hàng")
    assert (sales["rows_total"], sales["rows_used"], sales["rows_dropped"]) == (97, 24, 73)   # 72 outside + 'Tổng'
    for f in logged:
        if f["kind"] in ("Bán hàng",) or f["kind"].startswith("Sổ cân đối"):
            assert f["rows_total"] == f["rows_used"] + f["rows_dropped"] and f["rows_total"] > 0, f
