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
APPROVE_TRIGGER = r"^\s*(đồng ý|dong y|ok|oke|okay|được|duoc|sửa đi|sua di|làm đi|lam di|yes)\b(\s+\S+){0,3}\s*[.!]?\s*$"
REJECT_TRIGGER = r"^\s*(thôi|thoi|không|khong|giữ nguyên|giu nguyen|no)\b(\s+\S+){0,2}\s*[.!]?\s*$"

_PENDING: Optional[dict] = None


@dataclass
class Beat:
    text: str
    ref: Optional[str] = None       # cell or range to point at while speaking, e.g. 'Doi chieu'!C7
    label: str = ""


def pending() -> bool:
    return _PENDING is not None


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


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


def _write_check_sheet(bridge, loaded: Optional[Loaded], truth, ledger: dict) -> Optional[str]:
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
        ["Chênh lệch Excel − sổ cái", "", "=C4-C6" if ledger else "", "=D4-D6" if ledger and "cost" in (loaded.columns if loaded else {}) else "",
         "Phải bằng 0"],
    ]
    bridge.write(f"{_q(sheet)}!A1", grid)
    bridge.format_numbers(f"{_q(sheet)}!B4:D7")
    return sheet


def _ledger(folder: Path) -> tuple:
    from engine.cli import read_rows
    tbs = sources.find_trial_balances(folder, read_rows)
    if not tbs:
        return tbs, {}
    name, _, tb = tbs[-1]
    acct = sources.revenue_account(tb)
    return tbs, {"name": name, "rev_acct": acct, "revenue": sources.ledger_movement(tb, acct, "cr"),
                 "cost": sources.ledger_movement(tb, "632", "dr")}


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
    beats = []

    kinds = Counter(f.date_order for f in files)
    detail = "; ".join(f"{f.name} ghi ngày kiểu {_order_vi(f.date_order)}" for f in files) if len(kinds) > 1 else \
        f"cả {len(files)} file đều ghi ngày kiểu {_order_vi(files[0].date_order)}"
    beats.append(Beat(
        f"Em đọc {len(files)} file đơn hàng: {truth.rows} dòng, {detail}. "
        f"Theo file gốc có {truth.unique_rows} chứng từ, doanh thu {spoken(truth.amount)}"
        + (f", trong đó {len(truth.duplicates)} chứng từ bị xuất hai lần." if truth.duplicates else ".")))
    if ledger:
        same = abs(truth.amount - ledger["revenue"]) <= TOLERANCE
        beats.append(Beat(
            f"Sổ cái {ledger['name']} có phát sinh Có TK {ledger['rev_acct']} {spoken(ledger['revenue'])}: "
            + ("khớp file gốc đến từng đồng." if same else
               f"file gốc {_gap_vi(truth.amount - ledger['revenue'])} so với sổ cái, anh cần xem lại file xuất.")))

    name, formula, origin = _query_to_check(pq, folder)
    fixed_m = m_code.folder_query(str(folder), {f.name: f.date_order or machine_order or sources.DAY_FIRST for f in files},
                                  _typed_columns(files), files[0].header[files[0].columns["doc"]] if "doc" in files[0].columns else None,
                                  folder_parameter=PARAM_NAME)
    if name is None:
        name, formula, origin = QUERY_NAME, None, "clicky"
        beats.append(Beat("Trong file chưa có query đơn hàng, em tạo query mới đọc đúng định dạng từng file."))
    elif origin != "workbook":
        formula, moved = _point_at_this_folder(formula, files)
        pq.add(name, formula)
        beats.append(Beat(f"Em nạp query có sẵn trong file {origin} vào Excel với tên {name} để kiểm tra."
                          + (f" Đường dẫn trong query là {moved}, máy này không có, nên em đổi sang thư mục đơn hàng ở đây "
                             "để kiểm tra đúng các bước còn lại." if moved else "")))

    if formula is not None:
        findings = m_code.lint(name, formula)
        if findings:
            worst = sorted(findings, key=lambda f: (_LINT_ORDER.index(f.code) if f.code in _LINT_ORDER else 99,
                                                    f.severity != "error"))[:3]
            beats.append(Beat(f"Query {name} có {len(findings)} bước đáng lo: "
                              + " ".join(f"Bước {f.step}: {f.message}" for f in worst)))
    else:
        pq.add(PARAM_NAME, m_code.parameter_query(str(folder)))
        pq.add(name, fixed_m)

    result = pq.load(name)
    if isinstance(result, str):
        _write_check_sheet(bridge, None, truth, ledger)
        beats.append(Beat(f"Excel báo lỗi khi tải query {name}: {result[:200]}"))
        if formula is None:
            return beats
        _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder)}
        beats.append(Beat("Em có bản query mới đọc đúng định dạng từng file và xoá trùng theo số chứng từ. "
                          "Anh nói đồng ý để em thay vào và tải lại, hoặc thôi để giữ nguyên."))
        return beats
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    check_sheet = _write_check_sheet(bridge, loaded, truth, ledger)
    gap = loaded.amount - (ledger["revenue"] if ledger else truth.amount)
    ref = f"{_q(check_sheet)}!C7" if check_sheet and ledger else None
    if abs(gap) <= TOLERANCE and len(rows) == truth.unique_rows:
        beats.append(Beat(f"Query giữ {len(rows)} dòng, doanh thu {spoken(loaded.amount)}, khớp sổ cái. Em đã kiểm tra lại, số đúng.",
                          ref, "Khớp ✓"))
        return beats + build_report(bridge, folder, tbs)

    beats.append(Beat(f"Query trong Excel chỉ giữ {len(rows)} dòng, doanh thu {spoken(loaded.amount)}, "
                      f"{_gap_vi(gap)} so với sổ cái. Chưa được dùng số này.", ref, _gap_vi(gap).capitalize()))
    why = diagnose(truth, loaded, machine_order)
    reasons = []
    if why["date"]:
        reasons.append(f"{len(why['date'])} chứng từ, {spoken(_amount(why['date']))}, mất vì máy này đọc ngày kiểu "
                       f"{_order_vi(machine_order)} nên file ghi kiểu khác bị đảo ngày hoặc báo lỗi rồi bị xoá")
    if why["distinct"]:
        reasons.append(f"{len(why['distinct'])} chứng từ, {spoken(_amount(why['distinct']))}, bị xoá nhầm ở bước xoá trùng "
                       "vì cùng khách mua hai lần trong một ngày")
    if why["other"]:
        reasons.append(f"{len(why['other'])} chứng từ, {spoken(_amount(why['other']))}, mất vì lý do khác em chưa xác định")
    if why["excel_dups"]:
        reasons.append(f"{len(why['excel_dups'])} chứng từ bị đếm hai lần")
    if reasons:
        beats.append(Beat("Nguyên nhân: " + "; ".join(reasons) + "."))
    _PENDING = {"stage": "fix_query", "name": name, "formula": fixed_m, "folder": str(folder)}
    beats.append(Beat("Em đề xuất sửa query: đọc ngày theo đúng kiểu của từng file, xoá trùng chỉ theo số chứng từ, "
                      "không xoá dòng lỗi, và đưa đường dẫn thư mục vào một tham số. Bản cũ được lưu lại. "
                      "Anh nói đồng ý để em sửa, hoặc thôi để giữ nguyên."))
    return beats


