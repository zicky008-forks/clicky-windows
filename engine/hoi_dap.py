"""BOD questions answered from the saved FP&A report, never from a new computation and never by the AI.

The saved workbook (engine.fpa_report.write, and engine.forecast.write when the folder has a forecast pack) is read
back, hashed, and indexed: every figure the answers may say is a cell of that file. A question is mapped by a local
matcher to one intent of a fixed menu with its parameters (a P&L line, month / year to date / full year, against
plan, a department or a cost element, how many, a bridge part). A code template fills the answer; every number in it
names its cell, and the file and row behind it when the report has one. A question with no figure in the report gets
"Câu này em chưa có số trong báo cáo, em ghi lại để chị trả lời sau"; a question the matcher does not understand gets
its own status. Nothing is guessed.

A follow-up ("+ vì sao?", "+ còn lũy kế?") is answered in the context of the question above it: what it does not say
is taken from that question.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from engine.monthly_report import fmt
from engine.tables import key

ANSWERED, NO_FIGURE, NOT_UNDERSTOOD = "trả lời", "chưa có số", "không hiểu câu hỏi"
NO_FIGURE_TEXT = "Câu này em chưa có số trong báo cáo, em ghi lại để chị trả lời sau."
NOT_UNDERSTOOD_TEXT = "Em chưa hiểu câu hỏi này, em ghi lại để chị trả lời sau."
SHEET = "Hoi dap BOD"
JSONL = "hoi-dap.jsonl"
HEADER = ["Số", "Câu hỏi", "Trả lời", "Trạng thái", "Số liệu và nguồn", "Bản kế hoạch"]

# P&L lines, longest phrase first so 'lợi nhuận gộp' is never read as 'lợi nhuận'
LINES = (("gp", ("loi nhuan gop", "lai gop", "lng", "gp", "gross profit", "gross margin")),
         ("op", ("loi nhuan hoat dong", "loi nhuan thuan", "lnhd", "ebit", "operating profit", "loi nhuan", "lai lo",
                 "ket qua kinh doanh")),
         ("opex", ("chi phi hoat dong", "chi phi ban hang va quan ly", "cphd", "opex", "chi phi")),
         ("cogs", ("gia von", "cogs")),
         ("revenue", ("doanh thu thuan", "doanh thu", "doanh so", "dtt", "revenue", "sales")))
LINE_NAMES = {"revenue": "Doanh thu thuần", "cogs": "Giá vốn hàng bán", "gp": "Lợi nhuận gộp",
              "opex": "Chi phí hoạt động", "op": "Lợi nhuận hoạt động"}
YTD_WORDS = ("luy ke", "tu dau nam", "ytd", "den nay", "den thoi diem nay")
FULL_WORDS = ("ca nam", "uoc ca nam", "uoc nam", "full year", "fy", "het nam", "cuoi nam", "forecast", "du bao", "le")
LY_WORDS = ("cung ky", "nam truoc", "nam ngoai", "ly", "last year")
WHY_WORDS = ("vi sao", "tai sao", "nguyen nhan", "do dau", "giai thich", "why", "ly do", "vi dau", "the nao ma")
TOP_WORDS = ("lon nhat", "top", "nhieu nhat", "dang chu y", "can hoi", "chenh lech lon", "chenh nhieu", "noi bat",
             "bat thuong")
ONE_OFF_WORDS = ("mot lan", "dot xuat", "one off", "phat sinh mot lan")
TIMING_WORDS = ("thoi diem", "timing", "chi bu", "chi truoc", "dao nguoc", "lech thang", "som hon", "muon hon")
BRIDGE_PARTS = (("volume", ("san luong", "volume", "luong ban")), ("mix", ("co cau", "mix")),
                ("price", ("gia ban", "price")), ("cost", ("gia von don vi", "chi phi don vi", "anh huong gia von")))
BRIDGE_WORDS = ("cau noi", "bridge", "anh huong", "tac dong", "phan tich lng", "phan tich loi nhuan gop")
BRIDGE_ROWS = {"volume": "san luong", "mix": "co cau", "price": "gia ban", "cost": "gia von"}
OPEN_WORDS = ("chua ro", "dang cho", "con mo", "diem mo", "chua tinh", "con thieu", "can chi", "can xac nhan",
              "cau hoi mo", "ton dong", "chua giai trinh")
BASIS_WORDS = ("ban ke hoach nao", "so voi ban nao", "ban nao", "phien ban", "duyet ngay", "ke hoach nao", "aop nao",
               "can cu nao", "so voi gi")
SKU_WORDS = ("ma hang", "sku", "san pham", "kenh", "channel", "nhan hang", "khach hang")
NUMBER_WORDS = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9,
                "muoi": 10}
NUM_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?![\d.])|-?\d+(?:,\d+)?(?=\s*(?:đ|%))")


# ── The saved report, read back ───────────────────────────────────────────────

@dataclass
class Cell:
    ref: str                      # "'P&L'!B5"
    value: object


@dataclass
class Book:
    path: Path
    sha256: str
    basis: str = ""
    pnl: dict = field(default_factory=dict)        # code -> {field: Cell}, plus "source", "ytd_source" text
    costs: list = field(default_factory=list)      # [{dept, element, verdict, cells: {field: Cell}}]
    variances: list = field(default_factory=list)  # [{label, causes, verdict, source, cells}]
    bridge: dict = field(default_factory=dict)     # part -> Cell
    bridge_said: str = ""                          # why there is no bridge, when the report says it
    open: list = field(default_factory=list)       # [Cell] receipt sentences that wait on the user
    forecast: Optional["Book"] = None
    full: dict = field(default_factory=dict)       # forecast: code -> {field: Cell}
    period: str = ""                               # "10/2026"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _dec(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool) or isinstance(v, str):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _code_of(label) -> Optional[str]:
    k = key(label)
    for code, name in LINE_NAMES.items():
        if k == key(name):
            return code
    return None


def read(path: Path, forecast: Optional[Path] = None) -> Book:
    """The FP&A report (and the forecast pack, when given) as the answers may cite it."""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    from engine import fpa_report
    path = Path(path)
    b = Book(path, sha256(path))
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        def ref(sheet, r, c):
            return f"'{sheet}'!{get_column_letter(c)}{r}"
        rows = list(wb[fpa_report.PNL].iter_rows(values_only=True)) if fpa_report.PNL in wb.sheetnames else []
        if rows:
            b.basis = str(rows[1][0] or "") if len(rows) > 1 else ""
            hit = re.search(r"(\d{1,2}/\d{4})", str(rows[0][0] or ""))
            b.period = hit.group(1) if hit else ""
        names = ("month", "plan", "var", "pct", "ytd", "ytd_plan", "ytd_var", "ytd_pct")
        for n, r in enumerate(rows[3:], 4):
            code = _code_of(r[0] if r else None)
            if code is None:
                continue
            entry = {f: Cell(ref(fpa_report.PNL, n, i + 2), r[i + 1]) for i, f in enumerate(names)
                     if i + 1 < len(r) and r[i + 1] is not None}
            entry["source"], entry["ytd_source"] = (str(r[9] or "") if len(r) > 9 else "",
                                                    str(r[10] or "") if len(r) > 10 else "")
            b.pnl[code] = entry
        if fpa_report.COSTS in wb.sheetnames:
            names = ("month", "plan", "var", "pct", None, "underlying", "ytd", "ytd_plan", "ytd_var")
            for n, r in enumerate(list(wb[fpa_report.COSTS].iter_rows(values_only=True))[3:], 4):
                if not r or not r[0] or key(r[0]) in ("tong cong",) or str(r[0]) == fpa_report.GAP_LINE:
                    continue
                cells = {f: Cell(ref(fpa_report.COSTS, n, i + 3), r[i + 2]) for i, f in enumerate(names)
                         if f and i + 2 < len(r) and r[i + 2] is not None}
                b.costs.append({"dept": str(r[0]), "element": str(r[1] or ""), "verdict": str(r[6] or ""),
                                "cells": cells})
        if fpa_report.VARIANCES in wb.sheetnames:
            for n, r in enumerate(list(wb[fpa_report.VARIANCES].iter_rows(values_only=True))[3:], 4):
                if not r or not r[0]:
                    continue
                b.variances.append({"label": str(r[0]), "causes": str(r[2] or ""), "verdict": str(r[4] or ""),
                                    "source": str(r[6] or "") if len(r) > 6 else "",
                                    "causes_ref": ref(fpa_report.VARIANCES, n, 3),
                                    "cells": {"reported": Cell(ref(fpa_report.VARIANCES, n, 2), r[1]),
                                              "underlying": Cell(ref(fpa_report.VARIANCES, n, 4), r[3])}})
        if fpa_report.BRIDGE in wb.sheetnames:
            brows = list(wb[fpa_report.BRIDGE].iter_rows(values_only=True))
            for n, r in enumerate(brows, 1):
                k = key(r[0]) if r and r[0] else ""
                for part, word in BRIDGE_ROWS.items():
                    if k == word and len(r) > 1 and r[1] is not None:
                        b.bridge[part] = Cell(ref(fpa_report.BRIDGE, n, 2), r[1])
                if k.startswith("loi nhuan gop ke hoach") and len(r) > 1:
                    b.bridge["gp_plan"] = Cell(ref(fpa_report.BRIDGE, n, 2), r[1])
                if k.startswith("loi nhuan gop thuc hien") and len(r) > 1:
                    b.bridge["gp_actual"] = Cell(ref(fpa_report.BRIDGE, n, 2), r[1])
                if k.startswith("chua tinh duoc"):
                    b.bridge_said = str(r[0])
        if fpa_report.RECEIPT in wb.sheetnames:
            for n, r in enumerate(wb[fpa_report.RECEIPT].iter_rows(values_only=True), 1):
                if r and r[0] == "File":
                    break
                text = str(r[0]) if r and r[0] and not any(v is not None for v in r[1:]) else ""
                if text and re.search(r"(can chi|cho chi|chua co trong danh muc|chua tinh|khong ro thang|chi cho biet"
                                      r"|chi kiem lai|chua lam duoc|chua giai thich)", key(text)):
                    b.open.append(Cell(ref(fpa_report.RECEIPT, n, 1), text))
    finally:
        wb.close()
    if forecast is not None:
        b.forecast = _read_forecast(Path(forecast))
    return b


def _read_forecast(path: Path) -> Book:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    from engine import forecast
    f = Book(path, sha256(path))
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        rows = list(wb[forecast.PNL].iter_rows(values_only=True))
        f.basis = str(rows[1][0] or "") if len(rows) > 1 else ""
        head = [key(v) for v in rows[2]] if len(rows) > 2 else []
        cols = {f_: head.index(key(h)) for f_, h in (("ytd", "Lũy kế thực hiện"), ("rest", "Các tháng còn lại"),
                                                      ("full", "Cả năm ước"), ("plan_full", "Kế hoạch năm"),
                                                      ("var", "Chênh lệch"), ("pct", "%")) if key(h) in head}
        for n, r in enumerate(rows[3:], 4):
            code = _code_of(r[0] if r else None)
            if code is None:
                continue
            f.full[code] = {k: Cell(f"'{forecast.PNL}'!{get_column_letter(c + 1)}{n}", r[c])
                            for k, c in cols.items() if c < len(r) and r[c] is not None}
    finally:
        wb.close()
    return f


# ── The question, mapped to the menu ─────────────────────────────────────────

@dataclass
class Query:
    intent: Optional[str] = None      # line, why, top, dept, element, bridge, one_off, timing, forecast, open, basis,
    line: Optional[str] = None        # sku, ly
    time: Optional[str] = None        # month, ytd, full
    target: Optional[str] = None      # a department or a cost element, as the report prints it
    n: Optional[int] = None
    part: Optional[str] = None


def _has(text: str, words) -> Optional[str]:
    for w in sorted(words, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text):
            return w
    return None


def _count(text: str) -> Optional[int]:
    hit = re.search(r"\b(?:top|nhung|cac|)\s*(\d{1,2})\b(?!\s*(?:/|thang|nam|%))", text)
    if hit and 1 <= int(hit.group(1)) <= 20:
        return int(hit.group(1))
    for w, n in NUMBER_WORDS.items():
        if re.search(rf"\b{w}\s+(khoan|dong|muc|bo phan|yeu to|chenh lech|nguyen nhan|van de)\b", text):
            return n
    return None


NOT_NAMES = {"nao", "gi", "nay", "do", "ban", "cac", "nhung", "chi", "co", "da", "thi", "la"}
DEPT_RE = re.compile(r"(?<![a-z])(?:bo phan|phong|khoi)\s+([a-z0-9]{2,12})(?![a-z0-9])")


def _short_for(short: str, name: str) -> bool:
    """'kd' for Kinh doanh (initials), 'mkt' for Marketing (its letters in order, same first letter)."""
    words = key(name).split()
    if not words or short in NOT_NAMES:
        return False
    if short == "".join(w[0] for w in words) or short == key(name).replace(" ", ""):
        return True
    rest = iter("".join(words))
    return len(short) >= 3 and short[0] == words[0][0] and all(ch in rest for ch in short)


def parse(question: str, book: Book) -> Query:
    """The intent and its parameters, from the words alone. Unknown words leave a field empty (None)."""
    t = key(question)
    q = Query()
    line = next((code for code, words in LINES if _has(t, words)), None)
    q.line = line
    if _has(t, YTD_WORDS):
        q.time = "ytd"
    elif _has(t, FULL_WORDS):
        q.time = "full"
    targets = sorted({(r["dept"], "dept") for r in book.costs} | {(r["element"], "element") for r in book.costs
                                                                  if r["element"]}, key=lambda x: -len(x[0]))
    target = next(((name, kind) for name, kind in targets if name and _has(t, [key(name)])), None)
    if target and target[0] and key(target[0]) in {w for _c, ws in LINES for w in ws}:
        target = None                                   # 'Chi phí' as an element name is the P&L line here
    named = None
    if target is None:
        hit = DEPT_RE.search(t)
        if hit and hit.group(1) not in NOT_NAMES:
            named = hit.group(1)
            found = {r["dept"] for r in book.costs if _short_for(named, r["dept"])}
            target = (found.pop(), "dept") if len(found) == 1 else None
    part = next((p for p, words in BRIDGE_PARTS if _has(t, words)), None)
    if _has(t, LY_WORDS):
        q.intent = "ly"
    elif _has(t, BASIS_WORDS):
        q.intent = "basis"
    elif _has(t, OPEN_WORDS):
        q.intent = "open"
    elif _has(t, ONE_OFF_WORDS):
        q.intent = "one_off"
    elif _has(t, TIMING_WORDS):
        q.intent = "timing"
    elif part or _has(t, BRIDGE_WORDS):
        q.intent, q.part = "bridge", part
    elif _has(t, SKU_WORDS) and not target:
        q.intent = "sku"
    elif target:
        q.intent, q.target = target[1], target[0]
    elif named:
        q.intent, q.target = "dept", named               # a department the report does not have: no figure, no total
    elif _has(t, TOP_WORDS):
        q.intent, q.line = "top", q.line or "opex"
    elif line and _has(t, WHY_WORDS):
        q.intent = "why"
    elif line:
        q.intent = "forecast" if q.time == "full" else "line"
    elif _has(t, WHY_WORDS):
        q.intent = "why"                                # 'vì sao?' alone: a follow-up takes the line from above
    q.n = _count(t)
    if q.intent == "line" and _has(t, WHY_WORDS):
        q.intent = "why"
    return q


def follow(q: Query, parent: Query) -> Query:
    """A follow-up takes what it does not say from the question above it."""
    if parent is None:
        return q
    out = replace(parent)
    if q.intent in (None, "why") and parent.intent in ("line", "forecast", "why", "dept", "element"):
        out.intent = "why" if q.intent == "why" else parent.intent
        if parent.intent in ("dept", "element") and q.intent == "why":
            out.intent = parent.intent                  # a department's why is its lines with their causes
    elif q.intent is not None:
        out.intent = q.intent
    for f in ("line", "time", "target", "n", "part"):
        if getattr(q, f) is not None:
            setattr(out, f, getattr(q, f))
    if out.time == "full" and out.intent == "line":
        out.intent = "forecast"
    elif out.time != "full" and out.intent == "forecast":
        out.intent = "line"                             # '+ còn lũy kế?' after a full-year question
    return out


# ── The answer, from the cells ───────────────────────────────────────────────

@dataclass
class Answer:
    text: str
    status: str
    cites: list = field(default_factory=list)       # [{"o", "gia_tri", "nguon"}] or [{"o", "van_ban", "nguon"}]
    query: Optional[Query] = None
    parts: list = field(default_factory=list)       # the answer one idea at a time, for the screen


SEP = "\x1e"                                         # between the parts of an answer, inside the helpers only


def _done(text: str, status: str, cites: list, q: Query) -> Answer:
    parts = [p.strip() for p in text.split(SEP) if p.strip()]
    text = re.sub(r":\s*" + SEP, ": ", text.replace("." + SEP, ". ")).replace(SEP, "; ")
    return Answer(re.sub(r"\s+;", ";", text), status, cites, q, [p if p.endswith(":") else p.rstrip(".") + "." for p in parts])


def _num(c: Cell, cites: list, source: str = "", pct: bool = False) -> str:
    v = _dec(c.value)
    if v is None:
        raise LookupError(c.ref)
    cites.append({"o": c.ref, "gia_tri": str(v), "nguon": source})
    if pct:
        return f"{str(v.quantize(Decimal('0.1'))).replace('.', ',')}%"
    return f"{fmt(v)} đ"


def _quote(ref: str, text: str, cites: list, source: str = "") -> str:
    """A sentence the report wrote (a cause, a receipt line) said as it stands: it is cited as that cell's text, so
    any figure inside it has the cell behind it too."""
    cites.append({"o": ref, "van_ban": text, "nguon": source})
    return text


def _line(b: Book, q: Query, cites: list) -> str:
    row = b.pnl.get(q.line or "")
    if not row:
        raise LookupError(q.line)
    ytd = q.time == "ytd"
    a, p, v, pc = ("ytd", "ytd_plan", "ytd_var", "ytd_pct") if ytd else ("month", "plan", "var", "pct")
    src = row.get("ytd_source" if ytd else "source", "")
    when = "lũy kế từ đầu năm" if ytd else (f"tháng {b.period}" if b.period else "tháng này")
    said = f"{LINE_NAMES[q.line]} {when}: thực hiện {_num(row[a], cites, src)}"
    if p in row:
        said += f", kế hoạch {_num(row[p], cites)}, chênh lệch {_num(row[v], cites)}"
        if pc in row:
            said += f" ({_num(row[pc], cites, pct=True)})"
    else:
        said += "; báo cáo chưa có kế hoạch cho dòng này"
    return said + (f". Nguồn: {src}." if src else ".")


def _variance_rows(b: Book, n: int, cites: list, rows=None) -> list:
    out = []
    for r in (rows if rows is not None else b.variances)[:n]:
        said = f"{r['label']}: chênh {_num(r['cells']['reported'], cites, r['source'])}"
        if r["causes"]:
            said += " (" + _quote(r["causes_ref"], r["causes"], cites, r["source"]).rstrip(".") + ")"
        said += f", số thực chất {_num(r['cells']['underlying'], cites, r['source'])}, {r['verdict']}"
        out.append(said + (f" (chứng từ: {r['source']})" if r["source"] else ""))
    return out


def _why(b: Book, q: Query, cites: list) -> str:
    head = _line(b, replace(q, intent="line"), cites)
    if q.line == "opex" or q.line == "op":
        rows = _variance_rows(b, q.n or 3, cites)
        if not rows:
            return head + " Không có dòng chi phí nào chênh trên ngưỡng."
        more = ""
        if q.line == "op" and "gp" in b.pnl and "var" in b.pnl["gp"]:
            more = f" Lợi nhuận gộp chênh {_num(b.pnl['gp']['var'], cites)}."
        return head + more + SEP + "Chi phí chênh nhiều nhất:" + SEP + SEP.join(rows) + "."
    if q.line in ("gp", "cogs", "revenue") and b.bridge:
        if q.line == "revenue":
            raise LookupError("revenue drivers")       # the bridge splits gross profit, not revenue
        parts = [f"{name} {_num(b.bridge[k], cites)}" for k, name in (("volume", "sản lượng"), ("mix", "cơ cấu"),
                                                                       ("price", "giá bán"), ("cost", "giá vốn"))
                 if k in b.bridge and (q.line == "gp" or k == "cost")]
        return head + " Cầu nối lợi nhuận gộp: " + ", ".join(parts) + "."
    raise LookupError("why")


def _lines_of(b: Book, q: Query, cites: list) -> str:
    rows = [r for r in b.costs if key(r["dept" if q.intent == "dept" else "element"]) == key(q.target)]
    if not rows:
        raise LookupError(q.target)
    ytd = q.time == "ytd"
    a, p, v = ("ytd", "ytd_plan", "ytd_var") if ytd else ("month", "plan", "var")
    parts = []
    for r in rows:
        other = r["element"] if q.intent == "dept" else r["dept"]
        c = r["cells"]
        said = f"{other} thực hiện {_num(c[a], cites)}"
        if p in c and v in c:
            said += f", kế hoạch {_num(c[p], cites)}, chênh {_num(c[v], cites)}"
        if not ytd and r["verdict"]:
            said += f", {r['verdict']}"
        parts.append(said)
    when = "lũy kế từ đầu năm" if ytd else (f"tháng {b.period}" if b.period else "tháng này")
    text = f"{q.target} {when}:" + SEP + SEP.join(parts) + "."
    why = [x for x in b.variances if key(x["label"]).startswith(key(q.target)) or f" {key(q.target)}" in
           f" {key(x['label'])}"]
    if why:
        text += SEP + "Giải thích:" + SEP + SEP.join(_variance_rows(b, 3, cites, why)) + "."
    return text


def _bridge(b: Book, q: Query, cites: list) -> str:
    if not b.bridge:
        raise LookupError(b.bridge_said or "bridge")
    names = {"volume": "Sản lượng", "mix": "Cơ cấu", "price": "Giá bán", "cost": "Giá vốn"}
    keys = [q.part] if q.part else ["volume", "mix", "price", "cost"]
    parts = [f"{names[k]} {_num(b.bridge[k], cites)}" for k in keys if k in b.bridge]
    if not parts:
        raise LookupError(q.part)
    head = ""
    if not q.part and "gp_plan" in b.bridge and "gp_actual" in b.bridge:
        head = (f"Lợi nhuận gộp từ kế hoạch {_num(b.bridge['gp_plan'], cites)} đến thực hiện "
                f"{_num(b.bridge['gp_actual'], cites)}: ")
    return head + "; ".join(parts) + " (cầu nối lợi nhuận gộp, theo mã hàng gộp các kênh)."


def _by_verdict(b: Book, q: Query, cites: list, word: str, none: str) -> str:
    rows = [r for r in b.variances if word in key(r["verdict"]) or word in key(r["causes"])]
    if not rows:
        return none
    text = SEP.join(_variance_rows(b, q.n or 5, cites, rows))
    if word == "lech thoi diem":
        months = sorted({m for r in rows for m in re.findall(r"\d{2}/\d{4}", r["causes"])})
        if months:
            text += f". Tháng liên quan: {', '.join(months)}"
    return text + "."


def _forecast(b: Book, q: Query, cites: list) -> str:
    f = b.forecast
    if f is None or (q.line or "op") not in f.full:
        raise LookupError("forecast")
    row = f.full[q.line or "op"]
    first = len(cites)
    said = f"{LINE_NAMES[q.line or 'op']} cả năm ước {_num(row['full'], cites, f.basis)}"
    if "plan_full" in row:
        said += f", kế hoạch năm {_num(row['plan_full'], cites)}, chênh lệch {_num(row['var'], cites)}"
        if "pct" in row:
            said += f" ({_num(row['pct'], cites, pct=True)})"
    if "ytd" in row and "rest" in row:
        said += f"; gồm thực hiện lũy kế {_num(row['ytd'], cites)} và các tháng còn lại {_num(row['rest'], cites)}"
    for c in cites[first:]:
        c["file"] = f.path.name                         # these cells are the forecast pack's, not the report's
    return said + f". {f.basis}."


def answer(book: Book, question: str, parent: Optional[Query] = None) -> Answer:
    """One question; parent is the Query of the question above, for a follow-up."""
    q = parse(question, book)
    if parent is not None:
        q = follow(q, parent)
    cites = []
    try:
        if q.intent is None:
            return Answer(NOT_UNDERSTOOD_TEXT, NOT_UNDERSTOOD, [], q)
        if q.intent in ("ly", "sku"):
            raise LookupError(q.intent)
        if q.intent == "basis":
            if not b_basis(book):
                raise LookupError("basis")
            return Answer(f"Báo cáo so với {book.basis[0].lower() + book.basis[1:]}.", ANSWERED, [], q)
        if q.intent == "open":
            text = ("Các điểm còn chờ chị (Phiếu nhận file):"
                    + SEP + SEP.join(_quote(c.ref, c.value, cites).rstrip(".") + "." for c in book.open)) if book.open \
                else "Phiếu nhận file không còn điểm nào chờ chị trả lời."
            return _done(text, ANSWERED, cites, q)
        if q.intent == "line":
            text = _line(book, q, cites)
        elif q.intent == "why":
            if q.line is None:
                return Answer(NOT_UNDERSTOOD_TEXT, NOT_UNDERSTOOD, [], q)
            text = _why(book, q, cites)
        elif q.intent == "top":
            rows = _variance_rows(book, q.n or 3, cites)
            text = ("Chi phí chênh nhiều nhất so với kế hoạch:" + SEP + SEP.join(rows) + ".") if rows else \
                "Không có dòng chi phí nào chênh trên ngưỡng."
        elif q.intent in ("dept", "element"):
            text = _lines_of(book, q, cites)
        elif q.intent == "bridge":
            text = _bridge(book, q, cites)
        elif q.intent == "one_off":
            text = _by_verdict(book, q, cites, "khoan mot lan", "Tháng này em không thấy khoản một lần nào.")
        elif q.intent == "timing":
            text = _by_verdict(book, q, cites, "lech thoi diem", "Tháng này em không thấy khoản lệch thời điểm nào.")
        elif q.intent == "forecast":
            text = _forecast(book, q, cites)
        else:
            return Answer(NOT_UNDERSTOOD_TEXT, NOT_UNDERSTOOD, [], q)
    except LookupError:
        return Answer(NO_FIGURE_TEXT, NO_FIGURE, [], q)
    return _done(text, ANSWERED, cites, q)


def b_basis(book: Book) -> bool:
    return bool(book.basis) and "chua co ke hoach" not in key(book.basis)


def plan_version(book: Book) -> str:
    hit = re.search(r"phiên bản ([^,;.]+)", book.basis)
    return hit.group(1).strip() if hit else ""


# ── A question file, the record, the sheet ───────────────────────────────────

QNUM_RE = re.compile(r"^\s*(?:q|c|cau|câu)?\s*(\d{1,3})\s*[.):\-]\s*", re.IGNORECASE)


def read_questions(path: Path) -> list:
    """[(number, question, follow-up?)] from a UTF-8 file (with or without BOM, any line ends). A leading number is
    the question's own ('Q1.', '1)'), else they are counted; a line starting with '+' follows the one above."""
    raw = Path(path).read_bytes().decode("utf-8-sig")
    out, n, sub = [], 0, 0
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("+"):
            if not out:
                continue
            sub += 1
            out.append((f"{out[-1][0].split('.')[0]}.{sub}", text.lstrip("+").strip(), True))
            continue
        hit = QNUM_RE.match(text)
        n = int(hit.group(1)) if hit else n + 1
        sub = 0
        out.append((f"Q{n}", text[hit.end():].strip() if hit else text, False))
    return out


