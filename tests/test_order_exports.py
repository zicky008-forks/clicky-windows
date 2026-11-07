"""Order exports as accounting and sales software really saves them: legacy .xls of three kinds, print layouts,
English headers, cancelled orders. Grader Case 05 (07/11) stopped at step 1 on these classes: an .xls source was
ignored and Clicky said it saw no order file; an English-header CSV was skipped in silence."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import order_exports as ox
from engine import m_code, intake, sources, tabular
from office import month_close

PERIOD = (2026, 10)
WRITERS = {"ole2.xls": ox.write_ole2_xls, "misa_html.xls": ox.write_html_xls,
           "spreadsheetml.xls": ox.write_spreadsheetml}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_every_kind_of_xls_reads_the_same_orders_under_a_print_layout(tmp_path, name):
    path = WRITERS[name](tmp_path / name)
    f = sources.load_order_file(path)
    assert f is not None and f.kind == {"ole2.xls": "xls", "misa_html.xls": "html", "spreadsheetml.xls": "xml"}[name]
    assert [f.header[f.columns[r]] for r in ("doc", "date", "amount", "cost")] == \
        ["Số chứng từ", "Ngày chứng từ", "Thành tiền", "Giá vốn"]
    assert len(f.preamble) == 4 and [r[0] for r in f.rows] == ["DH001", "DH002", "DH003", "DH004", "DH005"]
    t = sources.truth([f], PERIOD)
    assert t.amount == ox.TOTAL and t.cost == sum(o[6] for o in ox.ORDERS) and t.unique_rows == 5
    assert t.unreadable_dates == 0 and not t.outside
    labels = [next((c for c in r if str(c).strip()), "") for r in f.footers]
    assert labels[0] == "Tổng cộng" and "Người lập biểu" in labels      # the signature block is not an order


def test_the_signature_date_in_the_date_column_is_not_an_order(tmp_path):
    # "Ngày 31 tháng 10 năm 2026" has digits, so the CSV rule alone would keep it as an order with a bad date.
    f = sources.load_order_file(ox.write_html_xls(tmp_path / "misa.xls", date_in_column=True))
    assert len(f.rows) == 5 and sources.truth([f], PERIOD).unreadable_dates == 0
    assert any("Ngày 31 tháng 10 năm 2026" in r for r in f.footers)


def test_an_english_header_sales_csv_is_read_and_its_cancelled_order_set_aside(tmp_path):
    f = sources.load_order_file(ox.write_english_csv(tmp_path / "shop_orders.csv"))
    assert f is not None and f.kind == "csv"
    assert {r: f.header[i] for r, i in f.columns.items()} == {
        "doc": "Order ID", "date": "Created at", "customer": "Customer", "item": "SKU", "amount": "Line total",
        "cost": "COGS", "status": "Status"}
    assert [r[0] for r in f.cancelled] == ["DH004"] and len(f.rows) == 4
    t = sources.truth([f], PERIOD)
    assert t.amount == ox.SALES_EN and t.unreadable_dates == 0


def test_more_names_never_outrank_the_ones_clicky_already_knew():
    cols = sources.find_columns(["Total", "Ngày", "Thành tiền", "Subtotal", "Line total"])
    assert cols["amount"] == 2                                   # "Thành tiền", not the first "Total"
    assert sources.find_columns(["Order ID", "Created at", "Subtotal", "Line total"])["amount"] == 3


@pytest.mark.parametrize("value,out", [("Cancelled", True), ("canceled", True), ("VOID", True), ("Đã hủy", True),
                                       ("đã huỷ", True), ("Huỷ", True), ("Refunded", False), ("Returned", False),
                                       ("Đang chuyển", False), ("Chờ huỷ", False), ("", False)])
def test_only_clear_cancelled_values_set_an_order_aside(value, out):
    assert sources.is_cancelled(value) is out


def test_every_row_of_every_format_is_accounted_for_on_the_receipt(tmp_path):
    # One folder per file: the five orders are the same in every format, so together they would be duplicates.
    writers = dict(WRITERS, **{"shop.csv": ox.write_english_csv})
    for name, write in writers.items():
        folder = tmp_path / name.replace(".", "_")
        folder.mkdir()
        f = sources.load_order_file(write(folder / name))
        receipt = intake.build(folder, [f], [], sources.truth([f], PERIOD), {}, None)
        line, = receipt.files
        assert line.rows_total == line.rows_used + line.rows_dropped == 12 if name != "shop.csv" else 7, name
        assert line.rows_total == line.rows_used + line.rows_dropped, name
        assert abs(line.total - line.used - line.dropped) <= 1 and line.total == ox.TOTAL, name
    shop = line
    assert shop.dropped == Decimal("4000000") and '"Refunded" 1 đơn' in shop.checks
    cancelled = [d for d in receipt.dropped if d.kind == "cancel"]
    assert [(d.file, d.amount, d.reason) for d in cancelled] == [("shop.csv", Decimal("4000000.00"),
                                                                  "đơn đã huỷ, không phải doanh thu")]
    left = " | ".join(intake.card(receipt)["left_out"])
    assert "đơn đã huỷ" in left and "dòng tiêu đề phía trên bảng" in left and not intake.unread(receipt)


def test_a_file_that_cannot_be_read_or_lacks_a_column_is_named_with_its_reason(tmp_path):
    (tmp_path / "hong.xls").write_bytes(b"\x00\x01\x02 not a table \x00" * 50)
    ox.write_html_xls(tmp_path / "thieu_ngay.xls", header=["Số chứng từ", "Mã khách hàng", "Mã hàng", "SL", "Thành tiền"])
    ox.write_english_csv(tmp_path / "no_date.csv", header=["Order ID", "Customer", "SKU", "Line total"])
    reasons = {d.file: (d.reason, d.kind) for d in intake.unused_files(tmp_path, set())}
    assert reasons["hong.xls"] == ("chưa đọc được định dạng: chưa đọc được định dạng của file này "
                                   "(không phải Excel, CSV, HTML hay XML)", "unread")
    assert reasons["thieu_ngay.xls"][0].startswith("thiếu cột ngày (em thấy các cột: Số chứng từ") and \
        reasons["thieu_ngay.xls"][1] == "unread"
    assert reasons["no_date.csv"][0].startswith("thiếu cột ngày (em thấy các cột: Order ID, Customer, SKU, Line total)")


# ── step 1 in the app ────────────────────────────────────────────────────────────

class _Bridge:
    def __init__(self, folder):
        self.folder = folder

    def available(self):
        return True

    def workbook_folder(self):
        return str(self.folder)

    def workbook_path(self):
        return str(self.folder / "BaoCao_T10.xlsx")

    def sheet_names(self):
        raise AssertionError("step 1 and a stopped step 2 never touch the workbook")


def _start(folder):
    month_close._PENDING = None
    month_close._CHOSEN.clear()
    return month_close.start(_Bridge(folder), None, sources.DAY_FIRST)


def test_step_one_never_says_it_saw_no_order_file_when_files_are_there(tmp_path, monkeypatch):
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    data = tmp_path / "T10"
    data.mkdir()
    (data / "DonHang_MISA.xls").write_bytes(b"\x00garbage\x00" * 64)
    ox.write_english_csv(data / "Shopee_T10.csv", header=["Order ID", "Customer", "SKU", "Line total"])
    beats = _start(data)
    text = beats[0].text
    assert "không thấy" not in text and "DonHang_MISA.xls" in text and "Shopee_T10.csv" in text
    assert "chưa đọc được định dạng" in text and "thiếu cột ngày" in text and "Em thấy 2 file" in text
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert {f["name"] for e in events if e["event"] == "intake_none" for f in e["files"]} == \
        {"DonHang_MISA.xls", "Shopee_T10.csv"}
    empty = tmp_path / "trong"
    empty.mkdir()
    assert "không thấy file đơn hàng nào" in _start(empty)[0].text          # true only when there is none


def _play(monkeypatch, beats):
    """Run play() with a manager that draws nothing, so the scene log holds what the robot reads."""
    import asyncio
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)

    class Sig:
        def emit(self, *a):
            pass

    class Manager:
        _excel = None

        def __init__(self):
            for name in ("sig_guided", "sig_clear_drawings", "sig_draw", "sig_point_hold", "sig_point_at",
                         "sig_point_release", "sig_choices", "sig_response_chunk", "sig_intake"):
                setattr(self, name, Sig())

        async def _reply_local(self, _text):
            return True

    asyncio.run(month_close.play(Manager(), beats))


def test_an_early_stop_still_shows_the_receipt_with_one_line_per_file(tmp_path, monkeypatch):
    """Grader F3a (Case 05): with no usable order file Clicky stopped with a sentence only. The receipt comes first
    anyway: every file in the folder on its own line with why, logged for the robot, and only "Để tôi xem"."""
    import tb_samples as tb
    from test_month_close import _write_tb
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    data = tmp_path / "T10"
    data.mkdir()
    t10 = tb.month(8400, 450, 210, 5800, 6000, 9000, 5500, 15, 70, (760, 420, 200), (520, 170), 5, 25, 139)
    _write_tb(data / "TB_T10.xlsx", tb.misa_rows(tb.OPENING_T9, t10, "Tháng 10 năm 2026"))
    (data / "DonHang_MISA.xls").write_bytes(b"\x00garbage\x00" * 64)
    ox.write_english_csv(data / "Shopee_T10.csv", header=["Order ID", "Customer", "SKU", "Line total"])
    (data / "anh_chup.png").write_bytes(b"png")
    beats = _start(data)
    assert len(beats) == 1 and beats[0].choices == ("Để tôi xem",) and month_close.stage() == "stopped"
    card, logged = beats[0].intake["card"], beats[0].intake["log"]
    titles = [r["title"] for r in card["rows"]]
    assert titles[0].startswith("TB_T10.xlsx · Sổ cân đối")
    lines = {r["title"].split(" · ")[0]: r for r in card["rows"][1:]}
    assert set(lines) == {"DonHang_MISA.xls", "Shopee_T10.csv", "anh_chup.png"}
    assert "chưa đọc được định dạng" in lines["DonHang_MISA.xls"]["detail"]
    assert "thiếu cột ngày" in lines["Shopee_T10.csv"]["detail"]
    assert "ảnh" in lines["anh_chup.png"]["detail"] and not any(r["ok"] for r in lines.values())
    assert not any("DonHang_MISA" in x or "Shopee" in x for x in card["left_out"])     # each file said once
    assert {f["name"] for f in logged["files"]} == {"TB_T10.xlsx", "DonHang_MISA.xls", "Shopee_T10.csv", "anh_chup.png"}
    assert "không thấy" not in beats[0].text and "Em thấy 2 file" in beats[0].text
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert "intake_none" in events and "roles" in events
    _play(monkeypatch, beats)                        # robot gate f703018: a folder with data files reaches the receipt
    played = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    receipt = [e for e in played if e["event"] == "intake"]
    assert len(receipt) == 1 and {f["name"] for f in receipt[0]["files"]} >= {"DonHang_MISA.xls", "Shopee_T10.csv"}
    after = month_close.reject()                     # "Để tôi xem": ends the turn, nothing pending, nothing changed
    assert month_close.stage() is None and "Em chưa sửa gì trong file" in after[0].text


def test_the_reconcile_step_says_the_same_honest_sentence(tmp_path):
    """Grader F3b: if the order files vanish between the receipt and the confirm, _reconcile names what is there
    instead of the old "Em không thấy file đơn hàng CSV nào"."""
    data = tmp_path / "T10"
    data.mkdir()
    (data / "DonHang_MISA.xls").write_bytes(b"\x00garbage\x00" * 64)
    text = month_close._reconcile(_Bridge(data), None, sources.DAY_FIRST)[0].text
    assert "CSV nào" not in text and "DonHang_MISA.xls" in text and "chưa đọc được định dạng" in text


def test_step_one_reads_an_xls_source_and_names_the_file_it_could_not_read(tmp_path):
    ox.write_html_xls(tmp_path / "DonHang_MISA_T10.xls")
    (tmp_path / "DonHang_cu.xls").write_bytes(b"\x00garbage\x00" * 64)
    beats = _start(tmp_path)
    assert month_close.intake_pending()
    text = beats[-1].text
    assert "5 đơn bán hàng từ 1 trên 2 file đơn hàng" in text
    assert "File DonHang_cu.xls em chưa đọc được: chưa đọc được định dạng" in text and "không thấy" not in text
    card = beats[-1].intake["card"]
    assert any("DonHang_cu.xls" in line for line in card["left_out"])


class _ReaderPQ:
    """Power Query on a vi-VN machine for the per-file query: loads what sources.query_rows says it must. The real
    query is checked against the same yardstick on the PC by tools/dien_tap/m_agree.py."""

    def __init__(self, bridge, folder):
        self.bridge, self.folder, self.queries = bridge, folder, {}

    def list(self):
        return list(self.queries.items())

    def add(self, name, formula):
        self.queries[name] = formula
        return True

    def load(self, name):
        formula = self.queries[name]
        assert "DocTungFile" in formula, "a folder with .xls or print-layout files gets the per-file query"
        files = sources.find_order_files(self.folder)
        roles = [r for r in m_code.CANON if any(r in f.columns for f in files) or r in ("doc", "date", "amount")]
        header = [m_code.CANON[r] for r in roles] + [m_code.SOURCE_COLUMN]
        rows = sources.query_rows(files, roles)
        self.bridge.sheets[name] = [header] + rows
        return name, header, rows


@pytest.mark.parametrize("make", [ox.write_ole2_xls, ox.write_html_xls, ox.write_spreadsheetml, ox.write_english_csv])
def test_step_two_loads_every_format_through_the_per_file_query(tmp_path, make):
    """F1 step 2: the stop card is gone. Confirm keeps the original, adds the folder parameter and a query that
    reads each file by its own format, and the loaded table agrees with the receipt to the đồng."""
    from test_month_close import CopyingBridge
    data = tmp_path / "T10"
    data.mkdir()
    make(data / ("DonHang_T10.csv" if make is ox.write_english_csv else "DonHang_T10.xls"))
    bridge = CopyingBridge(data)
    pq = _ReaderPQ(bridge, data)
    month_close._PENDING, month_close._CHOSEN = None, {}
    receipt = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert month_close.intake_pending(), receipt[0].text
    beats = month_close.confirm(bridge, pq)
    text = " ".join(b.text for b in beats)
    assert "Em dừng ở đây" not in text and "Excel khớp" in text, text
    assert set(pq.queries) == {month_close.PARAM_NAME, month_close.QUERY_NAME}
    m = pq.queries[month_close.QUERY_NAME]
    assert "Folder.Files(ThuMucDonHang)" in m and not m_code.lint(month_close.QUERY_NAME, m)
    want = ox.SALES_EN if make is ox.write_english_csv else ox.TOTAL
    assert sum(r[2] for r in bridge.sheets[month_close.QUERY_NAME][1:]) == want


@pytest.mark.parametrize("make,why", [
    (lambda d: ox.write_english_csv(d / "shop.csv"), "dòng tiêu đề phía trên bảng"),
    (lambda d: ox.write_ole2_xls(d / "ole.xls"), "Excel 97-2003"),
    (lambda d: ox.write_html_xls(d / "misa.xls"), "trang HTML"),
])
def test_files_the_plain_folder_query_cannot_read_get_the_per_file_one(tmp_path, make, why):
    make(tmp_path)
    files = sources.find_order_files(tmp_path)
    f, said = month_close.needs_reader(files)
    assert why in said and month_close.cannot_load(files) is None


def test_plain_csv_exports_keep_the_plain_folder_query(tmp_path):
    import csv
    for name, rows in (("HN.csv", [["HN1", "05/10/2026", "100"]]), ("HCM.csv", [["HCM1", "21/10/2026", "200"]])):
        with open(tmp_path / name, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows([["SoCT", "Ngay", "ThanhTien"]] + rows)
    assert month_close.needs_reader(sources.find_order_files(tmp_path)) is None


@pytest.mark.parametrize("name,make", [("ole.xls", ox.write_ole2_xls), ("misa.xls", ox.write_html_xls),
                                       ("ss.xls", ox.write_spreadsheetml), ("shop.csv", ox.write_english_csv)])
def test_the_query_yardstick_is_the_receipt(tmp_path, name, make):
    """sources.query_rows, which m_agree.py holds Excel's rows to, counts what the receipt counts."""
    make(tmp_path / name)
    files = sources.find_order_files(tmp_path)
    t = sources.truth(files)
    rows = sources.query_rows(files, ["doc", "date", "amount", "cost", "customer", "item", "status"])
    assert len(rows) == t.unique_rows and sum(r[2] for r in rows) == t.amount
    assert all(isinstance(r[1], date) for r in rows) and {r[-1] for r in rows} == {name}


