"""How fast Clicky answers a turn, measured the way the user feels it.

The clock starts when the user lets go of the talk key, presses Enter, presses a card button or
drops files, and marks the first time each kind of feedback reaches the screen:

    transcript    speech turned into text (voice turns only)
    context       Excel / screen context gathered, the AI request about to leave (AI turns only)
    ready         code finished computing and the story starts playing (code turns: first_point - ready
                  is how fast the screen follows once the answer exists)
    first_text    first words in the chat panel
    first_point   the cursor flies to a cell or a point
    first_draw    the first box or shape on the sheet
    first_card    the first card with buttons
    first_caption the first sentence shown under the cursor (and read aloud when a voice is on)
    first_voice   the first sound of Clicky's voice (after synthesis), when a voice is on
    done          Clicky is idle again

At "done" one line goes to clicky.log and, in rehearsals, a "latency" event to the scene log, in
milliseconds since the start, with mode "ai" when the turn asked the AI and "code" when code alone
answered (the month close, the sample, the buttons). Nothing is sent anywhere.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

_log = logging.getLogger("clicky.latency")

MARKS = ("transcript", "context", "ready", "first_text", "first_point", "first_draw", "first_card", "first_caption",
         "first_voice", "done")


class TurnClock:
    def __init__(self, now=time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._t0: Optional[float] = None
        self._source = ""
        self._label = ""
        self._marks: dict = {}

    @property
    def running(self) -> bool:
        return self._t0 is not None

    def start(self, source: str, label: str = "") -> None:
        """source: voice / typed / button / drop; label: what was asked (a button's text), for the report."""
        with self._lock:
            self._t0, self._source, self._label, self._marks = self._now(), source, label[:60], {}

    def mark(self, name: str) -> None:
        """Keep the first time only: later chunks, points or cards of the same turn do not move it."""
        with self._lock:
            if self._t0 is None or name in self._marks:
                return
            self._marks[name] = int(round((self._now() - self._t0) * 1000))

    def finish(self) -> Optional[dict]:
        """Close the turn; returns what was measured (None when no turn was running)."""
        with self._lock:
            if self._t0 is None:
                return None
            self._marks.setdefault("done", int(round((self._now() - self._t0) * 1000)))
            result = {"source": self._source, "label": self._label,
                      "mode": "ai" if "context" in self._marks else "code",
                      **{k: self._marks[k] for k in MARKS if k in self._marks}}
            self._t0 = None
        first = [result[k] for k in ("first_text", "first_point", "first_draw", "first_card", "first_caption")
                 if k in result]
        result["first_feedback"] = min(first) if first else result["done"]
        # What was typed stays out of clicky.log (which a bug report can attach); only the rehearsal log has it.
        _log.info("latency %s", " ".join(f"{k}={v}" for k, v in result.items() if k != "label"))
        try:
            import scene_log
            scene_log.record("latency", **result)
        except Exception:
            pass
        return result


def connect(manager, clock: TurnClock) -> None:
    """Mark the clock from what the manager already tells the screen. Signals fire on the worker thread."""
    manager.sig_response_chunk.connect(lambda text: clock.mark("first_text") if text and text.strip() else None)
    manager.sig_point_at.connect(lambda *_: clock.mark("first_point"))
    manager.sig_draw.connect(lambda *_: clock.mark("first_draw"))
    manager.sig_choices.connect(lambda *_: clock.mark("first_card"))
    manager.sig_caption.connect(lambda text: clock.mark("first_caption") if text and text.strip() else None)

    def _state(state):
        # A skill reads each card out and goes idle in between; the turn ends only when no task is left.
        if getattr(state, "name", str(state)) == "IDLE" and getattr(manager, "_current_task", None) is None:
            clock.finish()
    manager.sig_state_changed.connect(_state)
    try:
        import importlib
        playback = importlib.import_module("audio.playback")
        playback.on_play_start(lambda: clock.mark("first_voice"))
    except Exception:            # no audio stack (tests on a server): nothing to time
        pass


class _NoClock:
    running = False

    def start(self, source: str, label: str = "") -> None:
        pass

    def mark(self, name: str) -> None:
        pass

    def finish(self):
        return None


_NONE = _NoClock()


def of(owner):
    """The owner's clock, or one that measures nothing (stand-in managers in tests)."""
    return getattr(owner, "_clock", None) or _NONE
