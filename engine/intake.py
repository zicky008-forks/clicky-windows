"""The receipt shown before anything is computed: "Em đọc được những gì".

One line per input file (kind, period, unit, size, the check it passed) and the list of
everything left out, each with its file, amount and reason. Nothing read is ever dropped
silently: for every file, what was used plus what was left out equals what the file holds.

Pure code, no Excel: the receipt is built from the files on disk, before the workbook is touched.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine import roles as roles_mod, sources
from engine.monthly_report import fmt, spoken
from engine.trial_balance import ZERO, _is_total, fold, parse_amount

TOLERANCE = Decimal(1)
DATA_SUFFIXES = {".csv", ".xlsx", ".xlsm", ".xls", ".txt", ".m", ".pq", ".pdf", ".png", ".jpg", ".jpeg", ".docx", ".doc",
                 ".eml", ".msg"}
QUERY_SUFFIXES = (".txt", ".m", ".pq")
ORIGINAL_TAG = "_ban-goc"            # Clicky's copy of the workbook before its first change


@dataclass
class FileLine:
    name: str
    kind: str                        # "Sổ cân đối T10", "Bán hàng"
    period: str = ""                 # "01/10/2026–31/10/2026"
    unit: str = ""                   # "đồng"
    rows: int = 0                    # accounts for a trial balance, orders for a sales file
    rows_label: str = ""             # "186 tài khoản", "312 đơn"
    checks: str = ""                 # "khớp Nợ = Có"
    ok: bool = True
    total: Decimal = ZERO            # what the file holds (Phát sinh Nợ, or the sum of its order rows)
    used: Decimal = ZERO
    dropped: Decimal = ZERO
    shown: str = ""                  # the file's name on disk when `name` is a sheet-style stem (TB_T10 → TB_T10.xlsx)


@dataclass
class Dropped:
    what: str                        # short: "Dòng Tổng cộng", "Đơn HCM3 xuất 2 lần"
    file: str
    amount: Optional[Decimal]        # None for a whole file or a total line (its figure is in the reason)
    reason: str
    rows: int = 1


@dataclass
class Intake:
    files: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    orders_total: Decimal = ZERO
    orders_rows: int = 0
    branches: int = 0
    revenue_ok: Optional[bool] = None
    revenue_note: str = ""
    roles: dict = field(default_factory=dict)           # file name → role (engine.roles)

    @property
    def balanced(self) -> bool:
        """Every file: used + left out = what it holds; and the listed amounts add up to what was left out."""
        per_file = all(abs(f.total - f.used - f.dropped) <= TOLERANCE for f in self.files)
        listed = sum((d.amount for d in self.dropped if d.amount is not None), ZERO)
        return per_file and abs(listed - sum((f.dropped for f in self.files), ZERO)) <= TOLERANCE


def _period_text(period: Optional[tuple]) -> str:
    if not period or not period[0]:
        return ""
    y, m = period
    return f"01/{m:02d}/{y}–{calendar.monthrange(y, m)[1]:02d}/{m:02d}/{y}"


def _unit(rows) -> str:
    """'đồng' from a title line such as 'Đơn vị tính: đồng' / 'ĐVT: VND'; '' when the file does not say."""
    for row in rows[:10]:
        for c in row or []:
            t = fold(c)
            m = re.search(r"(don vi tinh|dvt|don vi)\s*[:\-]?\s*([a-z ]+)", t)
            if m and m.group(2).strip():
                word = m.group(2).strip()
                if word.startswith(("dong", "vnd")):
                    return "đồng"
                if word.startswith("nghin") or word.startswith("ngan"):
                    return "nghìn đồng"
                if word.startswith("trieu"):
                    return "triệu đồng"
                return str(c).split(":")[-1].strip()
    return ""


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def _amount(value) -> Decimal:
    try:
        return parse_amount(value)[0]
    except ValueError:
        return ZERO


def _tb_line(name: str, rows, tb, previous=None) -> tuple:
    period = sources.period_of(rows)
    month = f" T{period[1]:02d}" if period else ""
    leaves = tb.leaves
    dr = sum((l.dr for l in leaves), ZERO)
    cr = sum((l.cr for l in leaves), ZERO)
    balanced = abs(dr - cr) <= TOLERANCE
    dr_col = tb.columns.get("dr")
    dropped, items = ZERO, []
    for row_no, account, moved in tb.skipped:
        raw = rows[row_no - 1] if row_no - 1 < len(rows) else []
        amount = abs(_amount(_cell(raw, dr_col - 1))) if dr_col else ZERO
        dropped += amount
        items.append(Dropped(f"Dòng {row_no} mã TK \"{account}\"", name, amount,
                             f"mã tài khoản không đọc được, {spoken(moved)} chưa vào báo cáo"))
    # The sheet's own total line ("Tổng cộng") is not an account: it is set aside, and kept as a check.
    cr_col = tb.columns.get("cr")
    for i in range(tb.header_row, len(rows)):
        raw = rows[i] or []
        label = next((str(c).strip() for c in raw[:3] if isinstance(c, str) and c.strip() and _is_total(c)), None)
        if not label:
            continue
        t_dr = abs(_amount(_cell(raw, dr_col - 1))) if dr_col else ZERO
        t_cr = abs(_amount(_cell(raw, cr_col - 1))) if cr_col else ZERO
        if not (t_dr or t_cr):
            continue
        same = abs(t_dr - dr) <= TOLERANCE and abs(t_cr - cr) <= TOLERANCE
        items.append(Dropped(f"Dòng \"{label}\" cuối sổ (dòng {i + 1})", name, None,
                             f"không phải tài khoản, ghi phát sinh Nợ {spoken(t_dr)} / Có {spoken(t_cr)}; dùng làm mốc kiểm: "
                             + ("khớp tổng các tài khoản chi tiết" if same else "lệch tổng các tài khoản chi tiết")))
    new = []
    if previous is not None:
        before = {l.account for l in previous.leaves}
        new = [l.account for l in leaves if l.account not in before]
    checks = "khớp Nợ = Có" if balanced else f"Nợ − Có lệch {fmt(dr - cr, sign=True)}"
    if new:
        checks += f" · {len(new)} tài khoản mới so với kỳ trước"
    line = FileLine(name=name, kind=f"Sổ cân đối{month}", period=_period_text(period), unit=_unit(rows),
                    rows=len(tb.lines), rows_label=f"{len(tb.lines)} tài khoản", checks=checks, ok=balanced,
                    total=dr + dropped, used=dr, dropped=dropped)
    return line, items


def _order_lines(files: list, truth, period: Optional[tuple]) -> tuple:
    lines, items = [], []
    for f in files:
        a = f.columns.get("amount")
        total = sum((_amount(_cell(r, a)) for r in f.rows), ZERO)
        used_rows, used = truth.by_file.get(f.name, (0, ZERO))
        dups = [(doc, amt) for doc, name, amt in truth.duplicates if name == f.name]
        outside = [(doc, d, amt) for doc, name, d, amt in truth.outside if name == f.name]
        dropped = sum((amt for _, amt in dups), ZERO) + sum((amt for *_, amt in outside), ZERO)
        for doc, amt in dups:
            items.append(Dropped(f"Đơn {doc} bị xuất 2 lần", f.name, amt, "trùng hệt một dòng khác, bỏ 1 bản"))
        for doc, d, amt in outside:
            items.append(Dropped(f"Đơn {doc or '(không số)'} ngày {d:%d/%m/%Y}", f.name, amt,
                                 f"ngoài kỳ {period[1]:02d}/{period[0]} của sổ cái" if period else "ngoài kỳ"))
        for r in f.footers:
            label = next((str(c).strip() for c in r if str(c or "").strip() and not re.fullmatch(r"[\d.,\s-]+", str(c))),
                         "Dòng không có ngày")
            blank = not str(_cell(r, a) or "").strip()
            value = _amount(_cell(r, a))
            check = ("khớp tổng các dòng" if abs(value - total) <= TOLERANCE else f"lệch tổng các dòng {spoken(value - total)}")
            items.append(Dropped(f"Dòng \"{label}\" cuối file", f.name, None,
                                 "dòng tổng cuối file, không phải đơn hàng" +
                                 ("" if blank else f", ghi {spoken(value)}, dùng làm mốc kiểm: {check}")))
        lines.append(FileLine(name=f.name, kind="Bán hàng", period=_period_text(period), unit="đồng",
                              rows=used_rows, rows_label=f"{used_rows} đơn",
                              checks=f"tổng {spoken(used)}", total=total, used=used, dropped=dropped))
    return lines, items


_LEDGER_AMOUNT = ("sotien", "giatri", "psno", "psco", "phatsinh", "debit", "credit", "amount", "thanhtien")


def _csv_why(p: Path) -> str:
    """Why a CSV was not read, true to what its header holds (never 'no date column' when it has one)."""
    import csv as _csv
    try:
        with open(p, newline="", encoding="utf-8-sig") as f:
            header = next(_csv.reader(f), [])
    except (OSError, UnicodeDecodeError):
        return "em chưa mở được file CSV này"
    cols = sources.find_columns(header)
    keys = {sources._key(h) for h in header}
    has_date = "date" in cols or any(k.startswith(("ngay", "date")) for k in keys)
    # Wider than the order-file amount names on purpose: only used to say what the file is,
    # never to read a ledger detail as sales.
    has_amount = "amount" in cols or any(k.startswith(_LEDGER_AMOUNT) for k in keys)
    if not (has_date and has_amount):
        return "không có cột ngày và cột số tiền, không phải file đơn hàng"
    from engine.trial_balance import fold
    text = re.sub(r"[^a-z0-9]", "", fold(p.stem + " " + " ".join(str(h) for h in header)))
    kind = "sổ chi tiết chi phí" if any(w in text for w in ("chiphi", "cost", "expense", "tkno", "tkco", "sochitiet")) \
        else "file có cột ngày và số tiền nhưng không phải đơn bán hàng"
    return f"{kind}; bản này chưa đọc loại file này, em không lấy số từ file này"


def _unused_files(folder: Path, used: set, workbook: Optional[str], roles: dict) -> list:
    """Every data-looking file in the folder (and one level down) that Clicky did not read, with why."""
    out = []
    paths = sorted([p for p in folder.glob("*") if p.is_file()] + [p for p in folder.glob("*/*") if p.is_file()],
                   key=lambda p: str(p).lower())
    for p in paths:
        if p.name.startswith("~$") or p.name in used or p.name == workbook or ORIGINAL_TAG in p.stem:
            continue
        suffix = p.suffix.lower()
        role = roles_mod.of_file(p.name)
        roles[p.name] = role
        if role == roles_mod.PLAN:
            why = "vai trò Kế hoạch; báo cáo tháng này chưa so với kế hoạch, em không lấy số từ file này"
        elif role == roles_mod.REASON:
            why = "vai trò Lý do (email, văn bản): chỉ để hiểu nguyên nhân, em không lấy số từ file này"
        elif suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic"):
            why = "ảnh, vai trò Ghi chú: em không bao giờ lấy số từ ảnh"
        elif suffix == ".xls":
            why = "định dạng .xls cũ, em chưa đọc được; anh mở và lưu lại thành .xlsx"
        elif suffix == ".csv":
            why = _csv_why(p)
        elif suffix in (".xlsx", ".xlsm"):
            why = "không phải sổ cân đối tài khoản"
        elif suffix not in DATA_SUFFIXES:
            why = "không phải file số liệu (chương trình, lối tắt…), em không mở file này"
        else:
            why = "không phải file số liệu, em không lấy số từ file này"
        out.append(Dropped(f"File {p.name}", p.name, None, why, rows=0))
    return out


def query_files(folder: Path) -> list:
    """Power Query M saved as text in the folder (the query an AI or a colleague wrote): read to be checked."""
    out = []
    for p in sorted(q for suffix in QUERY_SUFFIXES for q in folder.glob(f"*{suffix}")):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if text.lstrip().lower().startswith("let") and re.search(r"Folder\.Files|Csv\.Document", text):
            out.append(p.name)
    return out


def build(folder: Path, files: list, tbs: list, truth, ledger: dict, workbook: Optional[str] = None,
          set_aside: Optional[list] = None) -> Intake:
    """tbs: [(name, rows, tb)] oldest first, one per period; set_aside: [(name, why)] other copies of a period."""
    intake = Intake()
    period = sources.period_of(tbs[-1][1]) if tbs else None
    picked = tbs[-2:]
    for i, (name, rows, tb) in enumerate(reversed(picked)):
        previous = picked[0][2] if i == 0 and len(picked) == 2 else None
        line, items = _tb_line(name, rows, tb, previous)
        on_disk = next(iter(sorted(folder.glob(f"{name}.xls*"))), None)
        line.shown = on_disk.name if on_disk else ""
        intake.files.append(line)
        intake.dropped += items
    for name, _rows, _tb in tbs[:-2]:
        intake.dropped.append(Dropped(f"Sổ cân đối {name}", name, None,
                                      "kỳ cũ hơn, báo cáo chỉ cần kỳ này và kỳ trước", rows=0))
    for name, why in set_aside or []:
        intake.dropped.append(Dropped(f"Sổ cân đối {name}", name, None, why, rows=0))
    for name, _rows, _tb in tbs:
        intake.roles[name] = roles_mod.OFFICIAL
    for name, _why in set_aside or []:
        intake.roles[name] = roles_mod.OFFICIAL
    for f in files:
        intake.roles[f.name] = roles_mod.DETAIL
    lines, items = _order_lines(files, truth, period)
    intake.files += lines
    intake.dropped += items
    intake.orders_total, intake.orders_rows = truth.amount, truth.unique_rows
    branches = set()
    for f in files:
        b = f.columns.get("branch")
        branches |= {str(_cell(r, b)).strip() for r in f.rows if b is not None and str(_cell(r, b) or "").strip()}
    intake.branches = len(branches) or len(files)
    if ledger:
        acct = f"Có TK {ledger['rev_acct']}" + (" − Nợ TK 521" if ledger.get("net") else "")
        diff = truth.amount - ledger["revenue"]
        intake.revenue_ok = abs(diff) <= TOLERANCE
        intake.revenue_note = f"khớp {acct}" if intake.revenue_ok else f"lệch {acct} {spoken(diff)}"
    for name in query_files(folder):
        intake.roles[name] = roles_mod.NOTE
        intake.files.append(FileLine(name=name, kind="Query Power Query", rows=0, rows_label="",
                                     checks="em dùng để kiểm tra query, không lấy số từ file này"))
    stems = [n for n, _, _ in tbs] + [n for n, _ in set_aside or []]
    used_names = set(query_files(folder)) | {f.path.name for f in files} | {p.name for name in stems
                                                                           for p in folder.glob(f"{name}.xls*")}
    intake.dropped += _unused_files(folder, used_names, workbook, intake.roles)
    return intake


def card(intake: Intake) -> dict:
    """What the receipt card shows: a few grouped lines, then what was left out. Plain data for the UI."""
    rows = []
    for f in intake.files:
        if f.kind.startswith("Sổ cân đối"):
            detail = " · ".join(x for x in (f"Kỳ {f.period}" if f.period else "", f"đơn vị {f.unit}" if f.unit else "",
                                            f.checks) if x)
            rows.append({"ok": f.ok, "title": f"{f.shown or f.name} · {f.kind}", "count": f.rows_label, "detail": detail,
                         "role": roles_mod.OFFICIAL})
    sales = [f for f in intake.files if f.kind == "Bán hàng"]
    if sales:
        where = f"{intake.branches} chi nhánh" if intake.branches > 1 else f"{len(sales)} file"
        detail = f"Tổng {spoken(intake.orders_total)}" + (f", {intake.revenue_note}" if intake.revenue_note else "")
        rows.append({"ok": intake.revenue_ok is not False, "title": f"Bán hàng · {where}",
                     "count": f"{intake.orders_rows} đơn", "detail": detail, "role": roles_mod.DETAIL})
    for f in intake.files:
        if f.kind == "Query Power Query":
            rows.append({"ok": True, "title": f"{f.name} · {f.kind}", "count": "", "detail": f.checks,
                         "role": roles_mod.NOTE})
    left = summarize_dropped(intake.dropped)
    return {"eyebrow": "TRƯỚC KHI TÍNH", "title": "Em đọc được những gì", "rows": rows,
            "left_out": left, "left_out_title": "Em đã bỏ ra ngoài" if left else "Em không bỏ dòng nào"}


def summarize_dropped(items: list, limit: int = 4) -> list:
    """Short phrases for the card; the full list with every amount goes to the Biên bản sheet."""
    groups = {}
    for d in items:
        key = ("dup" if d.what.startswith("Đơn") and "xuất 2 lần" in d.what else
               "out" if d.what.startswith("Đơn") else
               "foot" if d.what.startswith("Dòng \"") and "cuối file" in d.what else
               "tbfoot" if d.what.startswith("Dòng \"") else
               "tb" if d.what.startswith("Dòng ") else "file")
        groups.setdefault(key, []).append(d)
    out = []
    for key, ds in groups.items():
        files = sorted({d.file for d in ds})
        where = files[0] if len(files) == 1 else f"{len(files)} file"
        total = sum((d.amount for d in ds if d.amount is not None), ZERO)
        if key == "dup":
            out.append(f"{len(ds)} đơn trùng ở {where}: xuất 2 lần, bỏ 1 bản ({spoken(total)})")
        elif key == "out":
            out.append(f"{len(ds)} đơn ngoài kỳ ở {where} ({spoken(total)})")
        elif key == "foot":
            out.append(f"{len(ds)} dòng \"Tổng cộng\" cuối file (dùng làm mốc kiểm)")
        elif key == "tbfoot":
            out.append(f"{len(ds)} dòng \"Tổng cộng\" cuối sổ cân đối (dùng làm mốc kiểm)")
        elif key == "tb":
            out.append(f"{len(ds)} dòng sổ cân đối có mã TK không đọc được ({spoken(total)})")
        else:
            out.append(f"{len(ds)} file không dùng: " + ", ".join(d.file for d in ds[:2]) + ("…" if len(ds) > 2 else ""))
    return out[:limit] + ([f"và {len(out) - limit} nhóm khác, xem sheet Bien ban Clicky"] if len(out) > limit else [])


def log_fields(intake: Intake, text: str) -> dict:
    """The scene-log record the rehearsal robot checks (tools/dien_tap/beta_checks.py)."""
    return {"files": [{"name": f.name, "kind": f.kind, "period": f.period, "unit": f.unit, "rows": f.rows,
                       "checks": f.checks, "total": int(f.total), "used": int(f.used), "dropped": int(f.dropped)}
                      for f in intake.files],
            "dropped": [{"what": d.what, "amount": None if d.amount is None else int(d.amount), "reason": d.reason,
                         "file": d.file} for d in intake.dropped],
            "text": text}


def dropped_rows(intake: Intake) -> list:
    """Every item left out, for the Biên bản sheet: [what, file, amount, reason]."""
    return [[d.what, d.file, d.amount if d.amount is not None else "", d.reason] for d in intake.dropped]


def coverage_rows(intake: Intake) -> list:
    """Per file: [file, what it holds, used, left out, check] — used + left out must equal what it holds."""
    out = []
    for f in intake.files:
        ok = abs(f.total - f.used - f.dropped) <= TOLERANCE
        out.append([f.name, f.total, f.used, f.dropped, "✓ khớp" if ok else f"lệch {fmt(f.total - f.used - f.dropped)}"])
    return out
