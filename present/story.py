"""A talk that tells the report's story instead of reading it aloud.

The old draft (clicky_html.draft_script) put the report's own sentences into fixed slots in page order. Owners and
four demanding test readers (a chairman, a CFO, an FP&A owner, a presentation coach) said it sounded like a parrot.
This module builds the talk the way a good FP&A presenter does:

1. a fact book computed by code with Decimal from the report's own tables and cards (never by an AI): the total
   variance, the driver that holds most of it, how much the rest grew without it, cost growth against revenue
   growth, the ratio against its plan, the requested cap and cut;
2. a few beats in a fixed order (ask first, contrast, a question to the room, the main driver, what happens if
   nothing changes, the ask in figures, what the report does not say yet, close on the decision);
3. Vietnamese sentences with the figures put in by code. A figure the report does not print is said only when it
   survives the rounding of its inputs, and the sentence carries its formula ("so_tinh") so the number lock
   recomputes it from the page;
4. a pre-flight check for the owner only (never spoken to the Board): a claim the figures do not support, a KPI
   that does not recompute, an ask with no figure for what it saves.

Every figure in a line is either on the place the line frames, or computed by a formula whose inputs are on the
page. Words of degree ("gần gấp đôi", "hai phần ba") are chosen by code from computed ratios, never by an AI.
When the report has no table with a plan, an actual and a total, story() returns None and the old draft is used.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from present import clicky_html as C

HUNDRED = Decimal(100)
TOTAL = re.compile(r"^\W*(tổng|total|cộng|grand)\b", re.I)
REVENUE = re.compile(r"doanh thu|revenue|\bdtt\b|net sales|sales", re.I)
PLAN_WORD = re.compile(r"aop|kế hoạch|\bkh\b|budget|ngân sách|plan", re.I)
APPROX = re.compile(r"(khoảng|gần|hơn|chừng|xấp xỉ|độ)\s*$", re.I)
SAVE_ASK = re.compile(r"(\d+)\s*(vị trí|nhân sự|người|headcount|hc)\b", re.I)


# --- formulas the number lock can recompute ----------------------------------------------------------------

OP = re.compile(r"\s*(\d[\d.,]*|[-+×x*/()])")


def evaluate(expr: str, en: bool = False, nudge: Optional[dict] = None) -> Decimal:
    """The value of "(20,9 - 14,1) / (165,5 - 78,2) × 100" with the report's own number marks. nudge maps a
    literal's position to an amount added to it (to test the rounding of the inputs)."""
    toks, pos = [], 0
    while pos < len(expr):
        m = OP.match(expr, pos)
        if not m:
            raise ValueError(f"không đọc được công thức ở «{expr[pos:pos + 10]}»")
        toks.append(m.group(1))
        pos = m.end()
    lits = [i for i, t in enumerate(toks) if t[0].isdigit()]
    vals = {}
    for k, i in enumerate(lits):
        v = C.value(toks[i], en)
        if v is None:
            raise ValueError(f"số «{toks[i]}» trong công thức")
        vals[i] = v + (nudge or {}).get(k, 0)
    it = iter(range(len(toks)))

    def peek(i):
        return toks[i] if i < len(toks) else None

    def expr_(i):
        v, i = term(i)
        while peek(i) in ("+", "-"):
            op = toks[i]
            w, i = term(i + 1)
            v = v + w if op == "+" else v - w
        return v, i

    def term(i):
        v, i = atom(i)
        while peek(i) in ("×", "x", "*", "/"):
            op = toks[i]
            w, i = atom(i + 1)
            if op == "/":
                if w == 0:
                    raise ValueError("chia cho 0")
                v = v / w
            else:
                v = v * w
        return v, i

    def atom(i):
        t = peek(i)
        if t == "(":
            v, i = expr_(i + 1)
            if peek(i) != ")":
                raise ValueError("thiếu dấu ngoặc đóng")
            return v, i + 1
        if t == "-":
            v, i = atom(i + 1)
            return -v, i
        if t is None or not t[0].isdigit():
            raise ValueError("công thức thiếu số")
        return vals[i], i + 1

    del it
    v, i = expr_(0)
    if i != len(toks):
        raise ValueError("công thức thừa ký tự")
    return v


def literals(expr: str) -> list:
    return re.findall(r"\d[\d.,]*", expr)


