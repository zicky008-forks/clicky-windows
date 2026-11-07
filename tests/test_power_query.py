import json
import re
import sys

import pytest

from engine.m_code import add_culture, lint, steps
from office.power_query import PowerQuery, format_for_prompt
from office import pq_skill

BRANCH_QUERY = '''let
    Source = Excel.Workbook(File.Contents("C:\\Users\\ke.toan\\Desktop\\HN_T10.xlsx"), null, true),
    Data = Source{[Item="Data",Kind="Sheet"]}[Data],
    #"Promoted Headers" = Table.PromoteHeaders(Data, [PromoteAllScalars=true]),
    // Power Query tự thêm bước này
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers",{{"Ngày", type date}, {"Doanh thu", type number}, {"Ghi chú in", type text}}),
    #"Removed Errors" = Table.RemoveRowsWithErrors(#"Changed Type", {"Ngày"}),
    Merged = Table.NestedJoin(#"Removed Errors", {"Mã KH"}, KhachHang, {"Mã"}, "KH", JoinKind.Inner),
    Dedup = Table.Distinct(Merged)
in
    Dedup'''
CLEAN_QUERY = '''let
    Source = Excel.CurrentWorkbook(){[Name="KhachHang"]}[Content],
    Typed = Table.TransformColumnTypes(Source, {{"Mã", type text}})
in
    Typed'''


def test_steps_match_applied_steps_pane():
    names = [s.name for s in steps(BRANCH_QUERY)]
    assert names == ["Source", "Data", "Promoted Headers", "Changed Type", "Removed Errors", "Merged", "Dedup"]
    assert steps('Excel.CurrentWorkbook(){[Name="T"]}[Content]')[0].name == "Source"


def test_lint_finds_the_quiet_traps():
    found = {(f.code, f.step) for f in lint("Ban hang HN", BRANCH_QUERY)}
    assert found == {("fixed_path", "Source"), ("locale_types", "Changed Type"), ("remove_errors", "Removed Errors"),
                     ("inner_join", "Merged"), ("distinct_all", "Dedup")}
    assert lint("KhachHang", CLEAN_QUERY) == []           # text-only typing has no locale risk


def test_add_culture_rewrites_only_risky_type_steps():
    new, changed = add_culture(BRANCH_QUERY, "en-US")
    assert changed == ["Changed Type"]
    assert '{"Ghi chú in", type text}}, "en-US")' in new
    assert "locale_types" not in {f.code for f in lint("x", new)}
    assert add_culture(new, "en-US")[1] == []             # already has a culture: unchanged
    assert add_culture(CLEAN_QUERY, "vi-VN") == (CLEAN_QUERY, [])


class FakeQuery:
    def __init__(self, name, formula):
        self.Name, self.Formula = name, formula


class FakeQueries:
    def __init__(self, items):
        self.items = items
        self.Count = len(items)

    def Item(self, key):
        return self.items[key - 1] if isinstance(key, int) else next(q for q in self.items if q.Name == key)


class FakeBridge:
    def __init__(self, queries):
        self.queries = FakeQueries([FakeQuery(n, f) for n, f in queries])
        wb = type("WB", (), {"Name": "BanHang.xlsx", "Queries": self.queries})()
        self.app = type("App", (), {"ActiveWorkbook": wb})()
        self.written = {}

    def _excel(self):
        return self.app

    def available(self):
        return True

    def add_sheet(self, name):
        return name

    def write(self, ref, grid):
        self.written[ref] = grid
        return True


def test_check_skill_writes_findings_and_points_to_worst():
    bridge = FakeBridge([("Ban hang HN", BRANCH_QUERY), ("KhachHang", CLEAN_QUERY)])
    text = pq_skill.run_check(bridge)
    assert text.startswith("Em kiểm tra 2 query, thấy 5 điểm cần xem, trong đó 1 lỗi.")
    assert "bước Removed Errors" in text and "kiểu Mỹ" in text
    grid = bridge.written["'Kiem tra PQ'!A1"]
    assert len(grid) == 6 and grid[1][0] == "Ban hang HN"


