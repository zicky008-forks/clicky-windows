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


def _boxes(avoid) -> list:
    """avoid as a list of (l, t, r, b): one box, a list of boxes, or nothing."""
    if not avoid:
        return []
    if len(avoid) == 4 and all(isinstance(v, (int, float)) for v in avoid):
        return [tuple(avoid)]
    return [tuple(b) for b in avoid if b and len(b) == 4]


def overlap(rect, boxes) -> float:
    """Area of rect (l, t, r, b) lying over any of boxes (counted once per box)."""
    l, t, r, b = rect
    total = 0.0
    for bl, bt, br, bb in _boxes(boxes):
        w, h = min(r, br) - max(l, bl), min(b, bb) - max(t, bt)
        if w > 0 and h > 0:
            total += w * h
    return total


def place_card(anchor, size, screen, avoid=None, soft=None) -> tuple:
    """Top-left (x, y) for a card of size (w, h) explaining anchor (l, t, r, b) inside screen (l, t, r, b).

    avoid: what the card must not cover, the cells holding figures on screen: one box (the filled area)
    or a list of boxes (each block of figures, and other cards already up). soft: cells it should rather
    not cover either (labels). The card never covers the range it explains when anything else fits.

    Order: a spot beside the range or just past the figures; then the free spot nearest the range
    anywhere on screen (clear of figures and labels, then of figures only); last, where it covers the
    least figure area. Without avoid it simply goes below, above, right, then left of the range.
    """
    l, t, r, b = anchor
    w, h = size
    sl, st, sr, sb = screen
    hard, labels = _boxes(avoid), _boxes(soft)

    def clamp_x(x):
        return max(sl + MARGIN, min(x, sr - MARGIN - w))

    def clamp_y(y):
        return max(st + MARGIN, min(y, sb - MARGIN - h))

    def fits(x, y):
        return sl + MARGIN <= x and x + w <= sr - MARGIN and st + MARGIN <= y and y + h <= sb - MARGIN

    def clear(boxes, x, y):
        return all(x + w <= bl or br <= x or y + h <= bt or bb <= y for bl, bt, br, bb in boxes)

    beside = [(clamp_x(l), b + GAP), (clamp_x(l), t - GAP - h), (r + GAP, clamp_y(t)), (l - GAP - w, clamp_y(t))]
    if not hard and not labels:
        for x, y in beside:
            if fits(x, y):
                return x, y
        return clamp_x(r + GAP), clamp_y(b + GAP)

    everything = [tuple(anchor)] + hard
    al, at = min(bx[0] for bx in hard or [anchor]), min(bx[1] for bx in hard or [anchor])
    ar, ab = max(bx[2] for bx in hard or [anchor]), max(bx[3] for bx in hard or [anchor])
    past = [(ar + GAP, clamp_y(t)), (clamp_x(l), ab + GAP), (al - GAP - w, clamp_y(t))]
    for x, y in beside + past:
        if fits(x, y) and clear(everything + labels, x, y):
            return x, y

    return _nearest_free(anchor, size, screen, hard, labels, keep_off=tuple(anchor),
                         extra=(clamp_x(l), r + GAP, l - GAP - w), extra_y=(clamp_y(t), b + GAP, t - GAP - h))


def _nearest_free(target, size, screen, hard, labels, keep_off=None, extra=(), extra_y=()):
    """The spot nearest target (l, t, r, b) for a card of size (w, h): clear of figures and words, then of
    figures only, then the one covering the least figure area. keep_off is never covered when avoidable.

    Candidates hug the edges of the screen and of every box, so a free gap between two blocks of figures
    is found as surely as the empty columns past the report.
    """
    l, t, r, b = target
    w, h = size
    sl, st, sr, sb = screen

    def fits(x, y):
        return sl + MARGIN <= x and x + w <= sr - MARGIN and st + MARGIN <= y and y + h <= sb - MARGIN

    def clear(boxes, x, y):
        return all(x + w <= bl or br <= x or y + h <= bt or bb <= y for bl, bt, br, bb in boxes)

    firm = ([tuple(keep_off)] if keep_off else []) + list(hard)
    xs = {sl + MARGIN, sr - MARGIN - w, *extra}
    ys = {st + MARGIN, sb - MARGIN - h, *extra_y}
    for bl, bt, br, bb in firm + list(labels):
        xs.update((br + GAP, bl - GAP - w, max(sl + MARGIN, min(bl, sr - MARGIN - w))))
        ys.update((bb + GAP, bt - GAP - h, max(st + MARGIN, min(bt, sb - MARGIN - h))))
    spots = [(x, y) for x in xs for y in ys if fits(x, y)]
    if not spots:
        return max(sl + MARGIN, min(r + GAP, sr - MARGIN - w)), max(st + MARGIN, min(b + GAP, sb - MARGIN - h))

    def distance(spot):
        x, y = spot
        dx = max(l - (x + w), x - r, 0)
        dy = max(t - (y + h), y - b, 0)
        return dx * dx + dy * dy
    spots.sort(key=lambda s: (distance(s), s))
    for boxes in (firm + list(labels), firm):
        for x, y in spots:
            if clear(boxes, x, y):
                return x, y
    # Nowhere free: keep off keep_off if possible, and cover the least of the figures.
    return min(spots, key=lambda s: (bool(keep_off) and not clear([tuple(keep_off)], *s),
                                     overlap((s[0], s[1], s[0] + w, s[1] + h), hard), distance(s)))


