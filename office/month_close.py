"""Month close end to end from one voice command: load the sales query, check it against the files and the ledger,
fix it with the user's approval, then build the monthly report, pointing at each number as it is spoken.

    "làm báo cáo tháng từ thư mục này"   → stage 0: read the files only and show what was read ("Em đọc được những gì")
    "đúng rồi, làm tiếp"                → stage 1: keep a copy of the workbook, load the query, reconcile, propose a fix
    "đồng ý"                            → stage 2: apply the fix, reload, confirm, then build the report
    "thôi" / "có chỗ sai"               → keep the query as it is / stop before anything is changed

Nothing in the workbook changes before the user confirms the receipt, and every change after it
is written to the "Bien ban Clicky" sheet (engine.audit).

The folder is the one the open workbook is saved in (and its sub-folders). All
numbers come from code: the source files are re-read independently of Power Query,
and Clicky says a step is done only after reading the result back and finding it
equal to the ledger. Excel work runs in one worker thread per stage; the stage
returns Beats (sentence + cell to point at) that the app speaks and points at.
"""
import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import audit, intake as intake_mod, m_code, sources, visuals
from engine.monthly_report import spoken
from engine.trial_balance import ZERO
from office.power_query import LoadTimeout, ReadFailed
import scene_log

log = logging.getLogger(__name__)

QUERY_NAME = "DonHang"
PARAM_NAME = "ThuMucDonHang"
CHECK_SHEET = "Doi chieu"
TOLERANCE = Decimal(1)
# Findings that lose revenue rows come first: these are the ones that explain a gap with the ledger.
_LINT_ORDER = ["locale_types", "locale_fixed", "distinct_keys", "remove_errors", "distinct_all", "inner_join", "fixed_path"]

# "tháng" is optional: people say "làm báo cáo từ thư mục này" just as often.
START_TRIGGER = (r"(l[àa]m|l[ậa]p|ch[ạa]y|[đd][ốo]i\s*chi[ếe]u)\b.{0,25}b[áa]o\s*c[áa]o(\s*(th[áa]ng|qu[ảa]n\s*tr[ịi]))?.{0,30}"
                 r"(th[ưu]\s*m[ụu]c|t[ừu]\s*[đd][ầa]u|e2e|tr[ọo]n\s*g[óo]i|to[àa]n\s*b[ộo])"
                 r"|\bch[ạa]y\s*case\b|\bmonth[\s-]*close\b")
# Only short replies count, so a new question that starts with "không" is not taken as an answer.
APPROVE_TRIGGER = (r"^\s*(đồng ý|dong y|ok|oke|okay|được|duoc|sửa đi|sua di|sửa giúp|sua giup|làm đi|lam di|yes)\b"
                   r"(\s+\S+){0,3}\s*[.!]?\s*$")
REJECT_TRIGGER = (r"^\s*(thôi|thoi|không|khong|giữ nguyên|giu nguyen|để tôi xem|de toi xem|để anh xem|để chị xem|no)\b"
                  r"(\s+\S+){0,2}\s*[.!]?\s*$")
FIX_CHOICES = ("Sửa giúp tôi", "Để tôi xem")
# The receipt's buttons; the rehearsal robot presses the first one by its exact words.
INTAKE_CHOICES = ("Đúng rồi, làm tiếp", "Có chỗ sai")
CONFIRM_TRIGGER = r"^\s*(đúng rồi|dung roi|đúng|dung|làm tiếp|lam tiep|tiếp tục|tiep tuc)\b(\s+\S+){0,3}\s*[.!]?\s*$"
WRONG_TRIGGER = r"^\s*(có chỗ sai|co cho sai|sai rồi|sai roi|chưa đúng|chua dung)\b(\s+\S+){0,4}\s*[.!]?\s*$"
PICK_TRIGGER = r"^\s*(dùng|dung|chọn|chon|lấy|lay)\s+\S+"
FINAL_TRIGGER = r"\b(chốt|chot|chuyển sang|chuyen sang|đánh dấu|danh dau)\s+(là\s+|la\s+)?(chính thức|chinh thuc)\b"

_PENDING: Optional[dict] = None


@dataclass
class Beat:
    """One idea on screen: a short sentence (shown in the chat and spoken) and, when it has a range,
    that range lit up with one card beside it. Details go in the sheet, not in the sentence."""
    text: str                       # one short sentence, no file or step names
    ref: Optional[str] = None       # cell or range to light up while speaking, e.g. 'Doi chieu'!C7
    label: str = ""                 # card title
    mark: str = ""                  # "ok" green, "bad" red, "warn" amber, "" blue
    value: str = ""                 # the one figure that matters, big on the card
    note: str = ""                  # one line of context under the figure
    choices: tuple = ()             # buttons offered after the sentence, e.g. ("Sửa giúp tôi", "Để tôi xem")
    was: str = ""                   # the figure it replaces, shown struck through above the new one
    roll: tuple = ()                # (from, to) in đồng: the big figure counts from one to the other
    intake: Optional[dict] = None   # the receipt card ({"card": ..., "log": ...}) shown with this beat's buttons
    graphic: Optional[dict] = None  # leader tags, sticky notes or a profit bridge (engine.visuals), drawn beside the range
    book: str = ""                  # the workbook the ref is in, brought to the front first (read-only when Clicky opens it)


def stage() -> Optional[str]:
    return _PENDING.get("stage") if _PENDING else None


def intake_pending() -> bool:
    return stage() == "intake"


def pending() -> bool:
    return _PENDING is not None


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def _check_ref(cells: str) -> str:
    return f"{_q(CHECK_SHEET)}!{cells}"


def _col(i: int) -> str:
    from office.cellref import col_letters
    return col_letters(i + 1)


def _dec(v) -> Decimal:
    if v is None or v == "":
        return ZERO
    if isinstance(v, (int, Decimal)):
        return Decimal(v)
    try:
        return Decimal(str(round(float(v), 2)))
    except (TypeError, ValueError):
        return ZERO


def machine_date_order() -> Optional[str]:
    """'vi-VN' or 'en-US': how Power Query on this PC reads dates when a query gives no culture.

    Power Query follows the culture of the Windows region (en-US, vi-VN...), not a
    short-date pattern the user customised: a PC set to en-US with dd/MM/yyyy still
    reads 04/10/2026 as April 10 (seen on a tester's PC). So the order comes from
    the culture's own default pattern, ignoring user overrides.
    """
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        name = ctypes.create_unicode_buffer(85)
        if not k32.GetUserDefaultLocaleName(name, 85):
            return None
        buf = ctypes.create_unicode_buffer(80)
        # LOCALE_SSHORTDATE | LOCALE_NOUSEROVERRIDE
        if k32.GetLocaleInfoEx(name.value, 0x1F | 0x80000000, buf, 80):
            return order_from_pattern(buf.value) or order_from_culture(name.value)
        return order_from_culture(name.value)
    except Exception:
        return None


def order_from_pattern(pattern: str) -> Optional[str]:
    p = (pattern or "").strip().lower()
    for ch in p:
        if ch == "d":
            return sources.DAY_FIRST
        if ch == "m":
            return sources.MONTH_FIRST
        if ch == "y":
            return None               # year first (ISO, ja-JP): ambiguous for dd/mm files
    return None


def order_from_culture(name: str) -> Optional[str]:
    n = (name or "").lower()
    if n in {"en-us", "en-ph", "en-ca", "es-us", "en-029"} or n.startswith("en-us"):
        return sources.MONTH_FIRST
    if n.startswith(("vi", "fr", "de", "en-gb", "en-au", "es", "it", "pt", "nl", "ru", "id", "th", "en-sg", "en-in")):
        return sources.DAY_FIRST
    return None


def _gap_vi(gap: Decimal) -> str:
    return ("thiếu " if gap < 0 else "thừa ") + spoken(abs(gap))


def _order_vi(order: Optional[str]) -> str:
    return {"vi-VN": "ngày/tháng", "en-US": "tháng/ngày"}.get(order, "chưa rõ kiểu")


# ── Reading the query's output ────────────────────────────────────────────────

@dataclass
class Loaded:
    sheet: str
    header: list
    rows: list
    columns: dict

    @property
    def amount(self) -> Decimal:
        i = self.columns.get("amount")
        return sum((_dec(r[i]) for r in self.rows if i is not None and i < len(r)), ZERO)

    @property
    def cost(self) -> Decimal:
        i = self.columns.get("cost")
        return sum((_dec(r[i]) for r in self.rows if i is not None and i < len(r)), ZERO)

    def docs(self) -> list:
        i = self.columns.get("doc")
        return [str(r[i]).strip() for r in self.rows if i is not None and i < len(r) and r[i] not in (None, "")]


def diagnose(truth: sources.SourceTruth, loaded: Loaded, machine_order: Optional[str]) -> dict:
    """Why rows are missing from the query: dates read in the wrong format, or de-duplicated on the wrong columns."""
    present = Counter(loaded.docs())
    orders = {f.name: f.date_order for f in truth.files}
    kept = {(info.date, info.customer) for doc, info in truth.doc_numbers.items() if doc in present}
    out = {"date": [], "distinct": [], "other": [], "amount": [], "shared": [], "outside": [],
           "lines": [], "excel_dups": [d for d, n in present.items() if n > truth.multi.get(d, 1)]}
    if "doc" not in loaded.columns:
        return out
    loaded_by_doc = {}
    ai = loaded.columns.get("amount")
    for r in loaded.rows:
        di = loaded.columns["doc"]
        if di < len(r) and r[di] not in (None, ""):
            loaded_by_doc[str(r[di]).strip()] = loaded_by_doc.get(str(r[di]).strip(), ZERO) + (
                _dec(r[ai]) if ai is not None and ai < len(r) else ZERO)
    # Real orders that share a number with another branch's order, removed by a de-duplication on the number alone.
    for bucket, docs in (("shared", truth.collisions), ("lines", truth.invoice_lines)):
        for doc, n in docs.items():
            if 0 < present.get(doc, 0) < n:
                out[bucket].append((doc, n - present[doc], truth.doc_amounts[doc] - loaded_by_doc.get(doc, ZERO)))
    # Rows the query kept although they are dated outside the ledger's month.
    for doc, file, d, amount in truth.outside:
        if doc and doc in present and doc not in truth.doc_numbers:
            out["outside"].append((doc, d, loaded_by_doc.get(doc, amount)))
    # Rows that arrived but with a different amount: a number read in the wrong format, then the error
    # replaced by 0 or the digits taken as a decimal. Row counts match; the money does not.
    di, ai = loaded.columns["doc"], loaded.columns.get("amount")
    if ai is not None:
        got = {}
        for r in loaded.rows:
            if di < len(r) and ai < len(r) and r[di] not in (None, ""):
                got.setdefault(str(r[di]).strip(), _dec(r[ai]))
        for doc, info in truth.doc_numbers.items():
            if doc in got and doc not in truth.multi and abs(got[doc] - info.amount) > TOLERANCE:
                out["amount"].append((doc, info, got[doc]))
    for doc, info in truth.doc_numbers.items():
        if doc in present:
            continue
        file_order = orders.get(info.file)
        if machine_order and file_order and file_order != machine_order:
            if sources.parse_date(info.raw_date, machine_order) != info.date:
                out["date"].append((doc, info))
                continue
        if info.customer and (info.date, info.customer) in kept:
            out["distinct"].append((doc, info))
        else:
            out["other"].append((doc, info))
    return out


