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

# Lines that name the company, not the basis: they never make two periods incomparable.
_IDENTITY = ("cong ty", "tnhh", "co phan", "doanh nghiep", "dia chi", "ma so thue", "mst", "dien thoai", "fax",
             "email", "website", "du lieu gia lap", "company", "address", "tax code")
# Words that only say which period a line is about; with the numbers taken out they carry no basis.
_PERIOD_WORDS = ("tu ngay", "den ngay", "ngay", "thang", "quy", "nam", "ky bao cao", "ky", "from", "to", "date",
                 "period", "month", "quarter", "year")
# Names software gives the same report: one name, so "Bảng cân đối số phát sinh" equals "Bảng cân đối tài khoản".
_TB_TITLES = ("bang can doi so phat sinh", "bang can doi phat sinh tai khoan", "bang can doi phat sinh",
              "bang can doi tai khoan", "can doi tai khoan", "can doi so phat sinh", "trial balance")
_UNIT = re.compile(r"^(don vi tinh|dvt|don vi|unit|currency|tien te)\s*[:\-]?\s*(.*)$")
_YTD = ("luy ke", "tu dau nam", "year to date", "ytd", "cumulative")


_DATES = (r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}(?!\d)",          # 01/10/2026
          r"(?<!\d)\d{1,2}[/.\-](?:19|20)\d{2}(?!\d)",                   # 10/2026
          r"(?:tu ngay|den ngay|ngay|thang|quy|nam|ky|month|quarter|year|q)\s*\d{1,4}(?!\d)",
          r"(?<!\d)(?:19|20)\d{2}(?!\d)")                                 # a year on its own


def _strip_period(text: str) -> str:
    """The line without the period it names; other numbers stay ("Thông tư 200/2014" vs "Thông tư 99/2025")."""
    t = text
    for pattern in _DATES:
        t = re.sub(pattern, " ", t)
    for w in sorted(_PERIOD_WORDS, key=len, reverse=True):
        t = re.sub(rf"(^|[\s(,]){w}(?=$|[\s),:])", " ", t)
    return re.sub(r"[\s,.;\-–()/]+$", "", re.sub(r"^[\s,.;\-–()/]+", "", re.sub(r"\s+", " ", t))).strip()


def _unit_of(value: str) -> str:
    v = value.strip()
    if v.startswith(("dong", "vnd", "vn dong")):
        return "đồng"
    if v.startswith(("nghin", "ngan")):
        return "nghìn đồng"
    if v.startswith("trieu"):
        return "triệu đồng"
    return v


@dataclass
class Basis:
    lines: dict = field(default_factory=dict)       # signature key → (shown text, normalized value)

    def differs(self, other: "Basis") -> list:
        """[(what, this period's text, the other's text)] for every signature line that is not the same."""
        out = []
        for key in sorted(set(self.lines) | set(other.lines), key=lambda k: (k != "title", k)):
            a, b = self.lines.get(key), other.lines.get(key)
            if a and b and a[1] == b[1]:
                continue
            if key == "unit" and not (a and b):
                continue                  # a file that does not say its unit is not on another unit
            out.append((_what(key, a or b), a[0] if a else "", b[0] if b else ""))
        return out


def _what(key: str, line) -> str:
    if key == "title":
        return "tiêu đề"
    if key == "unit":
        return "đơn vị tính"
    if key == "movement":
        return "cột phát sinh"
    shown = line[0] if line else key
    return shown.split(":")[0].strip().lower() if ":" in shown else "dòng đầu file"


def basis(rows: list, tb: TrialBalance) -> Basis:
    """The basis signature of one trial balance: its header block above the column header, and what its
    movement columns hold (the period, or the year to date)."""
    sig = Basis()
    others = 0
    for row in rows[:max(tb.header_row - 1, 0)]:
        for cell in row or []:
            if not isinstance(cell, str) or not cell.strip():
                continue
            shown = re.sub(r"\s+", " ", cell.strip())
            t = fold(shown)
            if any(w in t for w in _IDENTITY):
                continue
            unit = _UNIT.match(t)
            if unit:
                if unit.group(2).strip():
                    sig.lines["unit"] = (shown, _unit_of(unit.group(2)))
                continue
            if ":" in t:
                label, value = t.split(":", 1)
                value = _strip_period(value)
                if value:
                    sig.lines[f"line:{_strip_period(label)}"] = (shown, value)
                continue
            value = _strip_period(t)
            if not value:
                continue                              # "Tháng 10 năm 2026": the period only
            for name in _TB_TITLES:
                value = value.replace(name, "bang can doi tai khoan")
            if "title" not in sig.lines:
                sig.lines["title"] = (shown, value)
            else:
                others += 1
                sig.lines[f"text:{others}"] = (shown, value)
    move = tb.columns.get("dr")
    if move is not None:
        header = []
        for r in range(max(tb.header_row - 1, 0), min(tb.header_row + 1, len(rows))):
            row = rows[r] or []
            # A merged section cell sits left of its Nợ column: look back to the nearest text.
            for i in range(move - 1, -1, -1):
                if i < len(row) and isinstance(row[i], str) and row[i].strip():
                    header.append(fold(row[i]))
                    break
        text = " ".join(header)
        ytd = any(w in text for w in _YTD)
        sig.lines["movement"] = ("Lũy kế từ đầu năm" if ytd else "Phát sinh trong kỳ", "ytd" if ytd else "period")
    return sig


# ── Account mapping and the opening-balance bridge (F5) ──────────────────────

