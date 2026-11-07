"""Whether the welcome ("Chào chị, em là Clicky" + 3 promises) still has to be shown.

The welcome is shown once. The old rule ("skip when setup_complete.flag exists") meant a
reinstall never greeted the user again, because the flag lives in %LOCALAPPDATA%\\Clicky and
survives an uninstall. Now the flag remembers WHICH install it was written for:

* The Inno Setup installer writes ``install-stamp.txt`` next to Clicky.exe on every install a
  person clicks through (a new time stamp each time; silent updates leave the old one).
* The flag stores the stamp that was current when the user pressed "Bắt đầu".
* The welcome shows when there is no flag, or when the flag's stamp differs from the
  installed one. Flags written by older builds contain "ok", so they never match a stamp.
* Without a stamp (running from source, or a Velopack install) the flag alone decides;
  Velopack's after-install hook calls :func:`forget_welcome` instead.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from storage import data_dir

FLAG_NAME = "setup_complete.flag"
STAMP_NAME = "install-stamp.txt"


def flag_path() -> Path:
    return data_dir() / FLAG_NAME


def app_dir() -> Optional[Path]:
    """The installed program folder (where Clicky.exe and the installer's stamp are), or None from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def install_stamp(folder: Optional[Path] = None) -> str:
    """The stamp the installer wrote for this install, or "" when there is none."""
    folder = folder if folder is not None else app_dir()
    if folder is None:
        return ""
    try:
        return (Path(folder) / STAMP_NAME).read_text(encoding="utf-8-sig", errors="replace").strip()[:200]
    except OSError:
        return ""


def welcome_needed(folder: Optional[Path] = None) -> bool:
    try:
        written = flag_path().read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return True                      # no flag (or unreadable): greet
    stamp = install_stamp(folder)
    return bool(stamp) and written != stamp


def mark_welcome_done(folder: Optional[Path] = None) -> None:
    try:
        flag_path().write_text(install_stamp(folder) or "ok", encoding="utf-8")
    except OSError:
        pass                             # at worst the welcome shows once more


def forget_welcome() -> None:
    """A fresh install happened (Velopack hook): the next start greets again."""
    try:
        flag_path().unlink(missing_ok=True)
    except OSError:
        pass
