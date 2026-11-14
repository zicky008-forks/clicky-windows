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
import unicodedata
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from present import clicky_html as C

HUNDRED = Decimal(100)
TOTAL = re.compile(r"^\W*(tổng|total|cộng|grand)\b", re.I)
REVENUE = re.compile(r"doanh thu|revenue|\bdtt\b|net sales|sales", re.I)
PLAN_WORD = re.compile(r"aop|kế hoạch|\bkh\b|budget|ngân sách|plan", re.I)
APPROX = re.compile(r"(khoảng|gần|hơn|chừng|xấp xỉ|độ|nói tròn là)\s*$", re.I)   # "= 63,16, nói tròn là 63": the exact figure is beside it
CLAIM = re.compile(r"\b(hai phần ba|một phần ba|ba phần tư|một phần tư|một nửa|phần lớn)\b", re.I)
CLAIMS = {"hai phần ba": Decimal("0.667"), "một phần ba": Decimal("0.333"), "ba phần tư": Decimal("0.75"),
          "một phần tư": Decimal("0.25"), "một nửa": Decimal("0.5"), "phần lớn": None}
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


def spread(expr: str, en: bool, exact: tuple = ()) -> tuple:
    """(lowest, highest) value of the formula when each printed input moves within its own rounding. A count of
    people (exact) does not move: 1.248 người is 1.248, not anything from 1.247,5 to 1.248,5."""
    lits = literals(expr)
    half = lambda t: Decimal(5) / Decimal(10) ** (decimals(t, en) + 1)                     # noqa: E731
    # one printed figure used twice is one quantity: it moves the same way in both places
    vary = [t for t in dict.fromkeys(lits) if t not in ("1", "100") and t not in exact][:8]
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
    xac_nhan: list = field(default_factory=list)     # printed figures this line names from another place
    bat: bool = True                                 # False: proposed, said only once the owner switches it on
    vai: str = ""                                    # the beat ("hoi", "dong_luc"…): how the page answers questions


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


PLAN_SAID = [(re.compile(r"^(aopr|aop\s*(điều chỉnh|đc|revised)|kế hoạch điều chỉnh|kh\s*điều chỉnh|bản điều chỉnh)$", re.I),
              "kế hoạch điều chỉnh"),
             (re.compile(r"^(aop|aop\s*gốc|kế hoạch gốc|kh\s*gốc|ngân sách gốc|kh)$", re.I), "kế hoạch gốc"),
             (re.compile(r"^(budget|bp|ngân sách)$", re.I), "ngân sách"),
             (re.compile(r"^(le|fc|forecast|dự báo|ước)$", re.I), "dự báo"),
             (re.compile(r"^(actual|th|thực hiện|thực tế)$", re.I), "thực hiện")]