def _dedup_key(files: list, truth: sources.SourceTruth) -> Optional[list]:
    """None: de-duplicate on the document number. When different orders share a number (each branch
    numbers its own), [branch column, doc column] if every file has both, else [] (identical rows only)."""
    if not truth.multi:
        return None
    if truth.invoice_lines:
        return []          # an invoice's lines share everything but the item: only identical rows are duplicates
    names = []
    for role in ("branch", "doc"):
        cols = {f.header[f.columns[role]] if role in f.columns else None for f in files}
        if len(cols) != 1 or None in cols:
            return []
        names.append(cols.pop())
    return names


def _amount(items) -> Decimal:
    return sum((info.amount for _, info in items), ZERO)


# ── Stage 1 ───────────────────────────────────────────────────────────────────

def _query_to_check(pq, folder: Path) -> tuple:
    """(name, formula, where it came from): the workbook's own folder query, else an M file in the folder."""
    for name, formula in pq.list():
        if name != PARAM_NAME and re.search(r"Folder\.Files|Csv\.Document", formula):
            return name, formula, "workbook"
    for p in sorted(folder.glob("*.txt")) + sorted(folder.glob("*.m")) + sorted(folder.glob("*.pq")):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if text.lstrip().lower().startswith("let") and re.search(r"Folder\.Files|Csv\.Document", text):
            return QUERY_NAME, text, p.name
    return None, None, None


def _point_at_this_folder(formula: str, files: list) -> tuple:
    """A query copied from another machine: swap a Folder.Files path that does not exist here for the order files' folder."""
    m = re.search(r'Folder\.Files\(\s*"((?:[^"]|"")*)"\s*\)', formula)
    if not m:
        return formula, None
    path = m.group(1).replace('""', '"')
    if Path(path).is_dir():
        return formula, None
    parents = {f.path.parent for f in files}
    here = parents.pop() if len(parents) == 1 else files[0].path.parent.parent
    return formula[:m.start()] + f"Folder.Files({m_code._m_text(str(here))})" + formula[m.end():], path


def _typed_columns(files: list) -> list:
    header = files[0].header
    cols = files[0].columns
    typed = []
    for role, mtype in (("date", "type date"), ("amount", "type number"), ("cost", "type number")):
        if role in cols:
            typed.append((header[cols[role]], mtype))
    for i, h in enumerate(header):
        if sources._key(h) in {"sl", "soluong", "quantity", "qty"}:
            typed.append((h, "Int64.Type"))
        if sources._key(h) in {"dongia", "unitprice", "price"}:
            typed.append((h, "type number"))
    return typed


ZERO_SHOWN = '#,##0;(#,##0);0'   # the gap must read 0 when it is closed, not an accounting dash
DETAIL_TOP = 9          # "Chi tiết kiểm tra" heading; detail rows start on the next row
_CHECK_COLUMNS = {"A": 30, "B": 11, "C": 18, "D": 18, "E": 64}
_INK, _MUTED, _LINE = "#0F172A", "#64748B", "#E2E8F0"
_GAP_STYLE = {"bad": ("#FDECEE", "#B42318"), "ok": ("#E7F6EE", "#157347"), None: ("#F1F5F9", "#64748B")}


def _detail_ref(i: int) -> str:
    row = DETAIL_TOP + 1 + i
    return _check_ref(f"A{row}:E{row}")


def _try(bridge, method: str, *args, **kwargs):
    """Styling helps reading but must never stop the reconciliation: a missing or failing call is skipped."""
    fn = getattr(bridge, method, None)
    if fn is None:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.debug("Excel %s failed: %s", method, exc)
        return None


def _write_check_sheet(bridge, loaded: Optional[Loaded], truth, ledger: dict,
                       details: Optional[list] = None, gap_state: Optional[str] = None) -> Optional[str]:
    """The reconciliation as a small, formatted table (rows 3-7) and the reasons below it (row 9 on).

    details: rows of [what, documents, revenue, '', explanation]; the chat only carries one sentence per
    idea, so this is where the full story lives, next to the numbers it explains.
    """
    names = bridge.sheet_names()
    sheet = CHECK_SHEET if CHECK_SHEET in names else bridge.add_sheet(CHECK_SHEET)
    if not sheet:
        return None
    if loaded and "amount" in loaded.columns:
        a, c = _col(loaded.columns["amount"]), _col(loaded.columns.get("cost", loaded.columns["amount"]))
        q = _q(loaded.sheet)
        excel_row = ["Power Query trong Excel", f"=COUNT({q}!{a}:{a})", f"=SUM({q}!{a}:{a})",
                     f"=SUM({q}!{c}:{c})" if "cost" in loaded.columns else "", "Công thức sống, đọc từ bảng query"]
    else:
        excel_row = ["Power Query trong Excel", "", "", "", "Chưa đọc được bảng query"]
    dups = ", ".join(d for d, _, _ in truth.duplicates[:5])
    grid = [
        ["Đối chiếu doanh thu tháng: Power Query, file gốc và sổ cái", "", "", "", ""],
        ["Số liệu file gốc và sổ cái do Clicky đọc bằng code; dòng Excel là công thức", "", "", "", ""],
        ["Nguồn", "Số dòng", "Doanh thu (đ)", "Giá vốn (đ)", "Ghi chú"],
        excel_row,
        ["File gốc (đọc bằng code)", truth.unique_rows, truth.amount, truth.cost,
         f"Đã bỏ {len(truth.duplicates)} chứng từ lặp: {dups}" if truth.duplicates else "Không có chứng từ lặp"],
        [f"Sổ cái {ledger.get('name', '')}", "", ledger.get("revenue", ""), ledger.get("cost", ""),
         (f"Có TK {ledger.get('rev_acct', '511')} − Nợ TK 521 (file gốc có phiếu trả hàng) / Nợ TK 632" if ledger.get("net")
          else f"Phát sinh Có TK {ledger.get('rev_acct', '511')} / Nợ TK 632") if ledger else "Chưa thấy file cân đối tài khoản"],
        ["Chênh lệch Excel − sổ cái", "", "=C4-C6" if ledger and loaded else "",
         "=D4-D6" if ledger and loaded and "cost" in loaded.columns else "",
         "Phải bằng 0" if loaded else "Chưa đối chiếu được: chưa đọc được bảng query"],
    ]
    if details:
        grid += [["", "", "", "", ""], ["Chi tiết kiểm tra", "Chứng từ", "Doanh thu (đ)", "", "Giải thích"]]
        grid += [list(r) + [""] * (5 - len(r)) for r in details]
    _try(bridge, "clear", f"{_q(sheet)}!A1:F60")
    bridge.write(f"{_q(sheet)}!A1", grid)
    bridge.format_numbers(f"{_q(sheet)}!B4:D7", ZERO_SHOWN)
    _style_check_sheet(bridge, sheet, len(details or []), gap_state if loaded else None)
    return sheet


def _style_check_sheet(bridge, sheet: str, n_details: int, gap_state: Optional[str]) -> None:
    s = _q(sheet)
    _try(bridge, "layout_sheet", sheet, _CHECK_COLUMNS, gridlines=False)
    _try(bridge, "style", f"{s}!A1", bold=True, size=15, color=_INK)
    _try(bridge, "style", f"{s}!A2", italic=True, size=9, color=_MUTED)
    _try(bridge, "style", f"{s}!A3:E3", bold=True, color="#FFFFFF", fill="#1E3A5F", height=24)
    _try(bridge, "style", f"{s}!B3:D3", halign="right")
    _try(bridge, "style", f"{s}!A4:E6", border_bottom=_LINE, height=21)
    _try(bridge, "style", f"{s}!E4:E7", color="#475569")
    fill, ink = _GAP_STYLE.get(gap_state, _GAP_STYLE[None])
    _try(bridge, "style", f"{s}!A7:E7", bold=True, fill=fill, height=26)
    _try(bridge, "style", f"{s}!A7:D7", color=ink)
    if n_details:
        top, last = DETAIL_TOP, DETAIL_TOP + n_details
        _try(bridge, "style", f"{s}!A{top}:E{top}", bold=True, color=_INK, border_bottom="#94A3B8", height=24)
        _try(bridge, "style", f"{s}!B{top}:D{top}", halign="right")
        _try(bridge, "style", f"{s}!A{top + 1}:E{last}", border_bottom=_LINE, wrap=True)
        _try(bridge, "style", f"{s}!A{top + 1}:A{last}", bold=True, color=_INK)
        _try(bridge, "style", f"{s}!E{top + 1}:E{last}", color="#334155")
        bridge.format_numbers(f"{s}!B{top + 1}:C{last}")


# The folder the user dropped on the drop zone; None reads the folder the open workbook is saved in.
_SOURCE: Optional[str] = None


def _data_folder(bridge) -> Optional[Path]:
    """Where the trial balances and sales exports are read from. The workbook must be saved either way:
    the original is kept next to it before the first change."""
    own = bridge.workbook_folder()
    if not own:
        return None
    return Path(_SOURCE or own)


def use_folder(folder: Optional[Path]) -> None:
    global _SOURCE
    _SOURCE = str(folder) if folder else None


# Which file the user chose when two trial balances cover the same period: (year, month) → file stem.
_CHOSEN: dict = {}


def _tb_groups(folder: Path) -> tuple:
    """(one trial balance per period, oldest first; the duplicates set aside; the first unresolved conflict).

    A conflict is two files for one of the two periods the report needs, with no choice from the user yet;
    Clicky asks instead of picking. Older periods are not used, so their copies need no answer.
    """
    from engine.cli import read_rows
    found = sources.find_trial_balances(folder, read_rows)
    groups = {}
    for t in found:
        groups.setdefault(sources.period_of(t[1]) or (0, 0), []).append(t)
    periods = sorted(groups)
    picked, aside, conflict = [], [], None
    for period in periods:
        group = groups[period]
        choice = next((t for t in group if t[0] == _CHOSEN.get(period)), None)
        if len(group) > 1 and choice is None and period in periods[-2:] and conflict is None:
            conflict = (period, group)
        keep = choice or max(group, key=lambda t: _mtime(folder, t[0]))
        picked.append(keep)
        aside += [(t[0], period) for t in group if t is not keep]
    return picked, aside, conflict


def _mtime(folder: Path, stem: str) -> float:
    p = next(iter(sorted(folder.glob(f"{stem}.xls*"))), None)
    try:
        return p.stat().st_mtime if p else 0.0
    except OSError:
        return 0.0


def _ledger(folder: Path) -> tuple:
    tbs = _tb_groups(folder)[0]
    if not tbs:
        return tbs, {}
    name, _, tb = tbs[-1]
    acct = sources.revenue_account(tb)
    revenue = sources.ledger_movement(tb, acct, "cr")
    return tbs, {"name": name, "rev_acct": acct, "revenue": revenue, "gross": revenue,
                 "returns": sources.ledger_movement(tb, "521", "dr"),
                 "cost": sources.ledger_movement(tb, "632", "dr")}


def _rescope(tbs, ledger: dict, truth) -> dict:
    """Compare against the 511 scope the order files cover (5111 alone or every 511 sub-account),
    so a sales file holding goods and finished products is not reported short by 5112."""
    if not ledger:
        return ledger
    tb = tbs[-1][2]
    acct = sources.revenue_scope(tb, truth.gross)
    if acct == ledger["rev_acct"]:
        return ledger
    revenue = sources.ledger_movement(tb, acct, "cr")
    return dict(ledger, rev_acct=acct, revenue=revenue, gross=revenue)


def _net_of_returns(ledger: dict, truth) -> dict:
    """When the order files carry return slips (negative rows) the files and the query hold net sales,
    so the ledger side becomes Có 511 − Nợ 521; sales and returns are still compared one by one."""
    if not ledger or not truth.returns:
        return ledger
    return {**ledger, "revenue": ledger["gross"] - ledger["returns"], "net": True}


