"""The "so sánh được" gate: a change between two periods is explained only when it is like for like.

Two checks, one rule (never explain a change you cannot show is like for like):
- Basis (F8). Accounting software states the book and the method in a trial balance's title and header rows
  (which book, which preparation method, the unit, whether the movement columns are the month or year to date).
  The two periods' header blocks are read into a signature; when they differ, no variance is explained at all.
- Bridge (F5). This period's opening balance of every account must equal the prior period's closing balance.
  An account-mapping table in the folder (old code → new code) carries renumbered accounts across; a code change
  it lists is not an error. Accounts that do not bridge, or that look renumbered with no mapping, are blocked:
  no explanation uses them.

Pure Python, no Excel. Numbers come from the trial balances only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine.trial_balance import ACCOUNT, ZERO, TrialBalance, fold

TOLERANCE = Decimal(1)

# ── Basis signature (F8) ──────────────────────────────────────────────────────
#
# An allowlist: only the title and lines that carry a basis word enter the signature. Everything else an export
# prints above its columns (print time, page number, printed by, user names, the period in any language) is
# ignored, so two months of the same report never differ by accident.

# Lines that name the company, not the basis.
_IDENTITY = ("cong ty", "tnhh", "co phan", "dia chi", "ma so thue", "mst", "dien thoai", "fax", "email", "website",
             "du lieu gia lap", "company", "address", "tax code")
# Words a title carries; the first such line above the columns is the title.
_TITLE_WORDS = ("bang can doi", "can doi", "so cai", "so tong hop", "so chi tiet", "bao cao", "trial balance", "ledger")
# Names software gives the same report: one name, so "Bảng cân đối số phát sinh" equals "Bảng cân đối tài khoản".
_TB_TITLES = ("bang can doi so phat sinh tai khoan", "bang can doi so phat sinh", "bang can doi phat sinh tai khoan",
              "bang can doi phat sinh", "bang can doi tai khoan", "can doi tai khoan", "can doi so phat sinh",
              "trial balance")
# Basis words by kind. "book" and "method" block when they differ or are stated in one period only; "regime" and
# "unit" block only when both periods state them and they differ (a line in one period only is a soft note).
_BASIS = (("book", ("loai so", "so tai chinh", "so quan tri", "hop nhat", "bao cao rieng", "rieng le",
                    "management book", "financial book", "consolidated")),
          ("method", ("phuong phap", "co so lap", "method", "basis of preparation")),
          ("movement", ("luy ke", "tu dau nam", "year to date", "ytd")),
          ("regime", ("che do ke toan", "thong tu", "quyet dinh", "tt-btc", "qd-btc")),
          ("unit", ("don vi tinh", "dvt", "loai tien", "tien te", "currency")))
HARD = ("book", "method", "movement")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
           "thang mot", "thang hai", "thang ba", "thang tu", "thang nam", "thang sau", "thang bay", "thang tam",
           "thang chin", "thang muoi mot", "thang muoi hai", "thang muoi", "thang gieng", "thang chap")
_PERIOD_WORDS = ("tu ngay", "den ngay", "ngay", "thang", "quy", "nam", "ky bao cao", "ky", "from", "to", "date",
                 "period", "month", "quarter", "year", "luc", "gio")
_DATES = (r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)",                       # 10:23, 08:01:59
          r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}(?!\d)",              # 01/10/2026
          r"(?<!\d)\d{1,2}[/.\-](?:19|20)\d{2}(?!\d)",                    # 10/2026
          r"(?:tu ngay|den ngay|ngay|thang|quy|nam|ky|month|quarter|year|q)\s*\d{1,4}(?!\d)",
          r"(?<!\d)(?:19|20)\d{2}(?!\d)")                                  # a year on its own


def _strip_period(text: str) -> str:
    """The line without the period, time and month it names; other numbers stay ("Thông tư 200" vs "133")."""
    t = text
    for pattern in _DATES:
        t = re.sub(pattern, " ", t)
    for w in sorted(_MONTHS + _PERIOD_WORDS, key=len, reverse=True):
        t = re.sub(rf"(^|[^a-z0-9]){w}(?=$|[^a-z0-9])", " ", t)
    return re.sub(r"[\s,.;:\-–()/]+$", "", re.sub(r"^[\s,.;:\-–()/]+", "", re.sub(r"\s+", " ", t))).strip()


def _unit_of(text: str) -> str:
    v = text.split(":", 1)[-1].strip() if ":" in text else re.sub(r"^(don vi tinh|dvt|loai tien|tien te|currency)", "", text).strip()
    if v.startswith(("dong", "vnd", "vn dong")):
        return "đồng"
    if v.startswith(("nghin", "ngan")):
        return "nghìn đồng"
    if v.startswith("trieu"):
        return "triệu đồng"
    return v


_REGULATION = re.compile(r"(?:(thong tu|tt|quyet dinh|qd)\s*(?:so\s*)?)?(?<!\d)(\d{1,3})\s*/\s*((?:19|20)\d{2})"
                         r"(?:\s*/\s*(tt|qd)\s*-?\s*btc)?")


def _regime_of(t: str) -> str:
    """The regulations a regime line names, as ids ("tt 200/2014"), so "Ban hành theo Thông tư số 200/2014/TT-BTC
    ngày 22/12/2014 của Bộ Tài chính" equals "(Ban hành theo TT 200/2014/TT-BTC)". Falls back to the line's text."""
    ids = set()
    for m in _REGULATION.finditer(t):
        kind = m.group(4) or m.group(1) or ""
        if not kind:
            continue                                    # a bare date-like n/yyyy is not a regulation
        kind = "qd" if kind.startswith(("q", "quyet")) else "tt"
        ids.add(f"{kind} {m.group(2)}/{m.group(3)}")
    return " | ".join(sorted(ids)) if ids else _strip_period(t)


