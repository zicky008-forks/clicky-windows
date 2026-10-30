import json
import re

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
    assert pq_skill.run_fix(bridge, "sửa query giúp anh").startswith("Anh nói rõ")


def test_prompt_context_lists_steps_and_findings():
    text = format_for_prompt([("Ban hang HN", BRANCH_QUERY)], lint("Ban hang HN", BRANCH_QUERY))
    assert "4.Changed Type" in text and "step 'Removed Errors'" in text
    assert pq_skill.culture_from("theo kiểu Việt Nam") == "vi-VN" and pq_skill.culture_from("en-gb") == "en-GB"
