"""Month close end to end from one voice command: load the sales query, check it against the files and the ledger,
fix it with the user's approval, then build the monthly report, pointing at each number as it is spoken.

    "làm báo cáo tháng từ thư mục này"   → stage 1: read files, load the query, reconcile, explain, propose a fix
    "đồng ý"                            → stage 2: apply the fix, reload, confirm, then build the report
    "thôi"                              → keep the query as it is

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

from engine import m_code, sources
from engine.monthly_report import spoken
from engine.trial_balance import ZERO
from office.power_query import ReadFailed

log = logging.getLogger(__name__)

QUERY_NAME = "DonHang"
PARAM_NAME = "ThuMucDonHang"
CHECK_SHEET = "Doi chieu"
TOLERANCE = Decimal(1)
# Findings that lose revenue rows come first: these are the ones that explain a gap with the ledger.
_LINT_ORDER = ["locale_types", "distinct_keys", "remove_errors", "distinct_all", "inner_join", "fixed_path"]

START_TRIGGER = (r"(l[àa]m|l[ậa]p|ch[ạa]y)\b.{0,25}b[áa]o\s*c[áa]o\s*th[áa]ng.{0,30}"
                 r"(th[ưu]\s*m[ụu]c|t[ừu]\s*[đd][ầa]u|e2e|tr[ọo]n\s*g[óo]i|to[àa]n\s*b[ộo])"
                 r"|\bch[ạa]y\s*case\b|\bmonth[\s-]*close\b")
# Only short replies count, so a new question that starts with "không" is not taken as an answer.
APPROVE_TRIGGER = (r"^\s*(đồng ý|dong y|ok|oke|okay|được|duoc|sửa đi|sua di|sửa giúp|sua giup|làm đi|lam di|yes)\b"
                   r"(\s+\S+){0,3}\s*[.!]?\s*$")
REJECT_TRIGGER = (r"^\s*(thôi|thoi|không|khong|giữ nguyên|giu nguyen|để tôi xem|de toi xem|để anh xem|để chị xem|no)\b"
                  r"(\s+\S+){0,2}\s*[.!]?\s*$")
FIX_CHOICES = ("Sửa giúp tôi", "Để tôi xem")

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
    out = {"date": [], "distinct": [], "other": [], "excel_dups": [d for d, n in present.items() if n > 1]}
    if "doc" not in loaded.columns:
        return out
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
         f"Phát sinh Có TK {ledger.get('rev_acct', '511')} / Nợ TK 632" if ledger else "Chưa thấy file cân đối tài khoản"],
        ["Chênh lệch Excel − sổ cái", "", "=C4-C6" if ledger and loaded else "",
         "=D4-D6" if ledger and loaded and "cost" in loaded.columns else "",
         "Phải bằng 0" if loaded else "Chưa đối chiếu được: chưa đọc được bảng query"],
    ]
    if details:
        grid += [["", "", "", "", ""], ["Chi tiết kiểm tra", "Chứng từ", "Doanh thu (đ)", "", "Giải thích"]]
        grid += [list(r) + [""] * (5 - len(r)) for r in details]
    _try(bridge, "clear", f"{_q(sheet)}!A1:F60")
    bridge.write(f"{_q(sheet)}!A1", grid)
    bridge.format_numbers(f"{_q(sheet)}!B4:D7")
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


def _ledger(folder: Path) -> tuple:
    from engine.cli import read_rows
    tbs = sources.find_trial_balances(folder, read_rows)
    if not tbs:
        return tbs, {}
    name, _, tb = tbs[-1]
    acct = sources.revenue_account(tb)
    return tbs, {"name": name, "rev_acct": acct, "revenue": sources.ledger_movement(tb, acct, "cr"),
                 "cost": sources.ledger_movement(tb, "632", "dr")}


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


def _cause_beats(why: dict, machine_order: Optional[str], first_row: int) -> tuple:
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
                 note=example or f"Máy này đọc ngày kiểu {_order_vi(machine_order)}, khác kiểu ghi trong file."),
            ["Đọc nhầm ngày tháng", n, amt, "", f"Máy này đọc ngày kiểu {_order_vi(machine_order)}; file ghi kiểu khác "
             f"bị đảo ngày hoặc báo lỗi rồi bị xoá. {example}".strip()])
    if why["distinct"]:
        n, amt = len(why["distinct"]), _amount(why["distinct"])
        add(Beat(f"Thêm {n} chứng từ bị xoá nhầm ở bước xoá trùng.",
                 label="Xoá trùng nhầm", mark="bad", value=spoken(amt),
                 note=f"{n} chứng từ: cùng khách mua hai lần trong một ngày bị coi là trùng."),
            ["Xoá trùng nhầm", n, amt, "", "Bước xoá trùng dùng Ngày + Mã KH; hai chứng từ khác nhau cùng khách, cùng ngày "
             "bị xoá mất một. Chỉ nên xoá trùng theo số chứng từ."])
    if why["other"]:
        n, amt = len(why["other"]), _amount(why["other"])
        add(Beat(f"Còn {n} chứng từ bị mất mà em chưa rõ lý do.", label="Chưa rõ nguyên nhân", mark="warn",
                 value=spoken(amt), note="Anh xem danh sách trong bảng chi tiết."),
            ["Chưa rõ nguyên nhân", n, amt, "", "Có trong file gốc nhưng không có trong bảng query."])
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


def start(bridge, pq, machine_order: Optional[str]) -> list:
    global _PENDING
    _PENDING = None
    if not bridge.available():
        return [Beat("Anh mở file Excel đã lưu trong thư mục dữ liệu tháng rồi gọi lại em.")]
    folder_text = bridge.workbook_folder()
    if not folder_text:
        return [Beat("File Excel này chưa được lưu. Anh lưu nó vào thư mục có các file đơn hàng và file cân đối tài khoản, rồi gọi lại em.")]
    folder = Path(folder_text)
    files = sources.find_order_files(folder)
    if not files:
        return [Beat(f"Em không thấy file đơn hàng CSV nào trong thư mục {folder.name}.")]
    truth = sources.truth(files)
    tbs, ledger = _ledger(folder)
    beats, notes = [], []

    source_note = f"{truth.unique_rows} chứng từ trong {len(files)} file" + (
        f" · đã bỏ {len(truth.duplicates)} chứng từ lặp" if truth.duplicates else "")
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
    fixed_m = m_code.folder_query(str(folder), {f.name: f.date_order or machine_order or sources.DAY_FIRST for f in files},
                                  _typed_columns(files), files[0].header[files[0].columns["doc"]] if "doc" in files[0].columns else None,
                                  folder_parameter=PARAM_NAME)
    if name is None:
        name, formula, origin = QUERY_NAME, None, "clicky"
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

    result = pq.load(name)
    if isinstance(result, ReadFailed):
        _write_check_sheet(bridge, None, truth, ledger, notes)
        return beats + [Beat("Em chưa đọc lại được bảng query, nên chưa có số chênh lệch. Đây là lỗi của Clicky.",
                             _check_ref("A7:E7"), "Chưa đối chiếu được", "warn", "Chưa có số",
                             "Không phải lỗi file của anh. Anh cập nhật Clicky rồi gọi lại em.")]
    if isinstance(result, str):
        _write_check_sheet(bridge, None, truth, ledger, [["Lỗi khi tải query", "", "", "", result[:300]]] + notes)
        beats.append(Beat(f"Excel báo lỗi khi tải query {name}.", _check_ref("A4:E4"), "Query chưa tải được", "bad",
                          "Lỗi tải", result[:140]))
        if formula is None:
            return beats
        _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder)}
        return beats + [_fix_offer()]
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    gap = loaded.amount - (ledger["revenue"] if ledger else truth.amount)
    clean = abs(gap) <= TOLERANCE and len(rows) == truth.unique_rows
    why = diagnose(truth, loaded, machine_order) if not clean else {"date": [], "distinct": [], "other": [], "excel_dups": []}
    cause_beats, cause_rows = _cause_beats(why, machine_order, 0)
    check_sheet = _write_check_sheet(bridge, loaded, truth, ledger, cause_rows + notes, "ok" if clean else "bad")
    ref = f"{_q(check_sheet)}!C7" if check_sheet and ledger else None
    if clean:
        beats.append(Beat(f"Excel khớp sổ cái đến từng đồng: doanh thu {spoken(loaded.amount)}.",
                          ref, "Excel khớp sổ cái", "ok", "0 đ", f"{len(rows)} chứng từ · {spoken(loaded.amount)}"))
        return beats + build_report(bridge, folder, tbs)

    beats.append(Beat(f"Báo cáo trong Excel đang {_gap_vi(gap)} so với sổ cái.", ref, "Excel lệch sổ cái", "bad",
                      _signed(gap), f"Query chỉ giữ {len(rows)}/{truth.unique_rows} chứng từ. Chưa được dùng số này."))
    beats += cause_beats
    _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder)}
    return beats + [_fix_offer()]


# ── Stage 2 ───────────────────────────────────────────────────────────────────

_BACKUP_NOTE = "Bản query cũ vẫn còn trong power_query_backups.json."


def approve(bridge, pq) -> list:
    global _PENDING
    job, _PENDING = _PENDING, None
    if not job:
        return []
    folder = Path(job["folder"])
    pq.add(PARAM_NAME, m_code.parameter_query(str(folder)))
    if not pq.add(job["name"], job["formula"]):
        return [Beat(f"Em không sửa được query {job['name']}. Anh kiểm tra file có đang bị khoá không.")]
    result = pq.load(job["name"])
    if isinstance(result, ReadFailed):
        return [Beat("Em đã sửa query nhưng chưa đọc lại được bảng kết quả. Đây là lỗi của Clicky.",
                     label="Chưa kiểm tra được", mark="warn", note=_BACKUP_NOTE)]
    if isinstance(result, str):
        return [Beat(f"Em đã sửa query nhưng Excel báo lỗi khi tải lại: {result[:200]}. {_BACKUP_NOTE}")]
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    files = sources.find_order_files(folder)
    truth = sources.truth(files)
    tbs, ledger = _ledger(folder)
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
    beats = [Beat("Đã sửa xong. Excel khớp sổ cái đến từng đồng.", ref, "Đã khớp sổ cái", "ok", "0 đ", note)]
    return beats + build_report(bridge, folder, tbs)


def reject() -> list:
    global _PENDING
    if not _PENDING:
        return []
    _PENDING = None
    return [Beat("Em giữ nguyên query. Số trong Excel vẫn lệch sổ cái, anh đừng dùng cho báo cáo nhé.")]


# ── Stage 3: the report ───────────────────────────────────────────────────────

def build_report(bridge, folder: Path, tbs: list) -> list:
    from engine.report_sheet import quote
    from office import report_skill
    if len(tbs) < 2:
        return [Beat("Em chưa thấy đủ hai file cân đối tài khoản, kỳ này và kỳ trước, nên chưa lập báo cáo.")]
    existing = set(bridge.sheet_names())
    for name, rows, _ in tbs[-2:]:
        if name not in existing:
            sheet = bridge.add_sheet(name)
            if sheet:
                bridge.write(f"{quote(sheet)}!A1", [list(r) for r in rows])
    bridge.activate(tbs[-1][0])
    summary, report, names = report_skill.run_report(bridge)
    if report is None:
        return [Beat(summary)]
    pat = report.figures[-1].value
    prev_pat = next((v.previous for v in report.variances if v.key == "pat"), None)
    row = 4 + len(report.figures)
    pat_ref = f"{quote(names['report'])}!C{row}"
    beats = [Beat(f"Em đã lập báo cáo ở sheet {names['report']}, mọi số là công thức trỏ về sổ cái.")]
    overstated = sources.returns_in_revenue(report.current)
    if overstated > 0:
        real = pat - overstated
        change = f" Giảm {spoken(prev_pat - real)} so với kỳ trước." if prev_pat is not None and prev_pat > real else ""
        beats.append(Beat(f"Lợi nhuận thực là {spoken(real)}, không phải {spoken(pat)} như trên báo cáo.",
                          pat_ref, "Lợi nhuận sau thuế thực", "warn", spoken(real),
                          f"{spoken(overstated)} hàng bán bị trả lại đang ghi thẳng vào Nợ TK 511. "
                          f"Cần chuyển sang TK 521 trước khi gửi sếp.{change}"))
    else:
        beats.append(Beat(summary, pat_ref, "Lợi nhuận sau thuế", "", spoken(pat)))
    if report.questions:
        beats.append(Beat(f"Em ghi sẵn {len(report.questions)} câu hỏi cho các bộ phận ở sheet {names['questions']}."))
    return beats


# ── Voice skill glue ──────────────────────────────────────────────────────────

BEAT_GAP_S = 0.5          # breath between two ideas
CLEAR_AFTER_S = 8.0       # the last card fades this long after Clicky stops talking (not when it asks a question)


def shapes_for(box: tuple, beat: Beat) -> list:
    """Overlay shapes for one beat: the range lit with the rest of the screen dimmed, and one card beside it."""
    from ui.card_layout import tone_of
    l, t, r, b = box
    tone = tone_of(beat.mark)
    shapes = [{"kind": "spotlight", "x1": l, "y1": t, "x2": r, "y2": b, "tone": tone}]
    if beat.label or beat.value or beat.note:
        shapes.append({"kind": "card", "anchor": [l, t, r, b], "title": beat.label, "value": beat.value,
                       "note": beat.note, "tone": tone})
    return shapes


async def _locate(manager, ref_text: str):
    from office.cellref import parse_ref
    ref = parse_ref(ref_text)
    try:
        if ref and ref.sheet:
            await asyncio.to_thread(manager._excel.activate, ref.sheet)
        rect = await asyncio.to_thread(manager._excel.screen_rect, ref_text, True)
        box = manager._physical_to_logical_bbox(rect) if rect else None
        if not box:
            log.warning("cannot place %s on screen (Excel rect %s)", ref_text, rect)
        return box
    except Exception as exc:
        log.warning("pointing at %s failed: %s", ref_text, exc)
        return None


async def play(manager, beats: list) -> None:
    """One idea at a time: light up the beat's range, show its card, say its one sentence, pause, next.

    A beat without a range keeps the previous card up, so the screen never goes blank mid-story.
    When the sentence cannot be read aloud (no Vietnamese voice), it stays up long enough to read.
    """
    from ui.card_layout import NO_BUBBLE, reading_seconds
    run = object()
    manager._month_close_run = run
    manager.sig_clear_drawings.emit()
    for i, beat in enumerate(beats):
        if i:
            manager.sig_response_chunk.emit("\n\n")
            if BEAT_GAP_S:
                await asyncio.sleep(BEAT_GAP_S)
        box = await _locate(manager, beat.ref) if beat.ref else None
        if box:
            manager.sig_clear_drawings.emit()
            for shape in shapes_for(box, beat):
                manager.sig_draw.emit(shape)
            l, t, r, b = box
            manager.sig_point_hold.emit(True)
            manager.sig_point_at.emit((l + r) / 2, (t + b) / 2, NO_BUBBLE)
        spoke = await manager._reply_local(beat.text)
        if spoke is False and BEAT_GAP_S:
            await asyncio.sleep(reading_seconds(beat.text))
        if box:
            manager.sig_point_release.emit()
        if beat.choices:
            manager.sig_choices.emit(beat.text, list(beat.choices))
    if beats and not beats[-1].choices:
        def fade():
            if getattr(manager, "_month_close_run", None) is run:
                manager.sig_clear_drawings.emit()
        try:
            asyncio.get_running_loop().call_later(CLEAR_AFTER_S, fade)
        except RuntimeError:
            pass


def _pq(manager):
    from office.power_query import PowerQuery
    return PowerQuery(manager._excel)


async def handle_start(manager, transcript: str) -> str:
    order = machine_date_order()
    beats = await asyncio.to_thread(lambda: start(manager._excel, _pq(manager), order))
    await play(manager, beats)
    return ""


async def handle_approve(manager, transcript: str) -> str:
    beats = await asyncio.to_thread(lambda: approve(manager._excel, _pq(manager)))
    await play(manager, beats)
    return ""


async def handle_reject(manager, transcript: str) -> str:
    await play(manager, reject())
    return ""


SKILLS = [
    {"name": "Báo cáo tháng trọn gói", "trigger": START_TRIGGER, "handler": handle_start,
     "description": "Từ thư mục dữ liệu tháng: tải query đơn hàng, đối chiếu với file gốc và sổ cái, chỉ ra chỗ lệch, "
                    "đề xuất sửa, rồi lập báo cáo tháng."},
    {"name": "Đồng ý bản sửa", "trigger": APPROVE_TRIGGER, "handler": handle_approve, "when": pending,
     "description": "Áp bản sửa query Clicky vừa đề xuất, tải lại và đối chiếu lại."},
    {"name": "Giữ nguyên query", "trigger": REJECT_TRIGGER, "handler": handle_reject, "when": pending,
     "description": "Bỏ bản sửa Clicky vừa đề xuất."},
]
