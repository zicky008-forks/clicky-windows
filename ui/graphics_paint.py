"""Painting the three explanation graphics on the overlay: leader tags, sticky notes, profit bridge.

The figures and words come from engine.visuals through the month close; this module only lays them out
(ui.card_layout, tested without Qt) and paints them. Each graphic is laid out once, logged for the rehearsal
robot with where every piece landed and whether it covers a figure, and registered on the shared board so
the button card keeps off it.
"""
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QPen

from ui.card_layout import (BOARD, BAR_W, CHART_LABEL, CHART_PAD, CHART_PLOT, CHART_TOP, NOTE_W, TONES, bridge_bars,
                            bridge_size, leaders_layout, notes_layout, overlap, place_card)

_INK, _MUTED, _LINE = QColor(15, 23, 42), QColor(71, 85, 105), QColor(226, 232, 240)
_FLAGS = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap)
STEP_S = 0.45            # one tag, note or column after another, like a hand placing them


def _font(size, weight=QFont.Weight.Normal):
    return QFont("Segoe UI", size, weight)


def _alpha(c, u):
    c = QColor(c)
    c.setAlpha(int(c.alpha() * max(0.0, min(1.0, u))))
    return c


def _shown(ann, i):
    """Fade of the i-th piece: pieces arrive one by one, STEP_S apart."""
    return max(0.0, min(1.0, (time.monotonic() - ann["start"] - i * STEP_S) / 0.3))


def _shadowed_box(p, rect, fill, u, radius=10, border=_LINE):
    p.setPen(Qt.PenStyle.NoPen)
    for spread, a in ((8, 14), (4, 22)):
        p.setBrush(QColor(15, 23, 42, int(a * u)))
        p.drawRoundedRect(rect.adjusted(-spread / 2, -spread / 4, spread / 2, spread), radius + 2, radius + 2)
    p.setBrush(_alpha(fill, u))
    p.setPen(QPen(_alpha(border, u), 1))
    p.drawRoundedRect(rect, radius, radius)


def _record(kind, ann, rects, extra):
    import scene_log
    figures = ann.get("avoid") or []
    scene_log.record("graphic", kind=kind, rects=[list(r) for r in rects], figures=len(figures),
                     covers_figures=sum(1 for r in rects for f in figures if overlap(r, [f]) > 0),
                     covers_range=any(overlap(r, [a]) > 0 for r in rects for a in ann.get("_ranges", [])), **extra)


def _take(ann, rects):
    for i, r in enumerate(rects):
        BOARD.take(f"card-g{id(ann)}-{i}", r)


def _avoid(ann):
    mine = f"card-g{id(ann)}-"
    return (ann.get("avoid") or []) + [r for n, r in BOARD._taken.items() if not n.startswith(mine)]


# ── 1. Leader tags ────────────────────────────────────────────────────────────

