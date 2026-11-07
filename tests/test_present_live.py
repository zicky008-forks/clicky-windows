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


# ---- the show must start, or the presenter is told why (robot run 07/11: ended 15 ms after start) ----------
class _View:
    def __init__(self, state=1):
        self.State = state


class _ShowWindow:
    def __init__(self, pres):
        self.Presentation, self.View = pres, _View()


class _Settings:
    def __init__(self, app, pres):
        self.app, self.pres, self.StartingSlide = app, pres, 1

    def Run(self):
        self.app.runs += 1
        if self.app.refuse:
            raise RuntimeError("PowerPoint busy")
        if self.app.ignore_run:             # cold PowerPoint, PC 07/11: the call returns, no show ever comes
            return _Stale()
        window = _ShowWindow(self.pres)
        if self.app.late:                   # the show window exists only from the second look on
            self.app.pending.append(window)
            return _Stale()
        self.app.windows.append(window)
        return window


class _Stale:
    @property
    def View(self):
        raise RuntimeError("not ready")


class _DocWindow:
    def __init__(self, app):
        self.app = app

    def Activate(self):
        self.app.activated += 1


class _Pres:
    def Close(self):
        self.app.closed += 1

    def Windows(self, i):
        return _DocWindow(self.app)

    def __init__(self, app, path):
        self.app = app
        self.FullName, self.Slides = app.reported(path), type("S", (), {"Count": 4})()
        self.Name = app.reported_name(path)
        self.SlideShowSettings = _Settings(app, self)

    @property
    def SlideShowWindow(self):
        mine = [w for w in self.app.windows if w.Presentation is self]
        if not mine:
            raise RuntimeError("no slide show")
        return mine[0]


class _Windows:
    def __init__(self, app):
        self.app = app

    @property
    def Count(self):
        self.app.windows += self.app.pending
        self.app.pending = []
        return len(self.app.windows)

    def __call__(self, i):
        return self.app.windows[i - 1]


class _Presentations:
    def __init__(self, app):
        self.app = app

    def Open(self, path, read_only, untitled, with_window):
        assert read_only and with_window and not untitled
        self.app.opened += 1
        return _Pres(self.app, path)


class _PowerPoint:
    """A fake PowerPoint over COM. It has no window classes at all: a show exists only as SlideShowWindows."""
    def __init__(self, late=False, refuse=False, reported=None, reported_name=None, ignore_run=False):
        self.late, self.refuse, self.ignore_run = late, refuse, ignore_run
        self.Path, self.Visible, self.closed = "", 0, 0
        self.reported = reported or (lambda path: path)
        self.reported_name = reported_name or (lambda path: path.replace("/", "\\").rsplit("\\", 1)[-1])
        self.windows, self.pending, self.runs, self.opened, self.activated = [], [], 0, 0, 0
        self.Presentations, self.SlideShowWindows = _Presentations(self), _Windows(self)


def _mode_with(app, monkeypatch, tmp_path, launch=None):
    from present.deck import PowerPointBridge
    from ui import present_mode
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(tmp_path / "scene.jsonl"))
    manager = FakeManager()
    mode = present_mode.PresentMode(manager, "spec", "a.xlsx", r"C:\case\deck.pptx")
    bridge = PowerPointBridge(app, launch=launch)
    mode.attached = bridge.attach(mode.deck_path)
    mode.bridge = bridge
    try:
        bridge.start(1)
    except Exception as exc:                # what start() does
        bridge.last_error = type(exc).__name__
    minutes = type("Minutes", (), {"markdown": lambda self: "# biên bản"})()
    mode.live = type("Live", (), {"poll": lambda self: None, "minutes": minutes})()
    import storage
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(present_mode.time, "monotonic", lambda: clock[0])
    mode._started = clock[0]
    return mode, manager, clock