def plan_said(name: str) -> str:
    """A column's name as the talk says it, one plain name per plan ("AOPR" is "kế hoạch điều chỉnh" in every line,
    never "AOPR" here and "bản điều chỉnh" there). A name the list does not know is said as printed."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    return next((said for pat, said in PLAN_SAID if pat.match(n)), n)


RATIO_SAID = {"DTT": "doanh thu thuần", "DT": "doanh thu", "LNG": "lợi nhuận gộp", "LNTT": "lợi nhuận trước thuế",
              "LNST": "lợi nhuận sau thuế"}


def plain(text: str) -> str:
    """A ratio's short form in words on the caption, as the voice already reads it: "nhân sự/DTT" is
    "nhân sự trên doanh thu thuần"."""
    text = re.sub(r"(?<=\w)\s*/\s*(" + "|".join(sorted(RATIO_SAID, key=len, reverse=True)) + r")\b",
                  lambda m: " trên " + RATIO_SAID[m.group(1)], text)
    # one plain name per plan in every spoken line, the owner's quoted asks too ("bằng AOP gốc" is "bằng kế hoạch gốc")
    return re.sub(r"\b(AOPR|AOP gốc)\b", lambda m: plan_said(m.group(1)), text)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


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
        head = first.text().rstrip(".:")
        # "<strong>Logistics phải giải trình trước khi duyệt</strong> phần tăng thuê kho.": the object belongs to it
        tail = re.split(r"[.,;:(]", n.text[len(first.text()):], maxsplit=1)[0].strip()
        if tail and tail[:1].islower() and len(tail.split()) <= 6 and not re.search(r"\d", tail):
            head = f"{head} {tail}"
        return head
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
    """The things the owner asks the Board to decide, in his words, as (clause, money): "giữ tỷ lệ … ở 2,40%" with
    "trần 177,5 tỷ" from the "tức …" that follows. Only sentences that ask for an action ("giữ…", "giãn…")."""
    body = C.RECO_START.sub("", note.text).strip().rstrip(".")
    parts = [p.strip() for p in re.split(r";|\.\s", body) if p.strip()]
    out = []
    for p in parts:
        m = re.search(r",?\s+(?:tức|nghĩa là|tương đương)\b\s*", p)
        clause, tail = (p[:m.start()], p[m.end():]) if m else (p, "")
        clause = re.sub(r"\s+như (AOP|kế hoạch)[^,;]*", "", clause.strip())
        if ACTION.search(clause):
            out.append(_Ask(_lc(clause), tail.strip()))
    return out


class _Ask(str):
    """An ask in the owner's words; .money is the figure that follows "tức" ("trần 177,5 tỷ"), if any."""

    def __new__(cls, text, money=""):
        obj = str.__new__(cls, text)
        obj.money = money
        return obj


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

    # B0: who speaks, then the decision first, one short sentence each, money before ratios
    count_w = {1: "một việc", 2: "hai việc", 3: "ba việc", 4: "bốn việc", 5: "năm việc", 6: "sáu việc"}
    nth = ["Một", "Hai", "Ba", "Bốn", "Năm", "Sáu"]
    many = len(ask_list) > 4                       # up to four asks are each said up front; more go to the end
    who = owner[:1].upper() + owner[1:]
    first = ask_pairs[0][0] if ask_pairs else None
    lines.append(("Mở đầu", Line(f"Thưa anh chị, em là trợ lý AI của {owner}.", cid(first) if first else "", vai="chao")))

    names = {str(getattr(r, "label", "") or "").split(" ")[0] for r in t.rows} - {""}

    def lc(a) -> str:                              # "Logistics phải giải trình" keeps the row's own name
        return a if str(a).split(" ")[0] in names else _lc(a)

    def said(a) -> str:
        money = getattr(a, "money", "")
        return f"{lc(a)}, tức {money}" if money else lc(a)

    if ask_list and not many:
        lines.append(("Mở đầu", Line(f"{who} xin anh chị quyết {count_w[len(ask_list)]}.", cid(first), vai="hoi")))
        for k, (n, a) in enumerate(ask_pairs):
            lines.append(("Mở đầu", Line(f"{nth[k]} là {said(a)}." if len(ask_pairs) > 1 else f"{_cap(said(a))}.", cid(n),
                                         vai="hoi")))
    elif ask_list:
        lines.append(("Mở đầu", Line(f"{who} xin anh chị quyết {count_w.get(len(ask_list), 'nhiều việc')}; việc đầu tiên "
                                     f"là {said(ask_list[0])}.", cid(first), vai="hoi")))

    # pre-flight first: a sentence the owner must check is never the talk's own evidence until he rules on it
    flags += preflight(items, places, t, en, note, cut, owner)
    con = contrast(items, en)
    con_doubt = None
    if con is not None:
        con_doubt = next((fl for fl in flags if fl.get("sai") and con[1].tok in fl["sai"]
                          and fl["sai"] in con[0].text), None)

    # B2 + B3: how big the change is, then the contrast that makes it matter
    if tdelta:
        pct = tpct[0] if tpct else None
        pct_expr = "" if tpct else f"({a_tok} - {p_tok}) / {p_tok} × 100"
        got = robust_token(pct_expr, en) if pct_expr else None
        pct = pct or (got[0] if got else None)
        size = (f"Theo {plan_said(t.actual_name)}, tổng là {a_tok} {unit}, {'cao' if up else 'thấp'} hơn "
                f"{plan_said(t.plan_name)} "
                f"{tdelta[0]} {unit}").replace("  ", " ")
        # the note's own cost growth is said next: never a second rate for the same change
        size += (f", tức khoảng {pct}%." if pct_expr else f", tức {pct}%.") if pct and con is None and not con_doubt else "."
        lines.append(("Vì sao cần quyết", Line(size.replace(" ,", ","), t.total.id, vai="quy_mo",
                                        so_tinh=[x for x in (f"{tdelta[0]} = {tdelta[2]}" if tdelta[2] else "",
                                                             f"{pct} = {pct_expr}" if pct_expr and pct else "") if x])))
    if con is not None:
        it, cost, rev = con
        faster = C.value(cost.tok, en) > C.value(rev.tok, en)
        printed = (f"Chi phí tăng {cost.tok}%, trong khi doanh thu chỉ tăng {rev.tok}%." if faster
                   else f"Chi phí tăng {cost.tok}%, doanh thu tăng {rev.tok}%.")
        table_says = None
        if con_doubt and tdelta:                    # the table's own rate: does "faster" hold under both figures?
            got = robust_token(f"({a_tok} - {p_tok}) / {p_tok} × 100", en)
            table_says = got and C.value(got[0], en) > C.value(rev.tok, en)
        if faster:                                 # the question on a line of its own, a breath, then the answer
            lines.append(("Vì sao cần quyết", Line("Vì sao cần quyết?", t.total.id if con_doubt and table_says else cid(it),
                                            dung=0.8, vai="cau_hoi")))
        if con_doubt and faster and table_says:
            # the printed rate disagrees with the table: say only what holds under both, the owner picks the rate
            # revenue's half of the sentence is not in doubt: keep its figure, frame the table's total
            money = f"Vì chi phí tăng thêm {tdelta[0]} {t.unit or ''}".rstrip()
            lines.append(("Vì sao cần quyết", Line(f"{money}, trong khi doanh thu chỉ tăng {rev.tok}%.",
                                            t.total.id, so_tinh=[f"{tdelta[0]} = {tdelta[2]}"] if tdelta[2] else [],
                                            vai="mau_thuan", xac_nhan=[rev.tok])))
            lines.append(("Vì sao cần quyết", Line(printed, cid(it), bat=False, vai="mau_thuan")))
            con_doubt["cau"] = [lines[-1][1].noi]
        else:
            lines.append(("Vì sao cần quyết", Line(printed, cid(it), vai="mau_thuan")))
        if faster:
            expr = f"{cost.tok} / {rev.tok}"
            ratio = evaluate(expr, en)
            if ratio >= Decimal("1.3"):
                tok = say(ratio, 1, en)
                tail = ", gần gấp đôi" if Decimal("1.85") <= ratio < Decimal("1.95") else ""
                lines.append(("Vì sao cần quyết", Line(f"Tức là chi phí tăng nhanh gấp khoảng {tok} lần doanh thu{tail}.", cid(it),
                                                so_tinh=[f"{tok} = {expr}"], vai="mau_thuan",
                                                bat=lines[-1][1].bat)))
                if not lines[-1][1].bat and con_doubt:
                    con_doubt["cau"].append(lines[-1][1].noi)

    # B4 + B5: a question to the room, then the main driver and what the rest did without it
    groups = group_rows(t, en)
    moves = [(r, delta(t, r, en)) for r in t.rows if r.id not in groups]
    same = [(r, d) for r, d in moves if d and d[1] and (d[1] > 0) == up]
    verb = "tăng" if up else "giảm"
    if same and tdelta:
        same.sort(key=lambda x: -abs(x[1][1]))
        top, (top_tok, top_val, top_f) = same[0]
        share = top_val / tdelta[1]
        noun = t.what or "chỗ"
        lines.append(("Phần tăng nằm ở đâu", Line(f"Vậy phần {verb} thêm nằm ở đâu?", t.total.id, dung=1.5, vai="cau_hoi")))
        sw = share_words(abs(share))
        doubted_txt = " ".join(jfold(fl.get("sai", "")) for fl in flags)
        if sw and jfold(sw.split(" ", 1)[-1]) in doubted_txt:   # never echo a doubted sentence's words: the figure instead
            pct = say(abs(share) * HUNDRED, 0, en)
            sw = f"khoảng {pct}%"
        name = _cap(_short(top.label))
        why = next((c for c in (top.cols or []) if re.search(r"giải trình|lý do|nguyên nhân|diễn giải|ghi chú", c.header, re.I)
                    and c.text.strip() and not re.search(r"\d", c.text) and len(c.text.split()) <= 8), None)
        cause = f"; giải trình ghi: {_lc(why.text.strip().rstrip('.'))}" if why else ""   # the row's own printed reason
        text = (f"{name} chiếm {sw} phần {verb}: {top_tok} {unit}{cause}." if sw
                else f"Lớn nhất là {_short(top.label)}: {verb} {top_tok} {unit}{cause}.").replace(" .", ".").replace(" ;", ";")
        st = [f"{top_tok} = {top_f}"] if top_f else []
        if sw and sw.endswith("%"):                # the share said as a figure: its own formula, checked like any other
            st.append(f"{sw.split()[1].rstrip('%')} = {top_tok} / {tdelta[0]} × 100")
        elif sw and CLAIM.search(sw):              # "gần hai phần ba": the share behind the words, for "hai phần ba à?"
            num = f"({top_f})" if top_f else top_tok          # a worked-out change is shown from the printed figures
            den = f"({tdelta[2]})" if tdelta[2] else tdelta[0]
            st.append(f"{say(abs(share) * HUNDRED, 0, en)} = {num} / {den} × 100")
        lines.append(("Phần tăng nằm ở đâu", Line(text, top.id, so_tinh=st, vai="dong_luc")))
        tp, ta = _num(top, t.plan, en), _num(top, t.actual, en)
        if tp and ta and abs(share) >= Decimal("0.4") and len(same) + len([m for m in moves if m[1]]) > 2:
            num = f"({tdelta[0]} - {top_tok})" if not (tdelta[2] or top_f) else \
                f"(({a_tok} - {p_tok}) - ({ta[0]} - {tp[0]}))"     # printed variances when the table has them
            expr = f"{num} / ({p_tok} - {tp[0]}) × 100"
            got = robust_token(expr, en)
            if got:
                rest_tok = got[0]
                others = f"các {noun} còn lại" if t.what else "phần còn lại"
                rest_v = evaluate(expr, en)
                whole = (a_val - p_val) / p_val * HUNDRED
                rev_v = C.value(con[2].tok, en) if con is not None else None
                # said as it is, never spun: a rest a little above revenue is "nhanh hơn doanh thu một chút", not
                # "sát nhịp"; "chỉ" only when the rest grew no faster than revenue (round-5 Chair and Coach)
                tail = ""
                if rest_v <= 0:
                    how = "giảm"
                elif rev_v is not None and rest_v > rev_v:
                    how = "vẫn tăng"
                    tail = (f", nhanh hơn doanh thu {con[2].tok}% một chút" if rest_v - rev_v <= 1
                            else ", nhanh hơn doanh thu")
                else:
                    how = "chỉ tăng" if rest_v < whole else "tăng"
                    if rev_v is not None:
                        tail = ", không nhanh hơn doanh thu"
                lines.append(("Phần tăng nằm ở đâu", Line(f"Bỏ {_short(top.label)} ra, {others} {how} khoảng {rest_tok}%{tail}.",
                                                     top.id, so_tinh=[f"{rest_tok} = {expr}"], vai="phan_con_lai",
                                                     xac_nhan=[con[2].tok] if con is not None and con[2].tok in tail else [])))
    bridge = headcount_bridge(places, t, en)
    if bridge is not None:                         # proposed: it may contradict the report's own words
        lines.append(("Phần tăng nằm ở đâu", Line(bridge[0], bridge[1], so_tinh=[bridge[2]], bat=False, vai="cau_noi")))

    # B6: what happens if nothing changes, the measure named
    rc = ratio_card(places, en)
    if rc is not None:
        p, a, b = rc
        over = next((g for g in cut.figs if g.sign == "-"), None) if cut is not None and cap is not None else None
        money = f", tức vượt trần {over.tok} {over.unit or unit}".replace("  ", " ") if over else ""   # the stakes in money
        lines.append(("Nếu giữ nguyên", Line(f"Nếu giữ nguyên, {_lc(p.label)} là {a.tok}%, thay vì {b.tok}% như kế hoạch "
                                             f"gốc{money}.", p.id, vai="neu_giu", xac_nhan=[over.tok] if over else [])))
    else:
        cond = if_note(items)
        if cond is not None:
            sens = bool(re.match(r"\s*nếu\s+doanh\s+(thu|số)", cond[1], re.I))
            lines.append(("Rủi ro nếu doanh thu hụt" if sens else "Nếu giữ nguyên",
                          Line(cond[1], cid(cond[0]), vai="neu_hut" if sens else "neu_giu")))

    # B8: the ask in figures
    if many:
        for k, (n, a) in enumerate(ask_pairs[:6]):
            lines.append(("Đề nghị", Line(f"{nth[k]} là {said(a)}.", cid(n), vai="hoi")))
    if cap is not None:
        f = cap.figs[0]
        lines.append(("Đề nghị", Line(f"Vì vậy {owner} đề xuất mức trần {f.tok} {f.unit or unit}.".replace("  ", " "), cap.id,
                                      vai="tran")))
    if cut is not None:
        f = next(g for g in cut.figs if g.sign == "-")
        lines.append(("Đề nghị", Line(f"Tức là phải giảm {f.tok} {f.unit or unit} so với {plan_said(t.actual_name)}.".replace("  ", " "),
                                      cut.id, dung=1.0, vai="tran")))

    elif cap is None and up:                       # the cap only inside an ask ("duyệt trần … 120,0 tỷ"): the cut it means
        for n, a in ask_pairs:
            m = re.search(r"\btrần\b[^0-9;]*?(\d[\d.,]*)\s*(tỷ|triệu)?", a)
            v = C.value(m.group(1), en) if m else None
            if v is None or (m.group(2) or unit) != unit or not (0 < v < a_val):
                continue
            expr = f"{a_tok} - {m.group(1)}"
            got = say(a_val - v, max(decimals(a_tok, en), decimals(m.group(1), en)), en)
            text = f"Trần {m.group(1)} {unit} nghĩa là phải giảm {got} {unit} so với {plan_said(t.actual_name)}.".replace("  ", " ")   # standalone: never "Tức là" after a line it does not follow from
            # said with its formula (user, 11-14), unless the cap it is worked out from is itself in doubt
            held = any(m.group(1) in (fl.get("so") or []) for fl in flags)
            lines.append(("Đề nghị", Line(text, cid(n), so_tinh=[f"{got} = {expr}"], dung=1.0, bat=not held, vai="tran")))
            flags.append({"xem": f"Câu «{text}» do em tính ({expr} = {got}); báo cáo chưa ghi số này. "
                                 + ("Em chưa nói vì mức trần đang bị nghi; anh quyết xong trần, đúng ý thì bật (bat: 'có')."
                                    if held else "Em nói kèm công thức; anh không muốn nói thì ghi bat: 'chưa'."),
                          "cau": [text]})
            break

    # B9: say plainly what the report does not give a figure for; the owner switches on one, both, or says why not
    for fl in flags:
        if fl.get("noi_thang") and note is not None:
            # no figure in it, so on by default: the Board hears the gap even before the owner fills it (round-5 #13)
            lines.append(("Đề nghị", Line(fl["noi_thang"], cid(note), vai="thang")))
            fl.setdefault("cau", []).append(fl["noi_thang"])
        if fl.get("de_xuat") and note is not None:       # a bound worked out from printed figures: said with its formula (user, 11-14)
            lines.append(("Đề nghị", Line(fl["de_xuat"][0], cid(note), so_tinh=fl["de_xuat"][1],
                                          xac_nhan=fl["de_xuat"][2], vai="uoc_tiet_kiem")))
            fl.setdefault("cau", []).append(fl["de_xuat"][0])
            if len(fl["de_xuat"]) > 3 and fl["de_xuat"][3]:
                lines.append(("Đề nghị", Line(fl["de_xuat"][3][0], cid(note), so_tinh=[fl["de_xuat"][3][1]],
                                              xac_nhan=fl["de_xuat"][2], vai="con_thieu")))
                fl["cau"].append(fl["de_xuat"][3][0])

    # B10: close on every decision asked at the start, then stop for questions
    if ask_list and many:                          # just listed one by one: the card after the talk holds them
        lines.append(("Chốt", Line(f"Tóm lại, {owner} xin anh chị quyết {count_w.get(len(ask_list), 'các việc')} vừa nêu; "
                                   "em để sẵn trên màn hình.", cid(first), dung=1.0, vai="chot")))
    elif ask_list:                                 # every ask again as a short tag, never word for word (round-4 coach)
        def tag(a) -> str:
            t = said(a)
            if ", tức " in t:                          # "giữ tỷ lệ … ở 2,40%, tức trần 177,5 tỷ": the money is the tag
                t = t.split()[0] + " " + t.split(", tức ", 1)[1]
            t = re.sub(r"\s+(trước khi|và gắn với|cho cả năm|để)\b.*$", "", t)
            if len(ask_pairs) > 2:                     # three asks or more: a few words each, or the close outruns the room
                m = re.search(r"\btrần\b[^0-9;]*?(\d[\d.,]*\s*(tỷ|triệu)?)", t)
                t = f"{t.split()[0]} trần {m.group(1)}" if m else \
                    re.sub(r"\s+(bằng|sang|cho|với|trong|từ)\b.*$", "", t).replace(" phải ", " ")
            return t
        tags = [tag(a) for _n, a in ask_pairs[:6]]
        body = "; ".join(f"{nth[k].lower()} là {x}" for k, x in enumerate(tags)) if len(tags) > 1 else tags[0]
        lines.append(("Chốt", Line(f"Tóm lại, {owner} xin anh chị quyết: {body}.", cid(first), dung=1.0, vai="chot")))
    elif cap is not None:
        f = cap.figs[0]
        lines.append(("Chốt", Line(f"Tóm lại, {owner} xin anh chị cho ý kiến về mức trần {f.tok} {f.unit or unit}."
                                   .replace("  ", " "), cap.id, dung=1.0, vai="chot")))
    lines.append(("Chốt", Line("Em kính mời anh chị cho ý kiến ạ.", "", dung=2.0, vai="moi_hoi")))

    # pacing: a breath after the greeting and the contrast, a longer one after the asks, a short one on the cap
    hoi = [k for k, (m_, ln) in enumerate(lines) if ln.vai == "hoi" and m_ == "Mở đầu"]
    for k, (muc, ln) in enumerate(lines):
        nxt = lines[k + 1][1].vai if k + 1 < len(lines) else ""
        if ln.dung or not ln.bat:
            continue
        if ln.vai == "chao":
            ln.dung = 0.8
        elif hoi and k == hoi[-1] and muc == "Mở đầu":
            ln.dung = 1.2
        elif ln.vai == "mau_thuan" and nxt != "mau_thuan":
            ln.dung = 0.8
        elif ln.vai == "tran":
            ln.dung = 0.4

    scenes: list = []
    for muc, ln in lines:
        if not scenes or scenes[-1]["muc"] != muc:
            scenes.append({"muc": muc, "cau": []})
        c = {"noi": plain(ln.noi), "chi": ln.chi}
        if ln.chi:
            c["neo"] = places[ln.chi].anchor
        if ln.so_tinh:
            c["so_tinh"] = ln.so_tinh
        if ln.dung:
            c["dung"] = ln.dung
        if ln.xac_nhan:
            c["xac_nhan"] = ln.xac_nhan
        if not ln.bat:
            c["bat"] = "chưa"
        elif ln.so_tinh:                           # a figure code worked out: said, and the owner can switch it off
            c["bat"] = "có"
        if ln.vai:
            c["vai"] = ln.vai
        scenes[-1]["cau"].append(c)
    checks = []
    for fl in flags:                               # each finding names the lines it governs and the sentence it doubts
        k = {"muc": fl["xem"]}
        if fl.get("cau"):
            k["cau"] = [plain(x) for x in fl["cau"]]
        if fl.get("sai"):
            k["ma_cau"] = fingerprint(fl["sai"])
        if fl.get("sai") or fl.get("tinh"):      # a doubted sentence, or a doubted figure printed on a card or in an ask
            if fl.get("so"):
                k["ma_so"] = list(fl["so"])
            if fl.get("tinh"):
                k["tinh"] = fl["tinh"]
        checks.append(k)
    return {"canh": scenes, "bo_qua": [], "chua_ro": [], "phan_bien": [fl["xem"] for fl in flags], "kieu": "ke_chuyen",
            "kiem_tra": checks, "cho_kiem": [fingerprint(fl["sai"]) for fl in flags if fl.get("sai")]}