def record(number: str, question: str, a: Answer, book: Book, at: Optional[str] = None) -> dict:
    return {"so": number, "cau_hoi": question, "tra_loi": a.text, "trang_thai": a.status, "so_lieu": a.cites,
            "ban_ke_hoach": plan_version(book), "bao_cao": str(book.path), "sha256": book.sha256,
            "du_bao": str(book.forecast.path) if book.forecast else "",
            "du_bao_sha256": book.forecast.sha256 if book.forecast else "",
            "luc": at or dt.datetime.now().isoformat(timespec="seconds")}


def answer_file(book: Book, questions: list) -> list:
    """Every question of a file, follow-ups in the context of the question above."""
    out, parent = [], None
    for number, text, is_follow in questions:
        a = answer(book, text, parent if is_follow else None)
        if not is_follow:
            parent = a.query
        out.append(record(number, text, a, book))
    return out


def numbers_without_source(rec: dict) -> list:
    """The amounts and percents an answer says that no citation of it holds (the robot's and the tests' check)."""
    values = [Decimal(c["gia_tri"]) for c in rec["so_lieu"] if "gia_tri" in c]
    said = {fmt(v) for v in values} | {str(v.quantize(Decimal("0.1"))).replace(".", ",") for v in values}
    said |= {n for c in rec["so_lieu"] if "van_ban" in c for n in NUM_RE.findall(c["van_ban"])}
    return [n for n in NUM_RE.findall(rec["tra_loi"]) if n not in said]