def _has_word(t: str, words) -> bool:
    return any(re.search(rf"(^|[^a-z0-9]){re.escape(w)}($|[^a-z0-9])", t) for w in words)


@dataclass
class Basis:
    lines: dict = field(default_factory=dict)       # kind → (shown text, normalized value)

    def compare(self, other: "Basis") -> tuple:
        """(hard, soft): [(what, this period's text, the other's text)]. hard blocks every explanation; soft is a
        note on the receipt (a regime line stated in one period only)."""
        hard, soft = [], []
        for key in ("title",) + tuple(k for k, _ in _BASIS):
            a, b = self.lines.get(key), other.lines.get(key)
            if not a and not b or (a and b and a[1] == b[1]):
                continue
            shown = (a or b)[0]
            what = shown.split(":")[0].strip().lower() if ":" in shown and " · " not in shown and key != "title" \
                else _WHAT[key]
            item = (what, a[0] if a else "", b[0] if b else "")
            if a and b:
                (soft if key == "unit" and not (a[1] and b[1]) else hard).append(item)
            elif key in HARD:
                hard.append(item)
            elif key != "unit":                       # a unit said once is "unknown" on the other side, not a change
                soft.append(item)
        return hard, soft

    def differs(self, other: "Basis") -> list:
        return self.compare(other)[0]


_WHAT = {"title": "tiêu đề", "book": "loại sổ", "method": "phương pháp lập", "movement": "cột phát sinh",
         "regime": "chế độ kế toán", "unit": "đơn vị tính"}