def headcount_card(places: dict):
    """A card with a headcount and its change ("Headcount bình quân 1.248 người, +64")."""
    return next((p for p in places.values() if p.kind == "card" and p.figs and len(p.figs) >= 2
                 and p.figs[0].unit == "người" and p.figs[1].unit == "người" and p.figs[1].sign), None)


def headcount_bridge(places: dict, t: Table, en: bool) -> Optional[tuple]:
    """(line, place, formula): how much of the change comes from more people at the plan's cost per head, said as
    a share so it is not mistaken for another figure in tỷ. None without a headcount card."""
    hc, d = headcount_card(places), delta(t, t.total, en)
    if hc is None or not d or not d[1]:
        return None
    p_tok = _num(t.total, t.plan, en)[0]
    n_tok, dn_tok = hc.figs[0].tok, hc.figs[1].tok
    sign = "-" if hc.figs[1].sign == "-" else ""
    expr = f"{sign}{dn_tok} × {p_tok} / ({n_tok} - {sign}{dn_tok}) / {d[0]} × 100".replace("- -", "+ ")
    try:
        got = robust_token(expr, en, most=0) or (say(evaluate(expr, en), 0, en), 0)
        share = evaluate(expr, en)
    except ValueError:
        return None
    if share <= 0 or share >= 100:
        return None
    rest = "phần lớn hơn" if share < 50 else "phần còn lại"
    return (f"Phần tăng do thêm người chiếm khoảng {got[0]}%; {rest} là chi phí mỗi người tăng." if share < 50 else
            f"Phần tăng do thêm người chiếm khoảng {got[0]}%; {rest} là chi phí mỗi người tăng.", hc.id, f"{got[0]} = {expr}")


