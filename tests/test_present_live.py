"""Trợ lý bên cạnh: talk keys that never steal PowerPoint's keys, screen layout, following the show."""
import pytest

from present import hotkeys
from present.live import LiveTalk, layout
from present.placeholders import Resolver
from tests.test_present import BANK, SUMMARY, DictSource, FakeDeck, S


# ---- keys -----------------------------------------------------------------------------------
def test_parse_keys():
    assert hotkeys.parse("ctrl+alt+h") == (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("H"))
    assert hotkeys.parse("Ctrl + Alt + F9") == (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, 0x78)
    with pytest.raises(hotkeys.TalkKeyError):
        hotkeys.parse("ctrl+alt")


@pytest.mark.parametrize("combo", ["b", "h", "w", "space", "pagedown", "ctrl+h", "ctrl+p", "alt+b", "shift+b"])
def test_powerpoint_show_keys_are_refused(combo):
    with pytest.raises(hotkeys.TalkKeyError):
        hotkeys.check(combo)


def test_default_keys_hold_ctrl_and_alt():
    for combo in hotkeys.DEFAULT_KEYS.values():
        hotkeys.check(combo)


class FakeApi:
    """Registers like user32; delivers the queued presses, then WM_QUIT."""

    def __init__(self, taken=()):
        self.taken, self.registered, self.unregistered = set(taken), {}, []
        self.queue = []

    def register(self, ident, mods, vk):
        if vk in self.taken:
            return False
        self.registered[ident] = (mods, vk)
        return True

    def unregister(self, ident):
        self.unregistered.append(ident)

    def thread_id(self):
        return 1

    def next_message(self):
        return self.queue.pop(0) if self.queue else None

    def quit(self, tid):
        pass


def test_talk_keys_dispatch_and_report_taken_keys():
    hits = []
    api = FakeApi(taken={ord("B")})
    api.queue = [(hotkeys.WM_HOTKEY, 1), (0x0100, 1), (hotkeys.WM_HOTKEY, 1)]
    keys = hotkeys.TalkKeys({"ctrl+alt+h": lambda: hits.append("ask"), "ctrl+alt+b": lambda: hits.append("back")}, api=api)
    assert keys.start() == ["ctrl+alt+b"]
    keys._thread.join(2)
    assert hits == ["ask", "ask"] and api.unregistered == [1]


def test_talk_keys_refuse_bad_binding():
    with pytest.raises(hotkeys.TalkKeyError):
        hotkeys.TalkKeys({"b": lambda: None}, api=FakeApi())


# ---- screens --------------------------------------------------------------------------------
LAPTOP, PROJ = (0, 0, 1920, 1080), (1920, 0, 1280, 720)


def test_layout_extend_finds_projector_by_show_window():
    assert layout([LAPTOP, PROJ], (1920, 0, 1280, 720)) == layout([LAPTOP, PROJ], None)
    lay = layout([LAPTOP, PROJ], (1920, 0, 1280, 720))
    assert (lay.mode, lay.projector, lay.laptop) == ("extend", 1, 0)
    lay = layout([PROJ, LAPTOP], (0, 0, 1920, 1080), primary=1)      # show on the primary screen
    assert (lay.mode, lay.projector, lay.laptop) == ("extend", 1, 0)


def test_layout_one_or_mirrored_screen_is_duplicate():
    assert layout([LAPTOP], LAPTOP).mode == "duplicate"
    lay = layout([LAPTOP, LAPTOP], LAPTOP)
    assert lay.mode == "duplicate" and lay.laptop is None


# ---- following the show ----------------------------------------------------------------------
class ShowDeck(FakeDeck):
    def __init__(self, slides):
        super().__init__(slides)
        self.on_show, self.moves = 1, []

    def current(self):
        return self.on_show

    def goto(self, n):
        self.moves.append(n)
        self.on_show = n
        return n


def _live(deck, frames, hints, src=None):
    script = {"slides": [{"so": 1, "loi_noi": "{slide:title}: {tq:cost}.", "chi_vao": [{"shape_chua": "{tq:cost}"}]},
                         {"so": 2, "loi_noi": "Số {tq:khong_co}.", "chi_vao": [{"shape": "tiêu đề"}]}]}
    bank = dict(BANK, cau_hoi=BANK["cau_hoi"] + [
        {"id": "JUMP", "hoi_mau": "Cắt đều?", "tu_khoa": {"va": [["cat deu"]]}, "tra_loi": "Bước hai {tq:s1}.", "nhay_slide": 2}])
    return LiveTalk(script, bank, Resolver(src or DictSource(SUMMARY)), deck, frame=frames.append,
                    clear=lambda: frames.append("clear"), slide_hint=hints.append)


