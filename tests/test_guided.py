"""Guided first months: permission before the user's own files, safe self-presses, Stop, month count."""
import asyncio

import pytest

from office import drop, guided, month_close as mc


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    store = {}
    monkeypatch.setattr(guided, "setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(guided, "set_setting", lambda k, v: store.__setitem__(k, v))
    guided.reset()
    yield store
    guided.reset()


class Sig:
    def __init__(self):
        self.got = []

    def emit(self, *a):
        self.got.append(a)


class Manager:
    def __init__(self):
        self.sig_caption = Sig()
        self.sig_drop_zone = Sig()
        self.pressed = []
        self._month_close_run = None

    def submit_text(self, text):
        self.pressed.append(text)


def test_month_one_offers_do_it_first_and_month_two_offers_show_me_first(_fresh):
    assert guided.consent_choices()[0] == guided.DO_IT
    beat = guided.consent_beat()
    assert "bản gốc" in beat.text.lower() and "Dừng" in beat.text and beat.choices[-1] == guided.LATER
    _fresh[guided.MONTHS_KEY] = 1
    assert guided.consent_choices()[0] == guided.SHOW_ME
    assert "Tháng thứ 2" in guided.consent_beat().text


def test_the_first_drop_asks_before_anything_is_read(monkeypatch):
    shown, read = [], []

    async def play(manager, beats, keep_card=True):
        shown.extend(beats)
    monkeypatch.setattr(mc, "play", play)
    monkeypatch.setattr(drop, "prepare", lambda *a: read.append(a) or (None, []))
    m = Manager()
    m._excel = object()
    asyncio.run(drop.handle(m, ["C:/x/TB_T10.xlsx"]))
    assert not read, "nothing may be opened or read before the user answers"
    assert shown and shown[0].choices == guided.consent_choices()
    assert guided._STATE["held"] == ["C:/x/TB_T10.xlsx"]


def test_later_reads_nothing_and_asks_again_next_drop(monkeypatch):
    shown = []

    async def play(manager, beats, keep_card=True):
        shown.extend(beats)
    monkeypatch.setattr(mc, "play", play)
    guided.hold(["a.xlsx"])
    asyncio.run(guided.handle_consent(Manager(), guided.LATER))
    assert "chưa mở file nào" in shown[-1].text
    assert guided.needs_consent() and guided._STATE["held"] is None


def test_consent_goes_on_with_the_held_files(monkeypatch):
    went = []

    async def handle(manager, paths):
        went.append(paths)
    monkeypatch.setattr(drop, "handle", handle)
    guided.hold(["a.xlsx", "b.xlsx"])
    asyncio.run(guided.handle_consent(Manager(), guided.DO_IT))
    assert went == [["a.xlsx", "b.xlsx"]] and guided.mode() == guided.DO_IT


def test_consent_without_files_opens_the_drop_zone():
    m = Manager()
    asyncio.run(guided.handle_consent(m, "cho phép"))
    assert m.sig_drop_zone.got and guided.mode() == guided.DO_IT


@pytest.mark.parametrize("choices, pressed", [
    (mc.SAVE_CHOICES, "Lưu và kiểm tra"),
    (mc.FIX_CHOICES, "Sửa giúp tôi"),
    (mc.INTAKE_CHOICES, None),                 # "is what I read right?" is always the user's
    (("Dùng TB_T10", "Dùng TB_T10 (2)"), None),  # which file: the user's
    (mc.STOP_CHOICES, None),
])
def test_vera_presses_only_mechanical_buttons(choices, pressed):
    guided.grant(guided.DO_IT)
    assert guided.next_press(choices) == pressed


def test_nothing_is_pressed_without_consent_or_in_show_me_mode():
    assert guided.next_press(mc.SAVE_CHOICES) is None
    guided.grant(guided.SHOW_ME)
    assert guided.next_press(mc.SAVE_CHOICES) is None
    assert guided.nudge(mc.SAVE_CHOICES) == "Bước tiếp: chị bấm “Lưu và kiểm tra”."


def test_the_countdown_presses_after_the_delay(monkeypatch):
    monkeypatch.setattr(guided, "COUNTDOWN_S", 0.05)
    guided.grant(guided.DO_IT)
    m, run = Manager(), object()
    m._month_close_run = run

    async def go():
        guided.after_question(m, run, mc.SAVE_CHOICES)
        await asyncio.sleep(0.15)
    asyncio.run(go())
    assert m.pressed == ["Lưu và kiểm tra"]
    assert "Dừng" in m.sig_caption.got[0][0]


def test_stop_cancels_the_countdown_and_vera_presses_nothing_more(monkeypatch):
    monkeypatch.setattr(guided, "COUNTDOWN_S", 0.05)
    guided.grant(guided.DO_IT)
    m, run = Manager(), object()
    m._month_close_run = run

    async def go():
        guided.after_question(m, run, mc.SAVE_CHOICES)
        assert guided.halt() is True
        await asyncio.sleep(0.15)
        guided.after_question(m, run, mc.SAVE_CHOICES)
        await asyncio.sleep(0.15)
    asyncio.run(go())
    assert m.pressed == [] and guided.mode() == guided.SHOW_ME


def test_a_new_story_makes_an_old_countdown_harmless(monkeypatch):
    monkeypatch.setattr(guided, "COUNTDOWN_S", 0.05)
    guided.grant(guided.DO_IT)
    m, run = Manager(), object()
    m._month_close_run = run

    async def go():
        guided.after_question(m, run, mc.SAVE_CHOICES)
        m._month_close_run = object()          # the user pressed something else; a new story started
        await asyncio.sleep(0.15)
    asyncio.run(go())
    assert m.pressed == []


def test_a_delivered_own_month_counts_once(_fresh):
    guided.delivered()
    assert _fresh.get(guided.MONTHS_KEY) is None     # the sample, or a run without consent, never counts
    guided.grant(guided.DO_IT)
    guided.delivered()
    guided.delivered()
    assert _fresh[guided.MONTHS_KEY] == 1


def test_consent_is_never_written_to_disk(_fresh):
    guided.grant(guided.DO_IT)
    assert set(_fresh) <= {guided.MONTHS_KEY}


def test_the_permission_answers_reach_the_guided_skill():
    import re
    for label in guided.consent_choices() + ("cho phép", "Chưa, để lúc khác."):
        assert re.search(guided.CONSENT_TRIGGER, label, re.IGNORECASE)
    assert not re.search(guided.CONSENT_TRIGGER, "Để sau", re.IGNORECASE)   # the feedback card's own button