def preflight(items: list, places: dict, t: Table, en: bool, note, cut, owner: str = "người làm báo cáo") -> list:
    """What the owner should check before the Board sees the report. Never spoken to the Board, except the
    plain line that an ask has no figure yet ("noi_thang")."""
    flags = []
    hc = headcount_card(places)
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
            for it in items:                # a share the report claims for new people that the bridge does not give
                if it.kind != "note" or not re.search(r"tuyển|người|headcount|nhân sự mới", it.text, re.I):
                    continue
                for said in re.split(r"(?<=[.;])\s+", it.text):
                    m = CLAIM.search(said)
                    if not m or not re.search(r"tuyển|người|headcount|nhân sự mới", said, re.I):
                        continue
                    want = CLAIMS[m.group(1).lower()]
                    off = share < Decimal("0.5") if want is None else abs(share - want) > Decimal("0.10")
                    if off:
                        flags.append({"xem": f"Báo cáo viết «{C.clip(said, 80)}», nhưng tách theo số người × đơn giá "
                                             f"thì phần do tăng người chỉ khoảng {say(share * HUNDRED, 0, en)}%. Phần còn "
                                             "lại là đơn giá mỗi người tăng. Anh xem lại câu này trước khi gửi.",
                                      "sai": C.clip(said, 80)})
        except ValueError:
            pass
    con = contrast(items, en)
    if con is not None and d and d[1]:              # the note's cost growth against the table's own total
        expr = f"({a_tok} - {p_tok}) / {p_tok} × 100"
        try:
            lo, hi = spread(expr, en)
            printed = C.value(con[1].tok, en)
            k = decimals(con[1].tok, en)
            if not (rounded(min(lo, hi), k) <= printed <= rounded(max(lo, hi), k)):
                table_pct = say(evaluate(expr, en), 1, en)
                flags.append({"xem": f"Báo cáo ghi chi phí tăng {con[1].tok}%, nhưng bảng tính ra khoảng "
                                     f"{table_pct}%. Công thức: {expr}. Em chưa nói con số này; anh chọn: bật câu "
                                     f"{con[1].tok}% nếu số báo cáo đúng, hoặc sửa báo cáo, rồi ghi ly_do_bo.",
                              "so": [con[1].tok],
                              "tinh": f"tính từ bảng {pretty(expr)} ≈ {table_pct}%",
                              "sai": next((x for x in re.split(r"(?<=[.;])\s+", con[0].text) if con[1].tok in x),
                                          con[0].text)})
        except ValueError:
            pass
    if con is not None:                             # a cap said to keep the plan's cost/revenue ratio, against that ratio
        rev_tok = con[2].tok
        basis = next(((said, m) for it in items for said in re.split(r"(?<=[.;])\s+", it.text)
                      for m in [re.search(r"trần[^0-9;]*?(\d[\d.,]*)\s*(tỷ|triệu)?", said, re.I)]
                      if m and re.search(r"(bằng|giữ|như)\s+(nguyên\s+)?tỷ lệ.*(AOP gốc|kế hoạch gốc|ngân sách gốc)", said, re.I)), None)
        for said, m in [basis] if basis else []:
                expr = f"{p_tok} × (1 + {rev_tok} / 100)"
                try:
                    lo, hi = spread(expr, en)
                    cap, k = C.value(m.group(1), en), decimals(m.group(1), en)
                    if cap is not None and not (rounded(lo, k) <= cap <= rounded(hi, k)):
                        u = m.group(2) or t.unit or ""
                        got = say(evaluate(expr, en), k, en)
                        # a doubted figure: the Board asking "120 có đúng không" is held with both figures until he rules
                        flags.append({"xem": f"Anh sửa trần hoặc ghi quyet: báo cáo ghi trần {m.group(1)} "
                                             f"{u} «{C.clip(said, 70)}», nhưng giữ đúng tỷ lệ "
                                             f"chi phí trên doanh thu của {t.plan_name} thì trần là {pretty(expr)} = "
                                             f"{got} {u}.".replace("  ", " "),
                                      "so": [m.group(1)],
                                      "tinh": f"giữ đúng tỷ lệ chi phí trên doanh thu của {plan_said(t.plan_name)} thì trần là "
                                              f"{pretty(expr)} = {got} {u}".replace("  ", " ").strip()})
                except ValueError:
                    pass
    if hc and rate:
        n_tok, dn_tok = hc.figs[0].tok, hc.figs[1].tok
        expr = f"({a_tok} / {n_tok}) / ({p_tok} / ({n_tok} - {dn_tok})) × 100 - 100"
        try:
            exact = evaluate(expr, en)
            lo, hi = spread(expr, en, exact=(n_tok, dn_tok))
            printed = C.value(rate.figs[1].tok, en)
            places_ = decimals(rate.figs[1].tok, en)
            if not (rounded(lo, places_) <= printed <= rounded(hi, places_)):
                flags.append({"xem": f"Thẻ «{rate.label}» ghi tăng {rate.figs[1].tok}%, tính lại từ tổng chi phí và số "
                                     f"người thì ra khoảng {say(exact, 2, en)}%. Công thức: {expr}. Anh sửa thẻ, hoặc ghi "
                                     "quyet: 'giữ câu' nếu thẻ tính theo cách khác (ví dụ từ đơn giá đã làm tròn).",
                              "so": [rate.figs[1].tok],
                              "tinh": f"tính từ tổng chi phí và số người: {pretty(expr)} ≈ {say(exact, 2, en)}%"})
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
                    flag = {"xem": f"Đề xuất «{m.group(0)}» chưa ghi tiết kiệm được bao nhiêu. Theo bình quân "
                                   f"{_short(row.label)} ({per}), giãn trọn năm khoảng {say(year, 1, en)} "
                                   f"{t.unit or ''}{need}. Anh bổ sung số tháng giãn vào báo cáo, em đang nói câu "
                                   "ước tính kèm công thức (anh không muốn thì ghi bat: 'chưa').",
                            "noi_thang": f"Có một điểm báo cáo chưa có số: giãn {m.group(1)} {m.group(2)} tiết kiệm "
                                         "được bao nhiêu."}
                    expr = f"{m.group(1)} × {per}"
                    got = robust_token(expr, en, most=1)
                    got = (say(year, 1, en), 1) if got and decimals(got[0], en) == 0 and \
                        C.value(say(year, 1, en), en) != C.value(got[0], en) else got   # 3,89 is "khoảng 3,9", never "4"
                    if cut is not None and got:
                        ratio = year / C.value(cut.figs[0].tok, en)
                        enough = ("chưa bằng một nửa" if ratio < Decimal("0.45") else "chưa đủ" if ratio < 1 else "đủ bù")
                        flag["de_xuat"] = (f"Em tính theo chi phí bình quân một người ở {_short(row.label)}: kể cả giãn trọn năm, "
                                           f"{m.group(1)} {m.group(2)} cũng chỉ tiết kiệm "
                                           f"khoảng {got[0]} {t.unit or ''}, {enough} mức {cut.figs[0].tok} "
                                           f"{cut.figs[0].unit or ''} cần giảm.".replace("  ", " ").replace(" ,", ","),
                                           [f"{got[0]} = {expr}"], [cut.figs[0].tok], None)   # the cut is printed at its own place
                        if ratio < 1:                    # what is left of the cut: from the printed figures, never from 3,9
                            rest = f"{cut.figs[0].tok} - {expr}"
                            left = say(evaluate(rest, en), 1, en)
                            flag["de_xuat"] = flag["de_xuat"][:3] + ((f"Còn lại khoảng {left} {t.unit or ''}, báo cáo chưa nêu "
                                                                      "cách bù.".replace("  ", " "), f"{left} = {rest}"),)
                    flags.append(flag)
                except ValueError:
                    pass
    return flags



