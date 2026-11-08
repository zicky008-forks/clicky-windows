"""The month's operating costs split by department, one workbook each, for the department to check against what it
knows and answer in the file; then a draft email to each, and a local tracker of who answered what.

Inputs are engine.fpa's Variance (actual against the approved plan by department x cost element) and the cost lines
it was made from. Each department's workbook holds its own lines only: a summary by cost element for the month and
the year to date, the detail lines behind the month, and a 'Phản hồi' column (Đúng / Sai / Cần hỏi) with a note.
The files are new, in their own folder, marked SƠ BỘ; they are opened again after saving and checked against the
figures before they count as written. Amounts are Decimal; nothing here is written by the LLM.

Recipients come from the catalogue's department sheet ('Email', 'CC'). Lines without a department go to accounting
to be assigned (the rule 'Email kế toán', when the catalogue has it). A department without an address still gets its
workbook and a draft with no recipient, and is named, so it is never left out in silence.

Pay is personal data (Nghị định 13/2023): a department's workbook never carries the lines of a sensitive element
(salary, bonus, allowances, social insurance, by default; the catalogue adds more with the rule 'Yếu tố nhạy cảm').
Each such element is one line there, with its amount and line count; the detail stays with HR. The accounting file
for unassigned lines keeps every line.

Each workbook carries 'clicky-opex|<month>|<department>' in its keywords, so the file a department sends back is
known for what it is even when it was renamed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine.fpa import UNASSIGNED, Variance, _month_text
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
REPLIES = ("Đúng", "Sai", "Cần hỏi")
SUMMARY = "Tổng hợp"
DETAIL = "Chi tiết"
STATUS = "SƠ BỘ"
TAG = "clicky-opex"
HELD = "Chi tiết do phòng nhân sự giữ"
# Words that make a cost element personal (pay), in its name or group as printed: tones kept, since folded
# 'luong' is also 'lượng' (chất lượng) and 'thuong' is also 'thương mại'.
SENSITIVE_WORDS = ("lương", "tiền công", "thưởng", "phụ cấp", "bhxh", "bhyt", "bhtn", "kpcđ", "kpcd", "bảo hiểm xã hội",
                   "bảo hiểm y tế", "bảo hiểm thất nghiệp", "kinh phí công đoàn", "chi phí nhân viên", "salary", "wage",
                   "wages", "bonus", "payroll", "allowance")
# The same, for codes and names typed without tones: the start of the folded text, spaces removed.
SENSITIVE_CODES = ("luong", "tienluong", "tiencong", "thuong", "phucap", "bhxh", "bhyt", "bhtn", "kpcd", "salary",
                   "wage", "bonus", "payroll")
SENSITIVE_ACCOUNTS = ("6411", "6421")            # VAS 'chi phí nhân viên' of selling and of administration


@dataclass
class Detail:
    where: str
    element: str
    account: str
    ref: str
    text: str
    amount: Decimal


@dataclass
class DeptPack:
    dept: str                                    # '' for lines without a department
    name: str
    email: str = ""
    cc: str = ""
    month: list = field(default_factory=list)    # fpa.Line
    ytd: list = field(default_factory=list)
    details: list = field(default_factory=list)  # Detail, the month's lines behind the summary

    @property
    def totals(self) -> tuple:
        """(month actual, month plan, year-to-date actual, year-to-date plan)."""
        s = lambda ls, a: sum((getattr(l, a) for l in ls), ZERO)          # noqa: E731
        return s(self.month, "actual"), s(self.month, "plan"), s(self.ytd, "actual"), s(self.ytd, "plan")

    @property
    def file_name(self) -> str:
        code = re.sub(r"[^\w\-]+", "_", self.dept or "chua_gan", flags=re.UNICODE).strip("_") or "chua_gan"
        return f"Chi phi {code}.xlsx"


def split(v: Variance, gl: list, config=None) -> list:
    """One pack per department in the month or the year to date, unassigned last. Detail lines are the month's."""
    depts = getattr(config, "depts", {}) or {}
    packs = {}

    def pack(dept: str) -> DeptPack:
        if dept not in packs:
            info = depts.get(dept, {}) if dept else {}
            email = info.get("email", "") if dept else (config.rule("email", "ke toan", default="") if config else "")
            packs[dept] = DeptPack(dept, info.get("ten") or dept or UNASSIGNED, str(email or "").strip(),
                                   str(info.get("cc", "") or "").strip())
        return packs[dept]

    for l in v.month:
        pack(l.dept).month.append(l)
    for l in v.ytd:
        pack(l.dept).ytd.append(l)
    for e in gl:
        if e.period == v.period and (e.dept in packs):
            pack(e.dept).details.append(Detail(e.where, e.element or (f"TK {e.account}" if e.account else ""),
                                               e.account, e.ref, e.text, e.amount))
    for p in packs.values():
        if p.dept:                                   # accounting (unassigned lines) keeps every line
            p.details = _hold(p.details, config)
        p.details.sort(key=lambda d: (d.element, d.account, d.where))
    return sorted(packs.values(), key=lambda p: (p.dept == "", p.dept))


