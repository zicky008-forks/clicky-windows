"""Robot checks for the real-time Excel build (PR #13): nothing covers a figure, the 3 template graphics
add up, and every turn reports how fast the first feedback came.

Read only from the scene log — what Clicky itself measured when it placed each piece:

  card / choice_card / graphic / widget   {rect(s), covers_figures: int, covers_range: bool}   off the figures
  beat {graphic: {kind: "leaders", figure, sum, ok, tags: [{account, amount}]}}       number → accounts
  beat {graphic: {kind: "bridge", ok, bars: [{label, amount, total}]}}                profit bridge
  beat {graphic: {kind: "notes", notes: [{codes, warn, action, ref}]}}                one note per trap
  graphic {kind: "notes", total, notes}                                               notes really painted
  latency {source, mode: code|ai, label, first_feedback, ready, first_point, first_voice, done}  ms, one per turn

Checked only when the case's expected file has "layout", so an older build is not failed for it.
"""
from __future__ import annotations

from checks import Check

PLACED = ("card", "choice_card", "graphic", "widget")


def _graphics(events, kind):
    return [e["graphic"] for e in events
            if e.get("event") == "beat" and (e.get("graphic") or {}).get("kind") == kind]


def _millions(amount) -> int:
    return int(round(float(amount) / 1_000_000))


def _where(e) -> str:
    what = e.get("event") if e.get("event") != "graphic" else f"graphic {e.get('kind', '')}"
    title = e.get("title") or ""
    return f"{what}{(' «' + title + '»') if title else ''} lúc {e.get('t', '?')} s"


def cover_checks(events) -> list:
    placed = [e for e in events if e.get("event") in PLACED and "covers_figures" in e]
    over = [e for e in placed if (e.get("covers_figures") or 0) > 0]
    own = [e for e in placed if e.get("covers_range")]
    return [
        Check("Thẻ, nút chọn và hình không che con số (Clicky tự đo)", bool(placed) and not over,
              "không có sự kiện nào ghi covers_figures" if not placed else
              "; ".join(f"{_where(e)} che {e['covers_figures']} số, rect {e.get('rect') or e.get('rects')}"
                        for e in over[:4]) or f"{len(placed)} mảnh, không che số"),
        Check("Không mảnh nào đè lên chính vùng đang giải thích", bool(placed) and not own,
              "; ".join(_where(e) for e in own[:4]) or ""),
    ]


def leaders_checks(events, spec) -> list:
    shown = _graphics(events, "leaders")
    if not shown:
        return [Check("Hình dây nối: số → các tài khoản", False, "không có cảnh dây nối")]
    g = shown[0]
    tags = g.get("tags") or []
    accounts = [str(t.get("account")) for t in tags]
    want_fig = spec.get("figure")
    want_acc = [str(a) for a in spec.get("accounts") or []]
    sums = g.get("sum") == g.get("figure") and g.get("ok") is True
    fig_ok = want_fig is None or g.get("figure") == want_fig
    acc_ok = not want_acc or sorted(accounts) == sorted(want_acc)
    return [Check("Hình dây nối: số → các tài khoản", sums and fig_ok and acc_ok,
                  f"số {g.get('figure')} · cộng {g.get('sum')} · tài khoản {', '.join(accounts)}"
                  + ("" if fig_ok else f" · cần số {want_fig}")
                  + ("" if acc_ok else f" · cần tài khoản {', '.join(want_acc)}"))]


def bridge_checks(events, spec) -> list:
    shown = _graphics(events, "bridge")
    if not shown:
        return [Check("Hình cầu lợi nhuận: báo cáo → thực", False, "không có cảnh cầu lợi nhuận")]
    g = shown[0]
    bars = [_millions(b.get("amount", 0)) for b in g.get("bars") or []]
    want = spec.get("bars_millions")
    ok = g.get("ok") is True and (not want or bars == list(want))
    return [Check("Hình cầu lợi nhuận: báo cáo → thực", ok,
                  " → ".join(f"{b} tr" for b in bars) + ("" if g.get("ok") else " · cầu không khép")
                  + ("" if not want or bars == list(want) else f" · cần {' → '.join(f'{b} tr' for b in want)}"))]