def _signed(amount: Decimal) -> str:
    """Card figure with a real minus sign: −5,42 tỷ."""
    return ("−" if amount < 0 else "") + spoken(abs(amount))


def _date_example(items, machine_order: Optional[str]) -> tuple:
    """('04/10 → 10/04', 'Ví dụ HN-HD1: ngày 4/10 bị đọc thành ngày 10/4.') from the first flipped document."""
    unreadable = None
    for doc, info in items:
        wrong = sources.parse_date(info.raw_date, machine_order) if machine_order else None
        if wrong and info.date and wrong != info.date:
            return (f"{info.date:%d/%m} → {wrong:%d/%m}",
                    f"Ví dụ {doc}: ngày {info.date.day}/{info.date.month} bị đọc thành ngày {wrong.day}/{wrong.month}.")
        if unreadable is None and info.date:
            unreadable = (doc, info)
    if unreadable:
        doc, info = unreadable
        return f"{info.date:%d/%m} → lỗi", f"Ví dụ {doc}: ngày {info.date.day}/{info.date.month} không đọc được nên bị xoá."
    return "", ""


def explained(why: dict) -> Decimal:
    """How much the named causes move the query's total away from the source files (missing is negative)."""
    missing = sum((_amount(why.get(k, [])) for k in ("date", "distinct", "other")), ZERO)
    missing += sum((a for k in ("shared", "lines") for *_, a in why.get(k, [])), ZERO)
    missing += sum((info.amount - got for _, info, got in why.get("amount", [])), ZERO)
    return sum((a for *_, a in why.get("outside", [])), ZERO) - missing


def _cause_beats(why: dict, machine_order: Optional[str], first_row: int, reader: str = "Máy này đọc",
                 gap: Optional[Decimal] = None) -> tuple:
    """(beats, detail rows): one beat per cause, each lighting up its own row in the detail table."""
    beats, rows = [], []

    def add(beat, row):
        beat.ref = _detail_ref(first_row + len(rows))
        beats.append(beat)
        rows.append(row)

    if why["date"]:
        n, amt = len(why["date"]), _amount(why["date"])
        value, example = _date_example(why["date"], machine_order)
        add(Beat(f"Nguyên nhân chính: máy đọc nhầm ngày tháng, làm mất {spoken(amt)}.",
                 label=f"Đọc nhầm ngày · {n} chứng từ · {spoken(amt)}", mark="bad", value=value or spoken(amt),
                 note=example or f"{reader} ngày kiểu {_order_vi(machine_order)}, khác kiểu ghi trong file."),
            ["Đọc nhầm ngày tháng", n, amt, "", f"{reader} ngày kiểu {_order_vi(machine_order)}; file ghi kiểu khác "
             f"bị đảo ngày hoặc báo lỗi rồi bị xoá. {example}".strip()])
    if why["distinct"]:
        n, amt = len(why["distinct"]), _amount(why["distinct"])
        add(Beat(f"Thêm {n} chứng từ bị xoá nhầm ở bước xoá trùng.",
                 label="Xoá trùng nhầm", mark="bad", value=spoken(amt),
                 note=f"{n} chứng từ: cùng khách mua hai lần trong một ngày bị coi là trùng."),
            ["Xoá trùng nhầm", n, amt, "", "Bước xoá trùng dùng Ngày + Mã KH; hai chứng từ khác nhau cùng khách, cùng ngày "
             "bị xoá mất một. Chỉ nên xoá trùng theo số chứng từ."])
    if why.get("amount"):
        n = len(why["amount"])
        lost = sum((info.amount - got for _, info, got in why["amount"]), ZERO)
        doc, info, got = why["amount"][0]
        add(Beat(f"{n} chứng từ vẫn có trong Excel nhưng số tiền bị đọc sai, làm lệch {spoken(abs(lost))}.",
                 label=f"Đọc sai số tiền · {n} chứng từ", mark="bad", value=_signed(-lost),
                 note=f"Ví dụ {doc}: file ghi {spoken(info.amount)}, Excel đọc thành {spoken(got)}."),
            ["Đọc sai số tiền", n, -lost, "", f"Số tiền ghi kiểu khác cài đặt của query (ví dụ 1.250.000 so với 1,250,000), "
             f"bị đọc sai hoặc lỗi rồi thay bằng 0. Ví dụ {doc}: file {info.amount:,.0f}, Excel {got:,.0f}."])
    if why.get("shared"):
        n, amt = sum(k for _, k, _ in why["shared"]), sum((a for *_, a in why["shared"]), ZERO)
        example = why["shared"][0][0]
        add(Beat(f"{n} đơn thật bị xoá vì trùng số chứng từ với nơi khác, làm mất {spoken(amt)}.",
                 label=f"Trùng số giữa các nơi · {n} đơn", mark="bad", value=spoken(amt),
                 note=f"Ví dụ số {example}: hai nơi cùng đánh số này cho hai đơn khác nhau."),
            ["Xoá nhầm đơn trùng số giữa các nơi", n, amt, "", "Bước xoá trùng chỉ theo số chứng từ, nhưng mỗi chi nhánh "
             f"tự đánh số nên hai đơn khác nhau có cùng số. Ví dụ {example}. Cần xoá trùng theo Chi nhánh + Số CT."])
    if why.get("lines"):
        n, amt = sum(k for _, k, _ in why["lines"]), sum((a for *_, a in why["lines"]), ZERO)
        example = why["lines"][0][0]
        add(Beat(f"{n} dòng mặt hàng của cùng một hoá đơn bị xoá ở bước xoá trùng, làm mất {spoken(amt)}.",
                 label=f"Mất dòng hoá đơn · {n} dòng", mark="bad", value=spoken(amt),
                 note=f"Ví dụ hoá đơn {example}: nhiều mặt hàng nên có nhiều dòng cùng số, query chỉ giữ một dòng."),
            ["Xoá nhầm dòng của cùng hoá đơn", n, amt, "", "Bước xoá trùng theo số chứng từ giữ lại một dòng mỗi hoá đơn, "
             f"nên hoá đơn nhiều mặt hàng mất các dòng còn lại. Ví dụ {example}. Chỉ nên xoá dòng giống hệt nhau."])
    if why.get("outside"):
        n, amt = len(why["outside"]), sum((a for *_, a in why["outside"]), ZERO)
        months = sorted({f"{d.month:02d}/{d.year}" for _, d, _ in why["outside"]})
        add(Beat(f"Query còn giữ {n} dòng của tháng khác, cộng thừa {spoken(amt)}.",
                 label=f"Dòng ngoài kỳ · {n} dòng", mark="bad", value=f"+{spoken(amt)}",
                 note=f"Các dòng tháng {', '.join(months)} chưa được lọc ra."),
            ["Dòng ngoài kỳ còn trong query", n, amt, "", f"File xuất có dòng tháng {', '.join(months)}; query không lọc theo "
             "kỳ của sổ cái nên cộng cả vào."])
    if why["other"]:
        n, amt = len(why["other"]), _amount(why["other"])
        add(Beat(f"Còn {n} chứng từ bị mất mà em chưa rõ lý do.", label="Chưa rõ nguyên nhân", mark="warn",
                 value=spoken(amt), note="Anh xem danh sách trong bảng chi tiết."),
            ["Chưa rõ nguyên nhân", n, amt, "", "Có trong file gốc nhưng không có trong bảng query."])
    # The causes must add up to the gap; whatever they do not cover is said, never left silent.
    if gap is not None:
        rest = gap - explained(why)
        if abs(rest) > TOLERANCE:
            add(Beat(f"Còn {spoken(abs(rest))} em chưa tìm ra nguyên nhân.", label="Chưa rõ nguyên nhân", mark="warn",
                     value=_signed(rest), note="Phần này chưa được giải thích. Anh xem bảng query trước khi dùng số."),
                ["Phần lệch chưa rõ nguyên nhân", "", rest, "", "Tổng các nguyên nhân ở trên chưa bằng chỗ lệch giữa query và file gốc."])
    if why["excel_dups"]:
        n = len(why["excel_dups"])
        add(Beat(f"{n} chứng từ bị đếm hai lần trong Excel.", label="Đếm trùng", mark="bad", value=f"{n} chứng từ",
                 note="Một số chứng từ xuất hiện hai lần trong bảng query."),
            ["Đếm trùng trong Excel", n, "", "", ", ".join(why["excel_dups"][:5])])
    return beats, rows


def _lint_rows(findings) -> list:
    return [[f"Bước query: {f.step}", "", "", "", f.message] for f in findings]


def _fix_offer() -> Beat:
    return Beat("Em sửa query giúp anh nhé? Bản cũ vẫn được giữ lại.", choices=FIX_CHOICES)


_AUDIT: Optional[audit.AuditLog] = None


def _workbook_name(bridge) -> Optional[str]:
    path = _try(bridge, "workbook_path")
    return Path(path).name if path else None


def start(bridge, pq, machine_order: Optional[str]) -> list:
    """Stage 0: read every file in the folder and say what was read. Nothing in the workbook changes yet."""
    global _PENDING, _AUDIT
    _PENDING, _AUDIT = None, None
    _step("read_folder")
    _EXPECT.clear()
    if not bridge.available():
        return [Beat("Anh mở file Excel đã lưu trong thư mục dữ liệu tháng rồi gọi lại em.")]
    folder = _data_folder(bridge)
    if folder is None:
        return [Beat("File Excel này chưa được lưu. Anh lưu nó vào thư mục có các file đơn hàng và file cân đối tài khoản, rồi gọi lại em.")]
    files = sources.find_order_files(folder)
    if not files:
        return [Beat(f"Em không thấy file đơn hàng CSV nào trong thư mục {folder.name}.")]
    _tbs, aside, conflict = _tb_groups(folder)
    if conflict:
        return _ask_which(folder, conflict, machine_order)
    tbs, ledger = _ledger(folder)
    period = sources.period_of(tbs[-1][1]) if tbs else None
    truth = sources.truth(files, period)
    ledger = _net_of_returns(_rescope(tbs, ledger, truth), truth)
    receipt = intake_mod.build(folder, files, tbs, truth, ledger, _workbook_name(bridge),
                               set_aside=[(name, f"bản sổ cân đối T{p[1]:02d} khác; anh đã chọn dùng {_CHOSEN[p]}"
                                           if p in _CHOSEN else "bản cũ hơn của cùng kỳ")
                                          for name, p in aside])
    scene_log.record("roles", files=[{"name": n, "role": r} for n, r in receipt.roles.items()])
    n_tb = len(tbs[-2:])
    text = (f"Em đọc được {n_tb} sổ cân đối và {truth.unique_rows} đơn bán hàng. Anh xem giúp em có đúng không, "
            "em chưa sửa gì trong file.")
    _PENDING = {"stage": "intake", "order": machine_order, "intake": receipt, "folder": str(folder),
                "period": f"{period[1]:02d}/{period[0]}" if period else "", "picked": list(_PICKS)}
    return [Beat(text, choices=INTAKE_CHOICES,
                 intake={"card": intake_mod.card(receipt), "log": intake_mod.log_fields(receipt, text)})]


# The user's answers to "which file?", kept for the Biên bản: (what, chosen, set aside, when).
_PICKS: list = []