def test_the_per_file_query_is_written_from_the_engines_own_rules():
    """No second copy of the rules: every name, the cancelled values, the scan depth and the folding come from
    engine.sources and trial_balance.fold, so the query and the receipt cannot drift apart."""
    from engine.trial_balance import fold
    m = m_code.order_files_query("ThuMucDonHang", {'Chi "HN".xls': {"order": "en-US", "encoding": 1258}},
                                 ["doc", "date", "amount", "status"], None, (2026, 10))
    for names in list(sources.MORE_COLS.values()) + [sources.DOC_COLS, sources.DATE_COLS, sources.AMOUNT_COLS]:
        assert all(f'"{n}"' in m for n in names)
    assert all(f'"{v}"' in m for v in sources.CANCELLED)
    assert f"List.FirstN(rows, {sources.HEADER_SCAN})" in m
    for ch in "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ":
        assert f'#"{ch}" = "{fold(ch)}"' in m
    assert '#"Chi ""HN"".xls" = [thu = "en-US", ma_hoa = 1258]' in m
    assert "Date.Year([#\"Ngày\"]) = 2026" in m and "Web.Page" not in m and "Html.Table" not in m
    assert not m_code.lint("DonHang", m) and m_code.fixed_culture(m) is None
    assert m.count("(") == m.count(")") and m.count("{") == m.count("}") and m.count("[") == m.count("]")


