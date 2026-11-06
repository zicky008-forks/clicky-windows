"""Power Query in the open workbook over COM: list queries, read and replace M, keep a backup before any change.

Workbook.Queries (Excel 2016+) exposes each query's Name and Formula; Office.js
cannot read M, which is one reason this app talks to Excel over COM.
"""
import json
import logging
import os
import sys
import threading
import time
from typing import Callable, Optional

from engine import m_code
from storage import atomic_write, data_dir

log = logging.getLogger(__name__)
MAX_PROMPT_CHARS = 6000
# A query over a folder of exports loads in seconds; minutes mean Excel is waiting on someone (a credentials,
# privacy-level or "file in use" dialog) or is stuck. Clicky stops waiting and says why, never hangs.
LOAD_TIMEOUT = float(os.environ.get("CLICKY_PQ_TIMEOUT", "180") or 180)


def _scene(event: str, **data) -> None:
    import scene_log
    scene_log.record(event, **data)


class LoadTimeout(str):
    """Excel did not finish loading the query in time. .seconds waited, .dialog = the title of the dialog Excel
    showed meanwhile ('' when none was seen)."""
    seconds = 0.0
    dialog = ""


def excel_dialogs() -> list:
    """Titles of the dialogs Excel shows right now ('Truy cập Nội dung Web', 'Privacy levels', ...). A disabled
    main window means a modal dialog is up even when it has no title. [] off Windows or when none."""
    if sys.platform != "win32":
        return []
    try:
        import win32gui
        import win32process
        main = win32gui.FindWindow("XLMAIN", None)
        if not main:
            return []
        _, pid = win32process.GetWindowThreadProcessId(main)
        found = []

        def visit(hwnd, _):
            if hwnd != main and win32gui.IsWindowVisible(hwnd) and \
                    win32process.GetWindowThreadProcessId(hwnd)[1] == pid and win32gui.GetWindow(hwnd, 4) == main:
                title = win32gui.GetWindowText(hwnd).strip()       # GW_OWNER: a window Excel's main window owns
                if title:
                    found.append(title)
            return True
        win32gui.EnumWindows(visit, None)
        if not found and not win32gui.IsWindowEnabled(main):
            found.append("hộp thoại")
        return found
    except Exception as exc:                  # noqa: BLE001 - watching is best effort, never a failure
        log.debug("listing Excel dialogs failed: %s", exc)
        return []


def refresh_with_timeout(table, timeout: float = None, poll: float = 1.0,
                         dialogs: Optional[Callable[[], list]] = None, step=None) -> Optional[LoadTimeout]:
    """table.Refresh(False) on a worker thread, waited for at most `timeout` seconds. None when it finished;
    a LoadTimeout when it did not (the refresh is left to finish or fail inside Excel). An error raised by the
    refresh is raised here, as before. step(event, **data) is told when a dialog shows up while waiting."""
    timeout = LOAD_TIMEOUT if timeout is None else timeout
    dialogs = dialogs or excel_dialogs
    result, stream = {}, None
    oleobj = getattr(table, "_oleobj_", None)
    if oleobj is not None:                    # a COM object belongs to its thread: hand it over marshalled
        import pythoncom
        stream = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, oleobj)

    def work():
        com = False
        try:
            target = table
            if stream is not None:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                com = True
                target = win32com.client.Dispatch(
                    pythoncom.CoGetInterfaceAndReleaseStream(stream, pythoncom.IID_IDispatch))
            target.Refresh(False)
            result["ok"] = True
        except Exception as exc:              # noqa: BLE001 - handed back to the caller
            result["error"] = exc
        finally:
            if com:
                import pythoncom
                pythoncom.CoUninitialize()

    worker = threading.Thread(target=work, name="clicky-pq-refresh", daemon=True)
    start = time.monotonic()
    worker.start()
    seen = ""
    while True:
        worker.join(max(0.01, min(poll, timeout - (time.monotonic() - start))))
        if not worker.is_alive():
            break
        if not seen:
            titles = dialogs() or []
            if titles:
                seen = titles[0]
                if step:
                    step("pq_dialog", dialog=seen, seconds=round(time.monotonic() - start, 1))
        if time.monotonic() - start >= timeout:
            out = LoadTimeout(f"Power Query refresh did not finish in {timeout:.0f}s"
                              + (f" (Excel dialog: {seen})" if seen else ""))
            out.seconds, out.dialog = timeout, seen
            return out
    if "error" in result:
        raise result["error"]
    return None


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

    def load(self, name: str, timeout: float = None, step=None) -> tuple:
        """Load (or refresh) a query into a sheet table and return (sheet name, header, rows) or an error string.

        Uses the Mashup OLE DB provider the same way Close & Load does, and waits for
        the refresh to finish so the rows read back are the query's real output; at most `timeout` seconds,
        then a LoadTimeout comes back instead of a hang.
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
                late = refresh_with_timeout(lo.QueryTable, timeout, step=step or _scene)
            finally:
                try:
                    app.DisplayAlerts = alerts
                except Exception as exc:        # noqa: BLE001 - Excel still busy with a late refresh
                    log.debug("restoring DisplayAlerts failed: %s", exc)
            if late is not None:
                log.warning("Power Query load of %s stopped: %s", name, late)
                return late
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
