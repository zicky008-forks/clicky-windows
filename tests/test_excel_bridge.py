from office.cellref import col_index, col_letters, parse_ref
from office.excel_com import ExcelBridge, format_for_prompt, is_excel_window


class FakeRange:
    def __init__(self, sheet, address):
        self.sheet, self.address = sheet, address
        ref = parse_ref(address)
        self.Row, self.Column = ref.row1, ref.col1
        self.Left = (ref.col1 - 1) * 48.0          # 48 pt columns
        self.Top = (ref.row1 - 1) * 15.0           # 15 pt rows
        self.Width = (ref.col2 - ref.col1 + 1) * 48.0
        self.Height = (ref.row2 - ref.row1 + 1) * 15.0
        self.Address = "$" + address.replace(":", ":$")
        self.Rows = type("N", (), {"Count": ref.row2 - ref.row1 + 1})()
        self.Columns = type("N", (), {"Count": ref.col2 - ref.col1 + 1})()
        rows, cols = ref.row2 - ref.row1 + 1, ref.col2 - ref.col1 + 1
        # A block nobody wrote reads as v<row><col> from its own top-left, like a filled sheet.
        default = tuple(tuple(f"v{i}{j}" for j in range(cols)) for i in range(rows)) if rows * cols > 1 else ""
        self.Formula = sheet.cells.get(address, default)
        self.Value = self.Value2 = self.Formula

    def Resize(self, rows, cols):
        # Early-bound pywin32 wrappers do not expose Range.Resize as a call; the bridge must not use it.
        raise AttributeError("Resize")

    def __setattr__(self, name, value):
        if name == "Formula" and hasattr(self, "Formula"):
            self.sheet.written[self.address] = value
        object.__setattr__(self, name, value)


class FakeSheet:
    def __init__(self, name):
        self.Name, self.cells, self.written = name, {}, {}
        self.UsedRange = type("U", (), {"Address": "$A$1:$F$40"})()

    def Range(self, address):
        return FakeRange(self, address)


class FakePane:
    """Grid origin at screen (100, 200), zoom 100 %, 96 dpi, rows 1–30 and columns A–M visible."""
    def __init__(self, sheet, zoom=1.0):
        self.zoom = zoom
        self.VisibleRange = sheet.Range("A1:M30")

    def PointsToScreenPixelsX(self, pt):
        return int(100 + pt * self.zoom * 96 / 72)

    def PointsToScreenPixelsY(self, pt):
        return int(200 + pt * self.zoom * 96 / 72)


class FakeApp:
    def __init__(self, zoom=100):
        self.ActiveSheet = FakeSheet("Báo cáo T10")
        other = FakeSheet("Data")
        sheets = {1: self.ActiveSheet, 2: other, "Báo cáo T10": self.ActiveSheet, "Data": other}
        ws = lambda key: sheets[key]
        ws.Count = 2
        ws.Add = None
        self.ActiveWorkbook = type("W", (), {"Name": "TB_T10.xlsx", "Worksheets": staticmethod(ws)})()
        self.Selection = self.ActiveSheet.Range("B2:C3")
        pane = FakePane(self.ActiveSheet, zoom / 100)
        panes = lambda i: pane
        panes.Count = 1
        self.ActiveWindow = type("Win", (), {"Panes": staticmethod(panes), "Zoom": zoom})()
        self.Hwnd = 0

    def Intersect(self, visible, rng):
        v, r = parse_ref(visible.address), parse_ref(rng.address)
        if r.col1 > v.col2 or r.row1 > v.row2:
            return None
        return rng


def test_cell_refs():
    assert col_index("A") == 1 and col_index("AA") == 27 and col_letters(28) == "AB"
    ref = parse_ref("'Báo cáo T10'!$B$12:D20")
    assert ref.sheet == "Báo cáo T10" and ref.address == "B12:D20" and ref.cells == 27
    assert parse_ref("Sheet1!C5").address == "C5"
    assert parse_ref("Save button") is None
    assert parse_ref("XFE1") is None


def test_excel_window_titles():
    assert is_excel_window("TB_T10.xlsx - Excel")
    assert not is_excel_window("report.docx - Word")


def test_context_is_bounded_and_described():
    ctx = ExcelBridge(app=FakeApp()).context()
    assert ctx["workbook"] == "TB_T10.xlsx" and ctx["selection"] == "B2:C3"
    assert ctx["values"] == [["v00", "v01"], ["v10", "v11"]]
    text = format_for_prompt(ctx)
    assert "B2='v00'" in text and "C3='v11'" in text and "[CELL:" in text