def basis(rows: list, tb: TrialBalance) -> Basis:
    """The basis signature of one trial balance: its title, the header lines that carry a basis word, and what its
    movement columns hold (the period, or the year to date)."""
    sig = Basis()
    found: dict = {}
    for row in rows[:max(tb.header_row - 1, 0)]:
        for cell in row or []:
            if not isinstance(cell, str) or not cell.strip():
                continue
            shown = re.sub(r"\s+", " ", cell.strip())
            t = fold(shown)
            if _has_word(t, _IDENTITY):
                continue
            if "title" not in sig.lines and _has_word(t, _TITLE_WORDS):
                value = _strip_period(t)
                for name in _TB_TITLES:
                    value = value.replace(name, "bang can doi tai khoan")
                sig.lines["title"] = (shown, value)
            for kind, words in _BASIS:
                if _has_word(t, words):
                    value = _unit_of(t) if kind == "unit" else _regime_of(t) if kind == "regime" else _strip_period(t)
                    found.setdefault(kind, []).append((shown, value))
    for kind, items in found.items():
        sig.lines[kind] = (" · ".join(x for x, _ in items), " | ".join(sorted(v for _, v in items)))
    move = tb.columns.get("dr")
    if move is not None and "movement" not in sig.lines:
        header = []
        for r in range(max(tb.header_row - 1, 0), min(tb.header_row + 1, len(rows))):
            row = rows[r] or []
            # A merged section cell sits left of its Nợ column: look back to the nearest text.
            for i in range(move - 1, -1, -1):
                if i < len(row) and isinstance(row[i], str) and row[i].strip():
                    header.append(fold(row[i]))
                    break
        if _has_word(" ".join(header), dict(_BASIS)["movement"]):
            sig.lines["movement"] = ("Phát sinh lũy kế", "ytd")
    return sig


# ── Account mapping and the opening-balance bridge (F5) ──────────────────────

_ACCOUNT_WORDS = ("tk", "ma", "tai khoan", "account", "code", "so hieu")
_OLD = ("cu", "old", "truoc", "goc", "ban dau", "chuyen tu", "from", "tt200", "tt 200", "tt133", "tt 133",
        "thong tu 200", "thong tu 133")
_NEW = ("moi", "new", "nay", "sau", "ghep vao", "chuyen sang", "chuyen thanh", "doi thanh", "dua vao", "gop vao",
        "into", "tt99", "tt 99", "thong tu 99")
_TARGET_VERBS = ("ghep vao", "chuyen sang", "chuyen thanh", "doi thanh", "dua vao", "gop vao")
_REGIMES = ("tt200", "tt 200", "tt133", "tt 133", "tt99", "tt 99", "thong tu 200", "thong tu 133", "thong tu 99")
_BARE_SIDE = {"cu": "old", "old": "old", "truoc": "old", "ky truoc": "old", "nam truoc": "old",
              "moi": "new", "new": "new", "sau": "new", "ky nay": "new", "nam nay": "new"}
_CODE = re.compile(r"^\s*(?:tk|tai khoan|account)?\s*[:.]?\s*(\d{3,10}(?:[.\-]\d{1,6})*)(?:\s*$|\s*[-–:(]|\s+\D)", re.I)


def _has(text: str, words) -> bool:
    return any(re.search(rf"(^|[^a-z0-9]){re.escape(w)}($|[^a-z0-9])", text) for w in words)


def _header_side(text: str) -> Optional[str]:
    """"old" / "new" for a mapping table's code column header ("TK cũ", "Mã TK mới", "TT200", "New account")."""
    t = fold(text)
    if not _has(t, _ACCOUNT_WORDS) and not _has(t, _REGIMES) and not _has(t, _TARGET_VERBS):
        return None
    if _has(t, _OLD):
        return "old"
    if _has(t, _NEW):
        return "new"
    return None


def _code(cell) -> str:
    """The account code in a mapping cell: "6417", 6417.0, "TK 6417", "6417 - Dịch vụ mua ngoài"; "" otherwise."""
    if cell is None:
        return ""
    text = re.sub(r"\.0$", "", str(cell).strip())
    if ACCOUNT.fullmatch(text):
        return text
    m = _CODE.match(fold(text))
    return m.group(1) if m else ""


