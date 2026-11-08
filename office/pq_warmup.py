"""Warm Excel's Power Query engine while the files and the receipt are read, so the first press is not the slow one.

On a cold Excel (often the first run after the PC boots) the first query written into a workbook costs ~6 s
(Excel loads its Power Query engine) and the first refresh ~4 s more (it starts the engine's container). Warm,
both take well under a second. Clicky pays it while it reads the files and the user reads the receipt: it starts
right after stage 0's last Excel call (month_close.start), and anything needing Excel before it ends waits for it.

The user's workbook is never touched: nothing may change in it before "Đúng rồi, làm tiếp". The warm-up opens a
blank workbook of Clicky's own whose window is saved hidden (so no window appears and it never becomes the active
workbook), adds a one-cell query to it, loads it once, and closes it without saving. Any failure (Excel busy, a
cell being edited, a dialog) only means the first press is slower, as before. Never numbers: the warm-up's query
reads nothing.

CLICKY_PQ_WARMUP=0 turns it off (the rehearsal robot's A/B).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ENV = "CLICKY_PQ_WARMUP"
QUERY = "ClickyKhoiDong"
FORMULA = 'let\n    Nguon = #table({"a"}, {{1}})\nin\n    Nguon'
WAIT_S = 30.0                  # a press never waits longer than this for the warm-up
LOAD_S = 20.0                  # the warm-up's own refresh gives up before that

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_warm = False                  # this Clicky session already warmed (or loaded) Power Query


def enabled() -> bool:
    return os.getenv(ENV, "").strip() != "0"


def warm_file() -> Path:
    """A blank workbook whose only window is saved hidden: opening it shows nothing and leaves the active one."""
    from storage import data_dir
    path = data_dir() / "khoi-dong" / "clicky-khoi-dong.xlsx"
    if not path.exists():
        from openpyxl import Workbook
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        wb.active.title = "KhoiDong"
        wb.views[0].visibility = "hidden"
        wb.save(path)
    return path


def _part(part: str, t0: float, **extra) -> None:
    try:
        import scene_log
        scene_log.record("close_step", step="timing", part=part, ms=int(round((time.monotonic() - t0) * 1000)), **extra)
    except Exception:          # noqa: BLE001 - timing never breaks the step
        pass


def warm(bridge) -> bool:
    """Do the warm-up now, on this thread. True when Power Query was started; False when skipped or failed.

    A skip is logged as pq_warm_skip with why: no_excel (nothing to attach to), late (the load hit its cap), or
    error (Excel busy, a dialog, Excel closed meanwhile)."""
    global _warm
    t_all = time.monotonic()
    app = book = alerts = None
    try:
        app = bridge._excel() if bridge is not None else None
        if app is None:
            _part("pq_warm_skip", t_all, why="no_excel")
            return False
        # None when no workbook window is in front (Excel just started for a dropped folder): there is then
        # nothing to bring back afterwards, and the hidden workbook never becomes the active one either.
        active = app.ActiveWorkbook
        path = str(warm_file())
        alerts = app.DisplayAlerts
        app.DisplayAlerts = False       # off until the workbook is closed: no dialog may wait behind the overlay
        t0 = time.monotonic()
        book = app.Workbooks.Open(path, 0, True)              # UpdateLinks=0, ReadOnly=True
        _part("pq_warm_open", t0, active=active is not None, books=_count(app))
        if active is not None:
            now = app.ActiveWorkbook
            if now is None or now.FullName != active.FullName:
                active.Activate()                              # never leave the user on Clicky's workbook
        t0 = time.monotonic()
        book.Queries.Add(QUERY, FORMULA)
        _unchanged(book)
        _part("pq_warm_add", t0)
        t0 = time.monotonic()
        ws = book.Worksheets(1)
        source = (f'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
                  f'Location="{QUERY}";Extended Properties=""')
        lo = ws.ListObjects.Add(0, source, True, 1, ws.Range("$A$1"))
        lo.QueryTable.CommandType = 2                          # xlCmdSql
        lo.QueryTable.CommandText = f"SELECT * FROM [{QUERY}]"
        from office.power_query import refresh_with_timeout
        late = refresh_with_timeout(lo.QueryTable, LOAD_S)
        _unchanged(book)
        _part("pq_warm_load", t0, late=late is not None)
        if late is not None:
            _part("pq_warm_skip", t_all, why="late")
            return False
        _warm = True
        return True
    except Exception as exc:   # noqa: BLE001 - a busy or closed Excel only means the first press is slower
        from office.excel_com import _why
        log.info("Power Query warm-up skipped: %s", _why(exc))
        _part("pq_warm_skip", t_all, why="error", error=_why(exc))     # the error's kind and code, never its text
        return False
    finally:
        if book is not None:
            try:
                _unchanged(book)
                book.Close(False)
            except Exception as exc:   # noqa: BLE001 - Excel was closed meanwhile: the workbook went with it
                from office.excel_com import _why
                log.info("closing the warm-up workbook failed: %s", _why(exc))
            _part("pq_warm_total", t_all, warm=_warm)
        if app is not None and alerts is not None:
            try:
                app.DisplayAlerts = alerts
            except Exception:  # noqa: BLE001
                pass


def _count(app) -> Optional[int]:
    try:
        return int(app.Workbooks.Count)
    except Exception:          # noqa: BLE001 - only for the log
        return None


def _unchanged(book) -> None:
    """Mark Clicky's hidden workbook as saved, so Excel never asks to save it, even if the user quits Excel
    while it is open."""
    try:
        book.Saved = True
    except Exception:          # noqa: BLE001 - Excel gone or busy: Close(False) or Excel's exit handles it
        pass


class _SameExcel:
    """The Excel the month close is using, handed to the warm-up thread.

    A second GetActiveObject on another thread can miss an Excel that automation has just started (it is not in
    the running-object table yet: the folder was dropped while Excel was closed) or find a different instance.
    So the caller's own Excel object is marshalled across; without COM (tests) the bridge is asked again."""

    def __init__(self, bridge):
        self._bridge, self._stream, self._app = bridge, None, None
        try:
            app = bridge._excel()
        except Exception:      # noqa: BLE001
            app = None
        ole = getattr(app, "_oleobj_", None)
        if ole is not None:
            try:
                import pythoncom
                self._stream = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, ole)
            except Exception:  # noqa: BLE001 - fall back to attaching on the warm-up thread
                self._stream = None

    def _excel(self):
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                import pythoncom
                import win32com.client
                self._app = win32com.client.Dispatch(pythoncom.CoGetInterfaceAndReleaseStream(
                    stream, pythoncom.IID_IDispatch))
            except Exception:  # noqa: BLE001
                self._app = None
        return self._app if self._app is not None else self._bridge._excel()

    def release(self) -> None:
        self._app = None       # before the thread leaves COM


