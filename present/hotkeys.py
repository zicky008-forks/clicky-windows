"""Keys for a talk, registered with Win32 RegisterHotKey: no keyboard hook, nothing else is read.

PowerPoint's slide show owns single keys (B = black screen, W = white, H = next hidden slide,
N/P/Space/Enter/arrows/PageUp/PageDown = move, E = erase ink) and Ctrl+letter (pen, laser, hide
pointer). A clicker sends PageUp/PageDown or B. So a talk key must hold both Ctrl and Alt; anything
less is refused before it is registered, and the show keeps every key it already had.

RegisterHotKey delivers WM_HOTKEY to the thread that registered the key, so registration and the
message loop run on one background thread. Callbacks run on that thread: in Qt, emit a signal.
"""
from __future__ import annotations

import sys
import threading
from typing import Optional

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

DEFAULT_KEYS = {"ask": "ctrl+alt+h", "back": "ctrl+alt+b"}

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "windows": MOD_WIN}
_NAMED = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "pageup": 0x21, "pagedown": 0x22,
          "home": 0x24, "end": 0x23, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


class TalkKeyError(ValueError):
    pass


def parse(combo: str) -> tuple:
    """'ctrl+alt+h' -> (MOD_CONTROL | MOD_ALT, 0x48). Raises TalkKeyError on an unknown or missing key."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    mods, keys = 0, []
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        else:
            keys.append(p)
    if len(keys) != 1:
        raise TalkKeyError(f"phím '{combo}' phải có đúng một phím chính")
    k = keys[0]
    if len(k) == 1 and (k.isascii() and k.isalnum()):
        vk = ord(k.upper())
    elif k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        vk = 0x70 + int(k[1:]) - 1
    elif k in _NAMED:
        vk = _NAMED[k]
    else:
        raise TalkKeyError(f"không hiểu phím '{k}'")
    return mods, vk


def check(combo: str) -> tuple:
    """Parse and refuse any key PowerPoint's slide show could already be using."""
    mods, vk = parse(combo)
    if not (mods & MOD_CONTROL and mods & MOD_ALT):
        raise TalkKeyError(f"phím '{combo}' trùng phím trình chiếu của PowerPoint: cần giữ cả Ctrl và Alt")
    return mods, vk


class _Win32:
    """The four user32 calls this module needs, behind an object so tests can replace them."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._u = ctypes.windll.user32
        self._k = ctypes.windll.kernel32
        self._msg_t = wintypes.MSG
        self._byref = ctypes.byref

    def register(self, ident: int, mods: int, vk: int) -> bool:
        return bool(self._u.RegisterHotKey(None, ident, mods | MOD_NOREPEAT, vk))

    def unregister(self, ident: int) -> None:
        self._u.UnregisterHotKey(None, ident)

    def thread_id(self) -> int:
        return int(self._k.GetCurrentThreadId())

    def next_message(self) -> Optional[tuple]:
        """Blocks; (message, wParam) or None once WM_QUIT arrives."""
        msg = self._msg_t()
        if self._u.GetMessageW(self._byref(msg), None, 0, 0) <= 0:
            return None
        return int(msg.message), int(msg.wParam)

    def quit(self, thread_id: int) -> None:
        self._u.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)


class TalkKeys:
    """bindings: {"ctrl+alt+h": callback, ...}. start() returns the combos that could not be registered
    (another program holds them); [] means all are live. On a PC that is not Windows nothing is registered."""

    def __init__(self, bindings: dict, api=None):
        self.bindings = {combo: (check(combo), cb) for combo, cb in bindings.items()}
        self._api = api
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self.failed: list = []
        self._ready = threading.Event()

    def start(self, timeout: float = 2.0) -> list:
        if self._api is None:
            if sys.platform != "win32":
                self.failed = list(self.bindings)
                return self.failed
            self._api = _Win32()
        self._thread = threading.Thread(target=self._loop, name="talk-keys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.failed

    def _loop(self) -> None:
        api = self._api
        self._tid = api.thread_id()
        ids = {}
        for n, (combo, ((mods, vk), cb)) in enumerate(self.bindings.items(), start=1):
            if api.register(n, mods, vk):
                ids[n] = cb
            else:
                self.failed.append(combo)
        self._ready.set()
        try:
            while True:
                got = api.next_message()
                if got is None:
                    break
                message, ident = got
                if message == WM_HOTKEY and ident in ids:
                    try:
                        ids[ident]()
                    except Exception:           # noqa: BLE001 - a failing handler must not kill the keys
                        import logging
                        logging.getLogger(__name__).exception("talk key handler failed")
        finally:
            for n in ids:
                api.unregister(n)

    def stop(self) -> None:
        if self._thread is not None and self._tid:
            self._api.quit(self._tid)
            self._thread.join(timeout=2.0)
        self._thread = None
