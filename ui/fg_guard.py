"""Clicky's cards, frames and pointer belong to the Excel window they explain. When another app comes to the
front (the user switches to mail, a chat app, the browser) they must not stay painted over it at the old screen
spot: the whole drawing layer is hidden, and the story waits at the next sentence. When Excel comes back the
layer shows again; if the Excel window moved or changed size meanwhile, the old frames are cleared, because
their screen spots no longer sit on the cells (the next card is placed from the cells again).

Kept free of Qt and Win32 so it can be tested anywhere: front() says what is in front, the rest are callbacks.
"""
from __future__ import annotations

from typing import Callable, Optional

EXCEL, OWN, OTHER = "excel", "own", "other"


class Guard:
    def __init__(self, front: Callable[[], tuple], has_content: Callable[[], bool], hide: Callable[[bool], None],
                 clear: Callable[[], None], record: Callable[..., None]):
        self.front, self.has_content, self.hide, self.clear, self.record = front, has_content, hide, clear, record
        self.hidden = False
        self.rect: Optional[tuple] = None          # the Excel window's rect while the content was drawn
        self.excel_front = True

    def tick(self) -> None:
        if not self.has_content():
            if self.hidden:
                self._show("no_content")
            self.rect = None
            self.excel_front = True
            return
        try:
            who, rect = self.front()
        except Exception:                          # noqa: BLE001 - no answer from Windows: leave things as they are
            return
        if who == OWN:                             # Clicky's own card or button has the focus: still the story
            return
        if who == OTHER:
            self.excel_front = False
            if not self.hidden:
                self.hidden = True
                self.hide(True)
                self.record("overlay_hidden", reason="focus")
            return
        self.excel_front = True
        if self.rect is not None and rect is not None and tuple(rect) != tuple(self.rect):
            self.clear()                           # frames drawn for the old window spot: never left off the cells
            self.record("overlay_hidden", reason="moved", was=list(self.rect), now=list(rect))
        self.rect = tuple(rect) if rect is not None else self.rect
        if self.hidden:
            self._show("focus")

    def _show(self, why: str) -> None:
        self.hidden = False
        self.hide(False)
        self.record("overlay_shown", reason=why)
