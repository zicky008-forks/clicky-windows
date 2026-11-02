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


# A figure that changes is shown changing: it counts from where it was to where it is, then rests.
ROLL_S = 1.1


def roll_amount(start: int, end: int, elapsed: float) -> int:
    """The amount (whole đồng) to show `elapsed` seconds into a roll from start to end.

    Eases out so it slows into the final figure, and lands exactly on end.
    """
    if elapsed >= ROLL_S:
        return end
    u = max(0.0, elapsed) / ROLL_S
    return int(round(start + (end - start) * (1 - (1 - u) ** 3)))


def stem(anchor, card) -> tuple:
    """A short line from the card's edge to the range it explains, so the card reads as part of the sheet.

    anchor and card are (l, t, r, b); returns (x1, y1, x2, y2) from the card edge to the range edge,
    or () when they touch or overlap.
    """
    al, at, ar, ab = anchor
    cl, ct, cr, cb = card

    def mid(lo, hi, a_lo, a_hi):
        """A point inside both spans when they overlap, else the card point nearest the range."""
        x = (max(lo, a_lo) + min(hi, a_hi)) / 2 if max(lo, a_lo) < min(hi, a_hi) else min(max((a_lo + a_hi) / 2, lo + 20), hi - 20)
        return x

    if ct >= ab:                                   # card below
        x = mid(cl, cr, al, ar)
        return (x, ct, min(max(x, al), ar), ab) if ct > ab else ()
    if cb <= at:                                   # card above
        x = mid(cl, cr, al, ar)
        return (x, cb, min(max(x, al), ar), at) if cb < at else ()
    if cl >= ar:                                   # card right
        y = mid(ct, cb, at, ab)
        return (cl, y, ar, min(max(y, at), ab)) if cl > ar else ()
    if cr <= al:                                   # card left
        y = mid(ct, cb, at, ab)
        return (cr, y, al, min(max(y, at), ab)) if cr < al else ()
    return ()


def pointer_spot(box, edge: float = 14.0) -> tuple:
    """Where the pointer rests for a range: just left of it, at mid height, or just right when the
    range starts at the screen's left edge. It never sits on the figure it is pointing at."""
    l, t, r, b = box
    x = l - edge if l - edge >= 4 else r + edge
    return x, (t + b) / 2