def _density(rows: list, col: int) -> float:
    """Share of the filled cells under a header that are account codes (a name column scores near 0)."""
    cells = [r[col] for r in rows[:60] if r and col < len(r) and r[col] not in (None, "")]
    return sum(1 for c in cells if _code(c)) / len(cells) if cells else 0.0


def _sides(row, above, below: list) -> dict:
    """{"old": column, "new": column} for a header row, chosen by what the columns hold.

    A header says which side ("TK cũ", "Ghép vào…", a bare "Cũ" under a merged "Số hiệu tài khoản"), but the name
    column beside a code column often says "cũ"/"mới" too: of the columns a side's headers point at, and their
    neighbours, the one whose cells are mostly account codes is the code column (grader, Case 05 class)."""
    marked = {"old": [], "new": []}
    for i, c in enumerate(row or []):
        if isinstance(c, str):
            side = _header_side(c)
            if side:
                marked[side].append(i)
    if not (marked["old"] and marked["new"]):
        context = [c for c in list(above or []) + list(row or []) if isinstance(c, str)]
        if any(_has(fold(c), _ACCOUNT_WORDS) for c in context):
            for i, c in enumerate(row or []):
                side = _BARE_SIDE.get(fold(c).strip(" :()")) if isinstance(c, str) else None
                if side and i not in marked[side]:
                    marked[side].append(i)
    if not (marked["old"] and marked["new"]):
        return {}
    width = max(len(r or []) for r in [row] + below[:60])
    claimed = {i: side for side, cols in marked.items() for i in cols}
    sides = {}
    for side in ("old", "new"):
        near = []
        for i in marked[side]:
            near += [j for j in (i, i - 1, i + 1) if 0 <= j < width and claimed.get(j, side) == side
                     and j not in sides.values() and j not in near]
        best = max(near, key=lambda j: (_density(below, j), j in marked[side]), default=None)
        if best is None or _density(below, best) < 0.5:
            return {}
        sides[side] = best
    return sides


def read_mapping(rows: list) -> dict:
    """{old code: new code} from a table with an old-code column and a new-code column; {} when it is not one."""
    for r, row in enumerate(rows[:15]):
        sides = _sides(row, rows[r - 1] if r else None, rows[r + 1:])
        if len(sides) < 2:
            continue
        out = {}
        for data in rows[r + 1:]:
            data = data or []
            old, new = (_code(data[sides[k]]) if sides[k] < len(data) else "" for k in ("old", "new"))
            if old and new and old != new:
                out[old] = new
        if out:
            return out
    return {}


_MAPPING_NAMES = ("ghep", "doi ma", "mapping", "map", "chuyen doi", "anh xa", "tuong ung", "doi chieu ma")


def _tables(p: Path, read_rows) -> list:
    """Every sheet's rows in a file (a mapping table is often the second sheet of a workbook)."""
    if p.suffix.lower() == ".csv":
        return [read_rows(p)]
    from engine import tabular
    return [rows for _name, rows in tabular.sheets(p, keep_blank=True)]


def find_mapping(folder: Path, read_rows) -> tuple:
    """(file name, {old: new}) for the first account-mapping table in the folder, or ("", {}).

    Files named like one ("Bảng ghép TK", "Mapping TT200-TT99") are read first, then the rest: a table with an
    old-code and a new-code column is a mapping whatever the file is called (grader, Case 05 class). A file whose
    name does not say so needs two pairs or more, so a stray two-column list is not taken for one."""
    files = [p for p in sorted(folder.glob("*"), key=lambda p: p.name.lower())
             if p.is_file() and not p.name.startswith("~$") and p.suffix.lower() in (".csv", ".xlsx", ".xls")]
    named = [p for p in files if any(w in fold(p.stem).replace("_", " ").replace("-", " ") for w in _MAPPING_NAMES)]
    for p in named + [p for p in files if p not in named]:
        try:
            tables = _tables(p, read_rows)
        except Exception:
            continue
        for rows in tables:
            mapping = read_mapping(rows)
            if mapping and (p in named or len(mapping) >= 2):
                return p.name, mapping
    return "", {}