def _ask_which(folder: Path, conflict: tuple, machine_order) -> list:
    """Two files claim the same role for the same month: say when each was last changed and ask. Never pick."""
    global _PENDING
    from engine import roles
    (year, month), group = conflict
    group = sorted(group, key=lambda t: _mtime(folder, t[0]), reverse=True)[:2]
    paths = {name: next(iter(sorted(folder.glob(f"{name}.xls*"))), folder / name) for name, _, _ in group}
    stamps = {name: roles.modified(paths[name]) for name in paths}
    said = ", ".join(f"{paths[name].name} sửa ngày {stamps[name]}" if stamps[name] else paths[name].name
                     for name, _, _ in group)
    _PENDING = {"stage": "pick_source", "period": (year, month), "options": [name for name, _, _ in group],
                "stamps": stamps, "order": machine_order}
    return [Beat(f"Có 2 sổ cân đối T{month:02d}: {said}. Em dùng bản nào?",
                 choices=tuple(f"Dùng {name}" for name, _, _ in group))]


def pick(bridge, pq, answer: str) -> list:
    """The answer to _ask_which: remember the file, write it down for the Biên bản, then read the folder again."""
    global _PENDING
    job = _PENDING if stage() == "pick_source" else None
    if not job:
        return []
    text = fold_text(answer)
    # Longest name first: "Dùng TB_T10_cu" also contains "TB_T10".
    hit = next((n for n in sorted(job["options"], key=len, reverse=True) if fold_text(n) in text), None)
    if hit is None:
        return [Beat("Anh bấm một trong hai nút để chọn sổ cân đối nhé.", choices=tuple(f"Dùng {n}" for n in job["options"]))]
    _PENDING = None
    _CHOSEN[job["period"]] = hit
    others = [n for n in job["options"] if n != hit]
    _PICKS.append((f"Sổ cân đối T{job['period'][1]:02d}", f"{hit} (sửa {job['stamps'].get(hit, '')})",
                   ", ".join(others), audit.who()))
    return start(bridge, pq, job["order"])


def fold_text(text: str) -> str:
    from engine.trial_balance import fold
    return fold(text).replace(" ", "")


def pick_pending() -> bool:
    return stage() == "pick_source"


def _keep_original(bridge) -> str:
    """Save a copy of the workbook as it is now, next to it, before Clicky's first change: <name>_ban-goc.xlsx.

    An existing copy is the real original (from an earlier run) and is never overwritten.
    """
    path = _try(bridge, "workbook_path")
    if not path:
        return ""
    p = Path(path)
    copy = p.with_name(f"{p.stem}{intake_mod.ORIGINAL_TAG}{p.suffix}")
    if not copy.exists():
        _try(bridge, "save_copy", str(copy))
    return copy.name if copy.exists() else ""


def confirm(bridge, pq) -> list:
    """Stage 1, after "Đúng rồi, làm tiếp": keep the original, then reconcile and change what needs changing."""
    global _PENDING, _AUDIT
    job, _PENDING = _PENDING, None
    if not job or job.get("stage") != "intake":
        return []
    receipt = job["intake"]
    _step("confirmed")
    _AUDIT = audit.AuditLog(period=job.get("period", ""), original=_keep_original(bridge),
                            dropped=intake_mod.dropped_rows(receipt), coverage=intake_mod.coverage_rows(receipt))
    for what, chosen, others, agreed in job.get("picked", []):
        _AUDIT.add("—", f"Chọn nguồn: {what}", others, chosen,
                   "Hai file cùng vai trò Số chính thức cho cùng kỳ; người dùng chọn bản dùng", agreed, "Thư mục dữ liệu")
    before = set(bridge.sheet_names())
    beats = _reconcile(bridge, pq, job["order"])
    _note_new_sheets(bridge, before)
    _write_audit(bridge)
    return beats


def wrong() -> list:
    """ "Có chỗ sai": stop before anything changed and let the user say what is wrong in the chat."""
    global _PENDING
    _PENDING = None
    return [Beat("Anh nói giúp em chỗ nào chưa đúng nhé. Em chưa sửa gì trong file.")]


def _note_new_sheets(bridge, before: set) -> None:
    added = [n for n in bridge.sheet_names() if n not in before and n != audit.SHEET]
    if _AUDIT is not None and added:
        _AUDIT.add("—", "Thêm sheet", "—", ", ".join(added), "Sheet làm việc và báo cáo của Clicky; sheet gốc giữ nguyên",
                   audit.AUTO, "—")


_AUDIT_COLUMNS = {"A": 5, "B": 8, "C": 16, "D": 30, "E": 16, "F": 16, "G": 48, "H": 18, "I": 22}
_DRAFT_FILL, _DRAFT_INK = "#FEF3C7", "#B45309"
_FINAL_FILL, _FINAL_INK = "#DCFCE7", "#157347"


def _write_audit(bridge) -> Optional[str]:
    """Rewrite the whole Biên bản sheet from the log (it is small), then put the status on the report too."""
    if _AUDIT is None:
        return None
    names = bridge.sheet_names()
    sheet = audit.SHEET if audit.SHEET in names else bridge.add_sheet(audit.SHEET)
    if not sheet:
        return None
    rows, at = audit.grid(_AUDIT)
    s = _q(sheet)
    _try(bridge, "clear", f"{s}!A1:I200")
    bridge.write(f"{s}!A1", rows)
    first, last = at["changes"]
    bridge.format_numbers(f"{s}!E{first}:F{last}")
    bridge.format_numbers(f"{s}!C{at['dropped'] + 1}:C{at['coverage'] - 2}")
    bridge.format_numbers(f"{s}!B{at['coverage'] + 1}:D{at['last']}")
    _try(bridge, "layout_sheet", sheet, _AUDIT_COLUMNS, gridlines=False)
    _try(bridge, "style", f"{s}!A1", bold=True, size=15, color=_INK)
    _try(bridge, "style", f"{s}!A2", italic=True, size=9, color=_MUTED)
    for row in (4, at["dropped"], at["coverage"]):
        _try(bridge, "style", f"{s}!A{row}:I{row}", bold=True, color="#FFFFFF", fill="#1E3A5F", height=22)
    _try(bridge, "style", f"{s}!A{first}:I{last}", border_bottom=_LINE, wrap=True)
    _try(bridge, "style", f"{s}!F{first}:F{last}", bold=True, color=_FINAL_INK)
    fill, ink = (_FINAL_FILL, _FINAL_INK) if _AUDIT.status == audit.FINAL else (_DRAFT_FILL, _DRAFT_INK)
    _try(bridge, "style", f"{s}!A{at['status']}", bold=True, color=_INK)
    _try(bridge, "style", f"{s}!B{at['status']}", bold=True, color=ink, fill=fill)
    _show_status_on_report(bridge, _AUDIT.status)
    scene_log.record("status", label=_AUDIT.status)
    return sheet


def _show_status_on_report(bridge, status: str) -> None:
    from engine.build import SHEET_NAMES
    report = next((n for n in bridge.sheet_names() if n.startswith(SHEET_NAMES["report"])), None)
    if not report:
        return
    fill, ink = (_FINAL_FILL, _FINAL_INK) if status == audit.FINAL else (_DRAFT_FILL, _DRAFT_INK)
    bridge.write(f"{_q(report)}!I1", [[status]])
    _try(bridge, "style", f"{_q(report)}!I1", bold=True, color=ink, fill=fill, halign="center")


def make_final(bridge) -> list:
    """ "chốt chính thức": the one step that turns SƠ BỘ into CHÍNH THỨC, only ever on the user's word."""
    global _AUDIT
    if not bridge.available() or audit.SHEET not in bridge.sheet_names():
        return [Beat("Em chưa thấy sheet Bien ban Clicky trong file này. Anh chạy báo cáo tháng trước rồi mới chốt nhé.")]
    if _AUDIT is None:
        # Clicky was restarted: keep what the sheet already shows and only change its status line.
        rows = bridge.sheet_rows(audit.SHEET) or []
        row = next((i + 1 for i, r in enumerate(rows) if r and str(r[0]).strip() == "Trạng thái"), None)
        if row is None:
            return [Beat("Em không tìm thấy dòng Trạng thái trong sheet Bien ban Clicky, nên chưa chốt.")]
        s = _q(audit.SHEET)
        bridge.write(f"{s}!B{row}", [[audit.FINAL, "", f"Đã chuyển sang Chính thức: {audit.who()}."]])
        _try(bridge, "style", f"{s}!B{row}", bold=True, color=_FINAL_INK, fill=_FINAL_FILL)
        _show_status_on_report(bridge, audit.FINAL)
        scene_log.record("status", label=audit.FINAL)
    else:
        _AUDIT.add("—", "Trạng thái báo cáo", audit.DRAFT, audit.FINAL, "Người dùng chốt sau khi duyệt",
                   audit.who(), "—")
        _AUDIT.status = audit.FINAL
        _write_audit(bridge)
    return [Beat("Em đã chuyển báo cáo sang Chính thức và ghi vào biên bản. Anh bấm Ctrl + S để lưu nhé.",
                 f"{_q(audit.SHEET)}!A1:I4", "Đã chốt", "ok", audit.FINAL)]


def _step(step: str, **data) -> None:
    """One line per step of the month close in the scene log, so a run that stops shows where it stopped."""
    scene_log.record("close_step", step=step, **data)


def _load(pq, name: str):
    """pq.load with its start, end and outcome in the scene log."""
    import time
    _step("query_load", name=name, state="start")
    t0 = time.monotonic()
    result = pq.load(name)
    seconds = round(time.monotonic() - t0, 1)
    if isinstance(result, LoadTimeout):
        _step("query_load", name=name, state="timeout", seconds=seconds, dialog=result.dialog)
    elif isinstance(result, str):
        _step("query_load", name=name, state="error", seconds=seconds, why=load_error_vi(result))
    else:
        _step("query_load", name=name, state="ok", seconds=seconds, rows=len(result[2]))
    return result