def _events(tmp_path):
    import json
    p = tmp_path / "scene.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_a_show_window_that_comes_late_is_followed_not_taken_for_the_end(qt_app, monkeypatch, tmp_path):
    app = _PowerPoint(late=True)
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path)
    assert app.opened == 1 and not mode.attached              # opened read-only with a window, show started
    mode._tick()                                              # the first look: window not there yet
    assert mode.live is not None and mode.bridge.running()    # found on that look and followed
    assert any(e.get("event") == "present_attached" for e in _events(tmp_path))
    mode.bridge.show.View.State = 5                           # the presenter ends it
    mode._tick()
    assert mode.live is None


def test_when_powerpoint_will_not_show_the_presenter_gets_a_card_never_a_silent_end(qt_app, monkeypatch, tmp_path):
    from ui import present_mode
    app = _PowerPoint(refuse=True)
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path)
    finished = []
    mode.finished.connect(finished.append)
    mode._tick()
    assert mode.live is not None and not finished             # still waiting, not ended
    clock[0] += present_mode.RETRY_AFTER_S
    mode._tick()
    assert app.runs == 2                                      # started once more
    clock[0] += present_mode.START_GRACE_S
    mode._tick()
    assert finished == [""] and mode.live is None
    assert ("sig_notice", present_mode.NO_SHOW) in manager.log and "PowerPoint" in mode.panel.warning.text()
    failed = [e for e in _events(tmp_path) if e.get("event") == "present_failed"]
    assert failed and failed[0]["stage"] == "show" and failed[0]["why"] == "RuntimeError"
    assert [e["ok"] for e in _events(tmp_path) if e.get("event") == "present_retry"] == [False]


def test_a_onedrive_deck_whose_full_name_is_a_web_address_is_followed_by_its_file_name(qt_app, monkeypatch, tmp_path):
    """bhc_e2e 84603e7 stage 4: the show was started (retry ok) yet never found. PowerPoint gives a deck in a
    synced folder a web address as FullName, so the full-path match missed. SlideShowWindows.Count = 1 and the
    deck's own file name confirm it through COM, with no window class anywhere."""
    app = _PowerPoint(late=True, reported=lambda path: "https://d.docs.live.net/4f1c/case/deck.pptx")
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path)
    assert not mode.attached
    mode._tick()
    assert mode.live is not None and mode.bridge.running() and int(app.SlideShowWindows.Count) == 1
    attached = [e for e in _events(tmp_path) if e.get("event") == "present_attached"]
    assert attached and attached[0]["how"] == "name"
    assert not any(e.get("event") == "present_failed" for e in _events(tmp_path))


def test_the_deck_clicky_opened_confirms_its_own_show_when_no_name_matches(qt_app, monkeypatch, tmp_path):
    """A show whose deck reads under another name and path (short path, renamed copy): Presentation.SlideShowWindow
    of the copy Clicky opened still says the show is this deck's."""
    app = _PowerPoint(late=True, reported=lambda path: r"C:\\PROGRA~1\\x\\DECK~1.PPT",
                      reported_name=lambda path: "DECK~1.PPT")
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path)
    mode._tick()
    attached = [e for e in _events(tmp_path) if e.get("event") == "present_attached"]
    assert attached and attached[0]["how"] == "presentation" and mode.live is not None


def test_a_failed_show_logs_how_many_shows_powerpoint_had_and_never_a_deck_name(qt_app, monkeypatch, tmp_path):
    import json
    from ui import present_mode
    app = _PowerPoint(refuse=True)
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path)
    clock[0] += present_mode.START_GRACE_S
    mode._tick()
    failed = [e for e in _events(tmp_path) if e.get("event") == "present_failed"]
    assert failed and failed[0]["shows"] == 0 and failed[0]["same_name"] == 0
    assert "deck" not in json.dumps(failed[0])