def test_follow_show_frames_and_prompts_without_moving_slides():
    deck = ShowDeck([[S(1, "Thông điệp", is_title=True), S(2, "AOPR 520,0", top=100)], [S(1, "Cắt", is_title=True)]])
    frames, hints = [], []
    live = _live(deck, frames, hints)
    assert live.poll() == 1 and live.poll() == 1                  # a second poll on the same slide does nothing
    assert len(hints) == 1 and hints[0]["text"] == "Thông điệp: 520,0 tỷ." and hints[0]["targets"] == ["520,0 tỷ"]
    assert frames == ["clear", [((1, 2, 4, 6), "520,0 tỷ")]] and deck.moves == []
    deck.on_show = 2                                              # the presenter pressed Space
    live.poll()
    assert hints[-1]["text"] == "" and hints[-1]["missing"]       # a line with a hole is never shown as said
    assert deck.moves == []


def test_question_jumps_and_back_returns():
    deck = ShowDeck([[S(1, "Thông điệp", is_title=True)], [S(1, "Cắt", is_title=True)]])
    frames, hints = [], []
    live = _live(deck, frames, hints)
    live.poll()
    r = live.ask("Cắt đều thế nào?")
    assert r["text"] == "Bước hai 2,0 tỷ." and deck.on_show == 2 and live.slide == 2
    assert live.ask("Tổng chi phí AOPR là bao nhiêu?")["text"] == "Tổng 520,0 tỷ."
    assert live.back() == 1 and deck.moves == [2, 1]
    assert live.back() == 1 and deck.moves == [2, 1]              # nothing to go back to
    assert live.ask("Giá đường?")["text"] == "Câu này em ghi lại."
    assert live.minutes.open_questions == ["Giá đường?"]


def test_pick_department_after_clarifying_question():
    src = DictSource(SUMMARY)
    src.rows = lambda sheet, first, cols: [["P. IT"], ["P. Brand MKT"], ["P. Trade MKT"]]
    src.cells["De_xuat!B4"], src.cells["De_xuat!B6"] = 3.0, 1.5
    deck = ShowDeck([[S(1, "T", is_title=True)]])
    live = _live(deck, [], [], src)
    r = live.ask("Phòng MKT xin bao nhiêu?")
    assert r["choices"] and r["ask"]
    assert live.pick("DEPT", "P. Trade MKT")["text"] == "P. Trade MKT: 1,5 tỷ."


# ---- the app glue (Qt, offscreen) -------------------------------------------------------------
class Sig:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def emit(self, *args):
        self.log.append((self.name, *args))


class FakeManager:
    def __init__(self):
        self.log = []
        for n in ("sig_draw", "sig_point_hold", "sig_point_at", "sig_clear_drawings", "sig_point_release",
                  "sig_notice", "sig_error"):
            setattr(self, n, Sig(self.log, n))


class AppDeck(ShowDeck):
    def __init__(self, slides):
        super().__init__(slides)
        self.focused, self.on = 0, True

    def count(self):
        return len(self.slides)

    def running(self):
        return self.on

    def focus_show(self):
        self.focused += 1


def test_present_args():
    from ui.present_mode import present_args
    argv = ["Clicky.exe", "--present-spec", "D", "--present-xlsx", "a.xlsx", "--present-deck", "d.pptx"]
    assert present_args(argv) == {"spec": "D", "xlsx": "a.xlsx", "deck": "d.pptx"}
    assert present_args(["Clicky.exe", "--present-spec", "D"]) is None


def test_present_mode_projector_gets_frames_laptop_gets_words(qt_app):
    from present.live import Layout
    from ui.present_mode import PresentMode
    manager = FakeManager()
    mode = PresentMode(manager, "spec", "a.xlsx", "d.pptx")
    mode._to_logical = lambda rect: rect
    deck = AppDeck([[S(1, "Thông điệp", is_title=True), S(2, "AOPR 520,0", top=100)], [S(1, "Cắt", is_title=True)]])
    mode.bridge, mode.total, mode.layout = deck, 2, Layout("extend", 1, 0)
    mode.live = _live(deck, [], [])
    mode.live.frame, mode.live.clear, mode.live.slide_hint = mode._frame, mode._clear, mode._hint
    mode._tick()
    drawn = [e for e in manager.log if e[0] == "sig_draw"]
    assert drawn and drawn[0][1]["kind"] == "rect"
    assert ("sig_point_hold", True) in manager.log
    pointed = [e for e in manager.log if e[0] == "sig_point_at"]
    assert pointed and pointed[0][3].strip("⁣") == ""          # no words on the projector
    assert mode.panel.header.text() == "Slide 1/2" and mode.panel.prompt.text() == "Thông điệp: 520,0 tỷ."
    mode._on_asked("Cắt đều thế nào?")
    assert mode.panel.answer.text() == "Bước hai 2,0 tỷ." and deck.on_show == 2
    mode._on_back_key()
    assert deck.on_show == 1 and deck.focused == 1 and mode.panel.answer.text() == ""
    deck.on = False                                                     # the presenter ended the show
    mode._tick()
    assert mode.live is None and mode.panel.isHidden()


def test_present_mode_duplicate_screen_never_shows_the_ask_box(qt_app):
    from present.live import Layout
    from ui.present_mode import PresentMode
    manager = FakeManager()
    mode = PresentMode(manager, "spec", "a.xlsx", "d.pptx")
    mode.layout = Layout("duplicate", 0, None)
    mode._on_ask_key()
    assert mode.panel.isHidden() and manager.log[-1][0] == "sig_notice"