def test_screen_rect_follows_excel_layout():
    bridge = ExcelBridge(app=FakeApp(), dpi=96)
    left, top, right, bottom = bridge.screen_rect("B2")
    assert (left, top) == (164, 220) and (right, bottom) == (228, 240)
    assert bridge.screen_rect("Data!B2") is None          # not the sheet on screen
    assert bridge.screen_rect("Z99") is None              # scrolled out of view


def test_screen_rect_corrects_zoom_ignored_by_excel():
    app = FakeApp(zoom=200)
    app.ActiveWindow.Panes(1).zoom = 1.0                  # Excel reports unzoomed pixels
    left, top, right, bottom = ExcelBridge(app=app, dpi=96).screen_rect("A1")
    assert round(right - left) == 128 and round(bottom - top) == 40


def test_write_pads_rows_and_keeps_formulas():
    app = FakeApp()
    assert ExcelBridge(app=app).write("B5", [["Doanh thu", "=SUM(Data!C2:C40)"], ["Giá vốn"]])
    assert app.ActiveSheet.written["B5:C6"] == (("Doanh thu", "=SUM(Data!C2:C40)"), ("Giá vốn", ""))
    assert not ExcelBridge(app=app).write("not a cell", [["x"]])


def test_no_excel_means_no_pointing():
    bridge = ExcelBridge()
    bridge._excel = lambda: None
    assert bridge.screen_rect("B2") is None and bridge.context() is None and not bridge.available()


def test_cell_tags_are_hidden_from_speech():
    from companion_manager import ANY_TAG_RE, CELL_RE, RANGE_RE
    text = "Doanh thu giảm ở đây [CELL:'Báo cáo T10'!C12|Doanh thu] và vùng này [RANGE:B2:D20|Chi phí]."
    assert CELL_RE.search(text).groups() == ("'Báo cáo T10'!C12", "Doanh thu")
    assert RANGE_RE.search(text).group(1) == "B2:D20"
    assert ANY_TAG_RE.sub("", text) == "Doanh thu giảm ở đây  và vùng này ."


def test_missing_local_model_explains_what_to_do():
    from companion_manager import friendly_request_error

    class ConnectError(Exception):
        pass
    msg = friendly_request_error(ConnectError("All connection attempts failed"), "ollama")
    assert "chưa bật AI" in msg and "làm báo cáo tháng" in msg and "Ollama" in msg
    assert len(msg) < 200      # the chat keeps only the first 200 characters of an error
    assert friendly_request_error(ValueError("bad reply"), "ollama") == "bad reply"
    assert friendly_request_error(ConnectError("All connection attempts failed"), "claude") == "All connection attempts failed"


def test_sheet_rows_reads_the_used_block_without_resize():
    app = FakeApp()
    data = app.ActiveWorkbook.Worksheets("Data")
    data.UsedRange = data.Range("B3:C4")
    rows = ExcelBridge(app=app).sheet_rows("Data")
    # Row and column numbers match what the user sees: two empty rows, then a blank first column.
    assert rows[:2] == [[], []]
    assert rows[2:] == [[None, "v00", "v01"], [None, "v10", "v11"]]


def test_reveal_keeps_the_report_title_and_labels_in_view():
    from office.excel_com import reveal_scroll
    assert reveal_scroll(20, 1, 1, 24, 12) == (1, 1)       # fits under the title: start from the top
    assert reveal_scroll(20, 1, 3, 18, 12) == (14, 1)      # does not fit: a third of the way down, column A kept
    assert reveal_scroll(200, 1, 30, 30, 12) == (190, 30)


def test_figure_blocks_split_a_sheet_into_figures_and_words():
    from office.excel_com import figure_blocks
    grid = [
        ["Báo cáo tháng 10", None, None, None],
        ["Chỉ tiêu", "T09", "T10", None],
        ["Doanh thu", 1_200_000_000, 1_350_000_000, None],
        ["Giá vốn", 800_000_000, 905_000_000, None],
        [None, None, None, None],
        ["TK 6411", 12.5, None, "ghi chú"],
    ]
    blocks = figure_blocks(grid)
    assert ("t", 0, 3, 0, 0) in blocks                    # the title and the row labels under it: one column
    assert ("n", 1, 3, 1, 2) in blocks                    # "T09"/"T10" headers and the amounts: one block
    assert ("n", 5, 5, 0, 1) in blocks                    # an account code counts as a figure
    assert ("t", 5, 5, 3, 3) in blocks
    assert not any(b[1] <= 4 <= b[2] for b in blocks)     # the empty row stays free


