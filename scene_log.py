"""Scene log: one JSON line for everything Clicky shows, draws or says.

Used by the rehearsal robot (tools/dien_tap) to check a run the way a person watching
the screen would: which card came up, which cell was lit, whether the box landed on that
cell, whether the sentence was read aloud. The drawings are painted by Clicky itself, so
no accessibility tool can see them; this log is the only way to check them without a video.

Off unless the environment variable CLICKY_SCENE_LOG names a file. Privacy: the log holds
card text and figures from the open workbook, so it is only switched on for rehearsals on
practice data. It is written on this PC and never sent anywhere.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

ENV = "CLICKY_SCENE_LOG"
_log = logging.getLogger("clicky.scene_log")
_lock = threading.Lock()
_t0 = time.monotonic()
_failed = False


def path() -> Path | None:
    value = os.environ.get(ENV, "").strip()
    return Path(value) if value else None


def enabled() -> bool:
    return path() is not None


def _plain(value):
    """JSON-safe copy: tuples become lists, numbers stay numbers, anything else becomes text."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 2)
    return str(value)


def record(event: str, **data) -> None:
    """Append one event. Never raises: a broken log must not break the product."""
    global _failed
    target = path()
    if target is None or _failed:
        return
    line = {"t": round(time.monotonic() - _t0, 3), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
    line.update(_plain(data))
    try:
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:
        _failed = True
        _log.warning("scene log disabled, cannot write %s: %s", target, exc)


def connect(manager, choice_card=None) -> None:
    """Log what the manager tells the screen to do. Only wired when the log is on."""
    if not enabled():
        return
    record("start", pid=os.getpid())
    manager.sig_turn_started.connect(lambda text: record("turn", text=text))
    manager.sig_draw.connect(lambda shape: record("draw", shape=shape))
    manager.sig_clear_drawings.connect(lambda: record("clear"))
    manager.sig_point_at.connect(lambda x, y, label: record("point", x=x, y=y, label=label))
    manager.sig_choices.connect(lambda question, labels: record("choices", question=question, labels=labels))
    manager.sig_guided.connect(lambda active: record("guided", active=active))
    manager.sig_notice.connect(lambda text: record("notice", text=text))
    manager.sig_error.connect(lambda text: record("error", text=text))
    manager.sig_response_done.connect(lambda text: record("said", text=text[:600]))
    manager.sig_state_changed.connect(lambda state: record("state", state=getattr(state, "name", str(state))))
    if choice_card is not None:
        choice_card.chosen.connect(lambda label: record("chosen", label=label))
        choice_card.dismissed.connect(lambda: record("dismissed"))
