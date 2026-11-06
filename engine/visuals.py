"""What the three explanation graphics show, computed by code from the engine's own figures.

1. Leader tags: a report figure and the source accounts it is made of, with a check that they add up.
2. Sticky notes: one per trap the engine found, a warning and one thing to do; none is ever dropped.
3. Profit bridge: from the reported profit to the real one, one column per adjustment the engine made.

Nothing here comes from the AI. Amounts are whole đồng (int) so the overlay and the scene log can carry them
as plain JSON; the words are fixed Vietnamese templates around engine figures.
"""
from __future__ import annotations

import re
from decimal import Decimal

from engine.monthly_report import spoken

TOLERANCE = Decimal(1)
COST_KEYS = ("cogs", "selling", "admin", "fin_exp", "oth_exp", "tax")


def _int(amount) -> int:
    return int(Decimal(amount).quantize(Decimal(1)))


# ── 1. Leader tags ────────────────────────────────────────────────────────────

def figure_to_explain(figures: list, variances: list):
    """The cost line worth opening up: made of two or more accounts, and moved the most since last period."""
    moved = {v.key: abs(v.change) for v in variances}
    split = [f for f in figures if f.key in COST_KEYS and len({s[0] for s in f.sources}) >= 2]
    if not split:
        return None
    return max(split, key=lambda f: (moved.get(f.key, Decimal(0)), f.key == "selling"))


def leader_tags(figure) -> dict:
    """The figure, its accounts (biggest first), and whether they add up to it to the đồng."""
    by_account: dict = {}
    for account, name, _row, _side, amount in figure.sources:
        entry = by_account.setdefault(account, [name, Decimal(0)])
        entry[1] += amount
    tags = sorted(({"account": a, "name": n, "amount": _int(v), "text": spoken(v)}
                   for a, (n, v) in by_account.items()), key=lambda t: -t["amount"])
    total = sum((Decimal(t["amount"]) for t in tags), Decimal(0))
    gap = Decimal(figure.value) - total
    ok = abs(gap) <= TOLERANCE
    return {
        "title": figure.label, "figure": _int(figure.value), "figure_text": spoken(figure.value), "tags": tags,
        "sum": _int(total), "ok": ok,
        "check": (f"{len(tags)} tài khoản cộng lại đúng {spoken(figure.value)}" if ok else
                  f"Các tài khoản cộng lại {spoken(total)}, lệch {spoken(abs(gap))} so với báo cáo"),
    }


def leader_sentence(tags: dict) -> str:
    top = tags["tags"][0]
    text = (f"{tags['title']} {tags['figure_text']} đến từ {len(tags['tags'])} tài khoản, nhiều nhất là "
            f"TK {top['account']} {top['name'].lower()} {top['text']}.")
    if not tags["ok"]:
        text += f" Cảnh báo: {tags['check'].lower()}."
    return text


# ── 3. Profit bridge ──────────────────────────────────────────────────────────

def profit_bridge(reported, steps: list, real) -> dict:
    """Columns from the reported profit to the real one: [(label, change)] in between, all in đồng.

    ok says the columns add up to the real profit; when they do not, the overlay shows it in red.
    """
    bars = [{"label": "Trên báo cáo", "amount": _int(reported), "text": spoken(reported), "total": True}]
    running = Decimal(reported)
    for label, change in steps:
        running += Decimal(change)
        bars.append({"label": label, "amount": _int(change),
                     "text": ("−" if change < 0 else "+") + spoken(abs(Decimal(change))), "total": False})
    bars.append({"label": "Lợi nhuận thực", "amount": _int(real), "text": spoken(real), "total": True})
    return {"title": f"Lợi nhuận sau thuế: {spoken(reported)} → {spoken(real)}", "bars": bars,
            "ok": abs(running - Decimal(real)) <= TOLERANCE}


def bridge_sentence(bridge: dict) -> str:
    first, last = bridge["bars"][0], bridge["bars"][-1]
    steps = "; ".join(f"{'trừ' if b['amount'] < 0 else 'cộng'} {b['text'][1:]} {b['label'][:1].lower()}{b['label'][1:]}"
                      for b in bridge["bars"][1:-1])
    return f"Từ {first['text']} trên báo cáo xuống {last['text']} thực: {steps}."


# ── 2. Sticky notes ───────────────────────────────────────────────────────────

_TK = re.compile(r"TK (\d+)")


def _line_at(tb, row: int):
    return next((l for l in tb.lines if l.row == row), None) if tb is not None and row else None


