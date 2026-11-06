"""How fast a turn feels: the clock marks the first feedback of each kind and closes when the turn ends."""
import asyncio

import turn_clock
from office import month_close as mc


class _Now:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class _Sig:
    def __init__(self):
        self.slots, self.sent = [], []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        self.sent.append(args)
        for fn in self.slots:
            fn(*args)


class _State:
    def __init__(self, name):
        self.name = name


class _Manager:
    def __init__(self):
        for n in ("sig_response_chunk", "sig_point_at", "sig_draw", "sig_choices", "sig_caption", "sig_state_changed"):
            setattr(self, n, _Sig())
        self._current_task = None


def test_first_feedback_of_each_kind_and_the_end(monkeypatch):
    records = []
    monkeypatch.setattr("scene_log.record", lambda event, **data: records.append((event, data)))
    now, m = _Now(), _Manager()
    clock = turn_clock.TurnClock(now)
    turn_clock.connect(m, clock)
    clock.start("voice")
    now.t += 0.9
    clock.mark("transcript")
    now.t += 0.3
    m.sig_caption.emit("Em đang đọc các file trong thư mục…")
    now.t += 0.5
    m.sig_point_at.emit(1.0, 2.0, "")
    now.t += 0.5
    m.sig_point_at.emit(3.0, 4.0, "")                       # a later point does not move the first one
    m._current_task = object()
    m.sig_state_changed.emit(_State("IDLE"))                # a card read out mid-skill: the turn goes on
    assert clock.running
    m._current_task = None
    now.t += 1.0
    m.sig_state_changed.emit(_State("IDLE"))
    assert not clock.running
    (event, data), = records
    assert event == "latency" and data["source"] == "voice"
    assert (data["transcript"], data["first_caption"], data["first_point"], data["done"]) == (900, 1200, 1700, 3200)
    assert data["first_feedback"] == 1200 and "first_card" not in data


def test_no_turn_no_line(monkeypatch):
    records = []
    monkeypatch.setattr("scene_log.record", lambda event, **data: records.append(event))
    clock = turn_clock.TurnClock(_Now())
    clock.mark("first_point")
    assert clock.finish() is None and records == []


def test_every_slow_month_close_step_says_what_it_is_doing_at_once(monkeypatch):
    m = _Manager()
    m._excel = object()
    shown = []
    m.sig_caption.connect(shown.append)
    monkeypatch.setattr(mc, "_pq", lambda manager: None)
    monkeypatch.setattr(mc, "start", lambda *a: [])
    monkeypatch.setattr(mc, "confirm", lambda *a: [])
    monkeypatch.setattr(mc, "save_and_verify", lambda *a: [])

    async def play(manager, beats):
        shown.append("<card>")
    monkeypatch.setattr(mc, "play", play)
    m.sig_clear_drawings = _Sig()
    asyncio.run(mc.handle_start(m, "làm báo cáo tháng từ thư mục này"))
    asyncio.run(mc.handle_confirm(m, "Đúng rồi, làm tiếp"))
    asyncio.run(mc.handle_save(m, "Lưu và kiểm tra"))
    assert shown == [mc.WORKING["read"], "<card>", mc.WORKING["confirm"], "<card>", mc.WORKING["save"], "<card>"]
