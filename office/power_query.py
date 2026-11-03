"""Power Query in the open workbook over COM: list queries, read and replace M, keep a backup before any change.

Workbook.Queries (Excel 2016+) exposes each query's Name and Formula; Office.js
cannot read M, which is one reason this app talks to Excel over COM.
"""
import json
import logging
import time
from typing import Optional

from engine import m_code
from storage import atomic_write, data_dir

log = logging.getLogger(__name__)
MAX_PROMPT_CHARS = 6000


class PowerQuery:
    def __init__(self, bridge):
        self.bridge = bridge          # office.excel_com.ExcelBridge (or a fake in tests)

    def _queries(self):
        app = self.bridge._excel()
        return app.ActiveWorkbook.Queries if app is not None and app.ActiveWorkbook is not None else None

    def list(self) -> list:
        """[(name, formula)] for every query in the active workbook."""
        try:
            qs = self._queries()
            return [(qs.Item(i + 1).Name, qs.Item(i + 1).Formula) for i in range(qs.Count)] if qs else []
        except Exception as exc:
            log.debug("Power Query list failed: %s", exc)
            return []

    def lint_all(self) -> list:
        return [f for name, formula in self.list() for f in m_code.lint(name, formula)]

    def replace(self, name: str, formula: str) -> bool:
        """Replace one query's M after saving the old text to the backup log."""
        qs = self._queries()
        if not qs:
            return False
        try:
            query = qs.Item(name)
            self._backup(name, query.Formula)
            query.Formula = formula
            return True
        except Exception as exc:
            log.warning("Power Query replace failed for %s: %s", name, exc)
            return False

    def _backup(self, name: str, formula: str) -> None:
        path = data_dir() / "power_query_backups.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            rows = []
        app = self.bridge._excel()
        rows.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "workbook": app.ActiveWorkbook.Name,
                     "query": name, "formula": formula})
        atomic_write(path, json.dumps(rows[-200:], ensure_ascii=False, indent=1).encode("utf-8"))

    def add(self, name: str, formula: str) -> bool:
        """Add a query, or replace its M (with a backup) when one with that name exists."""
        qs = self._queries()
        if qs is None:                                  # not "if not qs": an empty COM collection is falsy
            return False
        if any(qs.Item(i + 1).Name == name for i in range(qs.Count)):
            return self.replace(name, formula)
        try:
            qs.Add(name, formula)
            return True
        except Exception as exc:
            log.warning("Power Query add failed for %s: %s", name, exc)
            return False

    def table_for(self, name: str):
        """The sheet table (ListObject) that loads this query, or None."""
        app = self.bridge._excel()
        try:
            wb = app.ActiveWorkbook
            for i in range(wb.Worksheets.Count):
                ws = wb.Worksheets(i + 1)
                for j in range(ws.ListObjects.Count):
                    lo = ws.ListObjects(j + 1)
                    try:
                        text = str(lo.QueryTable.CommandText)
                    except Exception:
                        continue
                    if f"[{name}]" in text or f'Location={name};' in text or f'Location="{name}"' in text:
                        return lo
        except Exception as exc:
            log.debug("table_for %s failed: %s", name, exc)
        return None

    def load(self, name: str) -> tuple:
        """Load (or refresh) a query into a sheet table and return (sheet name, header, rows) or an error string.

        Uses the Mashup OLE DB provider the same way Close & Load does, and waits for
        the refresh to finish so the rows read back are the query's real output.
        """
        app = self.bridge._excel()
        try:
            lo = self.table_for(name)
            if lo is None:
                sheet = self.bridge.add_sheet(name[:28])
                ws = app.ActiveWorkbook.Worksheets(sheet)
                source = (f'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
                          f'Location="{name}";Extended Properties=""')
                lo = ws.ListObjects.Add(0, source, True, 1, ws.Range("$A$1"))
                qt = lo.QueryTable
                qt.CommandType = 2                      # xlCmdSql
                qt.CommandText = f"SELECT * FROM [{name}]"
                qt.RowNumbers = False
                qt.RefreshStyle = 1                     # xlInsertDeleteCells
                qt.AdjustColumnWidth = True
            # No Excel dialog may sit behind the overlay waiting for a click: errors come back as exceptions.
            alerts = app.DisplayAlerts
            app.DisplayAlerts = False
            try:
                lo.QueryTable.Refresh(False)
            finally:
                app.DisplayAlerts = alerts
        except Exception as exc:
            log.warning("Power Query load failed for %s: %s", name, exc)
            return str(exc)
        try:
            # Value2 gives dates as serial numbers, so reading never depends on
            # pywin32's date conversion; the checks only use amounts and voucher numbers.
            grid = lo.Range.Value2
            rows = [list(r) for r in grid] if isinstance(grid, tuple) else [[grid]]
            return lo.Parent.Name, rows[0], rows[1:]
        except Exception as exc:
            log.warning("Reading the loaded table for %s failed: %s", name, exc)
            return ReadFailed(str(exc))

    def refresh_all(self) -> None:
        try:
            self.bridge._excel().ActiveWorkbook.RefreshAll()
        except Exception:
            pass


class ReadFailed(str):
    """Excel loaded the query but Clicky could not read the table back: Clicky's fault, not the query's."""


def format_for_prompt(queries: list, findings: list) -> str:
    """Query names, applied steps and lint findings for the model, bounded."""
    if not queries:
        return ""
    lines = ["\n\nPOWER QUERY (read from the workbook, exact):"]
    for name, formula in queries:
        names = [s.name for s in m_code.steps(formula)]
        lines.append(f"  query '{name}': steps " + " → ".join(f"{i + 1}.{n}" for i, n in enumerate(names)))
    if findings:
        lines.append("  Traps found by the checker (cite them, do not invent others as facts):")
        for f in findings[:20]:
            lines.append(f"    [{f.severity}] {f.query} / step '{f.step}': {f.message}")
    text = "\n".join(lines)
    return text[:MAX_PROMPT_CHARS]