def notes_checks(events, spec) -> list:
    out = []
    shown = _graphics(events, "notes")
    codes = {c for g in shown for n in g.get("notes") or [] for c in n.get("codes") or []}
    want = list(spec.get("codes") or [])
    missing = [c for c in want if c not in codes]
    out.append(Check("Giấy nhớ: mỗi lỗi trên sheet Kiem tra có một tờ", bool(shown) and not missing,
                     "không có cảnh giấy nhớ" if not shown else
                     (f"thiếu {', '.join(missing)}" if missing else f"{len(codes)} mã: {', '.join(sorted(codes))}")))
    painted = [e for e in events if e.get("event") == "graphic" and e.get("kind") == "notes"]
    dropped = [e for e in painted if len(e.get("notes") or []) < (e.get("total") or 0)]
    out.append(Check("Không tờ giấy nhớ nào bị bỏ khi vẽ", bool(painted) and not dropped,
                     "không có sự kiện vẽ giấy nhớ" if not painted else
                     "; ".join(f"{_where(e)}: vẽ {len(e.get('notes') or [])}/{e.get('total')}" for e in dropped)))
    sheet = spec.get("fix_sheet")
    if sheet:
        on_fix = [g for g in shown if any(sheet in (n.get("ref") or "") for n in g.get("notes") or [])]
        n_fix = max((len(g.get("notes") or []) for g in on_fix), default=0)
        need = spec.get("fix_causes")
        out.append(Check(f"Câu hỏi sửa trên '{sheet}': mỗi nguyên nhân một tờ",
                         bool(on_fix) and (need is None or n_fix == need),
                         f"{n_fix} tờ" + ("" if need is None else f", cần {need}")))
    return out


def _gap(e, a, b):
    return e[b] - e[a] if isinstance(e.get(a), (int, float)) and isinstance(e.get(b), (int, float)) else None


def turns_measured(events) -> bool:
    """Every turn so far has its latency line (Clicky writes it when the turn ends)."""
    turns = sum(e.get("event") == "turn" for e in events)
    return sum(e.get("event") == "latency" for e in events) >= turns


def _code_turns(events):
    """(latency line, whether its turn loaded a query) for each code turn, in order. The close_step events
    of a turn sit between its turn event and its latency line."""
    out, loaded = [], False
    for e in events:
        kind = e.get("event")
        if kind == "turn":
            loaded = False
        elif kind == "close_step" and e.get("step") == "query_load":
            loaded = True
        elif kind == "latency":
            if e.get("source") != "voice" and e.get("mode", "code") == "code":
                out.append((e, loaded))
            loaded = False
    return out


def ready_checks(events, spec, tagged) -> list:
    """Beta gates (lead, 07/11; first query turn 8 s after three clean cold runs 6.8/7.9/7.5 s): a code turn has its answer within ready_ms; the first turn of the session that
    loads a query (Excel's first Power Query start) may take up to first_excel_ready_ms, but only while the
    caption tells the user what it is waiting for."""
    limit, first_limit = spec.get("ready_ms"), spec.get("first_excel_ready_ms")
    if limit is None:
        return []
    turns = [(e, q) for e, q in _code_turns(events) if isinstance(e.get("ready"), (int, float))]
    if not turns:
        return []
    first_q = next((i for i, (_, q) in enumerate(turns) if q), None)
    slow, notes = [], []
    for i, (e, _) in enumerate(turns):
        name = e.get("label") or e.get("source", "?")
        if i == first_q and first_limit is not None:
            ok = e["ready"] <= first_limit and "first_caption" in e
            notes.append(f"lượt nạp Power Query đầu '{name}': {e['ready']} ms"
                         + ("" if "first_caption" in e else ", không có dòng chữ báo đang chờ"))
            if not ok:
                slow.append(notes[-1])
        elif e["ready"] > limit:
            slow.append(f"'{name}': {e['ready']} ms")
    title = f"Lượt chạy bằng code: tính xong ≤ {limit} ms" + (
        f" (lượt nạp Power Query đầu ≤ {first_limit} ms, có dòng chữ báo chờ)" if first_limit is not None else "")
    return [Check(title, not slow, "; ".join(slow[:4]) or
                  f"chậm nhất {max(e['ready'] for e, _ in turns)} ms" + (" · " + notes[0] if notes else ""),
                  blocking=tagged)]