def _extra(config) -> tuple:
    listed = config.rule("yeu to nhay cam", default="") if config is not None else ""
    return tuple(key(x) for x in re.split(r"[;,\n]", str(listed or "")) if key(x))


def sensitive(element: str, account: str = "", config=None) -> bool:
    """A cost element whose lines name people and their pay: by its name or group (tones kept), its code, an
    account of staff cost, or the catalogue's rule 'Yếu tố nhạy cảm' (codes or words)."""
    import unicodedata
    info = (getattr(config, "elements", {}) or {}).get(element, {}) if config is not None else {}
    printed = [unicodedata.normalize("NFC", str(x or "")).lower() for x in (info.get("ten"), info.get("nhom"))]
    if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t) for t in printed if t for w in SENSITIVE_WORDS):
        return True
    has_tones = lambda t: key(t) != t                                     # noqa: E731
    bare = [key(element).replace(" ", "")] + [key(t).replace(" ", "") for t in printed if t and not has_tones(t)]
    if any(b.startswith(SENSITIVE_CODES) for b in bare if b):
        return True
    extra = _extra(config)
    folded = [key(element)] + [key(t) for t in printed if t]
    if any(w and (w == f or re.search(rf"(^| ){re.escape(w)}( |$)", f)) for w in extra for f in folded if f):
        return True
    return any(a and a.startswith(SENSITIVE_ACCOUNTS) for a in (account, str(info.get("tk", "") or "")))


def _hold(details: list, config) -> list:
    """The lines of each sensitive element folded into one: its amount and how many lines, no reference, no text."""
    out, held = [], {}
    for d in details:
        if sensitive(d.element, d.account, config):
            held.setdefault(d.element, []).append(d)
        else:
            out.append(d)
    for element, ds in held.items():
        accounts = sorted({d.account for d in ds if d.account})
        out.append(Detail("", element, ", ".join(accounts), "", f"{HELD} ({len(ds)} dòng)",
                          sum((d.amount for d in ds), ZERO)))
    return out


def no_address(packs: list) -> list:
    return [p.name for p in packs if not p.email]


def tag(period: str, dept: str) -> str:
    return f"{TAG}|{period}|{dept}"


def read_tag(text) -> Optional[tuple]:
    """(month, department) from a workbook's keywords, or None when it is not one of these files."""
    parts = str(text or "").split("|")
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == TAG else None


# ── The workbook ────────────────────────────────────────────────────────────

def write_pack(p: DeptPack, v: Variance, folder: Path) -> Path:
    """The department's workbook, new in folder; read back and checked before it is returned."""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / p.file_name
    if path.exists():
        raise FileExistsError(f"đã có file {path.name}, em không ghi đè")
    wb = Workbook()
    ws = wb.active
    ws.title = SUMMARY
    month = _month_text(v.period)
    wb.properties.keywords = tag(v.period, p.dept)
    wb.properties.title = f"Chi phí tháng {month} · {p.name}"
    ws.append([f"{STATUS} · Chi phí hoạt động tháng {month} · {p.name}"])
    ws.append([v.basis or "Chưa có kế hoạch để so"])
    ws.append(["Yếu tố chi phí", f"Thực hiện T{month}", f"Kế hoạch T{month}", "Chênh lệch", "%",
               "Thực hiện lũy kế", "Kế hoạch lũy kế", "Chênh lệch lũy kế", "Phản hồi", "Ghi chú"])
    ytd = {l.element: l for l in p.ytd}
    for l in p.month:
        y = ytd.get(l.element)
        ws.append([l.element_name or l.element, l.actual, l.plan, l.variance, l.pct,
                   y.actual if y else ZERO, y.plan if y else ZERO, y.variance if y else ZERO, None, None])
    for el, y in ytd.items():
        if el not in {l.element for l in p.month}:
            ws.append([y.element_name or el, ZERO, ZERO, ZERO, None, y.actual, y.plan, y.variance, None, None])
    m_a, m_p, y_a, y_p = p.totals
    ws.append(["Tổng cộng", m_a, m_p, m_a - m_p, None, y_a, y_p, y_a - y_p, None, None])
    last = ws.max_row
    d = wb.create_sheet(DETAIL)
    d.append([f"{STATUS} · Các dòng chi phí tháng {month} · {p.name}"])
    d.append(["Yếu tố chi phí", "Tài khoản", "Số chứng từ", "Diễn giải", "Số tiền", "Nguồn", "Phản hồi", "Ghi chú"])
    for x in p.details:
        d.append([x.element, x.account, x.ref, x.text, x.amount, x.where, None, None])
    for sheet, col, first, end in ((ws, "I", 4, last - 1), (d, "G", 3, d.max_row)):
        if end >= first:
            dv = DataValidation(type="list", formula1='"' + ",".join(REPLIES) + '"', allow_blank=True)
            sheet.add_data_validation(dv)
            dv.add(f"{col}{first}:{col}{end}")
    from openpyxl.styles import Font
    for sheet, widths in ((ws, (34, 16, 16, 16, 8, 18, 18, 18, 12, 40)), (d, (24, 12, 16, 44, 16, 14, 12, 40))):
        for row in sheet.iter_rows():
            for c in row:
                if isinstance(c.value, Decimal):
                    c.number_format = "#,##0"
        for i, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = w
        sheet["A1"].font = Font(bold=True)
    for c in ws["E"][3:]:
        c.number_format = "0.0"
    for c in ws[last]:
        c.font = Font(bold=True)
    ws.freeze_panes = "B4"
    d.freeze_panes = "A3"
    wb.save(path)
    _check(path, p)
    return path