def test_loaded_canonical_names_read_back_as_their_roles():
    header = [m_code.CANON[r] for r in m_code.CANON] + [m_code.SOURCE_COLUMN]
    cols = sources.find_columns(header)
    assert {r: header[i] for r, i in cols.items()} == m_code.CANON


# ── trial balances and the report workbook ──────────────────────────────────────

def test_a_legacy_xls_trial_balance_is_read_like_the_xlsx_one(tmp_path):
    import xlwt
    import tb_samples as tb
    from engine.cli import read_rows
    rows = tb.misa_rows(tb.OPENING_T9, tb.month(8000, 500, 200, 5300, 5000, 8500, 5200, 20, 60, (700, 300, 150),
                                                (500, 160), 10, 5, 231), "Tháng 9 năm 2026")
    book = xlwt.Workbook(encoding="utf-8")
    ws = book.add_sheet("TB")
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is not None:
                ws.write(r, c, float(v) if isinstance(v, (int, float, Decimal)) else v)
    book.save(str(tmp_path / "TB_T09.xls"))
    found = sources.find_trial_balances(tmp_path, read_rows)
    assert [name for name, _, _ in found] == ["TB_T09"] and sources.period_of(found[0][1]) == (2026, 9)
    assert found[0][2].lines


def test_an_order_xls_is_never_taken_for_the_report_workbook(tmp_path):
    from office import drop
    (tmp_path / "BaoCao_T10.xlsx").write_bytes(b"")
    ox.write_html_xls(tmp_path / "DonHang_MISA_T10.xls")
    assert drop.report_workbook(tmp_path) == tmp_path / "BaoCao_T10.xlsx"