# --- questions the Board asks after the talk, answered from the talk itself ------------------------------------

def jfold(s: str) -> str:
    """The layer's fold(): no accents, lower case, only letters, digits and number marks. Kept identical to the
    JavaScript one so a pattern written here matches there."""
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%,.\-+ ]+", " ", s)).strip()


# What a question is about, on the folded text (no accents). Each answer is the owner's approved lines of that beat,
# so an answer never holds a figure the talk did not already say and the number lock did not already check.
INTENT = {
    "ai": [r"\b(em|ban|may|chi|anh) la ai\b", r"\bai (dang )?(noi|trinh bay|thuyet trinh)\b", r"\bla (nguoi|may) (that|hay)\b",
           r"\b(nguoi that|ai that|tri tue nhan tao)\b"],
    "hoi": [r"\b(tom lai|chot lai|noi gon|tom tat)\b", r"\bquyet (gi|nhung gi|viec gi|dieu gi|cai gi|may viec)\b",
            r"\b(can|xin|muon|de nghi)( anh chi| chung toi| bod| hdqt| ban giam doc| ban tong giam doc)? (quyet|duyet|phe duyet)\b",
            r"\b(de nghi|de xuat|xin) (gi|nhung gi|cu the|cai gi)\b",
            r"\b(noi ngan|ngan gon|mot cau|giai thich don gian|(toi|chung toi|bod|minh) can (lam|quyet) gi)\b",
            r"\b(nhac lai|phuong an nao|lam gi tiep|(hai|ba|bon|nam) viec (do |nay )?(la )?gi|don gian hon|chua hieu)\b"],
    "can_quyet": [r"\b(vi sao|tai sao|sao) (lai )?(phai|can)? ?quyet\b", r"\bquyet ngay\b", r"\bgap (the|vay)\b"],
    "tran": [r"\b(muc )?tran\b", r"\b(giam|cat) (bao nhieu|may|bot bao nhieu|di bao nhieu)\b", r"\b(can|phai) (giam|cat)\b"],
    "tiet_kiem": [r"\btiet kiem\b", r"\b(du|bu) (bu|du|duoc|lai|giam)\b"],
    "cau_noi": [r"\b(them nguoi|tuyen them|headcount|so nguoi|don gia|moi nguoi|binh quan|tang nguoi)\b"],
    "neu_giu": [r"\bneu (giu nguyen|de nguyen|khong (quyet|duyet|lam gi|cat|giam|lam))\b", r"\b(rui ro|hau qua)\b",
                r"\b(dieu|chuyen) gi (se )?xay ra\b"],
    "dong_luc": [r"\b(nam|tang|vuot|do) (o|tu) (dau|cho nao)\b",
                 r"\b(chu yeu|phan lon) (do|tu|o|la|nam)\b", r"\b(nguyen nhan|dong luc) chinh\b"],
    "neu_hut": [r"\bneu doanh (thu|so)\b", r"\bdoanh (thu|so) (giam|hut|khong dat|thap hon)\b"],
    "vi_sao": [r"\b(vi sao|tai sao|sao lai|ly do|vi dau)\b"],
}
# Words that send a question elsewhere even when a pattern above meets it: "tăng ít nhất" is the table's minimum,
# "giãn tuyển thì doanh thu có giảm không" is a question the report does not answer, never the savings line.
NOT = {"dong_luc": r"\b(it|thap|nho) nhat\b|\bgiam\b", "tiet_kiem": r"\bdoanh (thu|so)\b|\bban duoc\b|\bkhach hang\b",
       "neu_giu": r"\bdoanh (thu|so)\b", "tran": r"\bneu (khong|giu|de)\b"}
