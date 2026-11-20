"""Guided first months: Vera asks before she works on the user's own files, and the user can stop her.

The sample ("Xem em làm") plays by itself because it only touches made-up numbers. The user's own
files are different, so before the first step on them Vera asks once, on one card:

* "Cho phép, em làm giúp" (month 1 default): Vera presses the mechanical buttons herself, each after a
  visible countdown ("Em tự bấm … sau 3 giây. Chị bấm Dừng hoặc Esc để chặn."). She never presses a
  question only the user can answer: whether what she read is right, which column or file to use,
  or turning a report official. Those always wait for the user.
* "Tôi tự bấm, em chỉ chỗ" (default from month 2): Vera presses nothing. After each question she
  says which button comes next, so the user learns the month and does it alone.
* "Chưa, để lúc khác": nothing is opened or read; the files are left as they are.

Consent lives in memory for this app session only and is never written to disk: a new session, or a
press of Stop, asks again. Stop also turns the rest of the run into "Tôi tự bấm" mode, so a stopped
Vera never starts pressing again by herself. The month count (how many own months were delivered,
i.e. saved, re-read and matched) is the only thing stored; it picks which option is offered first.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import scene_log
from office.month_close import Beat
from storage import set_setting, setting

log = logging.getLogger(__name__)

DO_IT, SHOW_ME, LATER = "Cho phép, em làm giúp", "Tôi tự bấm, em chỉ chỗ", "Chưa, để lúc khác"
MONTHS_KEY = "guided_months_done"
COUNTDOWN_S = 3.0
# Only buttons whose effect is mechanical and undoable (the original is kept, the saved file is re-read).
# Never: "Đúng rồi, làm tiếp" (is what I read right?), column or file picks, "chính thức".
SAFE_TO_PRESS = ("Sửa giúp tôi", "Lưu và kiểm tra")

CONSENT_TRIGGER = (r"^\s*(cho phép, em làm giúp|cho phep, em lam giup|cho phép|cho phep"
                   r"|tôi tự bấm, em chỉ chỗ|toi tu bam, em chi cho|tôi tự bấm|toi tu bam"
                   r"|chưa, để lúc khác|chua, de luc khac)\s*[.!]?\s*$")

_STATE: dict = {"mode": None, "held": None, "own_run": False, "counted": False}
_TIMER: list = []                       # the pending auto-press, so Stop can cancel it


def months_done() -> int:
    try:
        return max(0, int(setting(MONTHS_KEY, 0) or 0))
    except (TypeError, ValueError):
        return 0


def mode() -> Optional[str]:
    """DO_IT or SHOW_ME once the user chose in this session, else None."""
    return _STATE["mode"]


def reset() -> None:
    """Forget this session's consent (tests, and a new session)."""
    _cancel_timer()
    _STATE.update(mode=None, held=None, own_run=False, counted=False)


def needs_consent() -> bool:
    return _STATE["mode"] is None


def consent_choices() -> tuple:
    """Month 1: Vera does it. From month 2 the user does it and Vera only points."""
    if months_done() >= 1:
        return (SHOW_ME, DO_IT, LATER)
    return (DO_IT, SHOW_ME, LATER)


def consent_beat() -> Beat:
    if months_done() >= 1:
        text = (f"Tháng thứ {months_done() + 1} rồi, chị tự bấm nhé, em chỉ chỗ. Hay chị vẫn muốn em làm giúp? "
                "Em chỉ đọc file và tạo sheet mới, bản gốc luôn được giữ, không gửi gì ra ngoài.")
    else:
        text = ("Em xin phép làm trên file của chị: mở file, đọc, tạo sheet báo cáo mới. Bản gốc luôn được giữ, "
                "không gửi gì ra ngoài. Chỗ nào cần chị quyết, em dừng lại hỏi. Chị bấm Dừng hoặc Esc lúc nào cũng được.")
    return Beat(text, label="Xin phép trước khi làm", choices=consent_choices())


def hold(paths: list) -> None:
    """Keep the dropped files until the user answers the consent card. Nothing is read yet."""
    _STATE["held"] = list(paths)


