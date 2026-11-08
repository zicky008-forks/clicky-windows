"""A general ledger whose account is a combination of segments (an ERP export: 'company-cost centre-account-...'),
read with a map file the user keeps beside it. Nothing about any company is built in: the number of segments, their
order, the delimiter, what each segment means and how its codes map to departments, cost elements and accounts all
come from the map file.

The map workbook (a file named 'Ánh xạ', 'mapping', 'phân đoạn' or 'segment'):
- a segments sheet: 'Thứ tự | Tên phân đoạn | Vai trò', one row per segment in order. Vai trò is one of Công ty,
  Bộ phận, Tài khoản, Khối, Kênh, Sản phẩm, or anything else (kept, not used);
- a map sheet: 'Phân đoạn | Mã | Bộ phận | Tên bộ phận | Yếu tố chi phí | Tên yếu tố | TK'. Phân đoạn names the
  segment (by its role or its name); Mã is a code, or a prefix ending in '*'; the other columns say what it maps to;
- optionally a 'Tham số | Giá trị' sheet with 'Dấu phân cách' (else it is found from the data) and 'TK chi phí hoạt
  động' (the accounts of the operating cost matrix; 641 and 642 when not said).

The export can carry the combination in one column or one column per segment (headed with the segment's name), and
the amount as Nợ/Có columns or one signed amount. Every row is used or skipped with its reason. A posting on an
operating cost account becomes a cost line (engine.bod_review.Entry) for the department x cost element matrix;
every posting, whatever its account, is kept for matching to the books (engine.bod_inputs.Posting). A code the map
does not have is named and still counted under its own code.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine.bod_inputs import CONFIG_SPECS, Posting, month_key, name_key, num
from engine.bod_review import Entry
from engine.tables import Table, key, read_table, sheets_of

ZERO = Decimal(0)
MAP_WORDS = ("anh xa", "mapping", "phan doan", "segment")
ROLES = {"site": ("cong ty", "phap nhan", "company", "entity", "don vi", "legal entity"),
         "dept": ("bo phan", "phong ban", "cost center", "cost centre", "trung tam chi phi", "department"),
         "account": ("tai khoan", "account", "natural account", "tk"),
         "division": ("khoi", "division", "business unit", "bu", "nganh hang", "nganh"),
         "channel": ("kenh", "channel"),
         "sku": ("san pham", "product", "ma hang", "sku")}
ROLE_TEXT = {"site": "công ty", "dept": "bộ phận", "account": "tài khoản", "division": "khối", "channel": "kênh",
             "sku": "sản phẩm"}
SEGMENTS_SPEC = {"thu_tu": ["thu tu", "vi tri", "stt", "position", "segment number", "so thu tu"],
                 "ten": ["ten phan doan", "segment name", "ten", "phan doan"],
                 "vai_tro": ["vai tro", "y nghia", "role", "dung lam", "loai"]}
MAP_SPEC = {"phan_doan": ["phan doan", "segment", "vai tro"],
            "ma": ["ma", "code", "gia tri", "segment value", "ma phan doan"],
            "bo_phan": ["bo phan", "ma bo phan", "phong ban", "department", "ma phong ban"],
            "ten_bo_phan": ["ten bo phan", "ten phong ban", "department name"],
            "yeu_to": ["yeu to chi phi", "ma yeu to", "cost element", "khoan muc", "ma khoan muc"],
            "ten_yeu_to": ["ten yeu to", "ten khoan muc", "ten cost element", "cost element name"],
            "tk": ["tk vas", "tai khoan vas", "tk", "so hieu tk"],
            "ten": ["ten", "dien giai", "description", "ten hien thi"]}
CLOSING = re.compile(r"\b(ket chuyen|closing|close|year end|cuoi nam)\b")


def is_map_file(stem: str) -> bool:
    return any(w in name_key(stem) for w in MAP_WORDS)


def _role(text) -> str:
    k = key(text)
    for role, words in ROLES.items():
        if k in words or any(k.startswith(w + " ") or k.endswith(" " + w) for w in words):
            return role
    return ""


@dataclass
class Segment:
    position: int
    name: str
    role: str                       # 'site', 'dept', 'account', 'division', 'channel', 'sku' or '' (kept, not used)


@dataclass
class SegmentMap:
    segments: list = field(default_factory=list)        # [Segment] in order
    delimiter: str = ""                                  # '' until said or found from the data
    codes: dict = field(default_factory=dict)            # role -> [(code or prefix*, {"bo_phan", "yeu_to", "tk", "ten", ...})]
    opex: tuple = ("641", "642")                         # accounts of the operating cost matrix
    file: str = ""

    def role_of(self, segment: str) -> str:
        """A map row's 'Phân đoạn' names a role or a segment's name."""
        for s in self.segments:
            if key(s.name) == key(segment):
                return s.role
        return _role(segment)

    def find(self, role: str, code: str) -> Optional[dict]:
        """The map row for a code: exact first, then the longest prefix ('641*')."""
        best, length = None, -1
        for pattern, row in self.codes.get(role, []):
            if pattern.endswith("*"):
                p = pattern[:-1]
                if code.startswith(p) and len(p) > length:
                    best, length = row, len(p)
            elif pattern == code:
                return row
        return best

    def by_role(self, role: str) -> Optional[Segment]:
        return next((s for s in self.segments if s.role == role), None)


def read_map(path: Path) -> SegmentMap:
    m = SegmentMap(file=Path(path).name)
    for sheet, rows in sheets_of(path).items():
        t = read_table(rows, sheet, SEGMENTS_SPEC, 2, required=("thu_tu",))
        if t is not None and "vai_tro" in t.columns:
            for _n, v in t.rows:
                pos = num(t.get(v, "thu_tu"))
                if pos is not None:
                    name = str(t.get(v, "ten", "") or "").strip()
                    m.segments.append(Segment(int(pos), name or f"Phân đoạn {int(pos)}", _role(t.get(v, "vai_tro", ""))))
            continue
        t = read_table(rows, sheet, MAP_SPEC, 2, required=("phan_doan", "ma"))
        if t is not None and ({"bo_phan", "yeu_to", "tk", "ten"} & set(t.columns)):
            for _n, v in t.rows:
                seg, code = str(t.get(v, "phan_doan", "") or "").strip(), str(t.get(v, "ma", "") or "").strip()
                if seg and code:
                    row = {f: str(t.get(v, f, "") or "").strip() for f in MAP_SPEC if f not in ("phan_doan", "ma")}
                    m.codes.setdefault(key(seg), []).append((code, row))
            continue
        t = read_table(rows, sheet, CONFIG_SPECS["rules"], 2)
        if t is not None:
            for _n, v in t.rows:
                k, val = key(t.get(v, "tham_so", "")), t.get(v, "gia_tri")
                if "phan cach" in k or "delimiter" in k or "separator" in k:
                    m.delimiter = str(val or "").strip()
                elif "chi phi hoat dong" in k or "opex" in k:
                    m.opex = tuple(p.strip() for p in re.split(r"[;,\s]+", str(val or "")) if p.strip()) or m.opex
    m.segments.sort(key=lambda s: s.position)
    # Map rows name a segment by role or by name: keep them by role.
    codes = {}
    for seg, rows in m.codes.items():
        codes.setdefault(m.role_of(seg) or seg, []).extend(rows)
    m.codes = codes
    return m


# ── The export ──────────────────────────────────────────────────────────────

def gl_spec(smap: SegmentMap) -> dict:
    spec = {f"seg{s.position}": [s.name, f"segment{s.position}", f"segment {s.position}", f"phan doan {s.position}"]
            for s in smap.segments}
    spec.update({
        "to_hop": ["account combination", "code combination", "to hop tai khoan", "account string", "gl account",
                   "chuoi tai khoan", "ma tai khoan day du", "account"],
        "ps_no": ["accounted dr", "entered dr", "debit", "phat sinh no", "so tien no", "dr"],
        "ps_co": ["accounted cr", "entered cr", "credit", "phat sinh co", "so tien co", "cr"],
        "tien": ["accounted amount", "net amount", "amount", "so tien"],
        "ky": ["period name", "period", "ky ke toan", "ky"],
        "ngay": ["gl date", "accounting date", "effective date", "ngay hach toan", "ngay ct", "ngay"],
        "dien_giai": ["line description", "description", "dien giai", "noi dung"],
        "so_ct": ["journal name", "je name", "so ct", "so chung tu", "document number", "voucher", "batch name"],
        "loai": ["journal category", "category", "source", "nguon", "loai but toan"]})
    return spec


@dataclass
class SegmentGL:
    table: Table
    entries: list = field(default_factory=list)          # Entry: operating cost lines
    postings: list = field(default_factory=list)         # Posting: every line
    unknown: dict = field(default_factory=dict)          # role -> {code: lines} codes the map does not have
    delimiter: str = ""

    def said(self) -> list:
        out = []
        for role, codes in self.unknown.items():
            if codes:
                listed = sorted(codes)
                out.append(f"Mã {ROLE_TEXT.get(role, role)} chưa có trong file ánh xạ: " + ", ".join(listed[:5])
                           + (f" và {len(listed) - 5} mã khác" if len(listed) > 5 else "")
                           + f" ({sum(codes.values())} dòng); em vẫn tính theo mã, cần chị bổ sung vào file ánh xạ")
        return out


def _delimiter(values: list, n: int) -> str:
    """The character that splits most combinations into exactly n parts."""
    counts = Counter()
    for v in values[:200]:
        for ch in set(re.sub(r"[A-Za-z0-9]", "", v)):
            if len(v.split(ch)) == n:
                counts[ch] += 1
    return counts.most_common(1)[0][0] if counts else ""


def read_segment_gl(rows: list, sheet: str, smap: SegmentMap, fy_start: int = 7) -> Optional[SegmentGL]:
    """None when the sheet is not a ledger in the map's layout."""
    n_seg = len(smap.segments)
    if not n_seg or smap.by_role("account") is None:
        return None
    t = read_table(rows, sheet, gl_spec(smap), 3)
    if t is None or not ({"ps_no", "ps_co", "tien"} & set(t.columns)):
        return None
    _prefer_accounted(t)
    seg_cols = [f"seg{s.position}" for s in smap.segments]
    found = [f for f in seg_cols if f in t.columns]
    candidates = ([t.columns["to_hop"]] if "to_hop" in t.columns else []) + [t.columns[f] for f in found]
    combo, delim = None, smap.delimiter
    for c in candidates:                         # the combination: a column whose values split into every segment
        texts = [str(v[c]) for _n, v in t.rows if c < len(v) and v[c] is not None]
        d = delim or _delimiter(texts, n_seg)
        if d and sum(len(x.split(d)) == n_seg for x in texts) * 2 >= len(texts) > 0:
            combo, delim = c, d
            break
    if combo is None and len(found) < n_seg:
        return None
    g = SegmentGL(t, delimiter=delim)
    roles = {s.role: i for i, s in enumerate(smap.segments) if s.role}
    for n, v in t.rows:
        if combo is not None:
            raw = str(v[combo]).strip() if combo < len(v) and v[combo] is not None else ""
            parts = [p.strip() for p in raw.split(delim)] if raw else []
            if len(parts) != n_seg:
                t.skipped.append((n, f"tổ hợp tài khoản có {len(parts)} phân đoạn, file ánh xạ ghi {n_seg}"))
                continue
        else:
            parts = [str(t.get(v, f, "") or "").strip() for f in seg_cols]
        dr, cr, amount = num(t.get(v, "ps_no")), num(t.get(v, "ps_co")), num(t.get(v, "tien"))
        if dr is None and cr is None:
            if amount is None:
                t.skipped.append((n, "không có số tiền"))
                continue
            dr, cr = max(amount, ZERO), max(-amount, ZERO)
        dr, cr = dr or ZERO, cr or ZERO
        _ingest(g, smap, roles, parts, dr, cr, t, v, n, fy_start)
    return g


