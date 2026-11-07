"""Run the desktop report skill against a workbook snapshot and return the edits as a plan.

The Excel add-in reads every sheet with Office.js, runs this file in the browser
with Pyodide, then applies the plan with Office.js. The numbers come from the same
engine as the desktop app, and the user's own sheets are never changed.
"""
import json
from decimal import Decimal


class PlanBridge:
    """The subset of office.excel_com.ExcelBridge that office.report_skill uses, over a snapshot."""

    def __init__(self, sheets: dict, order: list, active: str):
        self.sheets, self.order, self.active = sheets, list(order), active
        self.ops = []

    def available(self):
        return True

    def context(self):
        return {"sheet": self.active}

    def sheet_names(self):
        return list(self.order)

    def sheet_rows(self, name):
        return self.sheets.get(name) or []

    def add_sheet(self, name):
        taken = {n.lower() for n in self.order}
        actual, i = name, 2
        while actual.lower() in taken:
            actual, i = f"{name} ({i})", i + 1
        self.order.append(actual)
        self.ops.append({"op": "add_sheet", "name": actual})
        return actual

    def write(self, ref, rows):
        sheet, cell = _split(ref)
        width = max((len(r) for r in rows), default=0)
        grid = [[_cell(v) for v in r] + [""] * (width - len(r)) for r in rows]
        self.ops.append({"op": "write", "sheet": sheet, "cell": cell, "values": grid})
        return True

    def format_numbers(self, ref, fmt="#,##0;(#,##0)"):
        sheet, cells = _split(ref)
        self.ops.append({"op": "format", "sheet": sheet, "range": cells, "format": fmt})
        return True

    def activate(self, name):
        self.ops.append({"op": "activate", "sheet": name})
        return True


def _split(ref: str):
    sheet, cell = ref.rsplit("!", 1)
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, cell


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    return value


def plan(snapshot_json: str) -> str:
    """snapshot: {"sheets": {name: rows}, "order": [...], "active": name} → {"summary", "ops"}."""
    from office import report_skill
    snap = json.loads(snapshot_json)
    bridge = PlanBridge(snap["sheets"], snap["order"], snap.get("active", ""))
    summary = report_skill.run(bridge)
    return json.dumps({"summary": summary, "ops": bridge.ops}, ensure_ascii=False)
