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

    def refresh_all(self) -> None:
        try:
            self.bridge._excel().ActiveWorkbook.RefreshAll()
        except Exception:
            pass


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
