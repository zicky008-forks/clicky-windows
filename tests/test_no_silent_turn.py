"""Grader, Case 05: ~50 s with nothing new on screen. Two fixes kept here: reading a trial balance with
thousands of sub-accounts is no longer quadratic, and every slow stage says its own line, then once more if it
is still running two seconds later (the first Power Query load of a session says why it is slow)."""
import asyncio
import random
import time

from engine import trial_balance
from office import month_close as mc


def _old_leaf(codes):
    return {c: not any(o != c and o.startswith(c) for o in codes) for c in codes}


def test_leaf_accounts_are_the_same_as_the_old_rule():
    rng = random.Random(7)
    codes = {"111", "1111", "1112", "131", "5111", "51111", "511", "521", "632", "6421", "64211", "642111", "911"}
    for _ in range(400):
        codes.add(rng.choice(["111", "131", "331", "511", "642"]) + "".join(rng.choice("0123456789")
                                                                         for _ in range(rng.randint(0, 4))))
    rows = [["Số hiệu TK", "Tên tài khoản", "Nợ đầu kỳ", "Có đầu kỳ", "Phát sinh Nợ", "Phát sinh Có", "Nợ cuối kỳ", "Có cuối kỳ"]]
    rows += [[c, f"TK {c}", 1, 0, 0, 0, 1, 0] for c in sorted(codes)]
    tb = trial_balance.read_trial_balance(rows)
    want = _old_leaf({l.account for l in tb.lines})
    assert {l.account: l.leaf for l in tb.lines} == want
    assert sum(want.values()) < len(want)                      # the sample has parents as well as leaves


def test_a_trial_balance_with_thousands_of_sub_accounts_reads_in_well_under_a_second():
    """3000 sub-accounts took ~10 s per read before (the close reads each trial balance several times)."""
    rows = [["Số hiệu TK", "Tên tài khoản", "Nợ đầu kỳ", "Có đầu kỳ", "Phát sinh Nợ", "Phát sinh Có", "Nợ cuối kỳ", "Có cuối kỳ"]]
    for parent in ("131", "331", "511", "632", "642"):
        rows.append([parent, parent, 0, 0, 0, 0, 0, 0])
        rows += [[f"{parent}{i:04d}", f"Chi tiết {i}", 1, 0, 0, 0, 1, 0] for i in range(1, 1201)]
    t0 = time.perf_counter()
    tb = trial_balance.read_trial_balance(rows)
    assert time.perf_counter() - t0 < 1.5
    assert sum(not l.leaf for l in tb.lines) == 5


class FakeTimer:
    """threading.Timer without the waiting: the test fires it."""
    made = []

    def __init__(self, after, fn, args=()):
        self.after, self.fn, self.args, self.cancelled, self.started = after, fn, args, False, False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args)


def _busy(monkeypatch, loaded_once):
    FakeTimer.made.clear()
    events = []
    monkeypatch.setattr(mc.scene_log, "record", lambda event, **data: events.append((event, data)))
    monkeypatch.setattr(mc, "_LOADED_ONCE", loaded_once)
    shown = []
    return mc.Busy(shown.append, timer=FakeTimer), shown, events


def test_a_stage_still_running_after_two_seconds_says_so_once(monkeypatch):
    busy, shown, events = _busy(monkeypatch, loaded_once=True)
    busy.say("check_sheet")
    assert shown == ["Em đang ghi sheet Kiểm tra…"] and FakeTimer.made[0].after == mc.STILL_AFTER_S == 2.0
    FakeTimer.made[0].fire()
    FakeTimer.made[0].fire()                                   # never twice
    assert shown == ["Em đang ghi sheet Kiểm tra…", "Em đang ghi sheet Kiểm tra, chị chờ em chút nhé…"]
    assert [d for e, d in events if e == "close_step"] == [
        {"step": "caption", "stage": "check_sheet", "still": False},
        {"step": "caption", "stage": "check_sheet", "still": True}]


def test_the_same_stage_said_twice_keeps_its_timer(monkeypatch):
    busy, shown, _ = _busy(monkeypatch, loaded_once=True)
    busy.say("query")
    busy.say("query")
    FakeTimer.made[0].fire()
    assert len(FakeTimer.made) == 1 and shown == [mc.WORKING["query"], mc.still_line("query")]


def test_a_stage_that_ends_in_time_never_says_still(monkeypatch):
    busy, shown, _ = _busy(monkeypatch, loaded_once=True)
    busy.say("read")
    busy.say("load")                                           # the next stage disarms the first one's timer
    FakeTimer.made[0].fire()
    busy.stop()
    FakeTimer.made[1].fire()
    assert shown == [mc.WORKING["read"], mc.WORKING["load"]]
    assert FakeTimer.made[0].cancelled and FakeTimer.made[1].cancelled


def test_the_first_load_of_the_session_says_why_it_is_slow(monkeypatch):
    busy, shown, _ = _busy(monkeypatch, loaded_once=False)
    busy.say("load")
    FakeTimer.made[-1].fire()
    assert shown[-1] == ("Lần đầu chạy Power Query trong phiên Excel này nên mất vài giây, "
                         "chị chờ em chút nhé…")
    busy, shown, _ = _busy(monkeypatch, loaded_once=True)
    busy.say("load")
    FakeTimer.made[-1].fire()
    assert shown[-1] == "Em đang nạp đơn hàng vào Excel, chị chờ em chút nhé…"


def test_after_one_load_the_next_is_not_the_first(monkeypatch):
    monkeypatch.setattr(mc, "_LOADED_ONCE", False)

    class PQ:
        def load(self, name):
            return ("Sheet", ["a"], [])
    mc._load(PQ(), "DonHang")
    assert mc._LOADED_ONCE


def test_every_stage_has_a_line_and_a_still_line():
    for stage in set(mc._STAGE_OF.values()) | {"load"}:
        assert mc.WORKING[stage].startswith("Em đang ") and mc.WORKING[stage].endswith("…")
        assert mc.still_line(stage).endswith("chị chờ em chút nhé…")


def test_a_pressed_button_runs_with_the_stages_said_and_stops_after(monkeypatch):
    FakeTimer.made.clear()
    monkeypatch.setattr(mc.scene_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_LOADED_ONCE", True)
    real = mc.Busy
    monkeypatch.setattr(mc, "Busy", lambda emit: real(emit, timer=FakeTimer))
    shown = []

    class Sig:
        def emit(self, text):
            shown.append(text)

    class Manager:
        sig_caption = Sig()

    def work():
        mc._progress("load")
        FakeTimer.made[-1].fire()                              # Excel still loading two seconds later
        mc._progress("check_sheet")
        return ["done"]
    assert asyncio.run(mc._busy(Manager(), "confirm", work)) == ["done"]
    assert shown == [mc.WORKING["confirm"], mc.WORKING["load"], mc.still_line("load"), mc.WORKING["check_sheet"]]
    assert mc._PROGRESS is None and all(t.cancelled or t is FakeTimer.made[1] for t in FakeTimer.made)