def test_a_cold_powerpoint_that_ignores_run_gets_its_own_command_line_show(qt_app, monkeypatch, tmp_path):
    """bhc_e2e 84603e7 stage 4 on the PC: PowerPoint was not running, the deck opened read-only in editing view,
    Run() returned (the retry said ok=true) and no show ever appeared. Now: ok only when SlideShowWindows says
    so; Run once more with the deck's window forward; then POWERPNT /S; the show it opens is followed."""
    import os
    from ui import present_mode
    app = _PowerPoint(ignore_run=True)
    app.Path = str(tmp_path)
    (tmp_path / "POWERPNT.EXE").write_bytes(b"")
    launched = []

    def launch(argv):
        launched.append(argv)
        app.windows.append(_ShowWindow(_Pres(app, argv[-1])))   # PowerPoint opens the deck straight into a show

    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path, launch)
    assert app.Visible == -1                                     # a COM-started PowerPoint is made visible
    mode._tick()
    clock[0] += present_mode.RETRY_AFTER_S
    mode._tick()
    retry = [e for e in _events(tmp_path) if e.get("event") == "present_retry"]
    assert app.runs == 2 and app.activated == 1 and [e["ok"] for e in retry] == [False]   # not a false ok
    clock[0] += present_mode.COMMAND_LINE_AFTER_S - present_mode.RETRY_AFTER_S
    mode._tick()
    assert launched == [[os.path.join(str(tmp_path), "POWERPNT.EXE"), "/S", r"C:\case\deck.pptx"]]
    assert app.closed == 1                                       # Clicky's read-only copy, closed unsaved
    retry = [e for e in _events(tmp_path) if e.get("event") == "present_retry"]
    assert [(e["attempt"], e["how"], e["sent"], e["ok"]) for e in retry] == [
        (1, "run_again", True, False), (2, "command_line", True, True)]
    attached = [e for e in _events(tmp_path) if e.get("event") == "present_attached"]
    assert attached and attached[0]["how"] == "path" and attached[0]["started_by"] == "command_line"
    assert mode.live is not None and mode.bridge.running()
    clock[0] += present_mode.START_GRACE_S
    mode._tick()
    assert not any(e.get("event") == "present_failed" for e in _events(tmp_path)) and mode.live is not None


def test_a_show_that_comes_a_moment_after_the_command_line_is_credited_to_it(qt_app, monkeypatch, tmp_path):
    """PC fb08855: both retries said ok=false (no show yet at that instant), the show attached 0.5 s later.
    The retry says the call was sent; present_attached says which start brought the show."""
    from ui import present_mode
    app = _PowerPoint(ignore_run=True)
    app.Path = str(tmp_path)
    (tmp_path / "POWERPNT.EXE").write_bytes(b"")
    later = []
    mode, manager, clock = _mode_with(app, monkeypatch, tmp_path, lambda argv: later.append(argv[-1]))
    clock[0] += present_mode.RETRY_AFTER_S
    mode._tick()
    clock[0] += present_mode.COMMAND_LINE_AFTER_S - present_mode.RETRY_AFTER_S
    mode._tick()
    app.windows.append(_ShowWindow(_Pres(app, later[0])))       # PowerPoint needed a moment
    clock[0] += 0.5
    mode._tick()
    retry = [e for e in _events(tmp_path) if e.get("event") == "present_retry"]
    assert [(e["sent"], e["ok"]) for e in retry] == [(True, False), (True, False)]
    attached = [e for e in _events(tmp_path) if e.get("event") == "present_attached"]
    assert len(attached) == 1 and attached[0]["started_by"] == "command_line"


class _NoHwndShow:
    """A show window as PowerPoint gives it on the test PC: Left/Top/Width/Height in points, HWND refused."""
    Left, Top, Width, Height = 0.0, 30.75, 960.0, 540.0

    @property
    def HWND(self):
        raise RuntimeError("no HWND")


