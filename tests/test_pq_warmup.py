"""Cold Excel (first run after boot): the first query write cost ~6 s and the first refresh ~4 s on the press after the
receipt. Clicky now pays that while the receipt is read, in a hidden workbook of its own, never in the user's."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from office import month_close as mc
from office import pq_warmup


class FakeTable:
    def __init__(self, log):
        self.log, self.CommandType, self.CommandText = log, None, None

    def Refresh(self, background):
        self.log.append(("refresh", background, self.CommandText))


class FakeSheet:
    def __init__(self, log):
        self.log = log
        self.ListObjects = SimpleNamespace(Add=self._add)

    def _add(self, *args):
        self.log.append(("list_object", args[1]))
        return SimpleNamespace(QueryTable=FakeTable(self.log))

    def Range(self, ref):
        return ref


class FakeBook:
    def __init__(self, name, log, fail_add=False):
        self.FullName, self.log, self.fail_add = name, log, fail_add
        self._saved = True
        self.Queries = SimpleNamespace(Add=self._add_query)

    @property
    def Saved(self):
        return self._saved

    @Saved.setter
    def Saved(self, value):
        self._saved = value
        self.log.append(("saved", self.FullName, value))

    def _add_query(self, name, formula):
        if self.fail_add:
            raise RuntimeError("Excel is busy")
        self._saved = False                       # Excel marks the book changed
        self.log.append(("query", self.FullName, name))

    def Worksheets(self, i):
        return FakeSheet(self.log)

    def Close(self, save):
        if getattr(self, "gone", False):
            raise RuntimeError("The RPC server is unavailable. (0x800706BA)")
        self.log.append(("close", self.FullName, save, self._saved))

    def Activate(self):
        self.log.append(("activate", self.FullName))


class FakeApp:
    def __init__(self, fail_add=False, open_activates=False):
        self.log = []
        self.user = FakeBook("C:/data/BaoCao_T10.xlsx", self.log)
        self.ActiveWorkbook = self.user
        self.DisplayAlerts = True
        self.fail_add, self.open_activates = fail_add, open_activates
        self.Workbooks = SimpleNamespace(Open=self._open)

    def _open(self, path, update_links, read_only):
        self.log.append(("open", path.endswith("clicky-khoi-dong.xlsx"), update_links, read_only))
        book = FakeBook(path, self.log, self.fail_add)
        if self.open_activates:
            self.ActiveWorkbook = book
        return book


def _bridge(app):
    return SimpleNamespace(_excel=lambda: app)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(pq_warmup, "_warm", False)
    monkeypatch.setattr(pq_warmup, "_thread", None)
    monkeypatch.setattr(mc, "_LOADED_ONCE", False)
    monkeypatch.setenv(pq_warmup.ENV, "1")         # tests/conftest.py turns it off for every other test


def test_the_warm_up_workbook_opens_hidden():
    from openpyxl import load_workbook
    path = pq_warmup.warm_file()
    assert load_workbook(path).views[0].visibility == "hidden"
    assert pq_warmup.warm_file() == path                    # made once


def test_power_query_is_started_in_clickys_own_workbook_and_the_users_is_never_touched():
    app = FakeApp()
    assert pq_warmup.warm(_bridge(app))
    user = app.user.FullName
    assert not [e for e in app.log if user in e]            # no query, no sheet, no close on the user's workbook
    assert app.log[0] == ("open", True, 0, True)            # read-only, no link updates
    assert app.log[1] == ("query", app.log[1][1], pq_warmup.QUERY) and app.log[1][1] != user
    assert app.log[2] == ("saved", app.log[1][1], True)      # quitting Excel never asks to save Clicky's book
    assert ("refresh", False, f"SELECT * FROM [{pq_warmup.QUERY}]") in app.log
    assert app.log[-1][:3] == ("close", app.log[1][1], False) and app.log[-1][3] is True   # unsaved, no prompt
    assert app.DisplayAlerts is True and app.ActiveWorkbook is app.user


def test_the_user_stays_on_their_workbook_if_excel_switches_to_the_hidden_one():
    app = FakeApp(open_activates=True)
    pq_warmup.warm(_bridge(app))
    assert ("activate", app.user.FullName) in app.log


def test_a_busy_excel_only_skips_the_warm_up_and_still_closes_it():
    app = FakeApp(fail_add=True)
    assert not pq_warmup.warm(_bridge(app))
    assert app.log[-1][0] == "close" and not pq_warmup._warm
    assert not pq_warmup.warm(SimpleNamespace(_excel=lambda: None))


def test_started_once_after_the_receipt_and_never_when_off_or_already_loaded(monkeypatch):
    app = FakeApp()
    manager = SimpleNamespace(_excel=_bridge(app))
    monkeypatch.setattr(mc, "stage", lambda: "confirm")
    mc.warm_while_reading(manager)
    assert pq_warmup._thread is None                        # only while the receipt waits for a press
    monkeypatch.setattr(mc, "stage", lambda: "intake")
    monkeypatch.setenv(pq_warmup.ENV, "0")
    mc.warm_while_reading(manager)
    assert pq_warmup._thread is None
    monkeypatch.setenv(pq_warmup.ENV, "1")
    monkeypatch.setattr(mc, "_LOADED_ONCE", True)
    mc.warm_while_reading(manager)
    assert pq_warmup._thread is None
    monkeypatch.setattr(mc, "_LOADED_ONCE", False)
    mc.warm_while_reading(manager)
    pq_warmup.wait()
    assert pq_warmup._warm and mc._LOADED_ONCE              # the first real load is no longer announced as slow
    assert not pq_warmup.start(_bridge(app))                # once per session


def test_a_press_waits_for_the_warm_up_to_close_before_working_on_the_users_workbook(monkeypatch):
    order = []
    release = threading.Event()

    def slow_warm_up():
        release.wait(2)
        order.append("warm-up closed")
    monkeypatch.setattr(pq_warmup, "_thread", threading.Thread(target=slow_warm_up))
    pq_warmup._thread.start()
    monkeypatch.setattr(mc.scene_log, "record", lambda *a, **k: None)
    manager = SimpleNamespace(sig_caption=SimpleNamespace(emit=lambda text: None))
    threading.Timer(0.2, release.set).start()
    t0 = time.monotonic()
    assert asyncio.run(mc._busy(manager, "confirm", lambda: order.append("confirm") or ["done"])) == ["done"]
    assert order == ["warm-up closed", "confirm"] and time.monotonic() - t0 >= 0.15


class ClosingApp(FakeApp):
    """The user quits Excel while the warm-up holds its hidden workbook: every later call fails."""
    def _open(self, *args):
        book = super()._open(*args)
        book.Worksheets = self._quit
        self.book = book
        return book

    def _quit(self, i):
        self.book.gone = True
        raise RuntimeError("The RPC server is unavailable. (0x800706BA)")


def test_excel_closed_during_the_warm_up_leaves_nothing_and_raises_nothing():
    app = ClosingApp()
    assert not pq_warmup.warm(_bridge(app)) and not pq_warmup._warm
    assert ("saved", app.book.FullName, True) in app.log     # marked saved before Excel went: no save prompt
    assert app.DisplayAlerts is True


def test_a_stuck_warm_up_load_gives_up_and_still_closes(monkeypatch):
    from office import power_query
    monkeypatch.setattr(power_query, "refresh_with_timeout", lambda table, timeout: power_query.LoadTimeout("x"))
    app = FakeApp()
    assert not pq_warmup.warm(_bridge(app)) and not pq_warmup._warm
    assert app.log[-1][0] == "close" and app.DisplayAlerts is True


def test_a_press_goes_ahead_after_the_cap_and_the_warm_up_still_closes_later(monkeypatch):
    events, release = [], threading.Event()
    monkeypatch.setattr(pq_warmup, "_part", lambda part, t0, **extra: events.append((part, extra)))

    def stuck():
        release.wait(5)
        events.append(("closed", {}))
    monkeypatch.setattr(pq_warmup, "_thread", threading.Thread(target=stuck))
    pq_warmup._thread.start()
    t0 = time.monotonic()
    pq_warmup.wait(timeout=0.2)
    assert time.monotonic() - t0 < 2 and ("pq_warm_wait", {"timed_out": True}) in events
    release.set()
    pq_warmup._thread.join(2)
    assert events[-1][0] == "closed"