# ── Stage 2 ───────────────────────────────────────────────────────────────────

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
    if isinstance(result, str):
        return [Beat(f"Em đã sửa query nhưng Excel báo lỗi khi tải lại: {result[:200]}. Bản cũ vẫn còn trong power_query_backups.json.")]
    sheet, header, rows = result
    loaded = Loaded(sheet, header, rows, sources.find_columns(header))
    files = sources.find_order_files(folder)
    truth = sources.truth(files)
    tbs, ledger = _ledger(folder)
    check_sheet = _write_check_sheet(bridge, loaded, truth, ledger)
    target = ledger["revenue"] if ledger else truth.amount
    gap = loaded.amount - target
    ref = f"{_q(check_sheet)}!C7" if check_sheet and ledger else None
    if abs(gap) > TOLERANCE:
        return [Beat(f"Em đã sửa và tải lại: query còn {len(rows)} dòng, {spoken(loaded.amount)}, vẫn {_gap_vi(gap)} so với sổ cái. "
                     "Em dừng ở đây để anh xem, chưa lập báo cáo.", ref, _gap_vi(gap).capitalize())]
    cost_ok = not ledger or "cost" not in loaded.columns or abs(loaded.cost - ledger["cost"]) <= TOLERANCE
    beats = [Beat(f"Đã sửa và đọc lại: query giữ {len(rows)} chứng từ, doanh thu {spoken(loaded.amount)}"
                  + (", giá vốn khớp Nợ TK 632" if cost_ok and ledger and "cost" in loaded.columns else "")
                  + ". Khớp sổ cái đến từng đồng.", ref, "Khớp ✓")]
    return beats + build_report(bridge, folder, tbs)


