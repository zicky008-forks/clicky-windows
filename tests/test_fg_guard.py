"""User's PC, 9114deb: after switching to another app, Clicky's card and orange frame stayed painted over that
app at the old screen spot. The guard hides the drawing layer while another app is in front of Excel, shows it
again when Excel comes back, and drops frames drawn for a window that has since moved."""
import asyncio

from ui.fg_guard import EXCEL, OTHER, OWN, Guard


def _guard(front, content=True):
    seen = {"hide": [], "clear": 0, "log": []}
    state = {"front": front, "content": content}
    g = Guard(lambda: state["front"], lambda: state["content"], seen["hide"].append,
              lambda: seen.__setitem__("clear", seen["clear"] + 1), lambda e, **f: seen["log"].append((e, f)))
    return g, state, seen


def test_another_app_in_front_hides_the_story_and_excel_brings_it_back():
    g, state, seen = _guard((EXCEL, (0, 0, 800, 600)))
    g.tick()
    assert seen["hide"] == [] and g.excel_front
    state["front"] = (OTHER, None)
    g.tick(); g.tick()
    assert seen["hide"] == [True] and not g.excel_front
    assert seen["log"] == [("overlay_hidden", {"reason": "focus"})]
    state["front"] = (EXCEL, (0, 0, 800, 600))
    g.tick()
    assert seen["hide"] == [True, False] and g.excel_front and seen["clear"] == 0
    assert seen["log"][-1] == ("overlay_shown", {"reason": "focus"})


def test_clickys_own_card_in_front_changes_nothing():
    g, state, seen = _guard((EXCEL, (0, 0, 800, 600)))
    g.tick()
    state["front"] = (OWN, None)
    g.tick()
    assert seen["hide"] == [] and g.excel_front


def test_a_moved_excel_window_drops_the_old_frames():
    g, state, seen = _guard((EXCEL, (0, 0, 800, 600)))
    g.tick()
    state["front"] = (EXCEL, (100, 50, 900, 650))
    g.tick()
    assert seen["clear"] == 1 and seen["log"][-1][0] == "overlay_hidden" and seen["log"][-1][1]["reason"] == "moved"


def test_nothing_drawn_nothing_hidden():
    g, state, seen = _guard((OTHER, None), content=False)
    g.tick()
    assert seen["hide"] == [] and g.excel_front


def test_the_story_waits_at_the_sentence_boundary_until_excel_is_back(monkeypatch):
    from office import month_close as mc
    logged = []
    monkeypatch.setattr(mc.scene_log, "record", lambda e, **f: logged.append(e))

    class M:
        _excel_front = False
        _cancel_flag = False
    m = M()

    async def run():
        waiter = asyncio.create_task(mc._excel_in_front(m, 3))
        await asyncio.sleep(0.3)
        assert not waiter.done()
        m._excel_front = True
        await asyncio.wait_for(waiter, 1)
    asyncio.run(run())
    assert logged == ["story_paused", "story_resumed"]