def free_spot(preferred, size, screen, avoid=None, soft=None) -> tuple:
    """Top-left for a card that has its own usual place (preferred (x, y)), moved to the nearest free spot
    when that place would cover a figure, another card, or (if avoidable) words."""
    x, y = preferred
    w, h = size
    hard, labels = _boxes(avoid), _boxes(soft)
    rect = (x, y, x + w, y + h)
    if not overlap(rect, hard) and not overlap(rect, labels):
        return x, y
    return _nearest_free(rect, size, screen, hard, labels)


class Board:
    """What is on screen right now, shared by the overlay's cards and the button card, so neither is put
    over the sheet's figures or over the other. Coordinates are logical screen pixels."""

    def __init__(self):
        self.figures, self.words, self._taken = [], [], {}

    def show_sheet(self, figures, words=None) -> None:
        self.figures, self.words = _boxes(figures), _boxes(words)

    def take(self, name: str, rect) -> None:
        self._taken[name] = tuple(rect)

    def release(self, prefix: str = "") -> None:
        for name in [n for n in self._taken if n.startswith(prefix)]:
            del self._taken[name]

    def others(self, name: str = "") -> list:
        return [r for n, r in self._taken.items() if n != name]

    def clear(self) -> None:
        self.__init__()


BOARD = Board()


def record_widget(name: str, placed: dict) -> None:
    """Rehearsals: where a Clicky window landed and whether it covers a figure (scene log "widget")."""
    try:
        import scene_log
        scene_log.record("widget", name=name, **placed)
    except Exception:
        pass


def settle(name: str, preferred, size, bounds) -> tuple:
    """Where a Clicky window (button card, caption, notice card) goes: its usual place unless figures,
    a card or another Clicky window are there, then the nearest free place. It is then taken on the board.

    Returns (x, y, log) where log is what the rehearsal robot checks: rect, moved, covers_figures.
    """
    w, h = size
    x, y = free_spot(preferred, size, bounds, BOARD.figures + BOARD.others(name), BOARD.words)
    rect = (x, y, x + w, y + h)
    BOARD.take(name, rect)
    return x, y, {"rect": list(rect), "screen": list(bounds), "moved": (x, y) != tuple(preferred),
                  "figures": len(BOARD.figures),
                  "covers_figures": sum(1 for f in BOARD.figures if overlap(rect, [f]) > 0),
                  "covers_cards": sum(1 for f in BOARD.others(name) if overlap(rect, [f]) > 0)}


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
    line = _stem(anchor, card)
    if line and abs(line[2] - line[0]) + abs(line[3] - line[1]) > STEM_MAX:
        return ()            # a long line would cross other figures; the card's colour and the frame tie them
    return line


STEM_MAX = 48


def _stem(anchor, card) -> tuple:
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


def pointer_spot(box, screen=None) -> tuple:
    """Where the pointer rests for a framed range: just under its bottom edge, near its left end, with the tip
    touching the frame. Excel draws text at the bottom of a cell, so the top of the cell below is the emptiest
    place next to the range; when the range is at the bottom of the screen, it rests just above instead."""
    l, t, r, b = box
    x = min(l + 12, (l + r) / 2)
    y = b + 6
    if screen is not None and y + 14 > screen[3]:
        y = t - 6
    return x, y


# ── The three graphics: leader tags, sticky notes, profit bridge ─────────────

TAG_W, TAG_H, TAG_GAP, CHECK_H, FAN = 250, 54, 8, 30, 44
NOTE_W = 250
BAR_W, BAR_GAP, CHART_PAD, CHART_TOP, CHART_PLOT, CHART_LABEL = 88, 34, 22, 70, 150, 46