def paint_leaders(ov, p, ann, u):
    tags = ann.get("tags") or []
    anchor = ann.get("anchor")
    if not tags or not anchor:
        return
    layout = ann.get("_layout")
    if layout is None:
        layout = ann["_layout"] = leaders_layout(anchor, len(tags), ov._card_screen(anchor), _avoid(ann), ann.get("soft"))
        ann["_ranges"] = [anchor]
        _take(ann, [layout["group"]])
        _record("leaders", ann, layout["tags"] + [layout["check"]],
                {"title": ann.get("title", ""), "figure": ann.get("figure"), "sum": ann.get("sum"),
                 "ok": ann.get("ok"), "tags": [f"{t['account']} {t['text']}" for t in tags]})
    ox, oy = ov.x(), ov.y()
    accent = QColor(TONES["info"][0] if ann.get("ok") else TONES["bad"][0])
    # The figure itself: a soft frame so the wires have a clear origin.
    l, t, r, b = anchor
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_alpha(accent, u), 2))
    p.drawRoundedRect(QRectF(l - ox - 2, t - oy - 2, r - l + 4, b - t + 4), 5, 5)
    for i, (tag, wire, data) in enumerate(zip(layout["tags"], layout["wires"], tags)):
        k = _shown(ann, i) * u
        if k <= 0:
            continue
        (x1, y1), (xf, yf), (x2, y2) = [(px - ox, py - oy) for px, py in wire]
        path = QPainterPath(QPointF(x1, y1))
        path.lineTo(QPointF(xf, yf))
        mx = (xf + x2) / 2
        path.cubicTo(QPointF(mx, yf), QPointF(mx, y2), QPointF(x2, y2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_alpha(accent, 0.85 * k), 1.8))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(accent, k))
        p.drawEllipse(QPointF(x1, y1), 3.5, 3.5)
        rect = QRectF(tag[0] - ox, tag[1] - oy, tag[2] - tag[0], tag[3] - tag[1])
        _shadowed_box(p, rect, QColor(255, 255, 255, 250), k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(accent, k))
        p.drawRoundedRect(QRectF(rect.x() + 6, rect.y() + 10, 4, rect.height() - 20), 2, 2)
        p.setFont(_font(9, QFont.Weight.DemiBold))
        p.setPen(QPen(_alpha(_MUTED, k), 1))
        name = QFontMetrics(p.font()).elidedText(f"TK {data['account']} · {data['name']}", Qt.TextElideMode.ElideRight,
                                                 int(rect.width() - 30))
        p.drawText(QRectF(rect.x() + 18, rect.y() + 7, rect.width() - 26, 18), _FLAGS, name)
        p.setFont(_font(14, QFont.Weight.Bold))
        p.setPen(QPen(_alpha(_INK, k), 1))
        p.drawText(QRectF(rect.x() + 18, rect.y() + 24, rect.width() - 26, 26), _FLAGS, data["text"])
    k = _shown(ann, len(tags)) * u
    if k > 0:
        c = layout["check"]
        rect = QRectF(c[0] - ox, c[1] - oy, c[2] - c[0], c[3] - c[1])
        tone = TONES["ok" if ann.get("ok") else "bad"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(QColor(tone[1]), k))
        p.drawRoundedRect(rect, 8, 8)
        p.setFont(_font(9, QFont.Weight.DemiBold))
        p.setPen(QPen(_alpha(QColor(tone[0]), k), 1))
        mark = "✓ " if ann.get("ok") else "⚠ "
        p.drawText(rect.adjusted(10, 6, -8, -4), _FLAGS, mark + ann.get("check", ""))


# ── 2. Sticky notes ───────────────────────────────────────────────────────────

_NOTE_FILL, _NOTE_BAND, _NOTE_INK = QColor(254, 240, 138), QColor(250, 204, 21), QColor(66, 32, 6)


def _note_parts(note):
    warn_f, act_f = _font(10, QFont.Weight.DemiBold), _font(10)
    inner = NOTE_W - 28
    hw = QFontMetrics(warn_f).boundingRect(0, 0, inner, 400, _FLAGS, note.get("warn", "")).height()
    ha = QFontMetrics(act_f).boundingRect(0, 0, inner, 400, _FLAGS, "→ " + note.get("action", "")).height()
    return warn_f, act_f, hw, ha, 14 + 8 + hw + 6 + ha + 12


