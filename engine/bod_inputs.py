"""Read a review folder: cost detail, plans (AOP, revised), last year, FX rates, cut parameters, sales,
and the client's configuration workbook. Columns are found by synonyms (engine.tables), so snake_case
exports and Vietnamese headers with title rows both work. Files are recognised by their content, not
their names; a name only breaks ties.

Amounts are Decimal đồng. Months are 'YYYY-MM'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from engine.bod_review import Entry
from engine.tables import Table, key, read_table, sheets_of

ZERO = Decimal(0)

GL_SPEC = {
    "ngay": ["ngay ct", "ngay chung tu", "ngay hach toan", "ngay", "posting date", "date"],
    "so_ct": ["so ct", "so chung tu", "so phieu", "document", "doc no"],
    "thang": ["thang", "ky", "period", "thang ke toan"],
    "nam": ["nam", "nam tai chinh", "year"],
    "phap_nhan": ["phap nhan", "site", "don vi", "cong ty", "entity"],
    "khoi": ["khoi", "division", "bo phan kinh doanh"],
    "kenh": ["kenh", "channel", "kenh ban"],
    "pb": ["phong ban ma", "ma phong ban", "ma pb", "cost center", "trung tam chi phi"],
    "pb_ten": ["phong ban ten", "ten phong ban", "phong ban"],
    "ce": ["cost element ma", "ma cost element", "ma khoan muc", "cost element", "ma ce"],
    "ce_ten": ["cost element ten", "ten cost element", "ten khoan muc"],
    "tk_no": ["tk no", "tai khoan no", "no tk", "debit account"],
    "tk_co": ["tk co", "tai khoan co", "co tk", "credit account"],
    "tk": ["tai khoan", "tk", "account", "so hieu tk"],
    "tien": ["so tien vnd", "so tien", "thanh tien", "amount vnd", "amount", "gia tri"],
    "nguyen_te": ["nguyen te", "so tien nguyen te", "amount fc", "usd"],
    "ma_tt": ["ma tien te", "loai tien", "currency"],
    "ty_gia": ["ty gia", "rate", "exchange rate"],
    "dien_giai": ["dien giai", "noi dung", "description", "dien giai chi tiet"],
}

PLAN_SPEC = {
    "phien_ban": ["phien ban", "version", "kich ban", "scenario"],
    "nam": ["nam", "nam tai chinh", "year", "fy"],
    "thang": ["thang", "period", "month"],
    "khoi": ["khoi", "division"],
    "pb": ["phong ban ma", "ma phong ban", "ma pb"],
    "ce": ["cost element ma", "ma cost element", "ma ce"],
    "dong": ["dong bod", "dong bao cao", "chi tieu", "khoan muc bod"],
    "kenh": ["kenh", "channel"],
    "tien": ["so tien vnd", "so tien", "gia tri", "amount"],
    "ty_gia": ["ty gia gia dinh", "ty gia"],
}

FX_SPEC = {
    "thang": ["thang", "ky", "month", "period"],
    "nam": ["nam", "year"],
    "loai": ["loai ty gia", "loai", "type", "kieu", "phuong phap"],
    "ma_tt": ["ma tien te", "loai tien", "currency", "tien te"],
    "ty_gia": ["ty gia", "rate", "gia tri"],
}

SALES_SPEC = {
    "ngay": ["ngay hd", "ngay hoa don", "ngay ct", "ngay", "date"],
    "thang": ["thang", "ky", "period"],
    "phap_nhan": ["phap nhan", "site", "don vi", "cong ty"],
    "khoi": ["khoi", "division"],
    "kenh": ["kenh", "channel", "kenh ban"],
    "dt_thuan": ["doanh thu thuan", "dtt", "net revenue", "net sales"],
    "dt": ["doanh thu", "thanh tien", "doanh so", "gross", "gia tri"],
    "ck": ["chiet khau", "giam gia", "discount"],
    "tra_lai": ["tra lai", "hang ban bi tra lai", "returns"],
}


def num(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    from engine.trial_balance import parse_amount
    try:
        return parse_amount(value)[0]
    except (ValueError, InvalidOperation):
        return None


def fiscal_years(nam) -> Optional[tuple]:
    """'FY2627', '2026/27', '2026-2027', 2627 -> (2026, 2027); 2026 -> (2026, None) (calendar)."""
    s = str(nam or "").strip()
    m = re.search(r"(20\d\d)\s*[/\-–]\s*(20)?(\d\d)", s)
    if m:
        return int(m.group(1)), 2000 + int(m.group(3))
    m = re.search(r"(?:fy|nd|nam)\s*(\d\d)\s*[/\-–]\s*(\d\d)\b", s, re.I) or re.fullmatch(r"(\d\d)\s*[/\-–]\s*(\d\d)", s)
    if m and int(m.group(2)) == (int(m.group(1)) + 1) % 100:
        return 2000 + int(m.group(1)), 2000 + int(m.group(2))
    m = re.search(r"(?:fy|nd)?\s*(\d\d)(\d\d)\b", s, re.I)
    if m and len(s.replace("FY", "").replace("fy", "").replace("ND", "").strip()) == 4 and int(m.group(2)) == int(m.group(1)) + 1:
        return 2000 + int(m.group(1)), 2000 + int(m.group(2))
    m = re.search(r"(20\d\d)", s)
    if m:
        return int(m.group(1)), None
    return None


def month_key(thang, ngay=None, nam=None, fy_start: int = 7) -> Optional[str]:
    """Normalise a period to 'YYYY-MM'. thang may be '2026-10', '10/2026', 10, a date; nam may be a fiscal year."""
    import datetime as dt
    for v in (thang, ngay):
        if isinstance(v, (dt.date, dt.datetime)):
            return f"{v.year:04d}-{v.month:02d}"
    s = str(thang or "").strip()
    m = re.fullmatch(r"(20\d\d)[-/.](\d{1,2})(?:[-/.]\d{1,2})?(?:[ T].*)?", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(?:T|th|thang\s*)?(\d{1,2})[-/.](20\d\d)", s, re.I)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(?:T|th|thang\s*)?(\d{1,2})(?:\.0)?", s, re.I)
    if m and 1 <= int(m.group(1)) <= 12:
        month = int(m.group(1))
        years = fiscal_years(nam)
        if years:
            first, second = years
            year = first if second is None or month >= fy_start else second
            return f"{year:04d}-{month:02d}"
        d = month_key(ngay) if ngay else None
        if d:
            return f"{d[:4]}-{month:02d}"
    if ngay is not None and ngay is not thang:
        s = str(ngay).strip()
        m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d)", s)
        if m:
            return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}"
        m = re.match(r"(20\d\d)[/.-](\d{1,2})", s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return None


# ── Configuration workbook ──────────────────────────────────────────────────────

CONFIG_SPECS = {
    "depts": {"ma": ["phong ban ma", "ma phong ban", "ma pb", "ma"], "ten": ["phong ban ten", "ten phong ban", "ten"],
              "khoi": ["khoi", "division"], "cha": ["phong cha", "ma cha", "cap tren"],
              "khong_cat": ["khong cat", "mien cat", "bao ve", "khong cat giam", "danh sach khong cat"]},
    "elements": {"ma": ["cost element ma", "ma cost element", "ma ce", "ma khoan muc", "ma"],
                 "ten": ["cost element ten", "ten cost element", "ten khoan muc", "ten"],
                 "tk": ["tai khoan", "tk", "so hieu tk", "account"], "nhom": ["nhom", "group cost", "nhom chi phi"],
                 "dong": ["dong bod", "dong bao cao"]},
    "channels": {"kenh": ["kenh", "channel", "ma kenh", "ten kenh"],
                 "mau_so": ["mau so", "mau so dtt", "mau so %dtt", "co so tinh", "pham vi mau so", "dtt dung de chia"]},
    "bod": {"dong": ["dong bod", "dong bao cao", "chi tieu bod", "chi tieu"],
            "tk": ["tai khoan", "tk", "dau tk"], "ce": ["cost element", "ma ce", "ma cost element"],
            "nhom": ["nhom", "group cost"]},
    "rules": {"tham_so": ["tham so", "quy tac", "ten tham so", "khoa", "muc", "noi dung"],
              "gia_tri": ["gia tri", "value", "thiet lap"]},
}


@dataclass
class Config:
    depts: dict = field(default_factory=dict)        # code -> {"ten", "khoi", "cha", "khong_cat": bool}
    elements: dict = field(default_factory=dict)     # code -> {"ten", "tk", "nhom", "dong"}
    channels: dict = field(default_factory=dict)     # folded channel -> (name as printed, base text)
    bod: list = field(default_factory=list)          # [{"dong", "tk", "ce", "nhom"}]
    rules: dict = field(default_factory=dict)        # folded key -> value
    rule_text: dict = field(default_factory=dict)    # folded key -> key as printed

    def rule(self, *words, default=None):
        """First rule whose key contains every word (folded)."""
        for k, v in self.rules.items():
            if all(key(w) in k for w in words):
                return v
        return default

    def no_cut(self) -> set:
        names = {d["ten"] for d in self.depts.values() if d.get("khong_cat")} | {c for c, d in self.depts.items() if d.get("khong_cat")}
        listed = self.rule("khong cat")
        if isinstance(listed, str):
            names |= {p.strip() for p in re.split(r"[;,\n]", listed) if p.strip()}
        return names


def _truthy(v) -> bool:
    return key(v) in ("x", "co", "yes", "y", "true", "1", "khong cat", "dung")


def read_config(path: Path) -> Config:
    cfg = Config()
    for sheet, rows in sheets_of(path).items():
        for kind, spec in CONFIG_SPECS.items():
            need = 2
            t = read_table(rows, sheet, spec, need)
            if t is None:
                continue
            if kind == "depts" and "ma" in t.columns and "ten" in t.columns and not ({"tk", "mau_so"} & set(t.columns)):
                # a cost element sheet also has ma/ten: departments need a division, parent or no-cut column
                if not ({"khoi", "cha", "khong_cat"} & set(t.columns)) or "cost element" in key(sheet) or "khoan muc" in key(sheet):
                    continue
                for _, v in t.rows:
                    code = str(t.get(v, "ma", "")).strip()
                    if code:
                        cfg.depts[code] = {"ten": str(t.get(v, "ten", code)).strip(), "khoi": str(t.get(v, "khoi", "")).strip(),
                                           "cha": str(t.get(v, "cha", "")).strip(), "khong_cat": _truthy(t.get(v, "khong_cat", ""))}
            elif kind == "elements" and "ma" in t.columns and ("tk" in t.columns or "nhom" in t.columns) and "mau_so" not in t.columns:
                if "phong ban" in key(sheet) or "kenh" in key(sheet):
                    continue
                for _, v in t.rows:
                    code = str(t.get(v, "ma", "")).strip()
                    if code:
                        cfg.elements[code] = {"ten": str(t.get(v, "ten", code)).strip(), "tk": str(t.get(v, "tk", "")).strip(),
                                              "nhom": str(t.get(v, "nhom", "")).strip(), "dong": str(t.get(v, "dong", "")).strip()}
            elif kind == "channels" and {"kenh", "mau_so"} <= set(t.columns):
                for _, v in t.rows:
                    name = str(t.get(v, "kenh", "")).strip()
                    if name:
                        cfg.channels[key(name)] = (name, str(t.get(v, "mau_so", "")).strip())
            elif kind == "bod" and "dong" in t.columns and ({"tk", "ce", "nhom"} & set(t.columns)):
                for _, v in t.rows:
                    cfg.bod.append({f: str(t.get(v, f, "")).strip() for f in ("dong", "tk", "ce", "nhom")})
            elif kind == "rules" and {"tham_so", "gia_tri"} <= set(t.columns):
                for _, v in t.rows:
                    k = t.get(v, "tham_so")
                    if k is not None:
                        cfg.rules[key(k)] = t.get(v, "gia_tri")
                        cfg.rule_text[key(k)] = str(k)
    return cfg


# ── Detail, plans, rates, sales ─────────────────────────────────────────────────

def _cost_account(acct: str, prefixes) -> bool:
    return any(acct.startswith(p) for p in prefixes)


def read_gl(t: Table, cost_prefixes=("6",), fy_start: int = 7) -> list:
    """Cost detail lines. With debit and credit account columns, a cost account on the credit side is a negative cost."""
    out = []
    for n, v in t.rows:
        amount = num(t.get(v, "tien"))
        if amount is None:
            t.skipped.append((n, "không có số tiền"))
            continue
        acct = str(t.get(v, "tk", "") or "").strip()
        dr, cr = str(t.get(v, "tk_no", "") or "").strip(), str(t.get(v, "tk_co", "") or "").strip()
        if dr or cr:
            if _cost_account(dr, cost_prefixes):
                acct = dr
            elif _cost_account(cr, cost_prefixes):
                acct, amount = cr, -amount
            else:
                acct = dr or cr
        period = month_key(t.get(v, "thang"), t.get(v, "ngay"), t.get(v, "nam"), fy_start)
        fc = num(t.get(v, "nguyen_te"))
        cur = str(t.get(v, "ma_tt", "") or "").strip().upper()
        out.append(Entry(period or "", amount, division=str(t.get(v, "khoi", "") or "").strip(),
                         site=str(t.get(v, "phap_nhan", "") or "").strip(), channel=str(t.get(v, "kenh", "") or "").strip(),
                         dept=str(t.get(v, "pb", "") or "").strip(), element=str(t.get(v, "ce", "") or "").strip(),
                         account=acct, usd=fc if fc is not None and (cur in ("", "USD")) else None,
                         ref=str(t.get(v, "so_ct", "") or "").strip(), text=str(t.get(v, "dien_giai", "") or "").strip(),
                         where=f"{t.sheet}!{n}"))
    return out


@dataclass
class PlanRow:
    version: str
    month: str
    amount: Decimal
    division: str = ""
    dept: str = ""
    element: str = ""
    line: str = ""
    channel: str = ""
    rate: Optional[Decimal] = None
    where: str = ""


def read_plan(t: Table, default_version: str = "", fy_start: int = 7) -> list:
    out = []
    for n, v in t.rows:
        amount = num(t.get(v, "tien"))
        if amount is None:
            t.skipped.append((n, "không có số tiền"))
            continue
        out.append(PlanRow(str(t.get(v, "phien_ban", default_version) or default_version).strip(),
                           month_key(t.get(v, "thang"), None, t.get(v, "nam"), fy_start) or "", amount,
                           str(t.get(v, "khoi", "") or "").strip(), str(t.get(v, "pb", "") or "").strip(),
                           str(t.get(v, "ce", "") or "").strip(), str(t.get(v, "dong", "") or "").strip(),
                           str(t.get(v, "kenh", "") or "").strip(), num(t.get(v, "ty_gia")), f"{t.sheet}!{n}"))
    return out


def rate_type(text) -> str:
    """Folded rate type: 'binh quan', 'cuoi ky', 'aop', 'dau ky', or the folded text."""
    k = key(text)
    for word, name in (("binh quan", "binh quan"), ("trung binh", "binh quan"), ("average", "binh quan"), ("avg", "binh quan"),
                       ("cuoi ky", "cuoi ky"), ("cuoi thang", "cuoi ky"), ("closing", "cuoi ky"), ("dau ky", "dau ky"),
                       ("aop", "aop"), ("ke hoach", "aop"), ("budget", "aop"), ("gia dinh", "aop")):
        if word in k:
            return name
    return k


def read_fx(t: Table, fy_start: int = 7) -> dict:
    """{(month, type): rate}. A table with one rate column per type (wide) is read too."""
    rates = {}
    if "loai" in t.columns:
        for n, v in t.rows:
            r = num(t.get(v, "ty_gia"))
            m = month_key(t.get(v, "thang"), None, t.get(v, "nam"), fy_start)
            if r is not None and m:
                rates[(m, rate_type(t.get(v, "loai")))] = r
        return rates
    type_cols = [(i, rate_type(h)) for i, h in enumerate(t.headers) if rate_type(h) in ("binh quan", "cuoi ky", "aop", "dau ky")]
    for n, v in t.rows:
        m = month_key(t.get(v, "thang"), None, t.get(v, "nam"), fy_start)
        for i, typ in type_cols:
            r = num(v[i] if i < len(v) else None)
            if r is not None and m:
                rates[(m, typ)] = r
    return rates


@dataclass
class Sale:
    month: str
    net: Decimal
    site: str = ""
    division: str = ""
    channel: str = ""


def read_sales(t: Table, fy_start: int = 7) -> list:
    out = []
    for n, v in t.rows:
        net = num(t.get(v, "dt_thuan"))
        if net is None:
            gross = num(t.get(v, "dt"))
            if gross is None:
                t.skipped.append((n, "không có doanh thu"))
                continue
            net = gross - abs(num(t.get(v, "ck")) or ZERO) - abs(num(t.get(v, "tra_lai")) or ZERO)
        out.append(Sale(month_key(t.get(v, "thang"), t.get(v, "ngay"), None, fy_start) or "", net,
                        str(t.get(v, "phap_nhan", "") or "").strip(), str(t.get(v, "khoi", "") or "").strip(),
                        str(t.get(v, "kenh", "") or "").strip()))
    return out


# ── The folder ──────────────────────────────────────────────────────────────────

@dataclass
class Bundle:
    folder: Path
    config: Config = field(default_factory=Config)
    gl: list = field(default_factory=list)
    plans: list = field(default_factory=list)          # PlanRow, every version (AOP, revised, last year)
    fx: dict = field(default_factory=dict)
    sales: list = field(default_factory=list)
    cut: list = field(default_factory=list)            # (sheet, rows) of the cut parameters, read by the reviewer
    packs: list = field(default_factory=list)          # paths of BOD drafts
    receipt: list = field(default_factory=list)        # [(file, role, used rows, skipped [(row, reason)])]
    unused: list = field(default_factory=list)         # [(file, reason)]


PACK_WORDS = ("pack", "bod", "ban nhap", "bao cao hdqt", "trinh")


def name_key(stem: str) -> str:
    """'DanhMuc_CauHinh' -> 'danh muc cau hinh': file names are often written in CamelCase."""
    return key(re.sub(r"(?<=[a-zà-ỹđ])(?=[A-ZÀ-ỸĐ])", " ", str(stem)))


def load(folder: Path, fy_start: int = 7) -> Bundle:
    folder = Path(folder)
    b = Bundle(folder)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xlsm"))
    rest = []
    for p in files:                                            # configuration first: its rules shape the rest
        if any(w in name_key(p.stem) for w in ("danh muc", "cau hinh", "config")):
            b.config = read_config(p)
            b.receipt.append((p.name, "Cấu hình", len(b.config.depts) + len(b.config.elements) + len(b.config.channels), []))
        else:
            rest.append(p)
    fy = b.config.rule("thang bat dau") or b.config.rule("nien do")
    fy_start = int(num(fy)) if num(fy) is not None and 1 <= int(num(fy)) <= 12 else fy_start
    for p in rest:
        k = name_key(p.stem)
        if any(w in k for w in PACK_WORDS) and p.suffix.lower() != ".csv":
            b.packs.append(p)
            b.receipt.append((p.name, "Bộ BOD cần soát", 0, []))
            continue
        try:
            sheets = sheets_of(p)
        except Exception as exc:                               # noqa: BLE001 - say it, never skip in silence
            b.unused.append((p.name, f"không đọc được: {exc}"))
            continue
        used = False
        for sheet, rows in sheets.items():
            if "cat giam" in k or "cut" in k:
                b.cut.append((sheet, rows))
                b.receipt.append((p.name, "Tham số cắt giảm", len(rows), []))
                used = True
                break
            t = read_table(rows, sheet, FX_SPEC, 2, required=("ty_gia",))
            if t is not None and ("ty gia" in k or "loai" in t.columns or "fx" in k):
                rates = read_fx(t, fy_start)
                if rates:
                    b.fx.update(rates)
                    b.receipt.append((p.name, "Tỷ giá", len(t.rows), t.skipped))
                    used = True
                    continue
            t = read_table(rows, sheet, GL_SPEC, 4, required=("tien",))
            if t is not None and ({"tk", "tk_no", "tk_co"} & set(t.columns)) and ({"pb", "ce"} & set(t.columns)):
                b.gl += read_gl(t, fy_start=fy_start)
                b.receipt.append((p.name, "Sổ chi tiết chi phí", len(t.rows), t.skipped))
                used = True
                continue
            t = read_table(rows, sheet, PLAN_SPEC, 3, required=("tien",))
            if t is not None and ({"phien_ban", "dong", "pb", "ce"} & set(t.columns)) and "thang" in t.columns:
                version = "" if "phien_ban" in t.columns else _version_from_name(p.stem)
                b.plans += read_plan(t, version, fy_start)
                b.receipt.append((p.name, "Kế hoạch" if "nam truoc" not in k else "Năm trước", len(t.rows), t.skipped))
                used = True
                continue
            t = read_table(rows, sheet, SALES_SPEC, 3)
            if t is not None and ({"dt_thuan", "dt"} & set(t.columns)) and "kenh" in t.columns:
                b.sales += read_sales(t, fy_start)
                b.receipt.append((p.name, "Bán hàng", len(t.rows), t.skipped))
                used = True
                continue
        if not used:
            b.unused.append((p.name, "không thuộc phần soát bộ BOD (vd. bảng cân đối dùng cho lớp chốt số)"))
    return b


def _version_from_name(stem: str) -> str:
    k = key(stem)
    for word, name in (("aopr2", "AOPR2"), ("aopr1", "AOPR1"), ("aopr", "AOPR"), ("aop", "AOP"), ("muf", "MUF"),
                       ("nam truoc", "LY")):
        if word in k.replace(" ", ""):
            return name
    return stem