def test_fix_skill_backs_up_then_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKY_DATA_DIR", str(tmp_path))
    bridge = FakeBridge([("Ban hang HN", BRANCH_QUERY), ("KhachHang", CLEAN_QUERY)])
    pattern = re.compile(pq_skill.FIX_TRIGGER, re.IGNORECASE)
    assert pattern.search("sửa query theo kiểu Mỹ giúp anh")
    text = pq_skill.run_fix(bridge, "sửa query theo kiểu Mỹ giúp anh")
    assert "en-US" in text and "Ban hang HN (bước Changed Type)" in text
    assert '"en-US")' in bridge.queries.Item("Ban hang HN").Formula
    assert bridge.queries.Item("KhachHang").Formula == CLEAN_QUERY
    backup = json.loads((tmp_path / "power_query_backups.json").read_text(encoding="utf-8"))
    assert backup[0]["query"] == "Ban hang HN" and backup[0]["formula"] == BRANCH_QUERY
    assert pq_skill.run_fix(bridge, "sửa query giúp anh").startswith("Chị nói rõ")


def test_prompt_context_lists_steps_and_findings():
    text = format_for_prompt([("Ban hang HN", BRANCH_QUERY)], lint("Ban hang HN", BRANCH_QUERY))
    assert "4.Changed Type" in text and "step 'Removed Errors'" in text
    assert pq_skill.culture_from("theo kiểu Việt Nam") == "vi-VN" and pq_skill.culture_from("en-gb") == "en-GB"


def test_add_creates_new_or_replaces_with_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKY_DATA_DIR", str(tmp_path))
    bridge = FakeBridge([("KhachHang", CLEAN_QUERY)])
    added = []
    bridge.queries.Add = lambda name, formula: added.append(FakeQuery(name, formula))
    pq = PowerQuery(bridge)
    monkeypatch.setattr(FakeQueries, "__len__", lambda self: self.Count, raising=False)   # COM collections are falsy when empty
    empty = FakeBridge([])
    empty.queries.Add = lambda name, formula: added.append(FakeQuery(name, formula))
    assert PowerQuery(empty).add("DonHang", BRANCH_QUERY) and added[0].Name == "DonHang"
    assert pq.add("DonHang", BRANCH_QUERY)
    assert pq.add("KhachHang", BRANCH_QUERY)
    assert bridge.queries.Item("KhachHang").Formula == BRANCH_QUERY
    backup = json.loads((tmp_path / "power_query_backups.json").read_text(encoding="utf-8"))
    assert backup[-1]["formula"] == CLEAN_QUERY


def test_a_refresh_is_waited_for_at_most_the_timeout_and_names_the_dialog_excel_shows():
    import time
    from office.power_query import LoadTimeout, refresh_with_timeout

    class Table:
        def __init__(self, seconds, error=None):
            self.seconds, self.error, self.done = seconds, error, False

        def Refresh(self, background):
            assert background is False
            time.sleep(self.seconds)
            if self.error:
                raise self.error
            self.done = True
    fast = Table(0)
    assert refresh_with_timeout(fast, timeout=5, poll=0.05, dialogs=list) is None and fast.done
    seen = []
    late = refresh_with_timeout(Table(2), timeout=0.3, poll=0.05, dialogs=lambda: ["Truy cập Nội dung Web"],
                                step=lambda event, **data: seen.append((event, data["dialog"])))
    assert isinstance(late, LoadTimeout) and late.dialog == "Truy cập Nội dung Web" and late.seconds == 0.3
    assert seen == [("pq_dialog", "Truy cập Nội dung Web")]
    quiet = refresh_with_timeout(Table(2), timeout=0.2, poll=0.05, dialogs=list)
    assert isinstance(quiet, LoadTimeout) and quiet.dialog == ""
    try:
        refresh_with_timeout(Table(0, ValueError("DataFormat.Error")), timeout=5, poll=0.05, dialogs=list)
    except ValueError as exc:
        assert "DataFormat" in str(exc)
    else:
        raise AssertionError("a refresh error must reach the caller, as before")


