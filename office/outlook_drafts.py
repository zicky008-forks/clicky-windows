"""Save emails as Outlook drafts, never send them.

Each draft is made with CreateItem(olMailItem), given its recipients, subject, plain body and attachment, and saved
to the Drafts folder with .Save(). The user opens Drafts, reads each one and presses Send. Clicky never calls .Send,
and never .Display, because a shown compose window can be sent by a stray Enter or a mail rule.

The mail item is wrapped in _NoSend, which passes through only what a draft needs and raises on Send and Display,
so a later change cannot send by mistake. Drafts sit in the user's own mailbox; nothing goes to Clicky's AI.

Without Outlook (not installed, blocked, another PC) each draft is written next to its workbook as an .eml file
marked unsent, which Outlook or any mail program opens as a new message for the user to check and send.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Callable, Optional

import scene_log
from engine.log_safe import scrub

log = logging.getLogger(__name__)

OL_MAIL_ITEM = 0
FORBIDDEN = ("Send", "Display")


class SendRefused(RuntimeError):
    """Raised when anything asks a draft to be sent or shown."""


class _NoSend:
    """A mail item that can be filled in and saved, never sent or shown."""
    _SETTABLE = ("To", "CC", "Subject", "Body")
    _READABLE = ("Attachments", "Save", "EntryID")

    def __init__(self, item):
        object.__setattr__(self, "_item", item)

    def __getattr__(self, name):
        if name in FORBIDDEN:
            raise SendRefused(f"Clicky never calls {name} on an email")
        if name in self._READABLE:
            return getattr(object.__getattribute__(self, "_item"), name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name not in self._SETTABLE:
            raise AttributeError(name)
        setattr(object.__getattribute__(self, "_item"), name, value)


@dataclass
class Saved:
    how: str            # "outlook", "eml" or "" when it could not be saved
    path: str = ""      # the .eml file, for "eml"
    why: str = ""       # plain words when it could not be saved


def _outlook():
    """Outlook's Application object, or None when there is none on this PC."""
    if sys.platform != "win32":
        return None
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        return win32com.client.Dispatch("Outlook.Application")
    except Exception as exc:                 # noqa: BLE001 - fall back to .eml files
        log.warning("Outlook is not available: %s", scrub(exc))
        return None


def to_outlook(app, d) -> None:
    """One draft into the Drafts folder of the user's mailbox."""
    item = _NoSend(app.CreateItem(OL_MAIL_ITEM))
    item.To = d.to
    if d.cc:
        item.CC = d.cc
    item.Subject = d.subject
    item.Body = d.body
    if d.attachment:
        item.Attachments.Add(str(Path(d.attachment).resolve()))
    item.Save()


def to_eml(d, path: Path) -> Path:
    """The draft as an unsent .eml file: opened in a mail program it is a new message, ready to check and send."""
    if path.exists():
        raise FileExistsError(path.name)
    m = EmailMessage(policy=SMTP)
    m["X-Unsent"] = "1"
    if d.to:
        m["To"] = d.to
    if d.cc:
        m["Cc"] = d.cc
    m["Subject"] = d.subject
    m.set_content(d.body)
    if d.attachment:
        a = Path(d.attachment)
        m.add_attachment(a.read_bytes(), maintype="application",
                         subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=a.name)
    path.write_bytes(m.as_bytes())
    return path


def save_all(drafts: list, folder: Path, outlook: Optional[Callable] = None) -> list:
    """Every draft saved, one Saved each, in order. outlook() gives the Application (tests pass a fake).
    One that Outlook refuses is written as .eml instead; nothing is ever sent."""
    app = (outlook or _outlook)()
    out = []
    for i, d in enumerate(drafts):
        if app is not None:
            try:
                to_outlook(app, d)
                out.append(Saved("outlook"))
                continue
            except SendRefused:
                raise
            except Exception as exc:         # noqa: BLE001 - this one goes to a file instead
                log.warning("Outlook did not save draft %d: %s", i + 1, scrub(exc))
        name = Path(d.attachment).stem if d.attachment else f"Thu nhap {i + 1}"
        try:
            out.append(Saved("eml", str(to_eml(d, folder / f"{name}.eml"))))
        except Exception as exc:             # noqa: BLE001 - named to the user, never silent
            log.warning("draft %d not saved: %s", i + 1, scrub(exc))
            out.append(Saved("", why="em chưa lưu được thư nháp này"))
    # counts only: never an address, a name or a file
    scene_log.record("drafts_saved", outlook=sum(s.how == "outlook" for s in out),
                     eml=sum(s.how == "eml" for s in out), failed=sum(not s.how for s in out), sent=0)
    return out