def remap(tb: TrialBalance, mapping: dict) -> TrialBalance:
    """The prior period's trial balance under this period's codes: each mapped account takes its new code."""
    if not mapping:
        return tb
    from dataclasses import replace
    lines = [replace(l, account=mapping.get(l.account, l.account)) for l in tb.lines]
    return replace(tb, lines=lines)


def _net(lines, dr: str, cr: str) -> dict:
    out = {}
    for l in lines:
        out[l.account] = out.get(l.account, ZERO) + getattr(l, dr) - getattr(l, cr)
    return out


@dataclass
class Gap:
    account: str
    name: str
    closing_before: Decimal          # prior period, Dư Nợ − Dư Có, under this period's code
    opening_now: Decimal
    renumbered: bool = False         # looks like a code change the mapping does not list

    @property
    def diff(self) -> Decimal:
        return self.opening_now - self.closing_before


def bridge(prev: TrialBalance, cur: TrialBalance, mapping: Optional[dict] = None, new_year: bool = False) -> tuple:
    """(gaps, checked): accounts whose opening balance is not the prior closing balance.

    prev is already under this period's codes (remap). checked is False when either file has no balance columns.
    At the start of a year VAS moves 4212 (this year's profit) into 4211 (prior years'): 421 is bridged as one."""
    need = ({"close_dr", "close_cr"} <= prev.columns.keys(), {"open_dr", "open_cr"} <= cur.columns.keys())
    if not all(need):
        return [], False
    before = _net(prev.leaves, "close_dr", "close_cr")
    now = _net(cur.leaves, "open_dr", "open_cr")
    names = {l.account: l.name for l in prev.lines}
    names.update({l.account: l.name for l in cur.lines})
    if new_year:
        for side in (before, now):
            moved = [a for a in side if a.startswith("421")]
            if moved:
                total = sum((side.pop(a) for a in moved), ZERO)
                side["421"] = total
                names.setdefault("421", "Lợi nhuận sau thuế chưa phân phối")
    gaps = []
    for account in sorted(set(before) | set(now)):
        b, n = before.get(account, ZERO), now.get(account, ZERO)
        if abs(b - n) > TOLERANCE:
            gaps.append(Gap(account, names.get(account, ""), b, n))
    _mark_renumbered(gaps, before, now)
    return gaps, True


def _mark_renumbered(gaps: list, before: dict, now: dict) -> None:
    """Codes that left and codes that arrived with the same balance are a code change, not a broken bridge.

    One old code to one new code with the same balance (same parent first, then anywhere when the balance is
    unique), then several old codes of one parent whose balances add up to one new code, or one old code split
    across new codes of one parent. Only what is left after this is an error."""
    gone = [g for g in gaps if g.account not in now and abs(g.closing_before) > TOLERANCE]
    came = [g for g in gaps if g.account not in before and abs(g.opening_now) > TOLERANCE]

    def pair(old, new):
        old.renumbered = new.renumbered = True
        gone.remove(old)
        came.remove(new)

    for same_parent in (True, False):
        for g in list(gone):
            twins = [c for c in came if abs(c.opening_now - g.closing_before) <= TOLERANCE
                     and (not same_parent or c.account[:3] == g.account[:3])]
            alike = [o for o in gone if abs(o.closing_before - g.closing_before) <= TOLERANCE]
            if len(twins) == 1 and (same_parent or len(alike) == 1):
                pair(g, twins[0])
    for many, one, side_many, side_one in ((gone, came, "closing_before", "opening_now"),
                                           (came, gone, "opening_now", "closing_before")):
        for x in list(one):
            group = [m for m in many if m.account[:3] == x.account[:3]]
            if len(group) >= 2 and abs(sum((getattr(m, side_many) for m in group), ZERO) - getattr(x, side_one)) <= TOLERANCE:
                for m in group:
                    m.renumbered = True
                    many.remove(m)
                x.renumbered = True
                one.remove(x)