def reject() -> list:
    global _PENDING
    if not _PENDING:
        return []
    _PENDING = None
    return [Beat("Em giữ nguyên query. Số trong Excel vẫn đang lệch sổ cái, anh đừng dùng cho báo cáo nhé.")]


# ── Stage 3: the report ───────────────────────────────────────────────────────

def build_report(bridge, folder: Path, tbs: list) -> list:
    from engine.report_sheet import quote
    from office import report_skill
    if len(tbs) < 2:
        return [Beat("Em chưa thấy đủ hai file cân đối tài khoản, kỳ này và kỳ trước, trong thư mục nên chưa lập báo cáo.")]
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
        change = f", giảm {spoken(prev_pat - real)} so với kỳ trước" if prev_pat is not None and prev_pat > real else ""
        beats.append(Beat(f"Lợi nhuận sau thuế trên báo cáo là {spoken(pat)}, nhưng có {spoken(overstated)} hàng bán bị trả lại "
                          f"ghi thẳng vào Nợ TK 511 nên doanh thu và lợi nhuận đang bị thổi lên. Lợi nhuận thực là {spoken(real)}{change}. "
                          "Anh cần kế toán chuyển khoản này sang TK 521 trước khi gửi sếp.",
                          pat_ref, f"Thực: {spoken(real)}"))
    else:
        beats.append(Beat(summary, pat_ref, "LNST"))
    if report.questions:
        beats.append(Beat(f"Em đã ghi {len(report.questions)} câu hỏi cần các bộ phận trả lời ở sheet {names['questions']}, "
                          "trước khi giải thích nguyên nhân với sếp."))
    return beats


# ── Voice skill glue ──────────────────────────────────────────────────────────

async def play(manager, beats: list) -> None:
    """Speak each beat, flying the pointer to its cell first; the sheet is activated so the cell is on screen."""
    from office.cellref import parse_ref
    for beat in beats:
        held = False
        if beat.ref:
            ref = parse_ref(beat.ref)
            try:
                if ref and ref.sheet:
                    await asyncio.to_thread(manager._excel.activate, ref.sheet)
                rect = await asyncio.to_thread(manager._excel.screen_rect, beat.ref, True)
                box = manager._physical_to_logical_bbox(rect) if rect else None
                if box:
                    l, t, r, b = box
                    manager.sig_point_hold.emit(True)
                    manager.sig_point_at.emit((l + r) / 2, (t + b) / 2, beat.label or beat.ref)
                    held = True
            except Exception as exc:
                log.debug("pointing at %s failed: %s", beat.ref, exc)
        await manager._reply_local(beat.text)
        if held:
            manager.sig_point_release.emit()


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