HOW = (r"\b(o dau ra|dau ra|tu dau|lay (o|tu) dau|tinh (the nao|sao|ra sao|bang cach nao|ra|lai)|cong thuc|"
       r"co (dung|chac|chinh xac) khong|kiem (lai|tra)|la sao|nghia la gi|lam sao ra|sao ra)\b")
BEATS = {"ai": ("chao",), "can_quyet": ("quy_mo", "mau_thuan"), "hoi": ("hoi",), "tran": ("tran",), "tiet_kiem": ("thang", "uoc_tiet_kiem"),
         "cau_noi": ("cau_noi",), "neu_giu": ("neu_giu",), "dong_luc": ("dong_luc", "phan_con_lai"),
         "vi_sao": ("quy_mo", "mau_thuan"), "neu_hut": ("neu_hut",)}
ANY_SCOPE = ("t", "ai", "hoi", "can_quyet")          # asked while pointing at a place, the rest are about that place instead


def fingerprint(sentence: str) -> str:
    """FNV-1a of the folded sentence: lets the page recognise a report sentence the owner was asked to check
    without the page carrying the owner's doubt in words. The layer computes the same on the page's sentences."""
    h = 0x811C9DC5
    for b in jfold(sentence).encode("ascii", "ignore"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")


def tok_pattern(tok: str) -> str:
    """A figure as it stands in a folded question: "8" in "con so 8% o dau ra", never inside "18" or "8,9"."""
    return r"(^|[^0-9.,])" + re.escape(jfold(tok)) + r"(?![0-9]|[.,][0-9])"


def pretty(expr: str) -> str:
    return re.sub(r"\s*\*\s*|\s+x\s+", " × ", expr).replace(" / ", " ÷ ").replace(" - ", " − ")


def formula_text(tok: str, expr: str, en: bool) -> str:
    """The formula behind a figure Clicky worked out, as the reader sees it: the exact result too when the talk said
    it rounded ("= 7,79, nói tròn là 8"), so nobody takes a rounded figure for the report's own."""
    try:
        exact = evaluate(expr, en)
    except ValueError:
        return f"{pretty(expr)} = {tok}"
    q = rounded(abs(exact), decimals(tok, en) + 2).normalize()          # 1,750 is said 1,75
    fine = say(exact, max(0, -q.as_tuple().exponent), en)
    return f"{pretty(expr)} = {tok}" if C.value(fine, en) == C.value(tok, en) else \
        f"{pretty(expr)} = {fine}, nói tròn là {tok}"


def facts(script: dict, places: dict, en: bool, owner: str) -> list:
    """The answers a Board member gets after the talk, before any search of the page. Built from the script the owner
    approved, so each answer is lines he read (and the number lock checked), in the order he said them:

    - "t": how a figure Clicky worked out was computed (the line, then its formula), for each so_tinh;
    - one entry per beat with lines: "hoi" (what is asked), "vi_sao", "dong_luc", "neu_giu", "tran"…

    Each entry: k (kind), y (patterns on the folded question), x (row names that send the question to that row
    instead), a (the answer shown), noi (what is said), chi (the place framed), tu (indices of the spoken lines
    used, as in the record), and for "t" tok and cong_thuc."""
    lines = []                                     # (index in the record, line) for every line Clicky says
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            if C.on(c) and str(c.get("noi") or "").strip():     # the record's own rule: its rows, in order
                lines.append((len(lines), c))
    t = main_table(places, en)
    names = []
    for r in (t.rows if t else []):
        m = COMMON_NOUN.match(r.label)
        core = jfold(r.label[m.end():] if m else r.label)
        if len(core) >= 3:
            names.append(core)
    out = []
    seen = set()
    for i, c in lines:                              # "con số 1,8 ở đâu ra": the line that says it, then the formula
        for f in c.get("so_tinh") or []:
            tok, _, expr = str(f).partition("=")
            tok, expr = tok.strip(), expr.strip()
            if not tok or not expr or tok in seen:
                continue
            seen.add(tok)
            said = str(c["noi"]).strip()
            y = [tok_pattern(tok) + r".*" + HOW, HOW + r".*" + tok_pattern(tok)]
            claim = CLAIM.search(said)
            if claim and claim.group(1).lower() != "phần lớn" and "× 100" in expr:   # "hai phần ba à?": the words, then the share
                cf = jfold(claim.group(1))
                y += [cf + r".*" + HOW, HOW + r".*" + cf, cf + r" (a|ha|that a|that khong|dung khong|la bao nhieu( phan tram)?)\s*$"]
            out.append({"k": "t", "y": y, "x": [],
                        "a": f"{said} Em tính từ các số trên báo cáo: {formula_text(tok, expr, en)}.",
                        "noi": f"{said} Công thức em hiện trên màn hình ạ.", "chi": c.get("chi") or "", "tu": [i],
                        "tok": tok, "cong_thuc": expr, "vai": c.get("vai") or ""})
    has = {c.get("vai") for _i, c in lines}
    for k, beats in BEATS.items():
        used = [(i, c) for i, c in lines if c.get("vai") in beats]
        pats = INTENT[k] + (INTENT["neu_giu"] if k == "neu_hut" and "neu_giu" not in has else [])
        if k == "hoi" and any("việc đầu tiên là" in str(c["noi"]) for _i, c in used) \
                and any(str(c["noi"]).startswith("Một là") for _i, c in used):    # many asks: the full list at the end
            used = used[next(j for j, (_i, c) in enumerate(used) if str(c["noi"]).startswith("Một là")):]
        if not used:
            continue
        said = " ".join(str(c["noi"]).strip() for _i, c in used)
        if k == "ai":
            said += f" Nội dung do {owner} soạn; em không tự đặt số, số nào em tính đều kèm công thức."
        x = [] if k in ANY_SCOPE else [n for n in names if n not in jfold(said)]
        out.append({"k": k, "y": pats, "n": NOT.get(k, "") if k != "neu_hut" else "", "x": x, "a": said, "noi": said,
                    "chi": next((c.get("chi") for _i, c in used if c.get("chi")), ""), "tu": [i for i, _c in used]})
    return out


def answer_fact(facts_: list, q: str, scoped: bool = False, weak: bool = True) -> Optional[dict]:
    """The layer's factMatch(), for tests and the command line: the first entry whose pattern the question meets.
    The page is searched first for every beat except "t", "ai" and "hoi"; weak=False is a page that found an answer."""
    fq = " " + jfold(q) + " "
    for f in facts_:
        if f["k"] not in ANY_SCOPE and (scoped or not weak):
            continue
        if any(re.search(p, fq) for p in f["y"]) and not any(f" {n} " in fq for n in f["x"]) \
                and not (f.get("n") and re.search(f["n"], fq)):
            return f
    return None


NTH = re.compile(r"^(Một|Hai|Ba|Bốn|Năm|Sáu)( là,?|,)\s*")


def decisions(script: dict) -> list:
    """The decisions asked, one per line, for the card shown after the talk: the owner's approved "Một, …" lines
    without their count ("Giữ tỷ lệ … ở 2,40%, tức trần 177,5 tỷ"). A talk that asks one thing gives that line."""
    said = [str(c["noi"]).strip() for s in script.get("canh") or [] for c in s.get("cau") or []
            if C.on(c) and c.get("vai") == "hoi" and str(c.get("noi") or "").strip()]
    listed = [_cap(NTH.sub("", x)).rstrip(".") for x in said if NTH.match(x)]
    if listed:
        return list(dict.fromkeys(listed))[:6]
    single = [x for x in said if not re.search(r"xin anh chị quyết (một|hai|ba|bốn|năm|sáu|nhiều) việc", x)]
    return [single[0].rstrip(".")] if single else []
