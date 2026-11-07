"""The Doi chieu and Bien ban sheets are sent to Excel in one go (ExcelBridge.paint) and come out exactly as the
old call-by-call writers left them: every cell's value, number format, font, fill, wrap, alignment and border,
every row height and column width, on Case 01 (the bundled sample) and on BHC (tools/dien_tap/bhc_case).

Excel is a small object model that keeps the state of every cell and counts the calls made into it."""
import copy
import sys
from pathlib import Path

import pytest

from engine import sources
from office import excel_com, month_close as mc, sample
from office.cellref import col_index, parse_ref
from office.excel_com import ExcelBridge, paint_plan

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools" / "dien_tap"))


# ── A tiny Excel: cell state and a count of the calls into it ────────────────────────────────────────────────

class Com:
    """Counts every property read or write and every method call by its COM (capitalised) name."""
    _app = None

    def __getattribute__(self, name):
        if name[:1].isupper():
            app = object.__getattribute__(self, "_app")
            if app is not None:
                app.calls += 1
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name[:1].isupper():
            self._app.calls += 1
            setter = getattr(type(self), "set_" + name, None)
            if setter is not None:
                setter(self, value)
                return
        object.__setattr__(self, name, value)


class Part(Com):
    """Font, Interior or one border of a range: setting a property sets it on every cell (or edge) it covers."""
    def __init__(self, rng, prefix, cells):
        object.__setattr__(self, "_app", rng._app)
        object.__setattr__(self, "_prefix", prefix)
        object.__setattr__(self, "_cells", cells)

    def __setattr__(self, name, value):
        self._app.calls += 1
        for cell in self._cells:
            cell[self._prefix + name] = value


class Range(Com):
    def __init__(self, sheet, address):
        object.__setattr__(self, "_app", sheet._app)
        object.__setattr__(self, "_sheet", sheet)
        areas = [parse_ref(a) for a in address.split(",")]
        assert all(areas), address
        object.__setattr__(self, "_areas", areas)
        object.__setattr__(self, "Rows", type("N", (), {"Count": areas[0].row2 - areas[0].row1 + 1})())

    def _keys(self):
        return [(r, c) for a in self._areas for r in range(a.row1, a.row2 + 1) for c in range(a.col1, a.col2 + 1)]

    def _cells(self, keys=None):
        return [self._sheet.cells.setdefault(k, {}) for k in (keys or self._keys())]

    def _put(self, name, value):
        for cell in self._cells():
            cell[name] = value

    def set_Formula(self, grid):
        a = self._areas[0]
        assert len(self._areas) == 1
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                self._sheet.cells.setdefault((a.row1 + i, a.col1 + j), {})["value"] = v

    def set_NumberFormat(self, v):
        self._put("NumberFormat", v)

    def set_WrapText(self, v):
        self._put("WrapText", v)

    def set_HorizontalAlignment(self, v):
        self._put("HorizontalAlignment", v)

    def set_VerticalAlignment(self, v):
        self._put("VerticalAlignment", v)

    def set_RowHeight(self, v):
        for a in self._areas:
            for r in range(a.row1, a.row2 + 1):
                self._sheet.heights[r] = v

    @property
    def Font(self):
        return Part(self, "Font.", self._cells())

    @property
    def Interior(self):
        return Part(self, "Interior.", self._cells())

    def Borders(self, index):
        if index == 9:                         # xlEdgeBottom: the last row of each block
            keys = [(a.row2, c) for a in self._areas for c in range(a.col1, a.col2 + 1)]
        else:                                  # xlInsideHorizontal: between the rows of each block
            assert index == 12
            keys = [(r, c) for a in self._areas for r in range(a.row1, a.row2) for c in range(a.col1, a.col2 + 1)]
        return Part(self, "Bottom.", self._cells(keys))

    def Clear(self):
        for k in self._keys():
            self._sheet.cells.pop(k, None)


class Column(Com):
    def __init__(self, sheet, col):
        object.__setattr__(self, "_app", sheet._app)
        object.__setattr__(self, "_sheet", sheet)
        object.__setattr__(self, "_col", col)

    def set_ColumnWidth(self, v):
        self._sheet.widths[self._col] = v


class Sheet(Com):
    def __init__(self, app, name):
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "cells", {})
        object.__setattr__(self, "heights", {})
        object.__setattr__(self, "widths", {})
        object.__setattr__(self, "gridlines", True)

    def set_Name(self, v):
        object.__setattr__(self, "Name", v)

    def Range(self, address):
        return Range(self, address)

    def Columns(self, col):
        return Column(self, col_index(col) if isinstance(col, str) else col)

    def Activate(self):
        self._app.active = self


class Worksheets(Com):
    def __init__(self, app):
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "items", [])

    def __call__(self, key):
        self._app.calls += 1
        if isinstance(key, int):
            return self.items[key - 1]
        return next(s for s in self.items if s.Name == key)

    @property
    def Count(self):
        return len(self.items)

    def Add(self, After=None):
        ws = Sheet(self._app, f"Sheet{len(self.items) + 1}")
        self.items.append(ws)
        return ws