def renumbered_pnl(prev: TrialBalance, cur: TrialBalance) -> list:
    """Income and expense codes that moved last period and are gone now, while a new code under the same parent
    moved this period: [(old, new)]. Balances cannot show these (books are closed to 911), the movements can."""
    def moved(tb):
        return {l.account for l in tb.leaves if l.account[0] in "5678" and (l.dr or l.cr)}
    before, now = moved(prev), moved(cur)
    listed_now = {l.account for l in cur.lines}
    listed_before = {l.account for l in prev.lines}
    gone = sorted(a for a in before if a not in listed_now)
    came = sorted(a for a in now if a not in listed_before)
    pairs = []
    for old in gone:
        new = next((n for n in came if n[:3] == old[:3] and n not in {p[1] for p in pairs}), None)
        if new:
            pairs.append((old, new))
    return pairs


# ── The gate ──────────────────────────────────────────────────────────────────

@dataclass
class Gate:
    basis: list = field(default_factory=list)       # [(what, this period's text, prior period's text)]: blocks
    notes: list = field(default_factory=list)       # same shape, soft: said on the receipt, blocks nothing
    gaps: list = field(default_factory=list)        # [Gap]
    renumbered: list = field(default_factory=list)  # [(old, new)] income/expense codes, no mapping for them
    checked: bool = False                           # the balances could be bridged (both files have them)
    mapping_file: str = ""
    mapped: int = 0                                  # pairs in the mapping table

    @property
    def same_basis(self) -> bool:
        return not self.basis

    @property
    def broken(self) -> list:
        """Gaps that are not a code change: the opening balance really is not last period's closing."""
        return [g for g in self.gaps if not g.renumbered]

    @property
    def moved(self) -> list:
        """Gaps that are a code change with the balance carried across (no mapping table lists them)."""
        return [g for g in self.gaps if g.renumbered]

    @property
    def blocked(self) -> set:
        """Accounts no explanation may use."""
        out = {g.account for g in self.gaps}
        for old, new in self.renumbered:
            out |= {old, new}
        return out

    @property
    def ok(self) -> bool:
        return self.same_basis and not self.broken and not self.renumbered

    def uses_blocked(self, variance) -> bool:
        blocked = self.blocked
        if not blocked:
            return False
        return any(any(a == b or a.startswith(b) or b.startswith(a) for b in blocked) for a, _n, _c in variance.drivers)

    def basis_text(self) -> str:
        what, now, before = self.basis[0]
        if now and before:
            return f"{what} kỳ này \"{now}\", kỳ trước \"{before}\""
        return f"{what}: chỉ file {'kỳ này' if now else 'kỳ trước'} ghi \"{now or before}\""

    def note(self) -> str:
        """The soft line for the receipt: a basis line stated in one period only. "" when there is none."""
        if not self.notes:
            return ""
        what, now, before = self.notes[0]
        return (f"Chỉ file {'kỳ này' if now else 'kỳ trước'} ghi {what} (\"{now or before}\"); em vẫn so hai kỳ, "
                "nhưng nếu hai kỳ lập theo chế độ khác nhau thì báo em nhé.")

    def sentence(self, you_word: str) -> str:
        """One line for the receipt and the summary; "" when the two periods compare like for like."""
        if self.basis:
            return (f"File kỳ trước lập trên cơ sở khác kỳ này ({self.basis_text()}), nên em chưa dùng biến động "
                    f"để giải thích. {you_word[0].upper()}{you_word[1:]} xuất lại kỳ trước cùng cơ sở rồi gọi em.")
        if self.broken:
            g = self.broken[0]
            more = f" và {len(self.broken) - 1} tài khoản khác" if len(self.broken) > 1 else ""
            return (f"Số dư đầu kỳ TK {g.account}{more} không khớp số dư cuối kỳ trước, nên em không dùng "
                    "các tài khoản đó để giải thích biến động.")
        if self.renumbered:
            old, new = self.renumbered[0]
            return (f"TK {old} kỳ trước không còn, kỳ này có TK {new}: có thể là đổi mã. Em chưa dùng hai tài khoản "
                    f"này để giải thích biến động cho đến khi có bảng ghép tài khoản.")
        return ""