def _reconcile(bridge, pq, machine_order: Optional[str]) -> list:
    global _PENDING
    _PENDING = None
    if not bridge.available():
        return [Beat("Anh mở file Excel đã lưu trong thư mục dữ liệu tháng rồi gọi lại em.")]
    folder = _data_folder(bridge)
    if folder is None:
        return [Beat("File Excel này chưa được lưu. Anh lưu nó vào thư mục có các file đơn hàng và file cân đối tài khoản, rồi gọi lại em.")]
    files = sources.find_order_files(folder)
    if not files:
        return [Beat(f"Em không thấy file đơn hàng CSV nào trong thư mục {folder.name}.")]
    tbs, ledger = _ledger(folder)
    period = sources.period_of(tbs[-1][1]) if tbs else None
    truth = sources.truth(files, period)
    ledger = _net_of_returns(_rescope(tbs, ledger, truth), truth)
    beats, notes = [], []
    if truth.outside:
        out_amt = sum((a for *_, a in truth.outside), ZERO)
        months = sorted({f"{d.month:02d}/{d.year}" for _, _, d, _ in truth.outside})
        notes.append(["Dòng ngoài kỳ sổ cái", len(truth.outside), out_amt, "",
                      f"File xuất có {len(truth.outside)} dòng của tháng {', '.join(months)}, tổng {spoken(out_amt)}, "
                      f"nằm ngoài kỳ {period[1]:02d}/{period[0]} của sổ cái nên không được tính khi đối chiếu."])

    source_note = f"{truth.unique_rows} chứng từ trong {len(files)} file" + (
        f" · đã bỏ {len(truth.duplicates)} chứng từ lặp" if truth.duplicates else "") + (
        f" · trả lại {spoken(truth.returns)}" if truth.returns else "")
    if ledger.get("net"):
        for label, mine, theirs, acct in (("Doanh thu bán", truth.gross, ledger["gross"], f"Có TK {ledger['rev_acct']}"),
                                          ("Hàng bán trả lại", truth.returns, ledger["returns"], "Nợ TK 521")):
            if abs(mine - theirs) > TOLERANCE:
                notes.append([f"{label} lệch sổ cái", "", mine - theirs, "",
                              f"File gốc {spoken(mine)}, sổ cái {acct} {spoken(theirs)}."])
    if ledger:
        diff = truth.amount - ledger["revenue"]
        same = abs(diff) <= TOLERANCE
        beats.append(Beat(
            f"File gốc và sổ cái khớp nhau: doanh thu {spoken(truth.amount)}." if same else
            f"File gốc {_gap_vi(diff)} so với sổ cái, anh cần xem lại file xuất.",
            _check_ref("C5:C6"), "File gốc khớp sổ cái" if same else "File gốc lệch sổ cái", "ok" if same else "bad",
            spoken(truth.amount) if same else _signed(diff), source_note + f" · TK {ledger['rev_acct']}"))
    else:
        beats.append(Beat(f"Em đọc {len(files)} file đơn hàng: {truth.unique_rows} chứng từ, doanh thu {spoken(truth.amount)}.",
                          _check_ref("B5:C5"), "File gốc", "", spoken(truth.amount), source_note))
    kinds = Counter(f.date_order for f in files)
    if len(kinds) > 1:
        notes.append(["Kiểu ngày trong file gốc", "", "", "",
                      "; ".join(f"{f.name}: {_order_vi(f.date_order)}" for f in files)])

    name, formula, origin = _query_to_check(pq, folder)
    keys = _dedup_key(files, truth)
    if truth.invoice_lines:
        lines = ", ".join(sorted(truth.invoice_lines)[:5])
        notes.append(["Hoá đơn nhiều dòng", sum(truth.invoice_lines.values()),
                      sum((truth.doc_amounts[d] for d in truth.invoice_lines), ZERO), "",
                      f"{len(truth.invoice_lines)} hoá đơn có nhiều dòng mặt hàng cùng số chứng từ ({lines}). "
                      "Đó là các dòng của một hoá đơn, không phải trùng; Clicky chỉ xoá dòng giống hệt nhau."])
    if truth.collisions:
        shared = ", ".join(sorted(truth.collisions)[:5])
        notes.append(["Số chứng từ trùng giữa các nơi", sum(truth.collisions.values()),
                      sum((truth.doc_amounts[d] for d in truth.collisions), ZERO), "",
                      f"{len(truth.collisions)} số chứng từ được dùng cho các đơn khác nhau ({shared}). Đây là đơn thật, "
                      + ("Clicky xoá trùng theo Chi nhánh + Số CT." if keys else "Clicky chỉ xoá dòng giống hệt nhau.")])
    fixed_m = m_code.folder_query(str(folder), {f.name: f.culture(machine_order) for f in files},
                                  _typed_columns(files), files[0].header[files[0].columns["doc"]] if "doc" in files[0].columns else None,
                                  folder_parameter=PARAM_NAME, key_columns=keys, period=period if truth.outside else None,
                                  delimiter={f.name: f.delimiter for f in files})
    if name is None:
        name, formula, origin = QUERY_NAME, None, "clicky"
        if _AUDIT is not None:
            _AUDIT.add(f"Query {QUERY_NAME}", "Query đơn hàng", "—", "Query mới",
                       "Trong file chưa có query đơn hàng; Clicky tạo query đọc đúng kiểu ngày của từng file",
                       audit.AUTO, f"{len(files)} file đơn hàng")
        notes.append(["Query đơn hàng", "", "", "", "Trong file chưa có query đơn hàng; Clicky tạo query mới đọc đúng kiểu ngày của từng file."])
    elif origin != "workbook":
        formula, moved = _point_at_this_folder(formula, files)
        pq.add(name, formula)
        notes.append(["Query đơn hàng", "", "", "", f"Lấy từ file {origin}, nạp vào Excel với tên {name} để kiểm tra."
                      + (" Thư mục trong query trỏ tới một máy khác, máy này không có, nên Clicky đổi sang thư mục "
                         "đơn hàng ở đây." if moved else "")])

    if formula is not None:
        findings = m_code.lint(name, formula)
        worst = sorted(findings, key=lambda f: (_LINT_ORDER.index(f.code) if f.code in _LINT_ORDER else 99,
                                                f.severity != "error"))
        notes += _lint_rows(worst)
    else:
        pq.add(PARAM_NAME, m_code.parameter_query(str(folder)))
        pq.add(name, fixed_m)

    _step("query_add", name=name, origin=origin or "clicky", existing=formula is not None)
    result = _load(pq, name)
    if isinstance(result, ReadFailed):
        _write_check_sheet(bridge, None, truth, ledger, notes)
        return beats + [Beat("Em chưa đọc lại được bảng query, nên chưa có số chênh lệch. Đây là lỗi của Clicky.",
                             _check_ref("A7:E7"), "Chưa đối chiếu được", "warn", "Chưa có số",
                             "Không phải lỗi file của anh. Anh cập nhật Clicky rồi gọi lại em.")]
    if isinstance(result, str):
        log.warning("loading query %s failed: %s", name, result)
        why_vi = load_error_vi(result)
        _write_check_sheet(bridge, None, truth, ledger,
                           [["Lỗi khi tải query", "", "", "", why_vi + " Chi tiết kỹ thuật ghi trong clicky.log."]] + notes)
        beats.append(Beat(f"Excel chưa tải được query {name}.", _check_ref("A4:E4"), "Query chưa tải được", "bad",
                          "Lỗi tải", why_vi))
        if formula is None or isinstance(result, LoadTimeout):
            return beats                        # Excel is busy or asking: a rewritten query would not load either
        _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder)}
        return beats + [_fix_offer()]
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    gap = loaded.amount - (ledger["revenue"] if ledger else truth.amount)
    clean = abs(gap) <= TOLERANCE and len(rows) == truth.unique_rows
    # A query that names its own culture reads dates that way on every machine, not the machine's way.
    culture = m_code.fixed_culture(formula) if formula else None
    read_order = (sources.MONTH_FIRST if culture.endswith("-US") else sources.DAY_FIRST) if culture else machine_order
    why = diagnose(truth, loaded, read_order) if not clean else {"date": [], "distinct": [], "other": [], "excel_dups": []}
    cause_beats, cause_rows = _cause_beats(why, read_order, 0, f"Query ghi cứng {culture}, nên đọc" if culture else "Máy này đọc",
                                           None if clean else loaded.amount - truth.amount)
    check_sheet = _write_check_sheet(bridge, loaded, truth, ledger, cause_rows + notes, "ok" if clean else "bad")
    ref = f"{_q(check_sheet)}!C7" if check_sheet and ledger else None
    if clean:
        beats.append(Beat(f"Excel khớp sổ cái đến từng đồng: doanh thu {spoken(loaded.amount)}.",
                          ref, "Excel khớp sổ cái", "ok", "0 đ", f"{len(rows)} chứng từ · {spoken(loaded.amount)}"))
        _EXPECT.update({_check_ref("C4"): int(loaded.amount), _check_ref("C7"): 0} if ledger else {})
        return beats + build_report(bridge, folder, tbs) + [_save_offer()]

    beats.append(Beat(f"Báo cáo trong Excel đang {_gap_vi(gap)} so với sổ cái.", ref, "Excel lệch sổ cái", "bad",
                      _signed(gap), f"Query chỉ giữ {len(rows)}/{truth.unique_rows} chứng từ. Chưa được dùng số này."))
    beats += cause_beats
    offer = _fix_offer()
    if cause_rows:
        # The question stays up with one note per cause, each beside its own row in the detail table.
        notes = visuals.cause_notes(cause_rows)
        for n in notes:
            n["ref"] = _detail_ref(n["row"])
        offer.graphic = {"kind": "notes", "notes": notes, "plain": True}
    _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder), "gap": int(gap),
                "before": int(loaded.amount), "before_rows": len(rows)}
    return beats + [offer]


# ── Stage 2 ───────────────────────────────────────────────────────────────────

_BACKUP_NOTE = "Bản query cũ vẫn còn trong power_query_backups.json."


def approve(bridge, pq) -> list:
    global _PENDING
    if stage() == "intake":                        # "ok" / "đồng ý" said to the receipt means the same as its button
        return confirm(bridge, pq)
    if stage() == "save":
        return save_and_verify(bridge)
    job, _PENDING = _PENDING, None
    if not job or job.get("stage") != "fix_query":
        return []
    # Reloading the query and adding the report sheets would flash a dozen sheets past the user;
    # the screen stays still and the result is shown when it is ready.
    _try(bridge, "screen_updating", False)
    before = set(bridge.sheet_names())
    try:
        return _approve(bridge, pq, job)
    finally:
        _note_new_sheets(bridge, before)
        _write_audit(bridge)
        _try(bridge, "screen_updating", True)


def load_error_vi(raw: str) -> str:
    """One plain Vietnamese sentence for a Power Query / COM load error; the raw text goes to the log only."""
    if isinstance(raw, LoadTimeout):
        if raw.dialog:
            return (f"Excel đang chờ trả lời hộp thoại \"{raw.dialog}\", nên query chưa tải xong. Chị xem cửa sổ "
                    "Excel, trả lời hoặc đóng hộp thoại đó, rồi gọi lại em.")
        return (f"Excel tải query quá {int(raw.seconds)} giây vẫn chưa xong, nên em dừng chờ. Chị đợi Excel rảnh "
                "(hoặc đóng bớt file đang mở), rồi gọi lại em.")
    text = str(raw or "")
    m = re.search(r"column '([^']+)' of the table wasn't found", text, re.I)
    if m:
        return f"Excel không tìm thấy cột {m.group(1)} trong file đơn hàng."
    if re.search(r"DataFormat\.Error|couldn't convert|could not convert", text, re.I):
        return "Excel gặp một giá trị không đổi được sang số hoặc ngày."
    if re.search(r"DataSource\.(Error|NotFound)|could(n't| not) find|access.*denied|being used by another", text, re.I):
        return "Excel không mở được thư mục hoặc một file đơn hàng (có thể file đang mở ở nơi khác)."
    if re.search(r"Formula\.Firewall|privacy", text, re.I):
        return "Excel chặn query vì cài đặt quyền riêng tư của Power Query."
    from engine import errors_vi                    # Excel busy / not answering / offline: the shared words
    if errors_vi.classify(text) != errors_vi.GENERIC:
        return errors_vi.one_line(text)
    return "Excel báo lỗi khi tải query."