def _run(bridge) -> None:
    com = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        com = True
    except Exception:          # noqa: BLE001 - not on Windows (tests)
        pass
    try:
        if warm(bridge):
            from office import month_close
            month_close._LOADED_ONCE = True        # the first real load is no longer "the first time"
    finally:
        getattr(bridge, "release", lambda: None)()
        if com:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def start(bridge, quiet: bool = False) -> bool:
    """Start the warm-up in the background once per session. True when it was started now. Unless quiet, a
    warm-up that will not run says why in the scene log (pq_warm_skip: off, or warm when already done)."""
    global _thread
    from office import month_close
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        why = "off" if not enabled() else "warm" if _warm or month_close._LOADED_ONCE else ""
        if why:
            if not quiet:
                _part("pq_warm_skip", time.monotonic(), why=why)
            return False
        _thread = threading.Thread(target=_run, args=(_SameExcel(bridge),), name="clicky-pq-warmup", daemon=True)
        _thread.start()
        return True


def running() -> bool:
    thread = _thread
    return thread is not None and thread.is_alive()


def wait(timeout: float = WAIT_S, by: str = "") -> None:
    """Block until a running warm-up is done: Excel serves one caller at a time, and the warm-up must be closed
    before Clicky works on the user's workbook. by names who waited (a step such as "confirm", or "play")."""
    thread = _thread
    if thread is not None and thread.is_alive():
        t0 = time.monotonic()
        thread.join(timeout)
        # Past the cap (Excel stuck for half a minute) the step goes ahead and meets the same stuck Excel with the
        # usual plain error card and "Thử lại" (errors_vi); the warm-up still closes its own workbook when it ends.
        _part("pq_warm_wait", t0, timed_out=thread.is_alive(), **({"by": by} if by else {}))