def grant(answer: str) -> Optional[list]:
    """Record the answer; returns the held paths to go on with, or None (not now, or nothing held)."""
    folded = answer.strip().rstrip(".!").lower()
    held, _STATE["held"] = _STATE["held"], None
    if folded.startswith(("chưa", "chua")):
        _STATE.update(mode=None, own_run=False)
        scene_log.record("guided_consent", choice="later")
        return None
    _STATE["mode"] = SHOW_ME if folded.startswith(("tôi tự", "toi tu")) else DO_IT
    _STATE.update(own_run=True, counted=False)
    scene_log.record("guided_consent", choice="do" if _STATE["mode"] == DO_IT else "show", months=months_done())
    return held


def next_press(choices) -> Optional[str]:
    """The button Vera may press herself after this question, or None when it waits for the user."""
    if _STATE["mode"] != DO_IT or not _STATE["own_run"] or not choices:
        return None
    first = list(choices)[0]
    return first if first in SAFE_TO_PRESS else None


def nudge(choices) -> Optional[str]:
    """In "Tôi tự bấm" mode: the line that names the next button (the first one, the usual way on)."""
    if _STATE["mode"] != SHOW_ME or not _STATE["own_run"] or not choices:
        return None
    return f"Bước tiếp: chị bấm “{list(choices)[0]}”."


def after_question(manager, run, choices) -> None:
    """Called when a story ends on a question (office.month_close.play)."""
    caption = getattr(manager, "sig_caption", None)
    line = nudge(choices)
    if line:
        scene_log.record("guided_nudge", button=list(choices)[0])
        if caption is not None:
            caption.emit(line)
        return
    label = next_press(choices)
    if not label:
        return
    if caption is not None:
        caption.emit(f"Em tự bấm “{label}” sau {int(COUNTDOWN_S)} giây. Chị bấm Dừng hoặc Esc để chặn.")
    scene_log.record("guided_countdown", button=label, seconds=COUNTDOWN_S)

    def fire():
        _TIMER.clear()
        if getattr(manager, "_month_close_run", None) is not run or _STATE["mode"] != DO_IT:
            return                                  # something else happened meanwhile, or Stop was pressed
        scene_log.record("guided_press", button=label)
        manager.submit_text(label)

    _cancel_timer()
    try:
        _TIMER.append(asyncio.get_running_loop().call_later(COUNTDOWN_S, fire))
    except RuntimeError:
        pass


def halt() -> bool:
    """Stop / Esc: cancel a pending press, and Vera presses nothing more in this session.

    Returns True when a countdown was cancelled."""
    cancelled = _cancel_timer()
    if _STATE["mode"] == DO_IT:
        _STATE["mode"] = SHOW_ME
        scene_log.record("guided_halt", cancelled=cancelled)
    return cancelled


def cancel_pending() -> bool:
    """A new turn began (a button, a typed line): drop a pending press, keep the mode."""
    return _cancel_timer()


def delivered() -> None:
    """The saved own file was re-read and matched: one more month done (counted once per run)."""
    if not _STATE["own_run"] or _STATE["counted"]:
        return
    _STATE["counted"] = True
    set_setting(MONTHS_KEY, months_done() + 1)
    scene_log.record("guided_month_done", months=months_done())


def _cancel_timer() -> bool:
    cancelled = False
    while _TIMER:
        handle = _TIMER.pop()
        handle.cancel()
        cancelled = True
    return cancelled


async def handle_consent(manager, transcript: str) -> str:
    from office import drop
    from office import month_close as mc
    held = grant(transcript)
    if mode() is None:                              # "Chưa, để lúc khác"
        await mc.play(manager, [Beat("Dạ. Em chưa mở file nào, file của chị vẫn nguyên. "
                                     "Lúc nào sẵn sàng chị thả file lại nhé.")])
        return ""
    if not held:                                    # consent said before any file was dropped
        manager.sig_drop_zone.emit()
        return ""
    await drop.handle(manager, held)
    return ""


SKILLS = [
    {"name": "Trả lời xin phép", "trigger": CONSENT_TRIGGER, "handler": handle_consent,
     "description": "Người dùng cho phép Vera làm giúp, tự bấm, hoặc để sau, trước khi Vera làm trên file thật."},
]
