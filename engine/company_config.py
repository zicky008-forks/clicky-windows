"""The company's own FP&A configuration: a folder of YAML files the user keeps on the company PC (or in a private
repository), never in this one. Nothing about any company is built in here; this module only reads the files.

Where: the folder in CLICKY_CONFIG_DIR, else 'cau-hinh' in Clicky's data folder. Every file is optional:

- kiem_tra.yaml: `kiem_tra:` the monthly checks (ma, ten, dung_sai / dung_sai_trieu / dung_sai_tan, chan: true =
  stop the next step when it fails, nguong: {pct, ty | trieu | dong} for the variance threshold, clicky: which of
  Clicky's checks it is, one of KINDS, else matched by its name), and
  `bay_ke_toan:` the accounting traps (ma, ten, dau_hieu, hanh_dong), said as "nghi vấn", never fixed;
- lich_thang.yaml: `buoc:` the month's steps (id, ten, ngay "YYYY-MM-DD" or "start/end", han, kiem, gui, duyet);
- anh_xa.yaml: the account segments, a mapping that holds `doan:` (one row per segment: so, ten, vai_tro) and
  `phan_cach` (the delimiter), and any `khong_tim_thay:` mapping, whose values are what the ledger prints for a code
  the mapping lacks (counted by the mapping check);
- cau_hoi.yaml: `cau_hoi:` the question bank (id, nhom, ai_hoi, cau, tra, tra_loi), kept for the Q&A step.

A value that still says TODO, and a due date on a weekend, are listed as open points for the user to confirm. A file
that cannot be read is said in one plain sentence; the others still load.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

ENV = "CLICKY_CONFIG_DIR"
# The checks Clicky runs that a company check can name with `clicky:` (else they are matched by its name).
KINDS = {"chi_phi_khop_so": "chi phí hoạt động (641/642) theo sổ chi tiết khớp sổ cân đối",
         "anh_xa_du": "không còn dòng chi phí mang giá trị thiếu mã",
         "luy_ke_khop_so": "chi phí lũy kế theo sổ chi tiết khớp sổ cân đối",
         "chenh_lech_giai_trinh": "mọi chênh lệch trên ngưỡng có nguyên nhân hoặc được hỏi"}
FILES = ("kiem_tra.yaml", "lich_thang.yaml", "anh_xa.yaml", "cau_hoi.yaml")
WEEKDAY = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")
# vai_tro words in the YAML -> engine.segment_gl roles
ROLE = {"site": "site", "company": "site", "cong_ty": "site", "phap_nhan": "site",
        "natural_account": "account", "account": "account", "tai_khoan": "account",
        "cost_element": "element", "yeu_to": "element", "yeu_to_chi_phi": "element",
        "phong_ban": "dept", "bo_phan": "dept", "dept": "dept", "cost_center": "dept",
        "khoi": "division", "division": "division", "kenh": "channel", "channel": "channel",
        "san_pham": "sku", "sku": "sku", "ma_hang": "sku"}


@dataclass
class Check:
    code: str
    name: str
    stop: bool = False                    # chan: true -> the next step does not run when this fails
    tolerance: Optional[Decimal] = None   # in unit
    unit: str = "đ"
    threshold: Optional[tuple] = None     # (đồng, percent) for the variance check
    clicky: str = ""                      # which of Clicky's checks this is (KINDS), said or inferred from its name

    @property
    def kind(self) -> str:
        if self.clicky in KINDS:
            return self.clicky
        from engine.tables import key
        name = key(self.name)
        if self.threshold is not None:
            return "chenh_lech_giai_trinh"
        if re.search(r"\b64[12]", name):
            return "chi_phi_khop_so"
        if "anh xa" in name or "mapping" in name:
            return "anh_xa_du"
        if "ytd" in name or "luy ke" in name:
            return "luy_ke_khop_so"
        return ""


@dataclass
class Trap:
    code: str
    name: str
    sign: str = ""
    action: str = ""


@dataclass
class Step:
    id: str
    name: str
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    due: Optional[dt.date] = None
    checks: list = field(default_factory=list)
    approver: str = ""
    to: str = ""


@dataclass
class Company:
    folder: Path
    checks: dict = field(default_factory=dict)      # code -> Check
    traps: list = field(default_factory=list)       # [Trap]
    steps: list = field(default_factory=list)       # [Step] in order
    segments: list = field(default_factory=list)    # [(position, name, role)] in order
    delimiter: str = ""
    unmapped: list = field(default_factory=list)    # values printed for a code the mapping lacks
    questions: list = field(default_factory=list)   # [dict]
    read: list = field(default_factory=list)        # file names read
    problems: list = field(default_factory=list)    # one plain sentence per file that could not be read
    open: list = field(default_factory=list)        # points still to confirm (TODO, a due date on a weekend)

    def check(self, code: str) -> Optional[Check]:
        return self.checks.get(code)

    def variance_threshold(self) -> Optional[tuple]:
        """(đồng, percent) from the first check that states one."""
        return next((c.threshold for c in self.checks.values() if c.threshold), None)


def _holds(p: Path) -> bool:
    return p.is_dir() and any((p / f).is_file() for f in FILES)


def folder() -> Optional[Path]:
    """The configuration folder, when one holds a configuration file. CLICKY_CONFIG_DIR, when set, is the only
    place looked at: a robot points it at an empty folder so the PC's own configuration is never read."""
    said = os.getenv(ENV)
    if said:
        p = Path(said)
        return p if _holds(p) else None
    try:
        from storage import data_dir
        p = data_dir() / "cau-hinh"
    except Exception:                          # noqa: BLE001 - no data folder: no configuration
        return None
    return p if _holds(p) else None