_ACCOUNT_WORDS = ("tk", "ma", "tai khoan", "account", "code", "so hieu")
_OLD = ("cu", "old", "truoc", "tt200", "tt 200", "tt133", "tt 133")
_NEW = ("moi", "new", "nay", "tt99", "tt 99")


def _has(text: str, words) -> bool:
    return any(re.search(rf"(^|[^a-z0-9]){re.escape(w)}($|[^a-z0-9])", text) for w in words)


def _header_side(text: str) -> Optional[str]:
    """"old" / "new" for a mapping table's code column header ("TK cũ", "Mã TK mới", "TT200", "New account")."""
    t = fold(text)
    if not _has(t, _ACCOUNT_WORDS) and not _has(t, ("tt200", "tt 200", "tt133", "tt 133", "tt99", "tt 99")):
        return None
    if _has(t, _OLD):
        return "old"
    if _has(t, _NEW):
        return "new"
    return None


def read_mapping(rows: list) -> dict:
    """{old code: new code} from a table with an old-code column and a new-code column; {} when it is not one."""
    for r, row in enumerate(rows[:15]):
        sides = {}
        for i, c in enumerate(row or []):
            if isinstance(c, str):
                side = _header_side(c)
                if side and side not in sides:
                    sides[side] = i
        if len(sides) < 2:
            continue
        out = {}
        for data in rows[r + 1:]:
            data = data or []
            old, new = (str(data[sides[k]]).strip() if sides[k] < len(data) and data[sides[k]] is not None else ""
                        for k in ("old", "new"))
            old, new = (re.sub(r"\.0$", "", x) for x in (old, new))
            if ACCOUNT.fullmatch(old) and ACCOUNT.fullmatch(new) and old != new:
                out[old] = new
        if out:
            return out
    return {}


def find_mapping(folder: Path, read_rows) -> tuple:
    """(file name, {old: new}) for the first account-mapping table in the folder, or ("", {})."""
    for p in sorted(folder.glob("*"), key=lambda p: p.name.lower()):
        if not p.is_file() or p.name.startswith("~$") or p.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            continue
        name = fold(p.stem).replace("_", " ").replace("-", " ")
        if not any(w in name for w in ("ghep", "doi ma", "mapping", "map", "chuyen doi", "anh xa", "tuong ung")):
            continue
        try:
            if p.suffix.lower() == ".xls":
                from engine import tabular
                found = tabular.sheets(p, keep_blank=True)
                rows = found[0][1] if found else []
            else:
                rows = read_rows(p)
        except Exception:
            continue
        mapping = read_mapping(rows)
        if mapping:
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
    # A code that left and one that arrived under the same parent with the same balance: renumbered, not listed.
    gone = {g.account: g for g in gaps if g.account not in now}
    came = {g.account: g for g in gaps if g.account not in before}
    for g in gone.values():
        twin = next((c for c in came.values() if c.account[:3] == g.account[:3]
                     and abs(c.opening_now - g.closing_before) <= TOLERANCE), None)
        if twin:
            g.renumbered = twin.renumbered = True
    return gaps, True


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
    basis: list = field(default_factory=list)       # [(what, this period's text, prior period's text)]
    gaps: list = field(default_factory=list)        # [Gap]
    renumbered: list = field(default_factory=list)  # [(old, new)] income/expense codes, no mapping for them
    checked: bool = False                           # the balances could be bridged (both files have them)
    mapping_file: str = ""
    mapped: int = 0                                  # pairs in the mapping table

    @property
    def same_basis(self) -> bool:
        return not self.basis

    @property
    def blocked(self) -> set:
        """Accounts no explanation may use."""
        out = {g.account for g in self.gaps}
        for old, new in self.renumbered:
            out |= {old, new}
        return out

    @property
    def ok(self) -> bool:
        return self.same_basis and not self.gaps and not self.renumbered

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

    def sentence(self, you_word: str) -> str:
        """One line for the receipt and the summary; "" when the two periods compare like for like."""
        if self.basis:
            return (f"File kỳ trước lập trên cơ sở khác kỳ này ({self.basis_text()}), nên em chưa dùng biến động "
                    f"để giải thích. {you_word[0].upper()}{you_word[1:]} xuất lại kỳ trước cùng cơ sở rồi gọi em.")
        if self.gaps:
            g = self.gaps[0]
            more = f" và {len(self.gaps) - 1} tài khoản khác" if len(self.gaps) > 1 else ""
            hint = " (có thể là đổi mã, thêm vào bảng ghép tài khoản nếu đúng)" if g.renumbered else ""
            return (f"Số dư đầu kỳ TK {g.account}{more} không khớp số dư cuối kỳ trước{hint}, nên em không dùng "
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
    g.basis = basis(cur_rows, cur).differs(basis(prev_rows, prev))
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
    if g.gaps:
        rows = {l.account: l.row for l in cur.lines}
        listed = "; ".join(f"TK {x.account}: đầu kỳ {fmt(x.opening_now)}, cuối kỳ trước {fmt(x.closing_before)}"
                           + (" (có thể đổi mã)" if x.renumbered else "") for x in g.gaps[:5])
        more = f"; còn {len(g.gaps) - 5} tài khoản khác" if len(g.gaps) > 5 else ""
        out.append(Issue("error", "opening_bridge",
                         f"Số dư đầu kỳ không khớp số dư cuối kỳ trước ở {len(g.gaps)} tài khoản ({listed}{more}); "
                         "chưa dùng các tài khoản này để giải thích biến động",
                         next((rows[x.account] for x in g.gaps if x.account in rows), 0), "số dư hai kỳ",
                         sum((abs(x.diff) for x in g.gaps), ZERO)))
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