# ── the reader itself ───────────────────────────────────────────────────────────

def test_sniffing_goes_by_the_bytes_not_the_name(tmp_path):
    assert tabular.sniff(tabular.OLE2 + b"\x00" * 100) == "xls"
    assert tabular.sniff(b"PK\x03\x04rest") == "xlsx"
    assert tabular.sniff(b"\xef\xbb\xbf<html><body><table><tr><td>1</td></tr></table>") == "html"
    assert tabular.sniff(b'<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">') == "xml"
    assert tabular.sniff("Số CT\tNgày\tThành tiền\n".encode("utf-16")) == "text"
    assert tabular.sniff(b"SoCT;Ngay;ThanhTien\n") == "text"


def test_numbers_and_dates_come_back_as_plain_text():
    from datetime import date, datetime
    assert [tabular.number_text(v) for v in (1250000.0, 1250000.5, -300, 1e16, "1.25E6")] == \
        ["1250000", "1250000.5", "-300", "10000000000000000", "1250000"]
    assert tabular.date_text(datetime(2026, 10, 13)) == "2026-10-13" and tabular.date_text(date(2026, 10, 1)) == "2026-10-01"


def test_html_colspan_keeps_columns_aligned():
    rows = tabular._html('<table><tr><td colspan="3">Tiêu đề</td></tr><tr><td>A</td><td>B&amp;C</td><td>1.000</td></tr></table>')
    assert rows == [("Bảng 1", [["Tiêu đề", "", ""], ["A", "B&C", "1.000"]])]