def find() -> Optional[Company]:
    """The company's configuration, read; None when no folder holds one."""
    p = folder()
    return load(p) if p is not None else None


def _date(text) -> Optional[dt.date]:
    if isinstance(text, dt.date):
        return text
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(text or ""))
    return dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _span(text) -> tuple:
    parts = str(text or "").split("/")
    start = _date(parts[0]) if parts and parts[0] else None
    end = _date(parts[1]) if len(parts) > 1 else start
    return start, end


def _dec(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).replace(",", "."))
    except InvalidOperation:
        return None


def _todos(node, where: str, out: list) -> None:
    """Every value or key still marked TODO, with where it sits."""
    if isinstance(node, dict):
        for k, v in node.items():
            if "TODO" in str(k):
                out.append(f"{where}: {v}")
            else:
                _todos(v, f"{where} · {k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            label = v.get("id") or v.get("ma") if isinstance(v, dict) else None
            _todos(v, f"{where} · {label or i + 1}", out)
    elif isinstance(node, str) and "TODO" in node:
        out.append(f"{where}: {node.strip()}")


def _text(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v or "").strip()


def _threshold(n: dict) -> Optional[tuple]:
    if not isinstance(n, dict):
        return None
    pct = _dec(n.get("pct") if "pct" in n else n.get("phan_tram"))
    amount = None
    for k, scale in (("ty", 10 ** 9), ("ti", 10 ** 9), ("trieu", 10 ** 6), ("dong", 1), ("vnd", 1)):
        if _dec(n.get(k)) is not None:
            amount = _dec(n.get(k)) * scale
            break
    if pct is None and amount is None:
        return None
    return amount, pct


def _checks(c: Company, doc: dict) -> None:
    for row in doc.get("kiem_tra") or []:
        if not isinstance(row, dict) or not row.get("ma"):
            continue
        k = Check(str(row["ma"]), _text(row.get("ten")), bool(row.get("chan")))
        for f, unit, scale in (("dung_sai", "đ", 1), ("dung_sai_trieu", "đ", 10 ** 6), ("dung_sai_tan", "tấn", 1),
                               ("dung_sai_diem_pct", "điểm %", 1)):
            if f in row and _dec(row[f]) is not None:
                k.tolerance, k.unit = _dec(row[f]) * scale, unit
                break
        k.threshold = _threshold(row.get("nguong"))
        k.clicky = str(row.get("clicky") or "").strip()
        c.checks[k.code] = k
    for row in doc.get("bay_ke_toan") or []:
        if isinstance(row, dict) and row.get("ma"):
            c.traps.append(Trap(str(row["ma"]), _text(row.get("ten")), _text(row.get("dau_hieu")),
                                _text(row.get("hanh_dong"))))


def _steps(c: Company, doc: dict) -> None:
    for row in doc.get("buoc") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        start, end = _span(row.get("ngay"))
        due = _date(row.get("han")) or end
        send = row.get("gui") if isinstance(row.get("gui"), dict) else {}
        s = Step(str(row["id"]), _text(row.get("ten")), start, end, due, [str(x) for x in row.get("kiem") or []],
                 _text(row.get("duyet")), _text(send.get("den")))
        c.steps.append(s)
        if s.due and s.due.weekday() >= 5:
            c.open.append(f"{s.id} {s.name}: hạn {s.due:%d/%m/%Y} rơi vào {WEEKDAY[s.due.weekday()]}")


def _mapping(c: Company, doc: dict) -> None:
    segments = next((v for v in doc.values() if isinstance(v, dict) and isinstance(v.get("doan"), list)), None)
    if segments is not None:
        c.delimiter = str(segments.get("phan_cach") or "")
        for row in segments["doan"]:
            if isinstance(row, dict) and row.get("so") is not None:
                role = ROLE.get(str(row.get("vai_tro") or "").strip().lower(), "")
                c.segments.append((int(row["so"]), _text(row.get("ten")) or f"Phân đoạn {row['so']}", role))
        c.segments.sort()

    def markers(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "khong_tim_thay" and isinstance(v, dict):
                    for f, x in v.items():
                        if f != "hanh_dong" and isinstance(x, (str, int)) and str(x) not in c.unmapped:
                            c.unmapped.append(str(x))
                else:
                    markers(v)
        elif isinstance(node, list):
            for v in node:
                markers(v)
    markers(doc)


def load(path: Path) -> Company:
    import yaml
    c = Company(Path(path))
    for name, read in (("kiem_tra.yaml", _checks), ("lich_thang.yaml", _steps), ("anh_xa.yaml", _mapping),
                       ("cau_hoi.yaml", lambda c, d: c.questions.extend(
                           q for q in d.get("cau_hoi") or [] if isinstance(q, dict)))):
        p = c.folder / name
        if not p.is_file():
            continue
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(doc, dict):
                raise ValueError("not a mapping")
            read(c, doc)
        except Exception as exc:                 # noqa: BLE001 - one file unreadable: the others still load
            mark = getattr(exc, "problem_mark", None)
            at = f" (dòng {mark.line + 1})" if mark is not None else ""
            c.problems.append(f"Em chưa đọc được file cấu hình {name}{at}; các file cấu hình khác vẫn dùng.")
            continue
        c.read.append(name)
        if name != "cau_hoi.yaml":
            _todos(doc, name.split(".")[0], c.open)
    return c


# ── Using it ────────────────────────────────────────────────────────────────

def apply(company: Optional[Company], config) -> None:
    """Put the company's variance threshold into the catalogue's rules (engine.variance_class.thresholds reads
    them), unless the folder's catalogue already states one: the folder's own file is nearer the month."""
    if company is None or config is None:
        return
    thr = company.variance_threshold()
    if thr is None or any("nguong" in k for k in config.rules):
        return
    amount, pct = thr
    if amount is not None:
        config.rules["nguong chenh lech"] = str(amount)
        config.rule_text["nguong chenh lech"] = "Ngưỡng chênh lệch"
    if pct is not None:
        config.rules["nguong chenh lech (%)"] = str(pct)
        config.rule_text["nguong chenh lech (%)"] = "Ngưỡng chênh lệch (%)"


def segment_map(company: Optional[Company]):
    """The account segments as an engine.segment_gl.SegmentMap (no code tables: those stay in the map workbook)."""
    if company is None or not company.segments:
        return None
    from engine.segment_gl import Segment, SegmentMap
    return SegmentMap([Segment(p, n, r) for p, n, r in company.segments], delimiter=company.delimiter,
                      file="anh_xa.yaml")


def unmapped_lines(company: Optional[Company], entries: list, period: str) -> list:
    """The month's cost lines whose department or cost element is the value printed for a missing code."""
    if company is None or not company.unmapped:
        return []
    marks = {m.strip().lower() for m in company.unmapped}
    return [e for e in entries if e.period == period
            and (str(e.dept or "").strip().lower() in marks or str(e.element or "").strip().lower() in marks)]


def today(company: Optional[Company], on: dt.date) -> list:
    """What the month's calendar says for a day, one sentence each: the steps running, the due dates within two
    days, else the next step."""
    if company is None or not company.steps:
        return []
    out = []
    running = [s for s in company.steps if s.start and s.end and s.start <= on <= s.end]
    for s in running:
        checks = f" Kiểm {', '.join(s.checks)}." if s.checks else ""
        out.append(f"Hôm nay {on:%d/%m}: {s.id} {s.name}.{checks}")
    for s in company.steps:
        if s.due and on <= s.due <= on + dt.timedelta(days=2) and s not in running:
            out.append(f"Sắp tới hạn: {s.id} {s.name}, hạn {s.due:%d/%m} ({WEEKDAY[s.due.weekday()]}).")
        elif s.due and s in running and s.due >= on and s.due.weekday() >= 5:
            out.append(f"{s.id} có hạn {s.due:%d/%m}, rơi vào {WEEKDAY[s.due.weekday()]}: chị xác nhận ngày gửi thật.")
    if not out:
        nxt = next((s for s in company.steps if s.start and s.start > on), None)
        if nxt is not None:
            out.append(f"Hôm nay {on:%d/%m} chưa có bước nào. Bước tới: {nxt.id} {nxt.name}, từ {nxt.start:%d/%m}.")
        else:
            out.append(f"Lịch tháng không còn bước nào sau {on:%d/%m}.")
    return out