def _prefer_accounted(t: Table) -> None:
    """'Accounted Dr/Cr' are in the books' currency; 'Entered Dr/Cr' in the document's. Take accounted when both."""
    for f, word in (("ps_no", "accounted dr"), ("ps_co", "accounted cr")):
        hit = next((c for c, h in enumerate(t.headers) if key(h) == word), None)
        if hit is not None:
            t.columns[f] = hit


def _ingest(g: SegmentGL, smap: SegmentMap, roles: dict, parts: list, dr, cr, t: Table, v, n: int, fy_start: int):
    code = {role: parts[i] for role, i in roles.items()}
    looked, missing = {}, []
    for role, c in code.items():
        if role not in smap.codes:
            continue                                   # the map says nothing about this segment: codes stand
        hit = smap.find(role, c)
        if hit is None:
            missing.append((role, c))
        looked[role] = hit or {}
    acct_row = looked.get("account", {})
    account = acct_row.get("tk") or code.get("account", "")
    element = acct_row.get("yeu_to", "")
    cost = bool(element or account.startswith(smap.opex))
    for role, c in missing:
        # An account the map lacks is said on every line. Other segments matter on the cost lines only (a balance
        # sheet line carries a default cost centre); a department fpa names itself, against the map's departments.
        if role == "account" or (cost and role != "dept"):
            g.unknown.setdefault(role, Counter())[c] += 1
    dept_row = looked.get("dept", {})
    dept = dept_row.get("bo_phan") or code.get("dept", "")
    name = lambda role: (looked.get(role, {}) or {}).get("ten") or code.get(role, "")   # noqa: E731
    period = month_key(t.get(v, "ky"), t.get(v, "ngay"), None, fy_start) or month_key(t.get(v, "ngay")) or ""
    text = str(t.get(v, "dien_giai", "") or "").strip()
    where = f"{t.sheet}!{n}"
    # Only a journal category or source that says closing marks a closing entry: 'kết chuyển' in a line's
    # description is also how an allocation or a prepaid release is written, and that is a real cost.
    closing = bool(CLOSING.search(key(t.get(v, "loai", "") or "")))
    g.postings.append(Posting(period, account, dr=dr, cr=cr, contra="911" if closing else "", where=where, text=text))
    if cost:
        if closing:
            return                                     # the closing entry to 911 is not a cost
        g.entries.append(Entry(period, dr - cr, division=name("division"), site=name("site"), channel=name("channel"),
                               dept=dept, element=element, account=account, ref=str(t.get(v, "so_ct", "") or "").strip(),
                               text=text, where=where, side="cr" if cr and not dr else "dr"))


def catalogue(smap: SegmentMap) -> tuple:
    """({department: name}, {cost element: name}) the map names, for the report's labels and the unknown-code check."""
    depts, elements = {}, {}
    for _code, row in smap.codes.get("dept", []):
        if row.get("bo_phan"):
            depts.setdefault(row["bo_phan"], row.get("ten_bo_phan") or row.get("ten") or row["bo_phan"])
    for _code, row in smap.codes.get("account", []):
        if row.get("yeu_to"):
            elements.setdefault(row["yeu_to"], row.get("ten_yeu_to") or row["yeu_to"])
    return depts, elements