def _bridge_on(show):
    from present.deck import PowerPointBridge
    b = PowerPointBridge(app=object())
    b.pres = type("P", (), {"PageSetup": type("PS", (), {"SlideWidth": 960.0, "SlideHeight": 540.0})()})()
    b.show = show
    return b


def test_without_an_hwnd_the_frame_uses_the_real_dpi_and_wraps_the_number(monkeypatch):
    """bhc_e2e fb08855 slide 3 at 150 %: the frame [77,290,442,444] sat above '0,5 tỷ' (text at y 455–520).
    The fallback turned PowerPoint's points into pixels at a fixed 96 dpi; at 150 % it is 144."""
    from present import deck
    from present.deck import ShapeInfo
    monkeypatch.setattr(deck, "system_dpi", lambda: 144)
    b = _bridge_on(_NoHwndShow())
    box = ShapeInfo(3, "TextBox 2", 58, 187, 274, 115, "0,5 tỷ")          # the bhc_case deck's slide 3
    left, top, right, bottom = b.shape_pixels(box)
    assert b.rect_from == "points" and b.hwnd_error.startswith(("RuntimeError", "AttributeError"))
    assert top <= 455 and bottom >= 520 and left == 116 and right == 664, (left, top, right, bottom)


def test_a_text_shape_is_framed_on_its_text_not_its_box(monkeypatch):
    """A box anchored at the bottom, taller than its text: the frame goes round the text PowerPoint reports."""
    from present import deck
    from present.deck import ShapeInfo
    monkeypatch.setattr(deck, "system_dpi", lambda: 96)
    b = _bridge_on(_NoHwndShow())
    box = ShapeInfo(3, "TextBox 2", 58, 187, 274, 200, "0,5 tỷ", text_box=(70, 330, 120, 50))
    l, t, r, btm = b.shape_pixels(box)
    k = 96 / 72
    assert (l, t) == (round(70 * k), round((30.75 + 330) * k)) and btm - t == round(50 * k)
    chart = ShapeInfo(4, "Chart", 58, 187, 274, 200, "", is_chart=True, text_box=(70, 330, 120, 50))
    assert b.shape_pixels(chart)[1] == round((30.75 + 187) * k)          # charts keep their own box


def test_describe_reads_the_text_bounds_from_powerpoint():
    from present.deck import describe
    rng = type("R", (), {"Text": "0,5 tỷ", "BoundLeft": 70.0, "BoundTop": 330.0, "BoundWidth": 120.0, "BoundHeight": 50.0})()
    tf = type("TF", (), {"HasText": True, "TextRange": rng})()
    shape = type("S", (), {"HasTextFrame": True, "TextFrame": tf, "Type": 17, "Name": "TextBox 2",
                           "Left": 58.0, "Top": 187.0, "Width": 274.0, "Height": 200.0})()
    info = describe(shape, 3)
    assert info.text == "0,5 tỷ" and info.text_box == (70.0, 330.0, 120.0, 50.0)
    assert (info.framed.top, info.framed.height) == (330.0, 50.0)


def test_hwnd_given_as_a_callable_stub_is_read_as_a_property(monkeypatch):
    """Test PC 07/11: late-bound HWND is a stub that raises 'Member not found' when called; the property get works."""
    import sys
    import types
    from present.deck import show_hwnd
    fake = types.ModuleType("pythoncom")
    fake.DISPATCH_PROPERTYGET = 2
    monkeypatch.setitem(sys.modules, "pythoncom", fake)

    class Ole:
        def GetIDsOfNames(self, name):
            assert name == "HWND"
            return 7

        def Invoke(self, dispid, lcid, flags, result):
            assert (dispid, flags, result) == (7, 2, True)
            return 0x1234

    def stub():
        raise RuntimeError("Member not found")

    win = type("W", (), {"HWND": staticmethod(stub), "_oleobj_": Ole()})()
    assert show_hwnd(win) == 0x1234
    assert show_hwnd(type("W", (), {"HWND": 99})()) == 99