def report_notes(issues: list, tb, overstated, line_of: dict) -> list:
    """One note per trap the engine found in this period's books: {"codes", "warn", "action", "line"}.

    line is a report line key when the trap sits on one (the note goes beside it), else None (beside the
    report's title). Traps that tell one story (returns booked in Nợ 511 shows up three ways) share a note,
    which lists every code it covers, so a trap is never left without a note.
    """
    notes, used = [], set()
    own = [i for i in issues if not i.message.startswith("[")]
    if overstated and overstated > 0:
        codes = [i.code for i in own if i.code in ("closing_mismatch", "profit_tie")]
        used.update(codes)
        notes.append({"codes": ["returns_in_revenue"] + codes,
                      "warn": f"{spoken(overstated)} hàng bán bị trả lại đang ghi Nợ TK 511",
                      "action": "Chuyển sang TK 521 trước khi gửi sếp", "line": line_of.get("returns")})
    for issue in own:
        if issue.code in used and issue.code in ("closing_mismatch", "profit_tie"):
            continue
        line = _line_at(tb, issue.row)
        account = line.account if line else (_TK.search(issue.message).group(1) if _TK.search(issue.message) else "")
        warn, action, where = _note_words(issue, line, account)
        notes.append({"codes": [issue.code], "warn": warn, "action": action,
                      "line": line_of.get(where) if where else None})
    earlier = [i for i in issues if i.message.startswith("[")]
    if earlier:
        notes.append({"codes": sorted({i.code for i in earlier}), "warn": f"Sổ kỳ trước còn {len(earlier)} lỗi",
                      "action": "Xem sheet Kiem tra trước khi so sánh", "line": None})
    return notes


def _note_words(issue, line, account) -> tuple:
    """(warning, action, report line key or None) for one trap: short enough to read at a glance."""
    code, name = issue.code, (line.name if line else "")
    if code == "wrong_side" and line is not None:
        return (f"TK {account} {name.lower()} âm {spoken(line.close_cr - line.close_dr)}",
                "Kiểm phiếu thu, nhập kho bị thiếu", None)
    if code == "recurring_missing":
        return (f"Chưa thấy {issue.what} (thiếu khoảng {spoken(issue.amount)})",
                "Hỏi kế toán đã ghi chưa rồi chạy lại", None)
    if code == "unmapped":
        return (f"TK {account} {name.lower()} chưa nằm trong dòng nào", "Xếp vào đúng dòng báo cáo", None)
    if code == "not_closed":
        return ("Sổ chưa khoá, số còn sơ bộ", "Khoá sổ rồi chạy lại trước khi gửi sếp", None)
    if code == "closing_mismatch":
        return ("Kết chuyển 911 khác thu nhập trên các TK", "Kiểm hàng trả lại, chiết khấu ghi Nợ 511", "net_rev")
    if code == "profit_tie":
        return ("Lợi nhuận khác số kết chuyển vào TK 4212", "Kiểm bút toán kết chuyển cuối kỳ", "pat")
    if code == "unbalanced":
        return ("Sổ cân đối lệch Nợ và Có", "Xuất lại sổ cân đối từ phần mềm kế toán", None)
    if code == "text_amount":
        return (f"Dòng {issue.row}: số tiền đang là chữ", "Đổi sang dạng số rồi chạy lại", None)
    if code == "unreadable_account":
        return (f"Dòng {issue.row}: mã TK không đọc được", "Sửa mã TK trong sổ cân đối", None)
    if code == "duplicate":
        return (f"TK {account} có hai dòng", "Gộp hoặc bỏ dòng lặp trong sổ cân đối", None)
    if code in ("rollforward", "parent_sum"):
        return (f"TK {account}: số dư không cộng đúng", "Kiểm lại dòng này trong sổ cân đối", None)
    return (issue.message.split(":")[0][:60], "Xem sheet Kiem tra", None)


def cause_notes(rows: list) -> list:
    """Notes for the causes found between Excel and the ledger (the detail rows on the check sheet).

    rows are the check sheet's detail rows: [cause, count, amount, "", explanation]."""
    out = []
    for i, row in enumerate(rows):
        cause, count, amount = row[0], row[1], row[2]
        size = f"{count} chứng từ" if isinstance(count, int) and count else ""
        money = spoken(Decimal(amount)) if isinstance(amount, (int, Decimal)) and amount else ""
        detail = " · ".join(x for x in (size, money) if x)
        out.append({"codes": [str(cause)], "warn": f"{cause}" + (f": {detail}" if detail else ""),
                    "action": "Anh xem bảng chi tiết trước khi dùng số" if "chưa rõ" in str(cause).lower()
                    else "Em sửa trong query, bản cũ vẫn giữ", "row": i})
    return out


def notes_sentence(notes: list) -> str:
    return f"Em dán {len(notes)} giấy nhớ ở các chỗ cần kiểm trước khi gửi sếp."
