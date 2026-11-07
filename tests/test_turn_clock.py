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


def test_code_turns_say_when_the_answer_was_ready_and_when_sound_started(monkeypatch):
    now = _Now()
    clock = turn_clock.TurnClock(now)
    m = _Manager()
    started = []
    import types
    import sys
    fake = types.ModuleType("audio.playback")
    fake.on_play_start = started.append
    monkeypatch.setitem(sys.modules, "audio.playback", fake)
    turn_clock.connect(m, clock)
    clock.start("typed", "Đúng rồi, làm tiếp")
    now.t += 4.0
    clock.mark("ready")                     # the reconcile took 4 s
    now.t += 0.3
    m.sig_point_at.emit(1, 2, "")
    now.t += 0.9
    started[0]()                            # the voice is heard
    result = clock.finish()
    assert result["mode"] == "code" and result["label"] == "Đúng rồi, làm tiếp"
    assert result["ready"] == 4000 and result["first_point"] == 4300 and result["first_voice"] == 5200
    assert result["first_point"] - result["ready"] <= 1500    # the screen follows the answer at once


def test_label_stays_out_of_clicky_log(caplog):
    now = _Now()
    clock = turn_clock.TurnClock(now)
    clock.start("typed", "doanh thu Công ty ABC")
    now.t += 1
    with caplog.at_level("INFO", logger="clicky.latency"):
        result = clock.finish()
    assert result["label"] == "doanh thu Công ty ABC" and result["mode"] == "code"
    assert "ABC" not in caplog.text and "mode=code" in caplog.text


class _Future:
    """A task's future: done callbacks run when it ends (done, failed or cancelled)."""
    def __init__(self):
        self.callbacks = []

    def add_done_callback(self, fn):
        self.callbacks.append(fn)

    def end(self):
        for fn in self.callbacks:
            fn(self)


def test_overlapping_turns_each_keep_their_own_line(monkeypatch):
    # PC gate on 263556f: the robot pressed "Đúng rồi, làm tiếp" while the typed turn was still reading its
    # receipt, and the press's start overwrote the typed turn's clock, so one line per lap went missing.
    records = []
    monkeypatch.setattr("scene_log.record", lambda event, **data: records.append(data) if event == "latency" else None)
    now, m = _Now(), _Manager()
    clock = turn_clock.TurnClock(now)
    turn_clock.connect(m, clock)
    typed, first_task = clock.start("typed", "Lập báo cáo tháng"), _Future()
    clock.bind(first_task, typed)
    now.t += 0.4
    m.sig_choices.emit("Phiếu nhận file", ["Đúng rồi, làm tiếp"])
    now.t += 1.0
    press, second_task = clock.start("typed", "Đúng rồi, làm tiếp"), _Future()   # pressed mid-read
    clock.bind(second_task, press)
    first_task.end()                          # the cancelled turn's task ends after the press began
    m.sig_state_changed.emit(_State("IDLE"))  # and its clean-up reports idle: the press goes on
    assert clock.running and len(records) == 1
    assert records[0]["label"] == "Lập báo cáo tháng" and records[0]["cut"] is True
    assert records[0]["first_card"] == 400 and records[0]["done"] == 1400
    now.t += 0.2
    m.sig_point_at.emit(1.0, 2.0, "")
    now.t += 2.0
    second_task.end()
    assert not clock.running and len(records) == 2
    assert records[1]["label"] == "Đúng rồi, làm tiếp" and "cut" not in records[1]
    assert (records[1]["first_point"], records[1]["done"]) == (200, 2200)


def test_a_bound_turn_ends_with_its_task_not_with_the_first_idle(monkeypatch):
    records = []
    monkeypatch.setattr("scene_log.record", lambda event, **data: records.append(data) if event == "latency" else None)
    now, m = _Now(), _Manager()
    clock = turn_clock.TurnClock(now)
    turn_clock.connect(m, clock)
    task = _Future()
    clock.bind(task, clock.start("typed", "Sửa giúp tôi"))
    m.sig_state_changed.emit(_State("IDLE"))
    assert clock.running and records == []
    now.t += 3.0
    task.end()
    task.end()                                # a second callback for the same turn writes nothing more
    assert len(records) == 1 and records[0]["done"] == 3000


def test_every_sample_press_is_a_turn_with_its_own_clock(monkeypatch):
    from office import sample
    started = []

    class Clock:
        def start(self, source, label=""):
            started.append((source, label))

    class Manager:
        _clock = Clock()
        sig_turn_started = _Sig()
        sig_clear_drawings = _Sig()

    async def no_wait(_s):
        return None
    monkeypatch.setattr(sample.asyncio, "sleep", no_wait)
    asyncio.run(sample._press(Manager(), "Đúng rồi, làm tiếp"))
    assert started == [("button", "Đúng rồi, làm tiếp")] and Manager.sig_turn_started.sent == [("",)]


def test_the_confirm_turn_logs_its_dispatch_its_end_and_its_first_card(monkeypatch):
    # PC lap 1 on dcf6bca: 2.67 s were covered by no mark. These three name the time outside confirm().
    records = []
    monkeypatch.setattr("scene_log.record", lambda event, **data: records.append((event, data)))
    now = _Now()
    m = _Manager()
    for n in ("sig_clear_drawings", "sig_point_hold", "sig_point_release", "sig_guided"):
        setattr(m, n, _Sig())
    m._clock = turn_clock.TurnClock(now)
    m._clock.start("typed", "Đúng rồi, làm tiếp")
    m._excel = object()
    now.t += 0.25

    def confirm(_bridge, _pq):
        now.t += 3.0
        return [mc.Beat("Excel khớp sổ cái.", "'Doi chieu'!C7", "Khớp", "ok", "0 đ")]

    async def locate(_m, _ref):
        now.t += 0.4
        return (10.0, 10.0, 50.0, 20.0)

    async def figures(_m):
        return [], []

    async def say(_text):
        return True
    m._reply_local = say
    monkeypatch.setattr(mc, "confirm", confirm)
    monkeypatch.setattr(mc, "_pq", lambda _m: None)
    monkeypatch.setattr(mc, "_locate", locate)
    monkeypatch.setattr(mc, "_figures", figures)
    monkeypatch.setattr(mc, "BEAT_GAP_S", 0)
    asyncio.run(mc.handle_confirm(m, "Đúng rồi, làm tiếp"))
    parts = {d["part"]: d["ms"] for e, d in records if e == "close_step" and d.get("step") == "timing"}
    assert parts["to_confirm"] == 250 and parts["confirm_done"] == 3250 and parts["first_card"] == 3650