def latency_checks(events, spec) -> list:
    """Code turns must answer fast; voice turns are measured only. Limits block only on a build that tags
    each turn with its mode (PR #16): an older build's numbers are shown but cannot fail the run."""
    turns = [e for e in events if e.get("event") == "turn"]
    lat = [e for e in events if e.get("event") == "latency"]
    voice = [e for e in lat if e.get("source") == "voice"]
    code = [e for e in lat if e.get("source") != "voice" and e.get("mode", "code") == "code"]
    tagged = any("mode" in e for e in lat)

    def line(e):
        bits = [f"{e.get('label') or e.get('source', '?')}: phản hồi đầu {e.get('first_feedback')} ms"]
        for key, word in (("ready", "tính xong"), ("first_point", "trỏ đầu"), ("first_voice", "giọng đầu")):
            if e.get(key) is not None:
                bits.append(f"{word} {e[key]} ms")
        return ", ".join(bits)

    out = [Check("Mỗi lượt có đo thời gian phản hồi", bool(lat) and len(lat) >= len(turns),
                 f"{len(lat)} lần đo / {len(turns)} lượt · " + "; ".join(line(e) for e in lat[:6]),
                 blocking=tagged)]
    limit = spec.get("first_feedback_ms")
    if limit is not None and code:
        # A mark Clicky did not measure has no key at all; 0 is a real 0 ms (build thread, 07/11).
        timed = [e for e in code if isinstance(e.get("first_feedback"), (int, float))]
        slow = [e for e in timed if e["first_feedback"] > limit]
        untimed = len(code) - len(timed)
        out.append(Check(f"Lượt chạy bằng code: phản hồi đầu ≤ {limit} ms mọi lượt", not slow,
                         "; ".join(line(e) for e in slow[:4]) or
                         (f"chậm nhất {max(e['first_feedback'] for e in timed)} ms" if timed else "")
                         + (f" · {untimed} lượt chưa đo phản hồi đầu" if untimed else ""),
                         blocking=tagged))
    out += ready_checks(events, spec, tagged)
    limit = spec.get("point_after_ready_ms")
    pairs = [(e, _gap(e, "ready", "first_point")) for e in code]
    pairs = [(e, g) for e, g in pairs if g is not None]
    if limit is not None and pairs:
        slow = [(e, g) for e, g in pairs if g > limit]
        out.append(Check(f"Có kết quả rồi thì con trỏ bay tới trong ≤ {limit} ms", not slow,
                         "; ".join(f"{e.get('label') or e.get('source')}: {g} ms" for e, g in slow[:4]) or
                         f"chậm nhất {max(g for _, g in pairs)} ms", blocking=tagged))
    if voice:
        out.append(Check("Lượt nói: thả phím → chữ → trỏ/giọng (chỉ đo)", True,
                         "; ".join(f"chữ {e.get('transcript', '—')} ms, trỏ {e.get('first_point', '—')} ms, "
                                   f"giọng {e.get('first_voice', '—')} ms" for e in voice[:4]), blocking=False))
    return out


_SHOWN = ("beat", "choices", "notice", "card", "choice_card", "draw", "point")


def silent_gaps(events) -> list:
    """(turn label, longest silence in s) per turn: from the turn's start to its first scene or question,
    the longest stretch with nothing new on screen. Stage captions (close_step "caption", #52) count as
    something new. The reading time between two scenes is not silence, so only the wait before the first
    scene is measured (Case 05 on ebffea4: ~50 s of nothing after the receipt was confirmed)."""
    out = []
    starts = [i for i, e in enumerate(events) if e.get("event") == "turn"]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(events)
        t0 = events[i].get("t")
        if not isinstance(t0, (int, float)):
            continue
        marks = [t0]
        for e in events[i + 1:end]:
            t = e.get("t")
            if not isinstance(t, (int, float)):
                continue
            if e.get("event") == "close_step" and e.get("step") == "caption":
                marks.append(t)
            elif e.get("event") in _SHOWN:
                marks.append(t)
                break
        gaps = [b - a for a, b in zip(marks, marks[1:])]
        if gaps:
            out.append(((events[i].get("text") or "?")[:40], round(max(gaps), 1)))
    return out


def silence_check(events, spec) -> list:
    limit = spec.get("silent_max_ms", 2000)
    gaps = silent_gaps(events)
    if not gaps:
        return []
    slow = [(label, g) for label, g in gaps if g * 1000 > limit]
    detail = "; ".join(f"{label}: {g} s" for label, g in (slow or gaps)[:6])
    return [Check(f"Không lượt nào im quá {limit / 1000:g} s trước cảnh đầu (chỉ đo)", not slow, detail,
                  blocking=bool(spec.get("silent_blocking")))]


def void_timing(checks_: list, why) -> list:
    """Timing gates measured on a throttled PC (on battery, CPU under 90%): shown, never failed or passed."""
    if not why:
        return checks_
    for c in checks_:
        c.blocking = False
        c.detail = f"KHÔNG TÍNH ({why}): " + c.detail
    return checks_


def evaluate(events, expected, void=None) -> list:
    """void: why this PC's timings do not count (power.void_reason); the latency gates then only inform."""
    spec = expected.get("layout")
    if spec is None:
        return []
    out = cover_checks(events)
    if "leaders" in spec:
        out += leaders_checks(events, spec["leaders"])
    if "bridge" in spec:
        out += bridge_checks(events, spec["bridge"])
    if "notes" in spec:
        out += notes_checks(events, spec["notes"])
    if "latency" in spec:
        out += void_timing(latency_checks(events, spec["latency"]) + silence_check(events, spec["latency"]), void)
    return out