def _approve(bridge, pq, job) -> list:
    folder = Path(job["folder"])
    try:
        before = dict(pq.list()).get(job["name"])
    except Exception as exc:
        log.warning("reading query %s before the fix failed: %s", job["name"], exc)
        before = None
    pq.add(PARAM_NAME, m_code.parameter_query(str(folder)))
    if not pq.add(job["name"], job["formula"]):
        return [Beat(f"Em không sửa được query {job['name']}. Anh kiểm tra file có đang bị khoá không.")]
    files = sources.find_order_files(folder)
    tbs, ledger = _ledger(folder)
    truth = sources.truth(files, sources.period_of(tbs[-1][1]) if tbs else None)
    ledger = _net_of_returns(_rescope(tbs, ledger, truth), truth)
    _step("query_fix", name=job["name"])
    result = _load(pq, job["name"])
    if isinstance(result, ReadFailed):
        return [Beat("Em đã sửa query nhưng chưa đọc lại được bảng kết quả. Đây là lỗi của Clicky.",
                     label="Chưa kiểm tra được", mark="warn", note=_BACKUP_NOTE)]
    if isinstance(result, str):
        # The raw COM/Power Query text is for the log, never the screen. Put the user's query back as it was,
        # so a failed fix leaves the workbook as Clicky found it, and end the turn in one clear sentence.
        log.warning("reloading the fixed query %s failed: %s", job["name"], result)
        restored = before is not None and pq.add(job["name"], before)
        kept = "Query cũ vẫn giữ nguyên." if restored else _BACKUP_NOTE
        why_vi = load_error_vi(result)
        _write_check_sheet(bridge, None, truth, ledger, [["Chưa sửa được query", "", "", "",
                                                          f"{why_vi} {kept} Chi tiết kỹ thuật ghi trong clicky.log."]])
        return [Beat(f"Em chưa sửa được query {job['name']}. {kept} Chưa lập báo cáo.",
                     _check_ref(f"A{DETAIL_TOP + 1}:E{DETAIL_TOP + 1}"), "Chưa sửa được", "warn", "Chưa sửa", why_vi)]
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    target = ledger["revenue"] if ledger else truth.amount
    gap = loaded.amount - target
    ok = abs(gap) <= TOLERANCE
    fixed_note = [["Đã sửa query", "", "", "", "Đọc ngày theo đúng kiểu của từng file, xoá trùng chỉ theo số chứng từ, "
                   "không xoá âm thầm dòng lỗi, đường dẫn thư mục nằm trong tham số " + PARAM_NAME + ". " + _BACKUP_NOTE]]
    check_sheet = _write_check_sheet(bridge, loaded, truth, ledger, fixed_note, "ok" if ok else "bad")
    ref = f"{_q(check_sheet)}!C7" if check_sheet and ledger else None
    if not ok:
        return [Beat(f"Em đã sửa nhưng Excel vẫn {_gap_vi(gap)} so với sổ cái. Em dừng ở đây để anh xem.",
                     ref, "Vẫn lệch sổ cái", "bad", _signed(gap), f"Query còn {len(rows)} chứng từ. Chưa lập báo cáo.")]
    cost_ok = not ledger or "cost" not in loaded.columns or abs(loaded.cost - ledger["cost"]) <= TOLERANCE
    note = f"{len(rows)} chứng từ · doanh thu {spoken(loaded.amount)}" + (
        " · giá vốn khớp TK 632" if cost_ok and ledger and "cost" in loaded.columns else "")
    if _AUDIT is not None:
        files_n = len(files)
        _AUDIT.add(f"Query {job['name']}", "Doanh thu đơn hàng trong Excel",
                   job.get("before", "—"), int(loaded.amount),
                   f"Sửa query: đọc ngày đúng kiểu từng file, xoá trùng chỉ theo số chứng từ; "
                   f"{job.get('before_rows', '?')} → {len(rows)} chứng từ, khớp sổ cái",
                   audit.who(), f"{files_n} file đơn hàng")
    # Shown straight as 0 đ: a figure rolling through "−2,9 tỷ" under the title "Đã khớp" reads as a contradiction.
    beats = [Beat("Đã sửa xong. Excel khớp sổ cái đến từng đồng.", ref, "Đã khớp sổ cái", "ok", "0 đ", note)]
    _EXPECT.update({_check_ref("C4"): int(loaded.amount), _check_ref("C7"): 0} if ledger else {})
    return beats + build_report(bridge, folder, tbs) + [_save_offer()]


# Clicky changes the open workbook but never saves it for the user; closing without saving
# loses the fix and the report, so the last thing it says is how to keep them.
SAVE_REMINDER = Beat("Em chưa lưu file. Anh bấm Ctrl + S để giữ kết quả nhé.")
SAVE_CHOICES = ("Lưu và kiểm tra", "Để tôi tự lưu")
SAVE_TRIGGER = r"^\s*(lưu và kiểm tra|luu va kiem tra|lưu đi|luu di|lưu giúp|luu giup)\b(\s+\S+){0,3}\s*[.!]?\s*$"
SELF_SAVE_TRIGGER = r"^\s*(để tôi tự lưu|de toi tu luu|tôi tự lưu|toi tu luu|để tôi lưu|de toi luu)\b(\s+\S+){0,3}\s*[.!]?\s*$"
VERIFY_TRIGGER = r"\b(kiểm tra|kiem tra|đối chiếu|doi chieu)\s+(lại\s+|lai\s+)?file\s+(đã\s+|da\s+)?lưu|\bkiem tra file da luu\b"
# Cells of the saved file that must read back as the code computed them (sheet!cell → đồng).
_EXPECT: dict = {}
# The last report's key figures, for the sample run's closing card (code-computed, never typed in).
_LAST: dict = {}


def _save_offer() -> Beat:
    """The last question: Clicky saves only when asked, then opens the saved file and checks it."""
    global _PENDING
    _PENDING = {"stage": "save"}
    return Beat("Em lưu file rồi mở lại bản đã lưu để đối chiếu với sổ cái nhé?", choices=SAVE_CHOICES)


def save_and_verify(bridge, target=None) -> list:
    """Save the workbook, re-read the saved xlsx from disk and compare its key cells with the code's figures.

    target is the exact file to save to when Clicky made the workbook itself (the sample, in Clicky's
    own folder); the user's own file is only ever saved where it already is."""
    global _PENDING
    if stage() == "save":
        _PENDING = None
    _step("save")
    if not bridge.available():
        return [Beat("Em không thấy file Excel đang mở, nên chưa lưu được.")]
    if not _try(bridge, "save", *([str(target)] if target else [])):
        return [Beat("Em chưa lưu được file. Anh bấm Ctrl + S rồi gọi \"kiểm tra file đã lưu\" nhé.",
                     label="Chưa lưu được", mark="warn")]
    return verify_saved(bridge)


def verify_saved(bridge) -> list:
    from engine import saved_check
    path = _try(bridge, "workbook_path")
    if not path or not _EXPECT:
        return [Beat("Em chưa có số nào để đối chiếu với file đã lưu. Anh chạy báo cáo tháng trước nhé.")]
    try:
        # The robot's tamper hook hands back a changed copy; without it this is the saved file itself.
        ok, diffs = saved_check.check(saved_check.tamper_from_env(Path(path)), _EXPECT)
    except Exception as exc:
        log.warning("re-reading the saved workbook failed: %s", exc)
        ok, diffs = False, [{"cell": "", "want": None, "got": None}]
    scene_log.record("verify", ok=ok, path=path, diffs=diffs)
    if ok:
        scene_log.record("delivered", path=path)
        return [Beat("Em đã mở lại file đã lưu: khớp sổ cái 0 đ. File sẵn sàng để gửi.",
                     _check_ref("A7:E7") if _check_ref("C7") in _EXPECT else None,
                     "Khớp sổ cái", "ok", "0 đ", f"Đã đọc lại {len(_EXPECT)} ô từ file trên đĩa · {Path(path).name}")]
    first = diffs[0]
    if first["cell"]:
        where = first["cell"]
        got = "trống" if first["got"] is None else spoken(Decimal(first["got"]))
        detail = f"Ô {where}: cần {spoken(Decimal(first['want']))}, file ghi {got}."
    else:
        where, detail = "", "Em không đọc lại được file đã lưu."
    more = f" Còn {len(diffs) - 1} ô khác lệch." if len(diffs) > 1 else ""
    return [Beat("File đã lưu không giống số em tính, em chưa giao file này. Anh đừng gửi bản này nhé.",
                 where or None, "File đã lưu bị lệch", "bad",
                 _signed(Decimal(first["got"] - first["want"])) if first.get("got") is not None and first.get("want") is not None
                 else "Lệch", detail + more)]


def self_save() -> list:
    return [Beat("Anh bấm Ctrl + S để lưu nhé. Lưu xong anh gõ \"kiểm tra file đã lưu\", em mở lại và đối chiếu.")]


def reject() -> list:
    global _PENDING
    if not _PENDING:
        return []
    if stage() == "intake":
        return wrong()
    if stage() == "save":
        _PENDING = None
        return self_save()
    _PENDING = None
    return [Beat("Em giữ nguyên query. Số trong Excel vẫn lệch sổ cái, anh đừng dùng cho báo cáo nhé.")]


# ── Stage 3: the report ───────────────────────────────────────────────────────

EXTRA_ROWS = 3        # returns line, real net revenue, real profit (engine.report_sheet.real_profit_rows)


def _audit_returns(report, names: dict, pat_row: int, overstated) -> None:
    """Biên bản lines for the three lines Clicky adds under the report when returns sit in Nợ 511."""
    if _AUDIT is None:
        return
    from engine.report_sheet import FIRST_LINE_ROW
    keys = [f.key for f in report.figures]
    value = {f.key: f.value for f in report.figures}
    tb = report.current
    acct = sources.revenue_account(tb)
    line = next((l for l in tb.leaves if l.account.startswith(acct) and l.dr), None)
    source = f"{names['tb_cur']} dòng {line.row}" if line else names["tb_cur"]
    adj = pat_row + 1
    net_row = FIRST_LINE_ROW + keys.index("net_rev")
    _AUDIT.add(f"C{adj}", "Trừ: hàng bán bị trả lại", 0, -int(overstated),
               f"{spoken(overstated)} hàng bán bị trả lại đang ghi Nợ {acct}, chuyển sang 521", audit.AUTO, source)
    _AUDIT.add(f"C{adj + 1}", "Doanh thu thuần thực", int(value["net_rev"]), int(value["net_rev"] - overstated),
               f"Tính lại theo dòng {adj} (doanh thu thuần ở ô C{net_row})", audit.AUTO, "Công thức")
    _AUDIT.add(f"C{adj + 2}", "Lợi nhuận sau thuế thực", int(value["pat"]), int(value["pat"] - overstated),
               f"Tính lại theo dòng {adj} (lợi nhuận sau thuế ở ô C{pat_row})", audit.AUTO, "Công thức")