def test_figure_boxes_map_each_block_to_screen_pixels():
    class Cols:
        def __init__(self, rng):
            self.rng = rng

        def __call__(self, j):
            return type("C", (), {"Left": self.rng.Left + (j - 1) * 48.0})()

    class Rows:
        def __init__(self, rng):
            self.rng = rng

        def __call__(self, i):
            return type("R", (), {"Top": self.rng.Top + (i - 1) * 15.0})()

    class Filled:
        Left, Top, Width, Height = 0.0, 0.0, 3 * 48.0, 3 * 15.0
        Value2 = (("Chỉ tiêu", "T10", None), ("Doanh thu", 1350.0, None), (None, None, "x"))

    filled = Filled()
    filled.Columns, filled.Rows = Cols(filled), Rows(filled)
    pane = type("P", (), {"VisibleRange": object(),
                          "PointsToScreenPixelsX": lambda self, pt: 100 + pt * 96 / 72,
                          "PointsToScreenPixelsY": lambda self, pt: 200 + pt * 96 / 72})()
    win = type("W", (), {"Zoom": 100, "Panes": type("PS", (), {"Count": 1, "__call__": lambda self, i: pane})()})()
    app = type("A", (), {"ActiveSheet": type("S", (), {"UsedRange": object()})(), "ActiveWindow": win,
                         "Hwnd": 0, "Intersect": lambda self, a, b: filled})()
    bridge = ExcelBridge(app=app, dpi=96)
    boxes = bridge.figure_boxes()
    assert boxes["numbers"] == [(164.0, 200.0, 228.0, 240.0)]          # B1:B2, "T10" and 1350
    assert (100.0, 200.0, 164.0, 240.0) in boxes["text"]               # A1:A2 labels
    assert (228.0, 240.0, 292.0, 260.0) in boxes["text"]               # C3


class SavingBook:
    """A workbook as Excel reports it: Path is empty for one with no file behind it."""
    def __init__(self, app, full="C:\\Mau\\Mau_SongHong_T10.xlsx", path="C:\\Mau", read_only=False, renames_to=None):
        self.app, self.FullName, self.Path, self.ReadOnly = app, full, path, read_only
        self.renames_to, self.saves, self.alerts_seen = renames_to, 0, []

    def Save(self):
        self.saves += 1
        self.alerts_seen.append(self.app.DisplayAlerts)
        if self.renames_to:                      # Excel could not replace the file and kept a temp copy
            self.FullName, self.Path = self.renames_to, ""

    def SaveAs(self, Filename, FileFormat):
        self.saved_as = (Filename, FileFormat, self.app.DisplayAlerts)
        self.FullName, self.Path, self.ReadOnly = Filename, Filename.rsplit("\\", 1)[0], False


def _saving_app(**kw):
    app = type("App", (), {})()
    app.DisplayAlerts = True
    app.ActiveWorkbook = SavingBook(app, **kw)
    return app


def test_save_never_opens_save_as_for_a_workbook_with_no_file():
    # PC --sample run on 37265be: the workbook had no real path, Save() opened a modal Save As and
    # every COM call after it was rejected. Clicky must refuse and say it could not save.
    app = _saving_app(full="65302810", path="")
    assert ExcelBridge(app=app).save() is False
    assert app.ActiveWorkbook.saves == 0 and app.DisplayAlerts is True


def test_save_refuses_a_read_only_workbook():
    app = _saving_app(read_only=True)
    assert ExcelBridge(app=app).save() is False and app.ActiveWorkbook.saves == 0


def test_save_runs_with_alerts_off_and_counts_only_if_the_path_held():
    app = _saving_app()
    assert ExcelBridge(app=app).save() is True
    assert app.ActiveWorkbook.alerts_seen == [False] and app.DisplayAlerts is True
    app = _saving_app(renames_to="C:\\Mau\\65302810")
    assert ExcelBridge(app=app).save() is False and app.DisplayAlerts is True


