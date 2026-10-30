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
        self.Formula = sheet.cells.get(address, "")
        self.Value = self.Formula

    def Resize(self, rows, cols):
        ref = parse_ref(self.address)
        r = FakeRange(self.sheet, f"{col_letters(ref.col1)}{ref.row1}:{col_letters(ref.col1 + cols - 1)}{ref.row1 + rows - 1}")
        r.Formula = tuple(tuple(f"v{i}{j}" for j in range(cols)) for i in range(rows))
        return r

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