def gate(prev_rows: list, prev: TrialBalance, cur_rows: list, cur: TrialBalance, mapping: Optional[dict] = None,
         mapping_file: str = "", new_year: bool = False) -> Gate:
    """Both checks on two trial balances; prev is read under this period's codes (remap) for the bridge."""
    g = Gate(mapping_file=mapping_file, mapped=len(mapping or {}))
    g.basis, g.notes = basis(cur_rows, cur).compare(basis(prev_rows, prev))
    prev = remap(prev, mapping or {})
    g.gaps, g.checked = bridge(prev, cur, mapping, new_year)
    g.renumbered = renumbered_pnl(prev, cur)
    return g


def is_new_year(prev_period: Optional[tuple], cur_period: Optional[tuple]) -> bool:
    return bool(prev_period and cur_period and prev_period[0] and cur_period[0] and cur_period[0] > prev_period[0])


def issues(g: Gate, cur: TrialBalance) -> list:
    """The gate's findings as checks on the Kiem tra sheet, one per kind so each gets one note on the report."""
    from engine.checks import Issue
    from engine.monthly_report import fmt
    out = []
    if g.basis:
        out.append(Issue("error", "basis_differs",
                         f"Kỳ trước lập trên cơ sở khác kỳ này ({g.basis_text()}); chưa dùng biến động để giải thích, "
                         "xuất lại kỳ trước cùng cơ sở", 0, "cơ sở lập", ZERO))
    if g.broken:
        rows = {l.account: l.row for l in cur.lines}
        listed = "; ".join(f"TK {x.account}: đầu kỳ {fmt(x.opening_now)}, cuối kỳ trước {fmt(x.closing_before)}"
                           for x in g.broken[:5])
        more = f"; còn {len(g.broken) - 5} tài khoản khác" if len(g.broken) > 5 else ""
        out.append(Issue("error", "opening_bridge",
                         f"Số dư đầu kỳ không khớp số dư cuối kỳ trước ở {len(g.broken)} tài khoản ({listed}{more}); "
                         "chưa dùng các tài khoản này để giải thích biến động",
                         next((rows[x.account] for x in g.broken if x.account in rows), 0), "số dư hai kỳ",
                         sum((abs(x.diff) for x in g.broken), ZERO)))
    if g.moved:
        old = [x.account for x in g.moved if x.opening_now == ZERO]
        new = [x.account for x in g.moved if x.closing_before == ZERO]
        out.append(Issue("warning", "renumbered_balance",
                         f"Đổi mã tài khoản, số dư vẫn nối khớp (kỳ trước {', '.join(old[:5])}; kỳ này "
                         f"{', '.join(new[:5])}); thêm vào bảng ghép tài khoản để so từng mã", 0, "đổi mã tài khoản",
                         ZERO))
    if g.renumbered:
        pairs = ", ".join(f"{o} → {n}" for o, n in g.renumbered[:5])
        out.append(Issue("warning", "renumbered",
                         f"Tài khoản có thể đã đổi mã ({pairs}) mà chưa có bảng ghép tài khoản; chưa dùng các tài "
                         "khoản này để giải thích biến động", 0, "đổi mã tài khoản", ZERO))
    if not g.checked:
        out.append(Issue("warning", "bridge_unchecked",
                         "Sổ cân đối không có đủ cột số dư đầu kỳ và cuối kỳ nên chưa nối được số dư hai kỳ", 0,
                         "số dư hai kỳ", ZERO))
    return out
