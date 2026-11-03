"""The one-button bug report ("Báo lỗi", spec 12).

What would be sent is clicky.log with names and IDs masked by the job-6 masker, plus the app
version, the time and the error kind. Never a user's file. The user sees exactly this text
before anything happens (:func:`prepare` → preview → :func:`send`).

The masking rules are not written here. :func:`mask_log` calls the job-6 masker
(engine.redact, or the session's office.outgoing masker that knows the workbook's names); when
there is none, or it fails, nothing is sent at all and the preview says the report needs the
next build. Sending is still a stub: the payload is written to
``data_dir()/bao-loi/<time>.txt`` and no network is used.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from storage import data_dir

log = logging.getLogger("clicky.bug_report")

TAIL_BYTES = 64_000
NOT_READY = ("Bản này chưa gửi được nhật ký, vì phần che tên chưa có. Bản sau em sẽ gửi được. "
             "Chưa có gì được gửi đi.")
SAVED = "Em đã chuẩn bị xong báo lỗi trên máy chị. Bản beta này chưa tự gửi lên mạng."


def find_masker() -> Optional[Callable[[str], str]]:
    """The job-6 masker (engine.redact) with an empty catalogue: identifiers by shape and label.
    The app passes its session masker instead (office.outgoing.Outgoing.mask), which also knows
    the customer / supplier / staff names read from the open workbook."""
    try:
        from engine import redact
        return redact.Masker(redact.Catalogue()).mask
    except Exception:
        return None


def _without_home(text: str) -> str:
    """The Windows user name sits in every path in the log (C:\\Users\\<name>\\...); it is not sent."""
    homes = {str(Path.home()), os.environ.get("USERPROFILE", "")}
    for value in sorted((h for h in homes if len(h) >= 4), key=len, reverse=True):
        text = text.replace(value, "~")
    user = os.environ.get("USERNAME", "")
    return text.replace(user, "[người dùng]") if len(user) >= 3 else text


class MaskerUnavailable(RuntimeError):
    pass


def mask_log(text: str, masker: Optional[Callable[[str], str]] = None) -> str:
    """``text`` with names and IDs masked by the job-6 rules. Raises MaskerUnavailable when
    there is no masker or it fails: an unmasked log is never sent."""
    masker = masker or find_masker()
    if masker is None:
        raise MaskerUnavailable("no masker")
    try:
        masked = masker(_without_home(text))
    except Exception as exc:
        log.warning("masking the log failed: %r", exc)
        raise MaskerUnavailable("masker failed") from exc
    if not isinstance(masked, str):
        raise MaskerUnavailable("masker returned no text")
    return masked


def log_path() -> Path:
    """clicky.log as main._setup_logging writes it."""
    own = data_dir() / "clicky.log"
    if own.exists():
        return own
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Clicky" / "clicky.log"


def _tail(path: Path, limit: int = TAIL_BYTES) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            raw = stream.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if size > limit:                                   # start on a whole line
        text = text.split("\n", 1)[-1]
    return text


def _version() -> str:
    try:
        from updates import VERSION
        return VERSION
    except Exception:
        return "?"


@dataclass
class Report:
    ready: bool
    body: str            # exactly what would be sent; "" when not ready
    message: str         # what to tell the user when not ready


def prepare(kind: str = "", masker: Optional[Callable[[str], str]] = None,
            now: Optional[float] = None) -> Report:
    """The report to preview. Contains only the masked log tail, version, time and error kind."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    try:
        masked = mask_log(_tail(log_path()), masker)
    except MaskerUnavailable:
        return Report(False, "", NOT_READY)
    from engine.errors_vi import CATALOGUE
    title = CATALOGUE[kind].title if kind in CATALOGUE else "không rõ"
    body = (f"Phiên bản: {_version()}\nThời gian: {stamp}\nLỗi: {title}\n"
            f"Không kèm file nào của người dùng.\n\n--- clicky.log (đã che tên) ---\n{masked}")
    return Report(True, body, "")


def send(report: Report, now: Optional[float] = None) -> Optional[Path]:
    """Stub: keep the exact previewed text in data_dir()/bao-loi/<time>.txt. Nothing goes on the network."""
    if not report.ready or not report.body:
        return None
    folder = data_dir() / "bao-loi"
    folder.mkdir(parents=True, exist_ok=True)
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    path = folder / f"{base}.txt"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{base}-{n}.txt"
    path.write_text(report.body, encoding="utf-8")
    return path