def build_report(bridge, folder: Path, tbs: list) -> list:
    from engine.report_sheet import quote
    from office import report_skill
    _step("build_report", trial_balances=len(tbs))
    if len(tbs) < 2:
        return [Beat("Em chưa thấy đủ hai file cân đối tài khoản, kỳ này và kỳ trước, nên chưa lập báo cáo.")]
    existing = set(bridge.sheet_names())
    for name, rows, _ in tbs[-2:]:
        if name not in existing:
            sheet = bridge.add_sheet(name)
            if sheet:
                bridge.write(f"{quote(sheet)}!A1", [list(r) for r in rows])
    bridge.activate(tbs[-1][0])
    # The rows already read from the files are what the report is built from; reading them back
    # out of Excel would only add a way to fail (cell types change on the way in and out).
    (cur, cur_rows, _), (prev, prev_rows, _) = tbs[-1], tbs[-2]
    summary, report, names = report_skill.run_report(bridge, ((cur, cur_rows), (prev, prev_rows)))
    if report is None:
        return [Beat(summary)]
    pat = report.figures[-1].value
    prev_pat = next((v.previous for v in report.variances if v.key == "pat"), None)
    row = 4 + len(report.figures)
    sheet = quote(names["report"])
    pat_ref = f"{sheet}!A{row}:G{row}"      # the whole line, label included: the figure never appears without its name
    overstated = sources.returns_in_revenue(report.current)
    if overstated > 0:
        # The report already carries the returns line, the real net revenue and the real profit under the reported one.
        pat_ref = f"{sheet}!A{row}:G{row + EXTRA_ROWS}"
        _audit_returns(report, names, row, overstated)
    _style_report_sheet(bridge, names["report"], row, extra=EXTRA_ROWS if overstated > 0 else 0)
    bridge.activate(names["report"])
    # The whole report, title to real profit, fits on screen; it is seen from the top and stays that way.
    # Ten empty rows are kept under it, so the profit card sits below the report instead of covering it.
    _try(bridge, "fit_rows", names["report"], f"A1:I{(row + EXTRA_ROWS if overstated > 0 else row) + 10}")
    _try(bridge, "scroll_home", names["report"])
    asks = (f", kèm {len(report.questions)} câu hỏi cho các bộ phận" if report.questions else "")
    note = "Mọi số là công thức trỏ về sổ cái." + (
        f" {len(report.questions)} câu hỏi xác minh ở sheet {names['questions']}." if report.questions else "")
    from engine.report_sheet import FIRST_LINE_ROW
    for i, fig in enumerate(report.figures):
        _EXPECT[f"{sheet}!C{FIRST_LINE_ROW + i}"] = int(fig.value)
    if overstated > 0:
        net = next(f.value for f in report.figures if f.key == "net_rev")
        _EXPECT.update({f"{sheet}!C{row + 1}": -int(overstated), f"{sheet}!C{row + 2}": int(net - overstated),
                        f"{sheet}!C{row + 3}": int(pat - overstated)})
    _LAST.clear()
    _LAST.update(pat=pat, overstated=overstated, real=pat - overstated, report=names["report"])
    beats = [Beat(f"Em đã lập báo cáo tháng{asks}.", f"{sheet}!A4:G{row}", "Báo cáo tháng đã lập", "", "", note)]
    line_row = {f.key: FIRST_LINE_ROW + i for i, f in enumerate(report.figures)}
    opened = visuals.figure_to_explain(report.figures, report.variances)
    if opened is not None:
        # Wires from the figure to the accounts it is made of: where a number comes from, seen at once.
        tags = visuals.leader_tags(opened)
        at = line_row[opened.key]
        beats.append(Beat(visuals.leader_sentence(tags), f"{sheet}!A{at}:C{at}",
                          graphic={"kind": "leaders", "figure_ref": f"{sheet}!C{at}", **tags}))
    if overstated > 0:
        real = pat - overstated
        change = f" Giảm {spoken(prev_pat - real)} so với kỳ trước." if prev_pat is not None and prev_pat > real else ""
        beats.append(Beat(f"Lợi nhuận thực là {spoken(real)}, không phải {spoken(pat)} như trên báo cáo.",
                          pat_ref, "Lợi nhuận sau thuế thực", "warn", spoken(real),
                          f"{spoken(overstated)} hàng bán bị trả lại đang ghi thẳng vào Nợ TK 511. "
                          f"Cần chuyển sang TK 521 trước khi gửi sếp.{change}",
                          was=spoken(pat), roll=(int(pat), int(real))))
        chart = visuals.profit_bridge(pat, [("Hàng trả lại ghi Nợ 511", -overstated)], real)
        beats.append(Beat(visuals.bridge_sentence(chart), pat_ref, graphic={"kind": "bridge", **chart}))
    else:
        beats.append(Beat(summary, pat_ref, "Lợi nhuận sau thuế", "", spoken(pat)))
    # Every trap the engine found gets a note beside the line it touches; they stay up while the save is asked.
    notes = visuals.report_notes(report.issues, report.current, overstated,
                                 {**line_row, "returns": row + 1 if overstated > 0 else line_row.get("net_rev")})
    if notes:
        end = row + EXTRA_ROWS if overstated > 0 else row
        for n in notes:
            n["ref"] = f"{sheet}!A{n['line']}:G{n['line']}" if n.get("line") else f"{sheet}!A1:G2"
        beats.append(Beat(visuals.notes_sentence(notes), f"{sheet}!A1:G{end}",
                          graphic={"kind": "notes", "notes": notes, "plain": True}))
    return beats            # the profit story, then what to check before it goes to the boss


_REPORT_COLUMNS = {"A": 40, "B": 12, "C": 17, "D": 17, "E": 17, "F": 9, "G": 15, "H": 34, "I": 14}


def _style_report_sheet(bridge, sheet: str, last: int, extra: int = 0) -> None:
    """The report reads like one: title, a header band, readable columns, the profit line stands out.

    extra: lines added under the profit line (returns adjustment, real net revenue, real profit)."""
    s = _q(sheet)
    _try(bridge, "layout_sheet", sheet, _REPORT_COLUMNS, gridlines=False)
    _try(bridge, "style", f"{s}!A1", bold=True, size=15, color=_INK)
    _try(bridge, "style", f"{s}!A2", italic=True, size=9, color=_MUTED)
    _try(bridge, "style", f"{s}!A4:I4", bold=True, color="#FFFFFF", fill="#1E3A5F", height=24, wrap=True)
    _try(bridge, "style", f"{s}!C4:F4", halign="right")
    _try(bridge, "style", f"{s}!A5:I{last}", border_bottom=_LINE, height=20)
    _try(bridge, "style", f"{s}!B5:B{last}", halign="center", color=_MUTED)
    _try(bridge, "style", f"{s}!H5:I{last}", color="#475569")
    _try(bridge, "style", f"{s}!A{last}:I{last}", bold=True, fill="#FEF3E2", height=26)
    _try(bridge, "ignore_text_numbers", f"{s}!B5:B{last}")
    if extra:
        end = last + extra
        _try(bridge, "format_numbers", f"{s}!C{last + 1}:C{end}")
        _try(bridge, "style", f"{s}!A{last + 1}:I{end}", border_bottom=_LINE, height=20)
        _try(bridge, "style", f"{s}!A{last}:I{last}", bold=False, fill="#FFFFFF", height=20)
        _try(bridge, "style", f"{s}!A{last + 1}:I{last + 1}", italic=True, color="#B45309")
        _try(bridge, "style", f"{s}!H{last + 1}:I{end}", color="#475569")
        _try(bridge, "style", f"{s}!A{end}:I{end}", bold=True, fill="#FEF3E2", height=26)


# ── Voice skill glue ──────────────────────────────────────────────────────────

BEAT_GAP_S = 0.5          # breath between two ideas
CLEAR_AFTER_S = 8.0       # the last card fades this long after Clicky stops talking (not when it asks a question)
FINAL_HOLD_S = 25.0       # ...unless it is a warning: that one stays up long enough to read, screenshot, act on


def shapes_for(box: tuple, beat: Beat, avoid=None, soft=None) -> list:
    """Overlay shapes for one beat: the range lit with the rest of the screen dimmed, and one card beside it.
    avoid: the figures on screen (one box or a list of boxes), which the card never covers when anything
    else fits; soft: the cells holding words, which it keeps off when it can."""
    from ui.card_layout import tone_of
    l, t, r, b = box
    tone = tone_of(beat.mark)
    shapes = [{"kind": "spotlight", "x1": l, "y1": t, "x2": r, "y2": b, "tone": tone}]
    if beat.label or beat.value or beat.note:
        shapes.append({"kind": "card", "anchor": [l, t, r, b], "title": beat.label, "was": beat.was,
                       "value": beat.value, "note": beat.note, "tone": tone, "roll": list(beat.roll),
                       "avoid": _as_boxes(avoid) or None, "soft": _as_boxes(soft) or None})
    return shapes


def _as_boxes(boxes) -> list:
    if not boxes:
        return []
    if len(boxes) == 4 and all(isinstance(v, (int, float)) for v in boxes):
        return [list(boxes)]
    return [list(b) for b in boxes if b]


async def _locate(manager, ref_text: str):
    from office.cellref import parse_ref
    ref = parse_ref(ref_text)
    try:
        if ref and ref.sheet:
            await asyncio.to_thread(manager._excel.activate, ref.sheet)
        rect = await asyncio.to_thread(manager._excel.screen_rect, ref_text, True)
        box = manager._physical_to_logical_bbox(rect) if rect else None
        scene_log.record("locate", ref=ref_text, excel_rect=rect, box=box)
        if not box:
            log.warning("cannot place %s on screen (Excel rect %s)", ref_text, rect)
        return box
    except Exception as exc:
        log.warning("pointing at %s failed: %s", ref_text, exc)
        scene_log.record("locate", ref=ref_text, excel_rect=None, box=None, error=str(exc)[:200])
        return None


async def _filled_area(manager):
    """Logical screen box of the cells holding values on screen, so a card can keep off the figures."""
    try:
        rect = await asyncio.to_thread(manager._excel.content_rect)
        return manager._physical_to_logical_bbox(rect) if rect else None
    except Exception as exc:
        log.debug("filled area unknown: %s", exc)
        return None


async def _figures(manager):
    """(figures, words) on screen as lists of logical boxes, so a card keeps off every figure.

    Each block of figures is its own box, so a card can use a gap inside a full sheet. Without the
    detailed read, the whole filled area counts as figures: the card then goes past it, as before.
    """
    read = getattr(manager._excel, "figure_boxes", None)
    found = None
    if read is not None:
        try:
            found = await asyncio.to_thread(read)
        except Exception as exc:
            log.debug("figures on screen unknown: %s", exc)
    if found and (found.get("numbers") or found.get("text")):
        many = getattr(manager, "_physical_to_logical_boxes", None)
        convert = many or (lambda rects: [manager._physical_to_logical_bbox(r) for r in rects])
        numbers = [b for b in convert(found.get("numbers", [])) if b]
        words = [b for b in convert(found.get("text", [])) if b]
        if numbers or words:
            return numbers, words
    filled = await _filled_area(manager)
    return ([filled] if filled else []), []


def graphic_seconds(graphic: dict) -> float:
    """Extra time on screen for a graphic beyond its sentence: each piece is read, and a bridge column
    stays readable for at least six seconds."""
    kind = graphic.get("kind")
    if kind == "leaders":
        return 1.5 + 1.0 * len(graphic.get("tags") or [])
    if kind == "notes":
        return 1.0 + 2.0 * len(graphic.get("notes") or [])
    if kind == "bridge":
        return max(6.0, 2.0 * len(graphic.get("bars") or []))
    return 0.0


def graphic_log(graphic: dict) -> dict:
    """What the robot needs to check a graphic: its figures and words, without screen positions."""
    keep = ("kind", "title", "figure", "sum", "ok", "check", "tags", "bars")
    out = {k: graphic[k] for k in keep if k in graphic}
    if graphic.get("notes") is not None:
        out["notes"] = [{k: n.get(k) for k in ("codes", "warn", "action", "ref")} for n in graphic["notes"]]
    return out


async def _box_of(manager, ref: str):
    """Logical box of a range already on screen, without scrolling (the beat's own range is shown first)."""
    try:
        rect = await asyncio.to_thread(manager._excel.screen_rect, ref, False)
        return manager._physical_to_logical_bbox(rect) if rect else None
    except Exception as exc:
        log.debug("graphic piece %s not on screen: %s", ref, exc)
        return None


async def _graphic_shape(manager, graphic: dict, box):
    """The overlay shape for a beat's graphic, every piece tied to a box on screen, or None if none is."""
    shape = {k: v for k, v in graphic.items() if k not in ("figure_ref", "plain")}
    if graphic["kind"] == "notes":
        notes, first = [], None
        for n in graphic.get("notes") or []:
            ref = n.get("ref")
            if ref and first is None and box is None:
                at = first = await _locate(manager, ref)          # shows the sheet the notes are on
            else:
                at = await _box_of(manager, ref) if ref else None
            # A note whose cells are off screen still shows, beside the beat's range: no trap goes unseen.
            notes.append({**n, "anchor": list(at or box or first or ()) or None})
        if not any(n["anchor"] for n in notes):
            return None
        shape["notes"] = notes
        return shape
    anchor = (await _box_of(manager, graphic["figure_ref"]) if graphic.get("figure_ref") else None) or box
    if not anchor:
        return None
    shape["anchor"] = list(anchor)
    return shape


def free_card(beat: Beat, screen: tuple) -> dict:
    """A card with no range to point at (the cell could not be shown): top right of the screen, no stem, no
    spotlight. Its note says where the cell is, so the figure is never tied to the wrong cells."""
    from ui.card_layout import tone_of
    sl, st, sr, sb = screen
    return {"kind": "card", "anchor": [sr - 24, st + 96, sr - 20, st + 100], "free": True, "title": beat.label,
            "was": beat.was, "value": beat.value, "note": beat.note, "tone": tone_of(beat.mark), "roll": []}