class Window(Com):
    def __init__(self, app):
        object.__setattr__(self, "_app", app)

    def set_DisplayGridlines(self, v):
        self._app.active.gridlines = v


class Excel(Com):
    def __init__(self, names):
        object.__setattr__(self, "calls", 0)
        object.__setattr__(self, "_app", self)
        sheets = Worksheets(self)
        for n in names:
            sheets.items.append(Sheet(self, n))
        object.__setattr__(self, "ActiveWorkbook", type("W", (), {"Worksheets": sheets})())
        object.__setattr__(self, "active", sheets.items[0])
        object.__setattr__(self, "ActiveWindow", Window(self))

    @property
    def ActiveSheet(self):
        return self.active

    def state(self) -> dict:
        return {s.Name: {"cells": {k: v for k, v in s.cells.items() if v}, "heights": s.heights,
                         "widths": s.widths, "gridlines": s.gridlines}
                for s in self.ActiveWorkbook.Worksheets.items}


class Handles(ExcelBridge):
    """ExcelBridge on the tiny Excel; counts how often it asks for Excel (GetActiveObject in the product)."""
    def __init__(self, app):
        super().__init__(app=app)
        self.handles = 0

    def _excel(self):
        self.handles += 1
        return super()._excel()


class Calls(Handles):
    """The bridge without paint, as before: every write and format is its own trip to Excel."""
    paint = property(lambda self: (_ for _ in ()).throw(AttributeError("paint")))


# ── The writers' inputs on Case 01 and BHC, taken from a month close run on the faked bridge ──────────────────

def _run(folder, book, pq_class):
    from tests.test_month_close import CopyingBridge
    seen = []
    real_check, real_audit = mc._write_check_sheet, mc._write_audit

    def check(bridge, *args, **kwargs):
        seen.append(("check", bridge.sheet_names(), copy.deepcopy(args), copy.deepcopy(kwargs), None))
        return real_check(bridge, *args, **kwargs)

    def audit_sheet(bridge):
        seen.append(("audit", bridge.sheet_names(), (), {}, copy.deepcopy(mc._AUDIT)))
        return real_audit(bridge)

    mc._write_check_sheet, mc._write_audit = check, audit_sheet
    try:
        bridge = CopyingBridge(folder, book)
        pq = pq_class(bridge, folder)
        mc.use_folder(None)
        mc._PENDING = None
        mc.start(bridge, pq, sources.DAY_FIRST)
        while mc.columns_pending():
            mc.answer_columns(bridge, pq, mc.COLUMN_NO)
        mc.confirm(bridge, pq)
        if mc.stage() == "fix_query":
            mc.approve(bridge, pq)
    finally:
        mc._write_check_sheet, mc._write_audit = real_check, real_audit
        mc._PENDING = None
    return seen


def _engine_pq():
    from tests.test_month_close import FakePQ

    class EnginePQ(FakePQ):
        """Loads the order files with their own column names, typed and de-duplicated as the receipt reads them."""
        def load(self, name):
            files = sources.find_order_files(self.folder)
            tbs, _ = mc._ledger(self.folder)
            period = sources.period_of(tbs[-1][1]) if tbs else None
            header, rows, seen = files[0].header, [], set()
            for f in files:
                roles = {i: r for r, i in f.columns.items()}
                for r in f.rows:
                    d = sources.parse_date(r[f.columns["date"]], f.date_order)
                    if period and d and (d.year, d.month) != tuple(period):
                        continue
                    doc = r[f.columns["doc"]] if "doc" in f.columns else None
                    if doc and doc in seen:
                        continue
                    seen.add(doc)
                    rows.append([d if roles.get(i) == "date" else
                                 sources.parse_amount(v)[0] if roles.get(i) in ("amount", "cost") else v
                                 for i, v in enumerate(r[:len(header)])])
            self.bridge.sheets[name] = [header] + rows
            return name, header, rows
    return EnginePQ


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    from tests.test_month_close import FakePQ
    import bhc_case
    root = tmp_path_factory.mktemp("batch")
    book = sample.prepare(root / "case01")
    out = {"case01": _run(book.parent, book.name, FakePQ)}
    bhc = root / "bhc"
    bhc.mkdir()
    bhc_case.make(bhc)
    out["bhc"] = _run(bhc, bhc_case.REPORT, _engine_pq())
    return out


def _old_writers():
    ns = dict(vars(mc))
    exec(compile((HERE / "old_sheet_writers.py").read_text(encoding="utf-8"), "old_sheet_writers.py", "exec"), ns)
    return ns


def _replay(writers, call, bridge):
    kind, _, args, kwargs, log = call
    if kind == "check":
        return writers["_write_check_sheet"](bridge, *copy.deepcopy(args), **copy.deepcopy(kwargs))
    writers["_AUDIT"] = copy.deepcopy(log)
    return writers["_write_audit"](bridge)