def decimals(tok: str, en: bool) -> int:
    mark = "." if en else ","
    return len(tok.split(mark)[1]) if mark in tok else 0


def spread(expr: str, en: bool) -> tuple:
    """(lowest, highest) value of the formula when each printed input moves within its own rounding."""
    lits = literals(expr)
    half = lambda t: Decimal(5) / Decimal(10) ** (decimals(t, en) + 1)                     # noqa: E731
    # one printed figure used twice is one quantity: it moves the same way in both places
    vary = [t for t in dict.fromkeys(lits) if t not in ("1", "100")][:8]
    out = []
    for signs in itertools.product((-1, 1), repeat=len(vary)):
        move = dict(zip(vary, signs))
        try:
            out.append(evaluate(expr, en, {k: half(t) * move[t] for k, t in enumerate(lits) if t in move}))
        except ValueError:
            continue
    v = evaluate(expr, en)
    return (min(out + [v]), max(out + [v]))


def rounded(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def say(v: Decimal, places: int, en: bool) -> str:
    """A Decimal as the report writes it: 7,8 · 1.248 (or 7.8 · 1,248 in an English report)."""
    q = rounded(abs(v), places)
    whole, _, frac = f"{q:f}".partition(".")
    groups = f"{int(whole):,}".replace(",", "," if en else ".")
    return groups + ((("." if en else ",") + frac) if places else "")


def additive(expr: str) -> bool:
    """Only sums and differences: the exact result of the printed figures is the figure a reader recomputes."""
    return not re.search(r"[×x*/]", expr)


def robust_token(expr: str, en: bool, most: int = 1) -> Optional[tuple]:
    """(spoken token, places) for a computed figure that does not change within its inputs' rounding: one
    decimal if that holds, else a whole number; None when even that moves."""
    lo, hi = spread(expr, en)
    for places in range(most, -1, -1):
        if rounded(lo, places) == rounded(hi, places):
            return say(evaluate(expr, en), places, en), places
    return None


def check_computed(tok: str, expr: str, before: str, en: bool, on_page: set) -> Optional[str]:
    """None when the spoken figure is what the formula gives from figures printed in the report; else why not.
    "khoảng 8%" may be one unit off the exact result; a bare figure must survive the rounding of the inputs."""
    for lit in literals(expr):
        if lit not in ("1", "100") and C.value(lit, en) not in on_page:
            return f"số {lit} trong công thức không có trong báo cáo"
    try:
        lo, hi = spread(expr, en)
        exact = evaluate(expr, en)
    except ValueError as exc:
        return f"công thức lỗi: {exc}"
    spoken = C.value(tok, en)
    places = decimals(tok, en)
    unit = Decimal(1).scaleb(-places)
    if spoken is None:
        return "không đọc được số"
    if APPROX.search(before):
        return None if abs(abs(exact) - spoken) <= unit else f"công thức ra {say(exact, places, en)}"
    if rounded(abs(lo), places) == rounded(abs(hi), places) == spoken:
        return None
    if additive(expr) and abs(exact) == spoken:
        return None                        # 966,4 - 490,2 is 476,2: what anyone gets from the printed figures
    if rounded(abs(exact), places) == spoken:
        return "số này thay đổi theo cách làm tròn của báo cáo; thêm chữ «khoảng» trước số"
    return f"công thức ra {say(exact, places, en)}"


# --- the fact book ---------------------------------------------------------------------------------------

@dataclass
class Line:
    noi: str
    chi: str
    so_tinh: list = field(default_factory=list)     # ["7,8 = (20,9 - 14,1) / (165,5 - 78,2) × 100"]
    dung: float = 0.0                                # seconds of silence after the line (a question to the room)


@dataclass
class Table:
    id: str
    rows: list            # Place rows that are not header, total or empty
    total: object         # the total row Place
    plan: int             # column index of the plan amount
    actual: int           # column index of the actual amount
    var: Optional[int]
    pct: Optional[int]
    plan_name: str
    actual_name: str
    unit: Optional[str]
    what: str             # what the rows are ("khối", "kênh")


def _num(place, col, en) -> Optional[tuple]:
    if col is None or col >= len(place.cols or []):
        return None
    figs = place.cols[col].figs
    if len(figs) != 1:
        return None
    v = C.value(figs[0].tok, en)
    if v is None:
        return None
    sign = -1 if figs[0].sign == "-" else 1
    return figs[0].tok, v * sign


def main_table(places: dict, en: bool) -> Optional[Table]:
    """The table with a plan column, an actual column and a total row: the one the talk walks through."""
    by_table: dict = {}
    for p in places.values():
        if p.kind == "row" and p.cols:
            by_table.setdefault(re.sub(r"r\d+$", "", p.id), []).append(p)
    best = None
    for tid, rows in by_table.items():
        head = rows[0].cols
        money = [i for i, c in enumerate(head) if c.kind == "value" and (c.unit in C.MONEY or c.unit is None)
                 and not C.HEAD_PEOPLE.search(c.header)]
        if len(money) < 2:
            continue
        plan = next((i for i in money if PLAN_WORD.search(head[i].header)
                     and not re.search(r"\br\b|revis|điều chỉnh|aopr", head[i].header, re.I)), None)
        if plan is None:
            continue                       # two periods side by side (a balance sheet) are not a plan and an actual
        actual = next((i for i in money if i != plan), None)
        var = next((i for i, c in enumerate(head) if c.kind == "var"), None)
        pct = next((i for i, c in enumerate(head) if c.kind == "pctvar"), None)
        body = [r for r in rows[1:] if r.label and not TOTAL.match(r.label) and _num(r, plan, en) and _num(r, actual, en)]
        total = next((r for r in rows if TOTAL.match(r.label or "")), None)
        if total is None or len(body) < 3 or not _num(total, plan, en) or not _num(total, actual, en):
            continue
        first = body[0].label.split()[0] if body[0].label.split() else ""
        what = first.lower() if all(r.label.split()[:1] == [first] for r in body) else ""
        unit = head[actual].unit
        if unit is None:                   # "Chênh lệch theo phòng (tỷ đồng)": the unit in the table's own heading
            sec = places.get(tid).section if places.get(tid) else None
            m = re.search(r"\b(nghìn tỷ|tỷ|triệu|nghìn)\b", places[sec].text if sec in places else "", re.I)
            unit = m.group(1).lower() if m else None
        t = Table(tid, body, total, plan, actual, var, pct, head[plan].name or head[plan].header,
                  head[actual].name or head[actual].header, unit, what)
        if best is None or len(body) > len(best.rows):
            best = t
    return best


def delta(t: Table, r, en: bool) -> Optional[tuple]:
    """(token, value, formula or "") of a row's change against plan: the variance column when the table has one,
    else actual minus plan worked out by code (both printed, both positive)."""
    if t.var is not None:
        got = _num(r, t.var, en)
        if got:
            return got[0], got[1], ""
    a, p = _num(r, t.actual, en), _num(r, t.plan, en)
    if not a or not p or a[1] < 0 or p[1] < 0:
        return None
    v = a[1] - p[1]
    return say(v, max(decimals(a[0], en), decimals(p[0], en)), en), v, f"{a[0]} - {p[0]}"


def group_rows(t: Table, en: bool) -> set:
    """Rows that add up a run of rows next to them (a group heading or a subtotal): not a driver of their own."""
    vals = []
    for r in t.rows:
        a, p = _num(r, t.actual, en), _num(r, t.plan, en)
        vals.append((a[1], p[1]) if a and p else None)
    out = set()
    for i, v in enumerate(vals):
        if v is None:
            continue
        for run in (range(i + 1, len(vals)), range(i - 1, -1, -1)):
            sa = sp = Decimal(0)
            n = 0
            for j in run:
                if vals[j] is None:
                    break
                sa, sp, n = sa + vals[j][0], sp + vals[j][1], n + 1
                tol = Decimal("0.05") * (n + 1)
                if n >= 2 and abs(sa - v[0]) <= tol and abs(sp - v[1]) <= tol:
                    out.add(t.rows[i].id)
                    break
            if t.rows[i].id in out:
                break
    return out


def share_words(r: Decimal) -> Optional[str]:
    """Words of degree, chosen from a computed share (never by an AI)."""
    if r >= Decimal("0.9"):
        return "gần như toàn bộ"
    if Decimal("0.70") < r < Decimal("0.9"):
        return "phần lớn"
    if Decimal("0.64") <= r <= Decimal("0.70"):
        return "khoảng hai phần ba"
    if Decimal("0.58") <= r < Decimal("0.64"):
        return "gần hai phần ba"
    if Decimal("0.52") < r < Decimal("0.58"):
        return "hơn một nửa"
    if Decimal("0.45") <= r <= Decimal("0.52"):
        return "khoảng một nửa"
    return None


def times_words(r: Decimal) -> Optional[str]:
    if Decimal("1.3") <= r < Decimal("1.7"):
        return "nhanh hơn hẳn"
    if Decimal("1.7") <= r < Decimal("1.95"):
        return "nhanh gần gấp đôi"
    if Decimal("1.95") <= r <= Decimal("2.1"):
        return "nhanh gấp đôi"
    if Decimal("2.1") < r < Decimal("2.9"):
        return "nhanh hơn gấp đôi"
    if Decimal("2.9") <= r <= Decimal("3.1"):
        return "nhanh gấp ba"
    if r > Decimal("3.1"):
        return "nhanh gấp nhiều lần"
    return None


def _lc(text: str) -> str:
    """The first letter lower-cased inside a sentence, unless the word is a code ("CNTT", "EC")."""
    return text[:1].lower() + text[1:] if text[1:2].islower() or text[1:2] in (" ", "") else text


COMMON_NOUN = re.compile(r"^(khối|phòng|ban|kênh|bộ phận|nhóm|chi nhánh|vùng|miền|mảng|ngành|dòng|trung tâm|"
                         r"nhà máy|công ty|cửa hàng|khu vực)\b", re.I)


def _short(label: str) -> str:
    """'Khối Bán hàng' said inside a sentence: 'khối Bán hàng'; a name ('Logistics') stays as it is."""
    m = COMMON_NOUN.match(label)
    return label[:m.end()].lower() + label[m.end():] if m else label


def _pcts(text: str, en: bool) -> list:
    return [f for f in C.figures(text) if f.unit == "%"]


def contrast(items: list, en: bool) -> Optional[tuple]:
    """A note that sets cost growth against revenue growth: (note item, cost %, revenue %)."""
    for it in items:
        if it.kind != "note":
            continue
        ps = _pcts(it.text, en)
        cost = next((f for f in ps if C.COST_WORDS.search(f.ctx) and not REVENUE.search(f.ctx[-25:])), None)
        rev = next((f for f in ps if f is not cost and REVENUE.search(f.ctx[-30:])), None)
        if cost and rev and C.value(rev.tok, en):
            return it, cost, rev
    return None


def ratio_card(places: dict, en: bool) -> Optional[tuple]:
    """A card with a ratio and its plan ("Tỷ lệ chi phí nhân sự/DTT 2,52% · AOP gốc 2,40%")."""
    for p in places.values():
        if p.kind != "card" or not p.figs or len(p.figs) != 2:
            continue
        a, b = p.figs
        if a.unit == "%" and b.unit == "%" and a.level and PLAN_WORD.search(b.ctx):
            return p, a, b
    return None


DECIDE_HEAD = re.compile(r"quyết định|cần quyết|xin ý kiến|phê duyệt|bod cần|hđqt cần|decisions?\b|asks?\b", re.I)
ACTION = re.compile(r"^\s*(giữ|duyệt|phê duyệt|cắt|giảm|giãn|dời|lùi|tạm dừng|tạm hoãn|hoãn|chuyển|gắn|bổ sung|yêu cầu|"
                    r"chấp thuận|thông qua|đồng ý|không duyệt|siết|khóa|đặt trần|áp trần|phân bổ|giải ngân|chi theo|"
                    r"[^:]{2,25}:\s*(phân bổ|chi theo|giữ|cắt|giãn|dời|hoãn))", re.I)


def _title(n) -> str:
    """A note's own bold title ("<li><strong>Giữ quỹ nhân sự…</strong> …"), or its first sentence."""
    first = next((c for c in n.node.children if c.tag in ("strong", "b", "h4", "h5")), None)
    if first is not None and len(first.text()) >= 8 and n.text.startswith(first.text()[:8]):
        return first.text().rstrip(".:")
    return re.split(r"(?<=[.;])\s+", n.text, maxsplit=1)[0].rstrip(".;")


def reco(items: list, places: dict) -> tuple:
    """The recommendation: (note item or None, [card places], [asks]). A part headed "BOD cần quyết định" gives one
    ask per note (its bold title); else a note that starts "Đề xuất:" gives the asks in its own sentences."""
    sec = next((i for i, s in enumerate(items) if s.kind == "section" and DECIDE_HEAD.search(s.text)), None)
    if sec is not None:
        notes = [n for n in items if n.kind == "note" and n.section == sec]
        titled = [n for n in notes if ACTION.search(_title(n)) or len(notes) <= 6]
        if titled:
            cards = [places[c.node.attrs["data-clicky"]] for c in items
                     if c.kind == "card" and c.section == sec and c.node.attrs.get("data-clicky") in places]
            return titled[0], cards, [(n, _title(n)) for n in titled]
    note = next((n for n in items if n.kind == "note" and C.RECO_START.search(n.text)), None)
    if note is None:
        sec = next((i for i, s in enumerate(items) if s.kind == "section" and C.RECO_HEAD.search(s.text)), None)
        note = next((n for n in items if n.kind == "note" and n.section == sec), None) if sec is not None else None
    if note is None:
        return None, [], []
    cards = [places[c.node.attrs["data-clicky"]] for c in items
             if c.kind == "card" and c.section == note.section and c.node.attrs.get("data-clicky") in places]
    return note, cards, [(note, a) for a in asks(note)]


def asks(note) -> list:
    """The things the owner asks the Board to decide, in his words, without the figures that follow "tức".
    Only sentences that ask for an action ("giữ…", "giãn…"); a note that only describes the table asks nothing."""
    body = C.RECO_START.sub("", note.text).strip().rstrip(".")
    parts = [p.strip() for p in re.split(r";|\.\s", body) if p.strip()]
    out = []
    for p in parts:
        p = re.split(r",?\s+(?:tức|nghĩa là|tương đương)\b", p, maxsplit=1)[0]
        p = re.sub(r"\s+như (AOP|kế hoạch)[^,;]*", "", p)
        p = re.sub(r"\s+và gắn với .*$", "", p)
        if ACTION.search(p):
            out.append(_lc(p))
    return out


def if_note(items: list) -> Optional[tuple]:
    """The owner's own "Nếu …" sentence with a figure: what happens if nothing changes, in his words."""
    for it in items:
        if it.kind != "note":
            continue
        for x in re.split(r"(?<=[.;])\s+", it.text):
            if re.match(r"^\s*(nếu|if)\b", x, re.I) and C.figures(x) and len(x) <= 160:
                return it, x.strip()
    return None


# --- the talk ---------------------------------------------------------------------------------------------

class _Hidden:
    """An item the reader cannot see: kept in place (items are found by position) but never used."""
    kind = "hidden"

    def __init__(self, it):
        self.node, self.text, self.section = it.node, it.text, it.section


def story(items: list, places: dict, owner: str, en: bool = False, hidden: frozenset = frozenset()) -> Optional[dict]:
    """The talk as scenes of lines (the script format clicky_html builds and checks), plus "phan_bien": what
    the owner should look at before the Board sees it. None when the report has no table to tell a story from.
    hidden: places the reader cannot see when the page opens (a tab not chosen yet); the talk never frames them."""
    if en:
        return None
    if hidden:
        places = {k: p for k, p in places.items()
                  if k not in hidden and re.sub(r"r\d+$", "", k) not in hidden}
        items = [it if it.node.attrs.get("data-clicky") not in hidden else _Hidden(it) for it in items]
    t = main_table(places, en)
    if t is None:
        return None
    cid = lambda it: it.node.attrs["data-clicky"]                                          # noqa: E731
    p_tok, p_val = _num(t.total, t.plan, en)
    a_tok, a_val = _num(t.total, t.actual, en)
    d_val = a_val - p_val
    if not d_val:
        return None
    up = d_val > 0
    unit = t.unit or ""
    tdelta = delta(t, t.total, en)
    tpct = _num(t.total, t.pct, en) if t.pct is not None else None
    lines: list = []
    flags: list = []
    note, cards, ask_pairs = reco(items, places)
    ask_list = [a for _n, a in ask_pairs]
    cut = next((c for c in cards if c.figs and any(f.sign == "-" for f in c.figs)), None)
    cap = next((c for c in cards if c is not cut and c.figs and c.figs[0].level), None)

    # B0: the decision first, and that the speaker is an AI
    count_w = {1: "một việc", 2: "hai việc", 3: "ba việc", 4: "bốn việc", 5: "năm việc", 6: "sáu việc"}
    many = len(ask_list) > 3                       # at most three things in one breath; the rest at the end
    if ask_list and not many:
        n = len(ask_list)
        what = ask_list[0] if n == 1 else ", và ".join([", ".join(ask_list[:-1]), ask_list[-1]])
        lines.append(("Mở đầu", Line(f"Thưa anh chị, em là trợ lý AI của {owner}. Em xin nói kết luận trước: "
                                     f"hôm nay em xin anh chị quyết {count_w[n]}: {what}.", cid(note))))
    elif ask_list:
        lines.append(("Mở đầu", Line(f"Thưa anh chị, em là trợ lý AI của {owner}. Em xin nói kết luận trước: hôm nay "
                                     f"có {count_w.get(len(ask_list), 'nhiều việc')} cần anh chị quyết, việc đầu tiên là: "
                                     f"{_lc(ask_list[0])}.", cid(ask_pairs[0][0]))))
    else:
        lines.append(("Mở đầu", Line(f"Thưa anh chị, em là trợ lý AI của {owner}. Em xin nói kết luận trước.", "")))

    # B3: the contrast
    con = contrast(items, en)
    if con is not None:
        it, cost, rev = con
        faster = C.value(cost.tok, en) > C.value(rev.tok, en)
        lines.append(("Mâu thuẫn", Line(f"Vì sao? Chi phí tăng {cost.tok}%, trong khi doanh thu chỉ tăng {rev.tok}%."
                                        if faster else f"Chi phí tăng {cost.tok}%, doanh thu tăng {rev.tok}%.", cid(it))))
        words = times_words(C.value(cost.tok, en) / C.value(rev.tok, en)) if faster else None
        if words:
            lines.append(("Mâu thuẫn", Line(f"Tức là chi phí đang chạy {words} doanh thu.", cid(it))))
    elif tdelta:
        pct = tpct[0] if tpct else None
        pct_expr = "" if tpct else f"({a_tok} - {p_tok}) / {p_tok} × 100"
        got = robust_token(pct_expr, en) if pct_expr else None
        pct = pct or (got[0] if got else None)
        lines.append(("Mâu thuẫn", Line(f"So với {t.plan_name}, tổng {'tăng' if up else 'giảm'} {tdelta[0]} {unit}"
                                        .replace("  ", " ") + (f", tức khoảng {pct}%." if pct_expr and pct else
                                                               f", tức {pct}%." if pct else "."),
                                        t.total.id, so_tinh=[x for x in (f"{tdelta[0]} = {tdelta[2]}" if tdelta[2] else "",
                                                                         f"{pct} = {pct_expr}" if pct_expr and pct else "") if x])))

    # B4 + B5: a question to the room, then the main driver and what the rest did without it
    groups = group_rows(t, en)
    moves = [(r, delta(t, r, en)) for r in t.rows if r.id not in groups]
    same = [(r, d) for r, d in moves if d and d[1] and (d[1] > 0) == up]
    if same and tdelta:
        same.sort(key=lambda x: -abs(x[1][1]))
        top, (top_tok, top_val, top_f) = same[0]
        share = top_val / tdelta[1]
        noun = t.what or "chỗ"
        lines.append(("Động lực chính", Line(f"Vậy phần {'tăng' if up else 'giảm'} thêm nằm ở đâu?", t.total.id, dung=1.5)))
        sw = share_words(abs(share))
        lead = f"{sw[:1].upper() + sw[1:]} nằm ở một chỗ" if sw else "Lớn nhất là"
        lines.append(("Động lực chính", Line(f"{lead}: {_short(top.label)}, {'tăng' if up else 'giảm'} {top_tok} {unit}."
                                             .replace(" .", "."), top.id,
                                             so_tinh=[f"{top_tok} = {top_f}"] if top_f else [])))
        tp, ta = _num(top, t.plan, en), _num(top, t.actual, en)
        if tp and ta and abs(share) >= Decimal("0.4") and len(same) + len([m for m in moves if m[1]]) > 2:
            num = f"({tdelta[0]} - {top_tok})" if not (tdelta[2] or top_f) else \
                f"(({a_tok} - {p_tok}) - ({ta[0]} - {tp[0]}))"     # printed variances when the table has them
            expr = f"{num} / ({p_tok} - {tp[0]}) × 100"
            got = robust_token(expr, en)
            if got:
                rest_tok = got[0]
                tail = ""
                if con is not None and abs(C.value(rest_tok, en) - C.value(con[2].tok, en)) <= 1:
                    tail = ", sát nhịp doanh thu"
                others = f"các {noun} còn lại" if t.what else "phần còn lại"
                rest_v = evaluate(expr, en)
                whole = (a_val - p_val) / p_val * HUNDRED
                verb = ("chỉ tăng" if rest_v < whole else "tăng") if rest_v > 0 else "giảm"
                lines.append(("Động lực chính", Line(f"Bỏ {_short(top.label)} ra, {others} {verb} khoảng {rest_tok}%{tail}.",
                                                     top.id, so_tinh=[f"{rest_tok} = {expr}"])))

    # B6: what happens if nothing changes
    rc = ratio_card(places, en)
    if rc is not None:
        p, a, b = rc
        lines.append(("Nếu giữ nguyên", Line(f"Nếu giữ nguyên, tỷ lệ này là {a.tok}%, thay vì {b.tok}% như kế hoạch gốc.",
                                             p.id)))

    if rc is None:
        cond = if_note(items)
        if cond is not None:
            lines.append(("Nếu giữ nguyên", Line(cond[1], cid(cond[0]))))

    # B8: the ask in figures
    if many:
        for k, (n, a) in enumerate(ask_pairs[:6]):
            lead = ["Một là", "Hai là", "Ba là", "Bốn là", "Năm là", "Sáu là"][k]
            lines.append(("Đề nghị", Line(f"{lead}, {_lc(a)}.", cid(n))))
    if cap is not None:
        f = cap.figs[0]
        lines.append(("Đề nghị", Line(f"Vì vậy em đề xuất mức trần {f.tok} {f.unit or unit}.".replace("  ", " "), cap.id)))
    if cut is not None:
        f = next(g for g in cut.figs if g.sign == "-")
        lines.append(("Đề nghị", Line(f"Tức là phải giảm {f.tok} {f.unit or unit} so với bản điều chỉnh.".replace("  ", " "),
                                      cut.id, dung=1.0)))

    # pre-flight (owner only) and B9: say plainly what the report does not give a figure for
    flags += preflight(items, places, t, en, note, cut, owner)
    gap_line = next((fl for fl in flags if fl.get("noi_thang")), None)
    if gap_line is not None and note is not None:
        lines.append(("Đề nghị", Line(gap_line["noi_thang"], cid(note))))

    # B10: close on the decision
    if cap is None and ask_list:
        lines.append(("Chốt", Line(f"Tóm lại, em xin anh chị cho ý kiến về {count_w.get(len(ask_list), 'các việc')} "
                                   "trên. Quyết định là của anh chị.", cid(ask_pairs[0][0]))))
    if cap is not None:
        f = cap.figs[0]
        lines.append(("Chốt", Line(f"Tóm lại, em xin anh chị cho ý kiến về mức trần {f.tok} {f.unit or unit}. "
                                   "Quyết định là của anh chị.".replace("  ", " "), cap.id)))
    lines.append(("Chốt", Line("Anh chị muốn em đi sâu vào phần nào, cứ bấm vào con số đó hoặc hỏi em ạ.", "")))

    scenes: list = []
    for muc, ln in lines:
        if not scenes or scenes[-1]["muc"] != muc:
            scenes.append({"muc": muc, "cau": []})
        c = {"noi": ln.noi, "chi": ln.chi}
        if ln.chi:
            c["neo"] = places[ln.chi].anchor
        if ln.so_tinh:
            c["so_tinh"] = ln.so_tinh
        if ln.dung:
            c["dung"] = ln.dung
        scenes[-1]["cau"].append(c)
    return {"canh": scenes, "bo_qua": [], "chua_ro": [], "phan_bien": [fl["xem"] for fl in flags], "kieu": "ke_chuyen"}


def preflight(items: list, places: dict, t: Table, en: bool, note, cut, owner: str = "người làm báo cáo") -> list:
    """What the owner should check before the Board sees the report. Never spoken to the Board, except the
    plain line that an ask has no figure yet ("noi_thang")."""
    flags = []
    hc = next((p for p in places.values() if p.kind == "card" and p.figs and len(p.figs) >= 2
               and p.figs[0].unit == "người" and p.figs[1].unit == "người" and p.figs[1].sign), None)
    rate = next((p for p in places.values() if p.kind == "card" and p.figs and len(p.figs) >= 2
                 and re.search(r"bình quân|trung bình|mỗi người|per head|/người", p.label, re.I)
                 and p.figs[1].unit == "%"), None)
    p_tok = _num(t.total, t.plan, en)[0]
    a_tok = _num(t.total, t.actual, en)[0]
    d = delta(t, t.total, en)
    if hc and d:
        n_tok, dn_tok = hc.figs[0].tok, hc.figs[1].tok
        heads = f"{dn_tok} × {p_tok} / ({n_tok} - {dn_tok})"
        try:
            hc_part = evaluate(heads, en)
            share = hc_part / d[1]
            flags.append({"xem": f"Cầu chênh lệch: số người tăng góp khoảng {say(hc_part, 1, en)} {t.unit or ''}"
                                 f" ({say(share * HUNDRED, 0, en)}%), đơn giá mỗi người tăng góp phần còn lại "
                                 f"({say(d[1] - hc_part, 1, en)} {t.unit or ''}). Công thức: {heads}.".replace(" )", ")")})
            claim = next((it for it in items if it.kind == "note" and re.search(r"phần ba|phần tư|một nửa|phần lớn", it.text, re.I)
                          and re.search(r"tuyển|người|headcount|nhân sự mới", it.text, re.I)), None)
            if claim is not None and share < Decimal("0.5"):
                said = next((x for x in re.split(r"(?<=[.;])\s+", claim.text)
                             if re.search(r"phần ba|phần tư|một nửa|phần lớn", x, re.I)), claim.text)
                flags.append({"xem": f"Báo cáo viết «{C.clip(said, 80)}», nhưng tách theo số người × đơn giá thì "
                                     f"phần do tăng người chỉ khoảng {say(share * HUNDRED, 0, en)}%. Phần lớn còn lại là "
                                     "đơn giá mỗi người tăng. Anh xem lại câu này trước khi gửi."})
        except ValueError:
            pass
    if hc and rate:
        n_tok, dn_tok = hc.figs[0].tok, hc.figs[1].tok
        expr = f"({a_tok} / {n_tok}) / ({p_tok} / ({n_tok} - {dn_tok})) × 100 - 100"
        try:
            exact = evaluate(expr, en)
            lo, hi = spread(expr, en)
            printed = C.value(rate.figs[1].tok, en)
            places_ = decimals(rate.figs[1].tok, en)
            if not (rounded(lo, places_) <= printed <= rounded(hi, places_)):
                flags.append({"xem": f"Thẻ «{rate.label}» ghi tăng {rate.figs[1].tok}%, tính lại từ tổng chi phí và số "
                                     f"người thì ra khoảng {say(exact, 2, en)}%. Công thức: {expr}."})
        except ValueError:
            pass
    if note is not None:
        m = SAVE_ASK.search(note.text)
        if m:
            row = next((r for r in t.rows if any(w.lower() in note.text.lower()
                                                 for w in r.label.split()[1:] if len(w) > 3)), None)
            head = next((i for i, c in enumerate(t.rows[0].cols) if C.HEAD_PEOPLE.search(c.header)), None)
            if row is not None and head is not None and _num(row, head, en):
                per = f"{_num(row, t.actual, en)[0]} / {_num(row, head, en)[0]}"
                try:
                    year = evaluate(f"{m.group(1)} × {per}", en)
                    need = f" so với {cut.figs[0].tok} {cut.figs[0].unit or ''} cần giảm" if cut is not None else ""
                    flags.append({"xem": f"Đề xuất «{m.group(0)}» chưa ghi tiết kiệm được bao nhiêu. Theo bình quân "
                                         f"{_short(row.label)} ({per}), giãn trọn năm khoảng {say(year, 1, en)} "
                                         f"{t.unit or ''}{need}. Anh bổ sung số tháng giãn, hoặc để em nói thẳng là chưa có số.",
                                  "noi_thang": f"Em xin nói thẳng: báo cáo chưa ghi giãn {m.group(1)} {m.group(2)} tiết kiệm "
                                               f"được bao nhiêu, phần này {owner} sẽ bổ sung ạ."})
                except ValueError:
                    pass
    return flags

