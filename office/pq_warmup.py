"""Warm Excel's Power Query engine while the user reads the receipt, so the first press is not the slow one.

On a cold Excel (often the first run after the PC boots) the first query written into a workbook costs ~6 s
(Excel loads its Power Query engine) and the first refresh ~4 s more (it starts the engine's container). Warm,
both take well under a second. The user spends that time reading the receipt anyway, so Clicky pays it then.

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
    """Do the warm-up now, on this thread. True when Power Query was started; False when skipped or failed."""
    global _warm
    t_all = time.monotonic()
    app = book = alerts = None
    try:
        app = bridge._excel() if bridge is not None else None
        if app is None:
            return False
        active = app.ActiveWorkbook
        if active is None:
            return False
        path = str(warm_file())
        alerts = app.DisplayAlerts
        app.DisplayAlerts = False       # off until the workbook is closed: no dialog may wait behind the overlay
        t0 = time.monotonic()
        book = app.Workbooks.Open(path, 0, True)              # UpdateLinks=0, ReadOnly=True
        _part("pq_warm_open", t0)
        if app.ActiveWorkbook.FullName != active.FullName:
            active.Activate()                                  # never leave the user on Clicky's workbook
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
            return False
        _warm = True
        return True
    except Exception as exc:   # noqa: BLE001 - a busy or closed Excel only means the first press is slower
        from office.excel_com import _why
        log.info("Power Query warm-up skipped: %s", _why(exc))
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


def _unchanged(book) -> None:
    """Mark Clicky's hidden workbook as saved, so Excel never asks to save it, even if the user quits Excel
    while it is open."""
    try:
        book.Saved = True
    except Exception:          # noqa: BLE001 - Excel gone or busy: Close(False) or Excel's exit handles it
        pass


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
        if com:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def start(bridge) -> bool:
    """Start the warm-up in the background once per session. True when it was started now."""
    global _thread
    from office import month_close
    with _lock:
        if not enabled() or _warm or month_close._LOADED_ONCE or (_thread is not None and _thread.is_alive()):
            return False
        _thread = threading.Thread(target=_run, args=(bridge,), name="clicky-pq-warmup", daemon=True)
        _thread.start()
        return True


def wait(timeout: float = WAIT_S) -> None:
    """Block until a running warm-up is done: Excel serves one caller at a time, and the warm-up must be closed
    before Clicky works on the user's workbook."""
    thread = _thread
    if thread is not None and thread.is_alive():
        t0 = time.monotonic()
        thread.join(timeout)
        # Past the cap (Excel stuck for half a minute) the step goes ahead and meets the same stuck Excel with the
        # usual plain error card and "Thử lại" (errors_vi); the warm-up still closes its own workbook when it ends.
        _part("pq_warm_wait", t0, timed_out=thread.is_alive())