def write(book: Book, records: list, out_dir: Optional[Path] = None) -> dict:
    """hoi-dap.jsonl beside the report, and a copy of the report with the 'Hoi dap BOD' sheet and a Biên bản line
    (the report itself is never changed: its sha256 stays the one every answer names). The copy is reopened and every
    cited cell checked. Returns {"jsonl", "xlsx", "verified", "diffs"}."""
    from openpyxl import load_workbook
    from engine import audit, saved_check
    out_dir = Path(out_dir) if out_dir else book.path.parent
    jl = out_dir / JSONL
    with open(jl, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    wb = load_workbook(book.path)
    ws = wb.create_sheet(SHEET)
    ws.append([f"{audit.DRAFT} · Hỏi đáp Ban Giám đốc · số lấy từ báo cáo {book.path.name}"])
    ws.append([f"sha256 của báo cáo: {book.sha256}"])
    ws.append(HEADER)
    for r in records:
        ws.append([r["so"], r["cau_hoi"], r["tra_loi"], r["trang_thai"],
                   "; ".join(f"{c['o']} = {c['gia_tri']}" + (f" ({c['nguon']})" if c["nguon"] else "")
                             for c in r["so_lieu"] if "gia_tri" in c), r["ban_ke_hoach"]])
    if audit.SHEET in wb.sheetnames:
        answered = sum(1 for r in records if r["trang_thai"] == ANSWERED)
        wb[audit.SHEET].append(["", dt.datetime.now().strftime("%d/%m/%Y %H:%M"), f"'{SHEET}'!A1", "Hỏi đáp BOD",
                                "", f"{len(records)} câu, {answered} câu trả lời bằng số của báo cáo",
                                "Thêm sheet hỏi đáp; số không tính lại, lấy từ ô của báo cáo", audit.AUTO,
                                book.path.name])
    path = out_dir / f"{book.path.stem}_HoiDap.xlsx"
    n = 1
    while path.exists():
        n += 1
        path = out_dir / f"{book.path.stem}_HoiDap ({n}).xlsx"
    wb.save(path)
    expect = {c["o"]: Decimal(c["gia_tri"]) for r in records for c in r["so_lieu"] if "gia_tri" in c and "file" not in c}
    ok, diffs = saved_check.check(saved_check.tamper_from_env(path), expect)
    pack = {c["o"]: Decimal(c["gia_tri"]) for r in records for c in r["so_lieu"] if "gia_tri" in c and "file" in c}
    if pack and book.forecast is not None:
        ok_f, diffs_f = saved_check.check(book.forecast.path, pack)
        ok, diffs = ok and ok_f, diffs + diffs_f
    return {"jsonl": jl, "xlsx": path, "verified": ok, "diffs": diffs}


# ── Where the report is ──────────────────────────────────────────────────────

def latest(folder: Path, prefix: str) -> Optional[Path]:
    """The newest report workbook Clicky wrote in folder (or the folder itself when it is one), never a Q&A copy."""
    folder = Path(folder)
    found = [p for p in folder.glob(f"{prefix}_T*/{prefix}_T*.xlsx") if "_HoiDap" not in p.stem]
    found += [p for p in folder.glob(f"{prefix}_T*.xlsx") if "_HoiDap" not in p.stem]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None
