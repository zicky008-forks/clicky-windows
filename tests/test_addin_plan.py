import json
import sys
from pathlib import Path

from tb_samples import t9_rows, t10_rows

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addin"))
import workbook_plan  # noqa: E402


def snapshot(sheets, active):
    return json.dumps({"sheets": sheets, "order": list(sheets), "active": active})


def test_addin_plan_matches_desktop_report_and_leaves_user_sheets_alone():
    sheets = {"T10": t10_rows(), "T09": t9_rows(), "BC thang": [["ghi chú của anh"]]}
    result = json.loads(workbook_plan.plan(snapshot(sheets, "T10")))
    assert result["summary"].startswith("Lợi nhuận sau thuế kỳ này 556 triệu")
    added = [op["name"] for op in result["ops"] if op["op"] == "add_sheet"]
    assert added == ["BC thang (2)", "Cau hoi xac minh", "Kiem tra", "TB ky nay", "TB ky truoc"]
    writes = {op["sheet"] for op in result["ops"] if op["op"] == "write"}
    assert writes == set(added)                                   # never writes into T10, T09 or BC thang
    report = next(op for op in result["ops"] if op["op"] == "write" and op["sheet"] == "BC thang (2)")
    assert len({len(row) for row in report["values"]}) == 1         # rectangular, as Office.js requires
    assert all(not isinstance(v, (list, dict)) for row in report["values"] for v in row)
    assert result["ops"][-1] == {"op": "activate", "sheet": "BC thang (2)"}


def test_addin_plan_explains_when_two_periods_are_missing():
    result = json.loads(workbook_plan.plan(snapshot({"T10": t10_rows()}, "T10")))
    assert result["ops"] == [] and result["summary"].startswith("Anh mở file Excel")