def test_a_workbook_with_no_file_is_saved_as_the_exact_file_clicky_made():
    # The sample lives in Clicky's own folder: a workbook Excel holds with no path goes through
    # SaveAs with that name and alerts off, never Save (which would open the Save As window).
    target = "C:\\Users\\chi\\AppData\\Local\\Clicky\\File mau\\20261107-130015\\Mau_SongHong_T10.xlsx"
    app = _saving_app(full="65302810", path="")
    assert ExcelBridge(app=app).save(target) is True
    book = app.ActiveWorkbook
    assert book.saves == 0 and book.saved_as == (target, 51, False) and app.DisplayAlerts is True
    app = _saving_app(full=target, path=target.rsplit("\\", 1)[0])
    assert ExcelBridge(app=app).save(target) is True and app.ActiveWorkbook.saves == 1


def test_save_logs_never_carry_a_workbook_name_or_folder(caplog):
    # clicky.log goes into bug reports: a company file's name or folder must never be in it, even when
    # Excel's own error text quotes them.
    import logging
    caplog.set_level(logging.WARNING)
    secret = "C:\\Users\\chi\\Documents\\AgriS\\BCTC_Q3_mat.xlsx"
    app = _saving_app(full="BCTC_Q3_mat", path="")
    ExcelBridge(app=app).save()
    app = _saving_app(full=secret, path="C:\\Users\\chi\\Documents\\AgriS", renames_to="C:\\Users\\chi\\AgriS\\65302810")
    ExcelBridge(app=app).save()

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", secret)
    app = _saving_app(full=secret, path="C:\\Users\\chi\\Documents\\AgriS")
    app.ActiveWorkbook.Save = boom
    ExcelBridge(app=app).save()
    ExcelBridge(app=app).save_copy(secret.replace(".xlsx", "_ban-goc.xlsx"))
    text = caplog.text
    assert "AgriS" not in text and "BCTC" not in text and "Users" not in text
    assert "Excel's temporary name" in text and "PermissionError 0x0000000D" in text and "a .xlsx file" in text


def test_the_original_is_copied_from_disk_when_excel_has_nothing_unsaved(tmp_path):
    # The first SaveCopyAs after Excel starts took seconds on the PC; a saved workbook is the file on disk.
    book = tmp_path / "BaoCao_T10.xlsx"
    book.write_bytes(b"saved bytes")
    copy = tmp_path / "BaoCao_T10_ban-goc.xlsx"
    asked = []
    wb = type("W", (), {"FullName": str(book), "Path": str(tmp_path), "Saved": True,
                        "SaveCopyAs": lambda self, p: asked.append(p)})()
    app = type("App", (), {"ActiveWorkbook": wb, "DisplayAlerts": True})()
    assert ExcelBridge(app=app).save_copy(str(copy)) and copy.read_bytes() == b"saved bytes" and asked == []
    wb.Saved = False                                  # unsaved changes: only Excel has the workbook as it is now
    other = tmp_path / "again.xlsx"
    assert ExcelBridge(app=app).save_copy(str(other)) and asked == [str(other)] and not other.exists()


def test_source_files_opened_to_point_are_closed_unsaved_and_logged_by_id(monkeypatch, tmp_path):
    # Grader 07/11: each source file Clicky opened read-only to light a cell is closed without saving, and
    # close_book carries the same id as show_book (Excel may report a OneDrive URL as FullName), never a name.
    import json
    from engine.log_safe import book_id
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    source, pack = "C:\\case\\KeHoach_AOPR1.xlsx", "C:\\case\\BOD_T10.xlsx"

    class Book:
        def __init__(self, full): self.FullName, self.closed = full, []
        def Activate(self): pass
        def Close(self, SaveChanges): self.closed.append(SaveChanges); books.remove(self)

    books = [Book(pack)]

    class Books:
        @property
        def Count(self): return len(books)
        def __call__(self, i): return books[i - 1]
        def Open(self, path, UpdateLinks, ReadOnly, AddToMru):
            assert ReadOnly is True and AddToMru is False
            books.append(Book("https://tenant-my.sharepoint.com/personal/chi/Documents/KeHoach_AOPR1.xlsx"))
            return books[-1]

    app = type("App", (), {"DisplayAlerts": True, "Workbooks": Books()})()
    bridge = ExcelBridge(app=app)
    assert bridge.show_workbook(source) is True
    opened = books[-1]
    assert bridge.close_opened(pack) == 1 and opened.closed == [False]
    text = log.read_text(encoding="utf-8")
    closes = [e for e in map(json.loads, text.splitlines()) if e["event"] == "close_book"]
    assert closes == [{**closes[0], "book": book_id(source), "saved": False, "pack": False}]
    assert "KeHoach" not in text and "sharepoint" not in text and "BOD_T10" not in text