def edge_point(box, toward) -> tuple:
    """The point on box's edge facing toward (x, y): where a wire leaves or reaches it."""
    l, t, r, b = box
    x, y = toward
    if x >= r:
        return r, min(max(y, t), b)
    if x <= l:
        return l, min(max(y, t), b)
    return min(max(x, l), r), (b if y >= b else t)


def leaders_layout(anchor, n_tags: int, screen, avoid=None, soft=None) -> dict:
    """Tags stacked in one column beside the figure, a check line under them, and one wire per tag.

    The column is placed like a card (beside the figure, never over figures when anything is free), so the
    tags cover no number; each wire runs from the figure's edge to the middle of its tag's near edge.
    """
    h = n_tags * TAG_H + (n_tags - 1) * TAG_GAP + TAG_GAP + CHECK_H
    x, y = place_card(anchor, (TAG_W, h), screen, avoid, soft)
    hard = _boxes(avoid)
    if x >= anchor[2]:
        # Room for the wires to fan out past the last column of figures, not over it.
        moved = min(x + FAN - GAP, screen[2] - MARGIN - TAG_W)
        if moved > x and not overlap((moved, y, moved + TAG_W, y + h), hard):
            x = moved
    tags = [(x, y + i * (TAG_H + TAG_GAP), x + TAG_W, y + i * (TAG_H + TAG_GAP) + TAG_H) for i in range(n_tags)]
    check = (x, tags[-1][3] + TAG_GAP, x + TAG_W, tags[-1][3] + TAG_GAP + CHECK_H) if tags else (x, y, x + TAG_W, y + CHECK_H)
    wires = []
    if x >= anchor[2]:
        # Tags to the right, past other columns of figures: every wire leaves along the figure's bottom
        # gridline (between two rows of text, so it strikes through no number) and fans out near the tags.
        past = [bx[2] for bx in hard if bx[1] <= anchor[3] <= bx[3] and anchor[2] <= bx[2] <= x]
        fan = max([anchor[2]] + [p + 6 for p in past]) if past else max(anchor[2], x - FAN)
        fan = min(fan, x - 8)
        for tag in tags:
            wires.append([(anchor[2], anchor[3]), (fan, anchor[3]), (x, (tag[1] + tag[3]) / 2)])
    else:
        for tag in tags:
            mid = ((tag[0] + tag[2]) / 2, (tag[1] + tag[3]) / 2)
            start = edge_point(anchor, mid)
            wires.append([start, start, edge_point(tag, start)])
    return {"group": (x, y, x + TAG_W, y + h), "tags": tags, "check": check, "wires": wires}


def notes_layout(anchors, heights, screen, avoid=None, soft=None) -> list:
    """One rect per note, each beside its own anchor, never over figures, the ranges, or another note."""
    placed, taken = [], _boxes(avoid)
    ranges = [tuple(a) for a in anchors if a]
    for anchor, h in zip(anchors, heights):
        others = taken + [r for r in ranges if r != tuple(anchor)] + placed
        x, y = place_card(anchor, (NOTE_W, h), screen, others, soft)
        placed.append((x, y, x + NOTE_W, y + h))
    return placed


def bridge_size(n_bars: int) -> tuple:
    return (2 * CHART_PAD + n_bars * BAR_W + (n_bars - 1) * BAR_GAP,
            CHART_TOP + CHART_PLOT + CHART_LABEL + CHART_PAD)


def bridge_bars(amounts, totals, plot) -> list:
    """Bar rects for a waterfall inside plot (l, t, r, b): totals stand on zero, steps float from the running sum.

    Returns [(l, t, r, b, top_y)] where top_y is where the value label sits (above the bar's upper end).
    """
    l, t, r, b = plot
    level, spans = 0, []
    for amount, total in zip(amounts, totals):
        if total:
            lo, hi = sorted((0, amount))
            level = amount
        else:
            lo, hi = sorted((level, level + amount))
            level += amount
        spans.append((lo, hi))
    low = min(0, *(s[0] for s in spans))
    high = max(0, *(s[1] for s in spans)) or 1
    scale = (b - t) / (high - low)

    def y_of(v):
        return b - (v - low) * scale
    out = []
    for i, (lo, hi) in enumerate(spans):
        x = l + i * (BAR_W + BAR_GAP)
        top, bottom = y_of(hi), y_of(lo)
        if bottom - top < 3:
            top = bottom - 3
        out.append((x, top, x + BAR_W, bottom, top))
    return out
