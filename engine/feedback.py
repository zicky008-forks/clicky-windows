"""Job 13: after the first report on the user's own files, one question: how much time did Clicky save?

The answer is the main figure for the workshop pitch, so it is kept exactly as chosen, with the app version and how
long the run took. Nothing from the files goes with it. Until a receiving address exists the answers stay on this PC
(data_dir()/gop-y.jsonl), one JSON line each; sending them will go through the same preview as Báo lỗi.

The question comes after a report on the user's own files was saved and re-checked (never after the sample). It is
asked once; "Để sau" asks again after the next report, at most ASK_LIMIT times in all.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from storage import data_dir, set_setting, setting

EYEBROW = "XONG BÁO CÁO ĐẦU TIÊN"
NOTE_QUESTION = "Có chỗ nào em làm sai hay khó hiểu không?"
NOTE_HINT = "Gõ, hoặc giữ phím đọc chính tả để nói… (không bắt buộc)"
SEND = "Gửi góp ý"
LATER = "Để sau"
# Four choices that leave no gap: half a day is four hours of work.
CHOICES = (("none", "Chưa bớt được"), ("lt1h", "Dưới 1 giờ"), ("1to4h", "1 đến 4 giờ"), ("half_day", "Nửa ngày trở lên"))
ASK_LIMIT = 3
NOTE_LIMIT = 2000
FILE_NAME = "gop-y.jsonl"
_ASKED = "feedback_asked"         # how many times the question was shown
_DONE = "feedback_done"           # answered: never asked again


def question() -> str:
    from engine.address import you
    return f"Lần này em giúp {you()} bớt được bao lâu?"


def thanks() -> str:
    from engine.address import you
    return f"Em cảm ơn {you()}. Em đã ghi lại góp ý trên máy này."


def due() -> bool:
    return not setting(_DONE, False) and int(setting(_ASKED, 0) or 0) < ASK_LIMIT


def card(run_s: Optional[float]) -> dict:
    """What the feedback card shows, and what it sends back with the answer."""
    set_setting(_ASKED, int(setting(_ASKED, 0) or 0) + 1)
    return {"eyebrow": EYEBROW if int(setting(_ASKED, 1)) == 1 else "XONG BÁO CÁO",
            "question": question(), "choices": [label for _, label in CHOICES],
            "note_question": NOTE_QUESTION, "note_hint": NOTE_HINT, "send": SEND, "later": LATER,
            "run_s": None if run_s is None else int(run_s)}


def _version() -> str:
    try:
        from updates import VERSION
        return VERSION
    except Exception:
        return "?"


def record(choice: str, note: str = "", run_s: Optional[int] = None, now: Optional[float] = None) -> dict:
    """Keep one answer. choice is the label shown on the card; an unknown label is refused."""
    keys = {label: key for key, label in CHOICES}
    if choice not in keys:
        raise ValueError(f"not a feedback choice: {choice!r}")
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now if now is not None else time.time())),
             "version": _version(), "saved": keys[choice], "note": (note or "").strip()[:NOTE_LIMIT],
             "run_s": run_s, "asked": int(setting(_ASKED, 0) or 0), "sent": False}
    with open(data_dir() / FILE_NAME, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    set_setting(_DONE, True)
    return entry


def answers() -> list:
    try:
        lines = (data_dir() / FILE_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
