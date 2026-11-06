"""Trợ lý bên cạnh: the presenter runs the slide show; Clicky follows it, points on the projector and
helps on the laptop. Qt-free, so the app, the robot and the tests share it.

- Clicky never moves a slide on its own while following. It reads the slide on show (poll) and, for
  that slide, frames the shapes the script names. The scripted line goes to the laptop as a prompt.
- A question (typed on the laptop) is answered from the bank. When the entry names a slide to show,
  Clicky goes there; back() returns to the slide the presenter was on before the question.
- Nothing about Clicky reaches the projector except the pointer and the frames.

Screens are plain (left, top, width, height) tuples in logical pixels so layout() needs no Qt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import scene_log
from present.talk import Minutes, Stop, Talk


@dataclass
class Layout:
    mode: str                     # "extend" | "duplicate"
    projector: Optional[int]      # index of the screen showing the slides
    laptop: Optional[int]         # index of the screen for the ask box; None = nowhere private


def _overlap(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return max(0, w) * max(0, h)


def layout(screens: list, show_window: Optional[tuple], primary: int = 0) -> Layout:
    """Which screen the audience sees and which one only the presenter sees.

    One screen (or two mirrored ones) = duplicate: the audience sees everything, so the ask box must not
    appear. Otherwise the projector is the screen holding most of the slide-show window and the laptop is
    the primary screen if it is not the projector, else the first other screen."""
    distinct = []
    for i, s in enumerate(screens):
        if all(tuple(s) != tuple(screens[j]) for j in distinct):
            distinct.append(i)
    if len(distinct) < 2:
        return Layout("duplicate", distinct[0] if distinct else None, None)
    if show_window:
        projector = max(distinct, key=lambda i: _overlap(screens[i], show_window))
        if _overlap(screens[projector], show_window) == 0:
            projector = next(i for i in distinct if i != primary)
    else:
        projector = next(i for i in distinct if i != primary)
    laptop = primary if primary != projector and primary in distinct else next(i for i in distinct if i != projector)
    return Layout("extend", projector, laptop)


class LiveTalk:
    """frame(rects_with_labels) draws on the projector; clear() wipes it; slide_hint(dict) and answer(dict)
    update the laptop box. rect = physical (left, top, right, bottom) from deck.shape_pixels; the UI maps it."""

    def __init__(self, script: dict, bank: dict, resolver, deck, frame: Callable[[list], None],
                 clear: Callable[[], None], slide_hint: Callable[[dict], None], whatif=None,
                 minutes: Optional[Minutes] = None):
        self._said: list = []
        self.talk = Talk(script, bank, resolver, _PixelDeck(deck), say=lambda shown, spoken: self._said.append(shown),
                         point=lambda rect, label: None, wait=lambda s: None, whatif=whatif, minutes=minutes)
        self.deck = deck
        self.items = {int(s["so"]): s for s in script.get("slides", []) if "so" in s}
        self.frame, self.clear, self.slide_hint = frame, clear, slide_hint
        self.slide = 0
        self.before: Optional[int] = None
        self.last_question = ""

    @property
    def minutes(self) -> Minutes:
        return self.talk.minutes

    # ---- following the show ----------------------------------------------------------------
    def poll(self) -> int:
        """Call often (a few times a second). Returns the slide on show; reacts only when it changed."""
        try:
            n = int(self.deck.current())
        except Exception:                   # noqa: BLE001 - the show ended or is between slides
            return self.slide
        if n != self.slide:
            self.on_slide(n)
        return n

    def on_slide(self, n: int) -> dict:
        self.slide = n
        self.talk.slide = n
        self.clear()
        item = self.items.get(n)
        info = {"slide": n, "text": "", "targets": [], "missing": []}
        if item is not None:
            stops_before = len(self.minutes.stops)
            targets = self.talk.targets(item)
            info["targets"] = [label for _, label in targets]
            info["missed"] = self.minutes.stops[stops_before:]
            filled = self.talk.resolver.fill(item.get("loi_noi", ""))
            info["missing"] = [f"{t}: {m}" for t, m in filled.missing]
            info["text"] = "" if filled.missing else filled.text      # a line with a hole is never shown as said
            if targets:
                self.frame(targets)
        scene_log.record("present_slide", slide=n, targets=info["targets"], rects=[r for r, _ in targets] if item else [],
                         missed=info.get("missed", []), missing=info["missing"])
        self.slide_hint(info)
        return info

    # ---- questions ------------------------------------------------------------------------
    def ask(self, question: str) -> dict:
        self.last_question = question
        if self.before is None:
            self.before = self.slide
        try:
            r = self.talk.answer(question)
        except Stop as exc:
            r = {"id": "", "text": "", "choices": [], "stop": str(exc)}
        return self._after_answer(question, r)

    def pick(self, entry_id: str, label: str) -> dict:
        """The asker picked one of the choices offered (an entry, a department or a channel)."""
        entry = next((e for e in self.talk.bank.entries if e.get("id") == entry_id), None)
        if entry is None:
            return {"id": "", "text": "", "choices": [], "stop": f"không có câu {entry_id}"}
        question = self.last_question
        if label and label != entry.get("hoi_mau", ""):
            question = f"{question} {label}"
        try:
            r = self.talk.answer_entry(entry, question)
        except Stop as exc:
            r = {"id": entry_id, "text": "", "choices": [], "stop": str(exc)}
        return self._after_answer(question, r)

    def _after_answer(self, question: str, r: dict) -> dict:
        r = dict(r)
        if not r.get("text") and self._said and not (r.get("choices") or r.get("ask") or r.get("stop")):
            r["text"] = self._said[-1]
        self._said.clear()
        target = r.get("slide")
        if target and int(target) != self.slide:
            try:
                self.deck.goto(int(target))
            except Exception as exc:        # noqa: BLE001 - say it on the laptop, keep the show where it is
                r["stop"] = f"không mở được slide {target}: {exc}"
            else:
                self.on_slide(int(target))
        scene_log.record("present_answer", question=question, id=r.get("id", ""), text=r.get("text", ""),
                         choices=[c for c in r.get("choices") or []], ask=r.get("ask", ""), stop=r.get("stop", ""),
                         slide=self.slide)
        return r

    def back(self) -> int:
        """Return to the slide the presenter was on before the first question of this round."""
        n = self.before
        self.before = None
        if n and n != self.slide:
            self.deck.goto(n)
            self.on_slide(n)
        scene_log.record("present_back", slide=self.slide)
        return self.slide


class _PixelDeck:
    """Talk asks deck.screen_rect(shape); in the app that must be physical pixels on the show window."""

    def __init__(self, deck):
        self._deck = deck

    def screen_rect(self, shape):
        if hasattr(self._deck, "shape_pixels"):
            return self._deck.shape_pixels(shape)
        x, y, w, h = self._deck.screen_rect(shape)
        return (x, y, x + w, y + h)

    def __getattr__(self, name):
        return getattr(self._deck, name)