def paint_notes(ov, p, ann, u):
    notes = [n for n in ann.get("notes") or [] if n.get("anchor")]
    if not notes:
        return
    layout = ann.get("_layout")
    if layout is None:
        parts = [_note_parts(n) for n in notes]
        anchors = [n["anchor"] for n in notes]
        rects = notes_layout(anchors, [pt[4] for pt in parts], ov._card_screen(anchors[0]), _avoid(ann),
                             ann.get("soft"))
        layout = ann["_layout"] = (rects, parts)
        ann["_ranges"] = anchors
        _take(ann, rects)
        _record("notes", ann, rects, {"notes": [f"{n.get('warn', '')} → {n.get('action', '')}" for n in notes],
                                      "codes": [c for n in notes for c in n.get("codes", [])],
                                      "total": len(ann.get("notes") or [])})
    rects, parts = layout
    ox, oy = ov.x(), ov.y()
    for i, (note, rect, (warn_f, act_f, hw, ha, h)) in enumerate(zip(notes, rects, parts)):
        k = _shown(ann, i) * u
        if k <= 0:
            continue
        box = QRectF(rect[0] - ox, rect[1] - oy, rect[2] - rect[0], rect[3] - rect[1])
        _shadowed_box(p, box, _NOTE_FILL, k, radius=4, border=QColor(234, 179, 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(_NOTE_BAND, k))
        p.drawRect(QRectF(box.x(), box.y(), box.width(), 8))
        p.setFont(warn_f)
        p.setPen(QPen(_alpha(_NOTE_INK, k), 1))
        p.drawText(QRectF(box.x() + 14, box.y() + 18, NOTE_W - 28, hw + 2), _FLAGS, note.get("warn", ""))
        p.setFont(act_f)
        p.drawText(QRectF(box.x() + 14, box.y() + 24 + hw, NOTE_W - 28, ha + 2), _FLAGS, "→ " + note.get("action", ""))


# ── 3. Profit bridge ──────────────────────────────────────────────────────────

def paint_bridge(ov, p, ann, u):
    bars = ann.get("bars") or []
    anchor = ann.get("anchor")
    if len(bars) < 2 or not anchor:
        return
    w, h = bridge_size(len(bars))
    layout = ann.get("_layout")
    if layout is None:
        x, y = place_card(anchor, (w, h), ov._card_screen(anchor), _avoid(ann), ann.get("soft"))
        plot = (x + CHART_PAD, y + CHART_TOP, x + w - CHART_PAD, y + CHART_TOP + CHART_PLOT)
        rects = bridge_bars([b["amount"] for b in bars], [b["total"] for b in bars], plot)
        layout = ann["_layout"] = ((x, y, x + w, y + h), rects)
        ann["_ranges"] = [anchor]
        _take(ann, [layout[0]])
        _record("bridge", ann, [layout[0]], {"title": ann.get("title", ""), "ok": ann.get("ok"),
                                             "bars": [f"{b['label']} {b['text']}" for b in bars]})
    (x, y, r, b), rects = layout
    ox, oy = ov.x(), ov.y()
    card = QRectF(x - ox, y - oy, r - x, b - y)
    _shadowed_box(p, card, QColor(255, 255, 255, 252), u, radius=14)
    p.setFont(_font(10, QFont.Weight.Bold))
    p.setPen(QPen(_alpha(_INK, u), 1))
    p.drawText(QRectF(card.x() + CHART_PAD, card.y() + 12, card.width() - 2 * CHART_PAD, 36), _FLAGS,
               ann.get("title", ""))
    if not ann.get("ok"):
        p.setFont(_font(9, QFont.Weight.DemiBold))
        p.setPen(QPen(_alpha(QColor(TONES["bad"][0]), u), 1))
        p.drawText(QRectF(card.x() + CHART_PAD, card.bottom() - CHART_PAD, card.width() - 2 * CHART_PAD, 18), _FLAGS,
                   "⚠ Các cột chưa cộng đúng tới số cuối")
    for i, (bar, rect) in enumerate(zip(bars, rects)):
        k = _shown(ann, i + 1) * u
        if k <= 0:
            continue
        l, t, rr, bb, top = (rect[0] - ox, rect[1] - oy, rect[2] - ox, rect[3] - oy, rect[4] - oy)
        if bar["total"]:
            colour = QColor(TONES["info"][0]) if i == len(bars) - 1 else QColor(148, 163, 184)
        else:
            colour = QColor(TONES["bad"][0] if bar["amount"] < 0 else TONES["ok"][0])
        grow = min(1.0, k * 1.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(colour, k))
        height = (bb - t) * grow
        p.drawRoundedRect(QRectF(l, bb - height if bar["total"] or bar["amount"] >= 0 else t, rr - l, height), 3, 3)
        if i + 1 < len(rects):
            # A thin step line carries the running total to the next column.
            nxt = rects[i + 1]
            level = t if (bar["total"] or bar["amount"] >= 0) else bb
            p.setPen(QPen(_alpha(QColor(148, 163, 184), k), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(rr, level), QPointF(nxt[0] - ox, level))
        p.setFont(_font(11, QFont.Weight.Bold))
        p.setPen(QPen(_alpha(colour if not bar["total"] else _INK, k), 1))
        p.drawText(QRectF(l - BAR_W * 0.2, top - 22, BAR_W * 1.4, 20),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom), bar["text"])
        p.setFont(_font(9))
        p.setPen(QPen(_alpha(_MUTED, k), 1))
        base = card.y() + CHART_TOP + CHART_PLOT + 8
        p.drawText(QRectF(l - BAR_W * 0.2, base, BAR_W * 1.4, CHART_LABEL),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap),
                   bar["label"])