def test_an_xml_file_with_entity_declarations_is_refused(tmp_path):
    p = tmp_path / "bom.xls"
    p.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
                 '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><x>&a;</x></Workbook>', encoding="utf-8")
    with pytest.raises(tabular.Unreadable):
        tabular.sheets(p)
    assert sources.load_order_file(p) is None


def test_the_per_file_query_parses_as_m(tmp_path):
    """Microsoft's M parser, where it is installed (npm i @microsoft/powerquery-parser, NODE_PATH set), checks the
    query's syntax for every format; elsewhere this test is skipped and m_agree.py on the PC is the check."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('@microsoft/powerquery-parser')"], capture_output=True).returncode:
        pytest.skip("Microsoft's M parser is not installed here")
    paths = []
    for i, make in enumerate((ox.write_ole2_xls, ox.write_html_xls, ox.write_spreadsheetml, ox.write_english_csv)):
        folder = tmp_path / f"d{i}"
        folder.mkdir()
        make(folder / ("x.csv" if make is ox.write_english_csv else "x.xls"))
        files = sources.find_order_files(folder)
        m = month_close._reader_query(files, sources.truth(files), None)
        paths.append(tmp_path / f"q{i}.m")
        paths[-1].write_text(m, encoding="utf-8")
    run = subprocess.run([node, str(Path(__file__).with_name("m_syntax.js"))] + [str(p) for p in paths],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


# ── C2: column names Clicky does not know ──────────────────────────────────────

POS_ROWS = [["Mã phiếu", "Thời điểm", "SL", "Giá trị", "Phí"],
            ["P1", "03/10/2026", "2", "1.250.000", "12.500"],
            ["P2", "09/10/2026", "1", "2.500.000", "25.000"],
            ["P3", "15/10/2026", "3", "3.750.500", "37.500"],
            ["", "Cộng", "", "7.500.500", ""]]


def _pos_folder(tmp_path, rows=POS_ROWS, name="POS_T10.csv"):
    import csv
    data = tmp_path / "T10"
    data.mkdir(exist_ok=True)
    with open(data / name, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return data


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """The remembered columns live in a temporary data folder, empty at the start."""
    import storage
    home = tmp_path / "clicky-data"
    home.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: home)
    sources.REMEMBERED.clear()
    month_close._COLUMN_PICKS.clear()
    yield home / month_close.COLUMNS_FILE
    sources.REMEMBERED.clear()
    month_close._COLUMN_PICKS.clear()


def test_columns_are_guessed_by_their_values_best_first():
    at, dates, amounts = sources.guess_columns([["Báo cáo bán lẻ"], [""]] + POS_ROWS[:4])
    assert at == 2 and dates == [1] and amounts == [3, 4, 2]            # a line total before a fee before a quantity


def test_an_order_file_under_unknown_names_is_asked_about_then_read_and_remembered(tmp_path, memory, monkeypatch):
    """F2 follow-up: instead of leaving the file out, Clicky asks which column is which (3 likeliest at most), the
    answer is remembered per header, the receipt reads the file, and the Biên bản records the choice."""
    from test_month_close import CopyingBridge
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    data = _pos_folder(tmp_path)
    bridge = CopyingBridge(data)
    pq = _ReaderPQ(bridge, data)
    month_close._PENDING, month_close._CHOSEN = None, {}
    ask = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert month_close.columns_pending() and len(ask) == 1
    assert "POS_T10.csv" in ask[0].text and "tên cột lạ" in ask[0].text and "số tiền" in ask[0].text
    assert ask[0].choices == ("Cột Giá trị", "Cột Phí", "Cột SL", "Không phải đơn hàng")
    assert bridge.writes == [] and pq.queries == {}
    receipt = month_close.answer_columns(bridge, pq, "Cột Giá trị")
    assert month_close.intake_pending(), receipt[0].text
    assert "3 đơn bán hàng" in receipt[0].text
    sales = [r for r in receipt[0].intake["card"]["rows"] if r["title"].startswith("Bán hàng")]
    assert sales and sales[0]["detail"].startswith("Tổng") and "chị đã chỉ" in sales[0]["detail"]
    saved = json.loads(memory.read_text(encoding="utf-8"))
    assert saved == {"maphieu|thoidiem|sl|giatri|phi": {"date": "thoidiem", "amount": "giatri"}}
    assert "POS" not in memory.read_text(encoding="utf-8")                # header names only, never a file name
    beats = month_close.confirm(bridge, pq)
    assert "Excel khớp" in " ".join(b.text for b in beats)
    m = pq.queries[month_close.QUERY_NAME]
    assert '#"maphieu|thoidiem|sl|giatri|phi" = [#"amount" = "giatri", #"date" = "thoidiem"]' in m
    month_close.approve(bridge, pq) if month_close.stage() == "fix_query" else None
    rows = bridge.sheets.get("Bien ban Clicky") or []
    pick = [r for r in rows if len(r) > 3 and str(r[3]).startswith("Chọn cột: POS_T10.csv")]
    assert pick and "Giá trị" in str(pick[0][5])
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [e for e in events if e.get("step") == "column_question"]
    # Next month: same header, nothing asked, the file is read and the Biên bản says the columns were remembered.
    sources.REMEMBERED.clear()
    month_close._COLUMN_PICKS.clear()
    month_close._PENDING = None
    again = month_close.start(bridge, pq, sources.DAY_FIRST)
    assert month_close.intake_pending() and "3 đơn bán hàng" in again[0].text
    assert any("chị đã chỉ" in r["detail"] for r in again[0].intake["card"]["rows"])


def test_one_date_and_one_amount_are_confirmed_in_one_question(tmp_path, memory):
    rows = [[c for i, c in enumerate(r) if i in (0, 1, 3)] for r in POS_ROWS]
    data = _pos_folder(tmp_path, rows)
    month_close._PENDING = None
    ask = month_close.start(_Bridge(data), None, sources.DAY_FIRST)
    assert ask[0].choices == ("Đúng, dùng file này", "Không phải đơn hàng")
    assert "\"Thời điểm\" là ngày bán" in ask[0].text and "\"Giá trị\" là số tiền" in ask[0].text


def test_not_an_order_file_is_remembered_and_named_on_the_receipt(tmp_path, memory):
    import tb_samples as tb
    from test_month_close import _write_tb
    data = _pos_folder(tmp_path)
    t10 = tb.month(8400, 450, 210, 5800, 6000, 9000, 5500, 15, 70, (760, 420, 200), (520, 170), 5, 25, 139)
    _write_tb(data / "TB_T10.xlsx", tb.misa_rows(tb.OPENING_T9, t10, "Tháng 10 năm 2026"))
    month_close._PENDING = None
    month_close.start(_Bridge(data), None, sources.DAY_FIRST)
    after = month_close.answer_columns(_Bridge(data), None, "Không phải đơn hàng")
    assert not month_close.columns_pending()
    lines = {r["title"].split(" · ")[0]: r for r in after[0].intake["card"]["rows"]}
    assert "chị đã chỉ đây không phải file đơn hàng" in lines["POS_T10.csv"]["detail"]
    month_close._PENDING = None
    sources.REMEMBERED.clear()
    again = month_close.start(_Bridge(data), None, sources.DAY_FIRST)           # read back from disk: not asked again
    assert not month_close.columns_pending() and again[0].intake


def test_the_remembered_header_in_m_matches_the_engines_signature():
    header = ["  Mã phiếu", "Thời điểm ", "SL", "Giá trị", "Phí", "", ""]
    assert sources.header_signature(header) == "maphieu|thoidiem|sl|giatri|phi"
    m = m_code.order_files_query("P", {}, ["doc", "date", "amount"], None, None,
                                 {sources.header_signature(header): {"date": "thoidiem", "amount": "giatri"}})
    assert 'Text.Combine(List.Range(k, dau, List.Count(k) - dau - duoi), "|")' in m
    assert m_code.order_files_query("P", {}, ["doc", "date", "amount"], None).count("Nho = []") == 1
