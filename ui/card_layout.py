"""Layout rules for Clicky's explanation cards, kept free of Qt so they can be tested anywhere.

One idea per screen: the range being explained stays lit, the rest of the screen
dims, and one card sits beside the range (never on top of it) with a title, the
one figure that matters and a single line of context.
"""

# Passed as the pointer label when the card already says everything: no speech bubble.
NO_BUBBLE = "⁣"

CARD_WIDTH = 360
GAP = 16
MARGIN = 12

# Tone → (accent colour, soft fill) as hex; the card's figure and the lit frame use the accent.
TONES = {
    "bad": ("#D93848", "#FDECEE"),
    "ok": ("#1E9E5A", "#E7F6EE"),
    "warn": ("#D97706", "#FEF3E2"),
    "info": ("#2F6FEB", "#EAF1FE"),
}


def tone_of(mark: str) -> str:
    return mark if mark in TONES else "info"


def place_card(anchor, size, screen) -> tuple:
    """Top-left (x, y) for a card of size (w, h) explaining anchor (l, t, r, b) inside screen (l, t, r, b).

    Tries below the range, then above, then right, then left, and takes the first
    spot that fits on the screen; the card never covers the range it explains.
    """
    l, t, r, b = anchor
    w, h = size
    sl, st, sr, sb = screen

    def clamp_x(x):
        return max(sl + MARGIN, min(x, sr - MARGIN - w))

    def clamp_y(y):
        return max(st + MARGIN, min(y, sb - MARGIN - h))

    for x, y in ((clamp_x(l), b + GAP), (clamp_x(l), t - GAP - h), (r + GAP, clamp_y(t)), (l - GAP - w, clamp_y(t))):
        if sl + MARGIN <= x and x + w <= sr - MARGIN and st + MARGIN <= y and y + h <= sb - MARGIN:
            return x, y
    return clamp_x(r + GAP), clamp_y(b + GAP)


def reading_seconds(text: str) -> float:
    """How long a sentence stays up when it is not read aloud: about 3 words a second, 2.5 to 9 seconds."""
    return max(2.5, min(9.0, 1.0 + len(text.split()) / 3.0))