def _scene(monkeypatch):
    seen = []
    log = type("L", (), {"record": staticmethod(lambda *a, **k: seen.append((a, k)))})()
    monkeypatch.setattr(excel_com, "scene_log", log)
    monkeypatch.setattr(mc.scene_log, "record", lambda *a, **k: seen.append((a, k)))
    return seen


@pytest.mark.parametrize("case", ["case01", "bhc"])
def test_batched_sheets_match_the_old_writer_cell_for_cell(cases, case, monkeypatch):
    calls = cases[case]
    assert {c[0] for c in calls} == {"check", "audit"}, calls
    old = _old_writers()
    new = {"_write_check_sheet": mc._write_check_sheet, "_write_audit": mc._write_audit}
    totals = {"old": [0, 0], "new": [0, 0]}
    for call in calls:
        names = call[1]
        before, after = Excel(names), Excel(names)
        log = _scene(monkeypatch)
        b1 = Calls(before)
        got_old = _replay(old, call, b1)
        old_log = list(log)
        log.clear()
        b2 = Handles(after)
        got_new = _replay(dict(new, _AUDIT=None), call, b2) if call[0] == "check" else None
        if call[0] == "audit":
            mc._AUDIT = copy.deepcopy(call[4])
            got_new = mc._write_audit(b2)
        assert got_old == got_new
        assert after.state() == before.state(), (case, call[0])
        assert log == old_log                                    # the robot reads the same scene-log lines
        for side, app, bridge in (("old", before, b1), ("new", after, b2)):
            totals[side][0] += app.calls
            totals[side][1] += bridge.handles
    mc._AUDIT = None
    # Far fewer trips: the cold confirm spent 1,3 s here on the PC (check_sheet 797 ms, audit_sheet 484 ms).
    assert totals["new"][0] * 2 < totals["old"][0], totals
    assert totals["new"][1] * 4 < totals["old"][1], totals


def test_the_sheets_carry_every_kind_of_format_the_writers_set(cases):
    """The comparison above means something only if the cells really carry the formats."""
    call = next(c for c in cases["case01"] if c[0] == "check" and c[2][0] is not None)
    app = Excel(call[1])
    _replay({"_write_check_sheet": mc._write_check_sheet}, call, Handles(app))
    sheet = app.state()[mc.CHECK_SHEET]
    kinds = {k for cell in sheet["cells"].values() for k in cell}
    assert {"value", "NumberFormat", "Font.Bold", "Font.Size", "Font.Color", "Interior.Color", "WrapText",
            "HorizontalAlignment", "VerticalAlignment", "Bottom.LineStyle"} <= kinds, kinds
    assert sheet["heights"] and sheet["widths"] and sheet["gridlines"] is False


def test_a_range_joins_a_format_only_where_no_later_format_said_otherwise():
    ops = [("style", "S!A1:B1", {"color": "#111111"}),
           ("style", "S!B1:C1", {"color": "#222222"}),
           ("style", "S!B1", {"color": "#111111"}),       # must not join the first group: B1:C1 came in between
           ("style", "S!D1", {"color": "#111111"}),       # free to join the latest #111111 group
           ("numbers", "S!A2:C2", None)]
    plan = [p for p in paint_plan(ops) if p[1] in ("color", "numbers")]
    assert plan == [("S", "numbers", "A2:C2", excel_com.DEFAULT_NUMBERS),
                    ("S", "color", "A1:B1", "#111111"), ("S", "color", "B1:C1", "#222222"),
                    ("S", "color", "B1,D1", "#111111")]


def test_a_long_list_of_ranges_is_split_under_excel_s_address_limit():
    ops = [("style", f"S!A{r}", {"bold": True}) for r in range(1, 200, 2)]
    plan = [p for p in paint_plan(ops) if p[1] == "bold"]
    assert len(plan) > 1 and all(len(p[2]) <= 255 for p in plan)
    assert ",".join(p[2] for p in plan) == ",".join(f"A{r}" for r in range(1, 200, 2))


def test_a_failing_format_is_skipped_and_the_rest_still_lands():
    app = Excel(["S"])

    class Picky(Handles):
        pass
    bridge = Picky(app)
    real = excel_com._set_format

    def flaky(cells, kind, value):
        if kind == "fill":
            raise RuntimeError("Excel is busy")
        real(cells, kind, value)
    excel_com._set_format = flaky
    try:
        assert bridge.paint([("write", "S!A1", [["x", 1]]), ("style", "S!A1:B1", {"bold": True, "fill": "#FFFFFF"})])
    finally:
        excel_com._set_format = real
    cells = app.state()["S"]["cells"]
    assert cells[(1, 1)]["value"] == "x" and cells[(1, 2)]["Font.Bold"] is True
    assert "Interior.Color" not in cells[(1, 1)]