def test_load_returns_the_timeout_and_puts_excel_alerts_back(monkeypatch):
    import time
    from office import power_query
    from office.power_query import LoadTimeout, PowerQuery

    class QT:
        CommandText = 'SELECT * FROM [DonHang]'

        def Refresh(self, background):
            time.sleep(1)

    class LO:
        QueryTable = QT()

    class Objects:
        Count = 1

        def __call__(self, i):
            return LO()

    class Sheet:
        ListObjects = Objects()

    class Sheets:
        Count = 1

        def __call__(self, i):
            return Sheet()

    class Book:
        Worksheets = Sheets()

    class App:
        ActiveWorkbook = Book()
        DisplayAlerts = True

    class Bridge:
        app = App()

        def _excel(self):
            return self.app
    monkeypatch.setattr(power_query, "excel_dialogs", list)
    bridge = Bridge()
    out = PowerQuery(bridge).load("DonHang", timeout=0.2, step=lambda *a, **k: None)
    assert isinstance(out, LoadTimeout) and bridge.app.DisplayAlerts is True


@pytest.mark.skipif(sys.platform != "win32", reason="COM marshalling exists on Windows only")
def test_a_real_com_object_is_marshalled_to_the_worker_thread():
    """Runs on the Windows CI runner, which has no Excel: a COM object every Windows has proves the
    CoMarshalInterThreadInterfaceInStream path the Power Query refresh takes."""
    import pythoncom
    import win32com.client
    from office.power_query import call_with_timeout
    pythoncom.CoInitialize()
    fso = win32com.client.Dispatch("Scripting.FileSystemObject")
    late, name = call_with_timeout(fso, "GetTempName", (), timeout=20, poll=0.1, dialogs=list)
    assert late is None and str(name).lower().endswith(".tmp")


def test_adding_a_query_logs_where_its_time_goes_and_skips_an_unchanged_m(tmp_path, monkeypatch):
    # PC 9ea1aa4: the first query add of an Excel session took 3.6 s. Split it: finding the query, the backup,
    # and Excel taking the M. The same M (line endings aside) is not written again and logs skipped=true.
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    monkeypatch.setenv("CLICKY_DATA_DIR", str(tmp_path))
    bridge = FakeBridge([("DonHang", BRANCH_QUERY.replace("\n", "\r\n"))])
    added = []
    bridge.queries.Add = lambda name, formula: added.append(FakeQuery(name, formula))
    pq = PowerQuery(bridge)
    assert pq.add("DonHang", BRANCH_QUERY)                         # same M, Excel's CRLF: nothing written
    assert bridge.queries.Item("DonHang").Formula == BRANCH_QUERY.replace("\n", "\r\n")
    assert not (tmp_path / "power_query_backups.json").exists()
    assert pq.add("DonHang", CLEAN_QUERY)                          # a real change: backed up, then written
    assert bridge.queries.Item("DonHang").Formula == CLEAN_QUERY
    assert pq.add("KhachHang", CLEAN_QUERY) and added[0].Name == "KhachHang"
    parts = [(e["part"], e.get("skipped"), e.get("found"), e.get("new")) for e in map(json.loads, log.read_text(encoding="utf-8").splitlines())
             if e.get("step") == "timing"]
    assert parts == [("pq_find", None, True, None), ("pq_read", None, None, None), ("formula_write", True, None, None),
                     ("pq_find", None, True, None), ("pq_read", None, None, None), ("pq_backup", None, None, None),
                     ("formula_write", False, None, None),
                     ("pq_find", None, False, None), ("formula_write", False, None, True)]
