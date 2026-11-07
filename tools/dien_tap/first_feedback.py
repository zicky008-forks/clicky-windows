"""The "bớt được bao lâu" card (engine/feedback.py, ui/feedback_card.py): closed with "Để sau", graded, isolated.

After a report on the user's own files is saved and re-checked, Clicky shows a separate card (scene events
"feedback_card" when shown, "feedback" when pressed; close_step "feedback_card" when asked). The robot presses
"Để sau" so the card closes and records nothing. The card must never come after the sample.

Clicky counts how often it asked in settings.json (feedback_asked, feedback_done; three asks at most). A robot
run would use up the tester's own asks, so each run starts with the two keys removed (the card is due) and puts
the tester's values back afterwards. Only these two keys are touched: Clicky writes other settings during the run.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

LATER = "Để sau"                    # engine/feedback.py LATER
KEYS = ("feedback_asked", "feedback_done")
SETTINGS = "settings.json"
_MISSING = object()


def _read(path: Path) -> dict:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return values if isinstance(values, dict) else {}


def _write(path: Path, values: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps(values, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def isolate_settings(data_dir: Path):
    """Start the run with the feedback card due; return the function that puts the tester's counts back."""
    path = data_dir / SETTINGS
    if not path.exists():
        return lambda: None
    before = _read(path)
    kept = {k: before.get(k, _MISSING) for k in KEYS}
    if any(v is not _MISSING for v in kept.values()):
        try:
            _write(path, {k: v for k, v in before.items() if k not in KEYS})
        except OSError:
            return lambda: None

    def restore() -> None:
        try:
            now = _read(path)
            for k, v in kept.items():
                if v is _MISSING:
                    now.pop(k, None)
                else:
                    now[k] = v
            _write(path, now)
        except OSError:
            pass
    return restore


class Later:
    """An AUTO_PRESS entry for the feedback card: "Để sau" once per card shown."""
    event = "feedback_card"

    def __call__(self, e: dict):
        return LATER


def asked(events: list) -> bool:
    return any(e.get("event") == "feedback_card" or
               (e.get("event") == "close_step" and e.get("step") == "feedback_card") for e in events)


def settled(events: list) -> bool:
    """After the re-check: the card was closed, or the re-check story ended and no card was asked."""
    at = max((i for i, e in enumerate(events) if e.get("event") == "verify"), default=None)
    if at is None:
        return False
    after = events[at + 1:]
    shown = [i for i, e in enumerate(after) if e.get("event") == "feedback_card"]
    if shown:
        return any(e.get("event") == "feedback" for e in after[shown[-1] + 1:])
    if asked(after):
        return False                                   # asked, card not on screen yet
    return any(e.get("event") == "story_end" for e in after)


def check_lines(events: list, sample: bool):
    """(name, ok, detail, blocking) rows for the report."""
    if sample:
        return [("Bản mẫu không hỏi \"bớt được bao lâu\"", not asked(events),
                 "có hỏi sau bản mẫu" if asked(events) else "không hỏi", True)]
    delivered = any(e.get("event") == "verify" and e.get("ok") for e in events)
    if not delivered:
        return []
    rows = [("Hỏi \"bớt được bao lâu\" sau báo cáo đã lưu và khớp", asked(events),
             "có hỏi" if asked(events) else "không hỏi", False)]
    shown = [i for i, e in enumerate(events) if e.get("event") == "feedback_card"]
    if shown:
        later = [e for e in events[shown[0] + 1:] if e.get("event") == "feedback"]
        ok = any(e.get("later") for e in later)
        rows.append((f"Thẻ góp ý đóng bằng \"{LATER}\"", ok,
                     f"đã bấm \"{LATER}\"" if ok else
                     ("thẻ ghi một câu trả lời, robot không chọn" if later else "thẻ vẫn mở"), True))
    elif asked(events):
        rows.append(("Thẻ góp ý hiện ra khi được hỏi", False, "Clicky hỏi nhưng thẻ không hiện", True))
    return rows