def _screen() -> tuple:
    try:
        from PyQt6.QtWidgets import QApplication
        g = QApplication.primaryScreen().availableGeometry()
        return g.left(), g.top(), g.right(), g.bottom()
    except Exception:
        return 0, 0, 1920, 1040


async def play(manager, beats: list, keep_card: bool = True) -> None:
    """One idea at a time: light up the beat's range, show its card, say its one sentence, pause, next.

    With keep_card, a beat without a range keeps the previous card up, so the screen never goes blank
    mid-story. A beat whose range cannot be shown never keeps another beat's card: the old card goes, and its
    own card shows without pointing (free_card). keep_card=False (a list of separate mistakes): every beat
    clears the card before it. When the sentence cannot be read aloud (no Vietnamese voice), it stays up long
    enough to read.
    """
    from ui.card_layout import NO_BUBBLE, pointer_spot, reading_seconds
    run = object()
    manager._month_close_run = run
    import turn_clock
    turn_clock.of(manager).mark("ready")
    guided = getattr(manager, "sig_guided", None)     # the chat panel steps aside while Clicky guides
    if guided is not None:
        guided.emit(True)
    manager.sig_clear_drawings.emit()
    caption = getattr(manager, "sig_caption", None)
    if caption is not None:
        caption.emit("")                               # the "Em đang …" line gives way to the first card
    last_card = None
    for i, beat in enumerate(beats):
        if i:
            manager.sig_response_chunk.emit("\n\n")
            if BEAT_GAP_S:
                await asyncio.sleep(BEAT_GAP_S)
        if beat.book and beat.ref:
            shown = await asyncio.to_thread(getattr(manager._excel, "show_workbook", lambda p: False), beat.book)
            scene_log.record("show_book", i=i, ref=beat.ref, ok=bool(shown))
        box = await _locate(manager, beat.ref) if beat.ref else None
        if box:
            manager.sig_point_hold.emit(True)
            # The pointer flies the moment the range is known; the frame and the card land while it travels.
            # It stops beside the range, never on it: it must not hide the figure being explained.
            manager.sig_point_at.emit(*pointer_spot(box), NO_BUBBLE)
        graphic = await _graphic_shape(manager, beat.graphic, box) if beat.graphic else None
        scene_log.record("beat", i=i, ref=beat.ref, box=box, title=beat.label, value=beat.value, was=beat.was,
                         note=beat.note, mark=beat.mark, text=beat.text, choices=list(beat.choices or []),
                         **({"graphic": graphic_log(beat.graphic)} if beat.graphic else {}))
        loose = None
        if not box and not graphic and (beat.ref or not keep_card):
            # Never leave another mistake's card up while this one is read out (grading round 8).
            manager.sig_clear_drawings.emit()
            if beat.ref and (beat.label or beat.value or beat.note):
                loose = free_card(beat, _screen())
                manager.sig_draw.emit(loose)
            scene_log.record("clear", i=i, ref=beat.ref, free_card=loose is not None)
        if box or graphic:
            manager.sig_clear_drawings.emit()
            figures, words = await _figures(manager)
            if box and not (graphic and beat.graphic.get("plain")):
                for shape in shapes_for(box, beat, figures, words):
                    manager.sig_draw.emit(shape)
            if graphic:
                graphic.update(avoid=_as_boxes(figures) or None, soft=_as_boxes(words) or None)
                manager.sig_draw.emit(graphic)
        log.info("beat: %s", beat.text)
        if beat.intake:
            # The receipt: what was read and what was left out, logged before anything is drawn or changed.
            scene_log.record("intake", **beat.intake.get("log", {}))
            sig = getattr(manager, "sig_intake", None)
            if sig is not None:
                sig.emit(beat.intake.get("card", {}))
        if beat.choices:
            # The question's buttons appear with the question, not after it has been read out.
            manager.sig_choices.emit(beat.text, list(beat.choices))
        spoke = await manager._reply_local(beat.text)
        scene_log.record("spoken", i=i, spoke=spoke)
        if spoke is False and BEAT_GAP_S:
            # Not read aloud: leave time to read the sentence, and with a card, to look at the figure and its note.
            await asyncio.sleep(reading_seconds(beat.text)
                                + ((1.0 + 0.6 * reading_seconds(beat.note) if beat.note else 1.0) if box else 0)
                                + (graphic_seconds(beat.graphic) if graphic else 0))
        elif graphic and BEAT_GAP_S:
            # Read aloud, the sentence is shorter than the graphic: leave time to look at every piece.
            await asyncio.sleep(max(0.0, graphic_seconds(beat.graphic) - reading_seconds(beat.text) / 2))
        if box:
            manager.sig_point_release.emit()
            last_card = beat
    # The story is over: the rehearsal robot waits for this, not for a quiet spell (a card read without
    # a voice can sit still for ten seconds while scenes are still to come).
    scene_log.record("story_end", beats=len(beats), last=beats[-1].text[:80] if beats else "",
                     choices=list(beats[-1].choices or []) if beats else [])
    if beats and not beats[-1].choices:
        def fade():
            if getattr(manager, "_month_close_run", None) is run:
                manager.sig_clear_drawings.emit()
                if guided is not None:
                    guided.emit(False)
        try:
            hold = FINAL_HOLD_S if last_card is not None and last_card.mark in ("warn", "bad") else CLEAR_AFTER_S
            asyncio.get_running_loop().call_later(hold, fade)
        except RuntimeError:
            pass


def _pq(manager):
    from office.power_query import PowerQuery
    return PowerQuery(manager._excel)


async def handle_start(manager, transcript: str) -> str:
    await run_from(manager, None)   # typed "làm báo cáo từ thư mục này": the open workbook's own folder
    return ""


# Said the moment a button is pressed, before the slow part (reading files, writing, saving), so the
# screen never sits still while Clicky works. Replaced by the first card as soon as it is ready.
WORKING = {
    "read": "Em đang đọc các file trong thư mục…",
    "confirm": "Em đang đối chiếu và sửa trong file…",
    "approve": "Em đang sửa query và tải lại…",
    "save": "Em đang lưu rồi mở lại file để kiểm…",
    "verify": "Em đang mở lại file đã lưu để kiểm…",
    "pick": "Em đang đọc lại với sổ chị chọn…",
    "final": "Em đang chuyển sang Chính thức…",
}


def _working(manager, step: str) -> None:
    sig = getattr(manager, "sig_caption", None)
    if sig is not None:
        sig.emit(WORKING[step])


async def run_from(manager, folder: Optional[Path]) -> None:
    """Start a fresh month close on a folder (the dropped one) or on the open workbook's folder."""
    use_folder(folder)
    _CHOSEN.clear()                 # a new run asks again: the folder may have changed since
    _PICKS.clear()
    _working(manager, "read")
    order = machine_date_order()
    beats = await asyncio.to_thread(lambda: start(manager._excel, _pq(manager), order))
    await play(manager, beats)


async def handle_approve(manager, transcript: str) -> str:
    # The question's frame must not stay frozen on screen while Excel reloads the query.
    manager.sig_clear_drawings.emit()
    _working(manager, "approve")
    beats = await asyncio.to_thread(lambda: approve(manager._excel, _pq(manager)))
    await play(manager, beats)
    return ""


async def handle_reject(manager, transcript: str) -> str:
    was_intake = intake_pending()
    await play(manager, reject())
    if was_intake:
        _open_chat(manager)
    return ""


def _open_chat(manager) -> None:
    sig = getattr(manager, "sig_open_chat", None)
    if sig is not None:
        sig.emit()


async def handle_confirm(manager, transcript: str) -> str:
    _working(manager, "confirm")
    beats = await asyncio.to_thread(lambda: confirm(manager._excel, _pq(manager)))
    await play(manager, beats)
    return ""


async def handle_wrong(manager, transcript: str) -> str:
    await play(manager, wrong())
    _open_chat(manager)
    return ""


async def handle_save(manager, transcript: str) -> str:
    manager.sig_clear_drawings.emit()
    _working(manager, "save")
    beats = await asyncio.to_thread(lambda: save_and_verify(manager._excel))
    await play(manager, beats)
    return ""


async def handle_verify(manager, transcript: str) -> str:
    _working(manager, "verify")
    beats = await asyncio.to_thread(lambda: verify_saved(manager._excel))
    await play(manager, beats)
    return ""


async def handle_self_save(manager, transcript: str) -> str:
    global _PENDING
    _PENDING = None
    await play(manager, self_save())
    return ""


def save_pending() -> bool:
    return stage() == "save"


async def handle_pick(manager, transcript: str) -> str:
    _working(manager, "pick")
    beats = await asyncio.to_thread(lambda: pick(manager._excel, _pq(manager), transcript))
    await play(manager, beats)
    return ""


async def handle_final(manager, transcript: str) -> str:
    _working(manager, "final")
    beats = await asyncio.to_thread(lambda: make_final(manager._excel))
    await play(manager, beats)
    return ""


SKILLS = [
    {"name": "Báo cáo tháng trọn gói", "trigger": START_TRIGGER, "handler": handle_start,
     "description": "Từ thư mục dữ liệu tháng: đọc các file và cho xem phiếu nhận file, rồi tải query đơn hàng, đối chiếu "
                    "với file gốc và sổ cái, chỉ ra chỗ lệch, đề xuất sửa, rồi lập báo cáo tháng."},
    {"name": "Chọn sổ cân đối", "trigger": PICK_TRIGGER, "handler": handle_pick, "when": pick_pending,
     "description": "Người dùng chọn một trong hai sổ cân đối cùng kỳ."},
    {"name": "Xác nhận phiếu nhận file", "trigger": CONFIRM_TRIGGER, "handler": handle_confirm, "when": intake_pending,
     "description": "Người dùng xác nhận Clicky đọc đúng các file; bắt đầu tính."},
    {"name": "Phiếu nhận file có chỗ sai", "trigger": WRONG_TRIGGER, "handler": handle_wrong, "when": intake_pending,
     "description": "Dừng trước khi sửa gì, mở khung chat để người dùng nói chỗ sai."},
    {"name": "Lưu và kiểm tra file", "trigger": SAVE_TRIGGER, "handler": handle_save, "when": save_pending,
     "description": "Lưu file, mở lại bản đã lưu và đối chiếu các tổng chính với sổ cái."},
    {"name": "Tự lưu file", "trigger": SELF_SAVE_TRIGGER, "handler": handle_self_save, "when": save_pending,
     "description": "Người dùng tự lưu; Clicky nhắc cách kiểm tra sau."},
    {"name": "Kiểm tra file đã lưu", "trigger": VERIFY_TRIGGER, "handler": handle_verify,
     "description": "Mở lại file đã lưu trên đĩa và đối chiếu các tổng chính với số Clicky đã tính."},
    {"name": "Chốt báo cáo chính thức", "trigger": FINAL_TRIGGER, "handler": handle_final,
     "description": "Đổi trạng thái báo cáo từ Sơ bộ sang Chính thức và ghi vào Biên bản."},
    {"name": "Đồng ý bản sửa", "trigger": APPROVE_TRIGGER, "handler": handle_approve, "when": pending,
     "description": "Áp bản sửa query Clicky vừa đề xuất, tải lại và đối chiếu lại."},
    {"name": "Giữ nguyên query", "trigger": REJECT_TRIGGER, "handler": handle_reject, "when": pending,
     "description": "Bỏ bản sửa Clicky vừa đề xuất."},
]
