"""Robot checks for the real-time Excel build (PR #13): nothing covers a figure, the 3 template graphics
add up, and every turn reports how fast the first feedback came.

Read only from the scene log — what Clicky itself measured when it placed each piece:

  card / choice_card / graphic   {rect(s), covers_figures: int, covers_range: bool}   placed off the figures
  beat {graphic: {kind: "leaders", figure, sum, ok, tags: [{account, amount}]}}       number → accounts
  beat {graphic: {kind: "bridge", ok, bars: [{label, amount, total}]}}                profit bridge
  beat {graphic: {kind: "notes", notes: [{codes, warn, action, ref}]}}                one note per trap
  graphic {kind: "notes", total, notes}                                               notes really painted
  latency {source, first_feedback, first_point, done}   ms                            one per turn

Checked only when the case's expected file has "layout", so an older build is not failed for it.
"""
from __future__ import annotations

from checks import Check

PLACED = ("card", "choice_card", "graphic")


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


def latency_checks(events, spec) -> list:
    turns = [e for e in events if e.get("event") == "turn"]
    lat = [e for e in events if e.get("event") == "latency"]
    limit = spec.get("first_feedback_ms")
    worst = max((e.get("first_feedback") or 0 for e in lat), default=None)
    detail = "; ".join(f"{e.get('source', '?')}: phản hồi đầu {e.get('first_feedback')} ms, trỏ đầu "
                       f"{e.get('first_point', '—')} ms" for e in lat[:6])
    out = [Check("Mỗi lượt có đo thời gian phản hồi", bool(lat) and len(lat) >= len(turns),
                 f"{len(lat)} lần đo / {len(turns)} lượt · {detail}")]
    if limit is not None:
        out.append(Check(f"Phản hồi đầu tiên dưới {limit} ms", worst is not None and worst <= limit,
                         f"chậm nhất {worst} ms", blocking=bool(spec.get("blocking"))))
    return out


def evaluate(events, expected) -> list:
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
        out += latency_checks(events, spec["latency"])
    return out