def _check(path: Path, p: DeptPack) -> None:
    """Open the saved file again: the summary total and the detail lines must be the figures."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb[SUMMARY].iter_rows(values_only=True))
        total = next(r for r in rows if r and r[0] == "Tổng cộng")
        details = [r for r in list(wb[DETAIL].iter_rows(values_only=True))[2:] if r and r[4] is not None]
    finally:
        wb.close()
    m_a = p.totals[0]
    written = sum((Decimal(str(r[4])) for r in details), ZERO)
    if Decimal(str(total[1])) != m_a or len(details) != len(p.details) or written != m_a or \
            written != sum((x.amount for x in p.details), ZERO):
        raise ValueError(f"mở lại file {path.name} không khớp số đã tính")


# ── The draft ───────────────────────────────────────────────────────────────

@dataclass
class Draft:
    to: str
    cc: str
    subject: str
    body: str
    attachment: str


def draft(p: DeptPack, v: Variance, attachment: Path, reply_by: str = "", sender: str = "") -> Draft:
    """The email for one department, in plain words: the month against plan, the lines that moved most, and what to
    do with the file. The body stays on this PC; Clicky only saves it as a draft."""
    month = _month_text(v.period)
    m_a, m_p, _y_a, _y_p = p.totals
    if not p.dept:                                        # lines without a department: accounting assigns them
        lines = ["Chào anh/chị kế toán,", "",
                 f"Tháng {month} có {len(p.details)} dòng chi phí chưa gán bộ phận, tổng {fmt(m_a)} đ (bản {STATUS}).",
                 "Trong file đính kèm, anh/chị ghi giúp em bộ phận đúng của từng dòng ở cột Ghi chú, rồi trả lời thư "
                 "này kèm file."]
        if reply_by:
            lines.append(f"Anh/chị phản hồi giúp em trước ngày {reply_by}.")
        lines += ["", "Cảm ơn anh/chị.", sender or ""]
        return Draft(p.email, p.cc, f"[{STATUS}] Chi phí tháng {month}: {len(p.details)} dòng chưa gán bộ phận",
                     "\n".join(lines).rstrip() + "\n", str(attachment))
    moved = sorted((l for l in p.month if l.plan or l.actual), key=lambda l: (-abs(l.variance), l.element))[:3]
    moved = [l for l in moved if l.variance]
    lines = [f"Chào anh/chị phòng {p.name},", "",
             f"Em gửi chi phí hoạt động tháng {month} của phòng (bản {STATUS}) để anh/chị kiểm tra giúp.",
             f"Tháng {month}: thực hiện {fmt(m_a)} đ, kế hoạch {fmt(m_p)} đ, chênh lệch {fmt(m_a - m_p)} đ."
             if v.plan is not None else f"Tháng {month}: thực hiện {fmt(m_a)} đ (chưa có kế hoạch để so)."]
    if moved and v.plan is not None:
        lines += ["", "Các khoản chênh nhiều nhất:"]
        lines += [f"- {l.element_name or l.element}: thực hiện {fmt(l.actual)} đ, kế hoạch {fmt(l.plan)} đ, "
                  f"chênh {fmt(l.variance)} đ" for l in moved]
    lines += ["", "Trong file đính kèm, anh/chị chọn ở cột Phản hồi: Đúng, Sai hoặc Cần hỏi cho từng dòng, ghi chú nếu "
                  "có, rồi trả lời thư này kèm file."]
    if reply_by:
        lines.append(f"Anh/chị phản hồi giúp em trước ngày {reply_by}.")
    lines += ["", "Cảm ơn anh/chị.", sender or ""]
    return Draft(p.email, p.cc, f"[{STATUS}] Chi phí tháng {month} - {p.name}: nhờ kiểm tra",
                 "\n".join(lines).rstrip() + "\n", str(attachment))
