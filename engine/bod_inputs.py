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
from engine.trial_balance import fold
from engine.tables import HEADER_SCAN, Table, find_header, header_texts, key, matches, read_table, sheets_of

ZERO = Decimal(0)

# A cost element column under its management-reporting names, in a plan, a ledger or the catalogue.
ELEMENT_WORDS = ["yeu to chi phi", "yeu to chi phi quan tri", "ma yeu to", "ma yeu to chi phi", "nhom chi phi",
                 "nhom chi phi quan tri", "cost element"]

GL_SPEC = {
    "ngay": ["ngay ct", "ngay chung tu", "ngay hach toan", "ngay", "posting date", "date"],
    "so_ct": ["so ct", "so chung tu", "so phieu", "document", "doc no"],
    "thang": ["thang", "ky", "period", "thang ke toan"],
    "nam": ["nam", "nam tai chinh", "year"],
    "phap_nhan": ["phap nhan", "site", "don vi", "cong ty", "entity", "nha may", "plant", "chi nhanh"],
    "khoi": ["khoi", "division", "bo phan kinh doanh", "khoi kinh doanh", "nganh hang"],
    "kenh": ["kenh", "channel", "kenh ban", "kenh phan phoi"],
    "pb": ["phong ban ma", "ma phong ban", "ma pb", "cost center", "trung tam chi phi", "ma bo phan"],
    "pb_ten": ["phong ban ten", "ten phong ban", "phong ban"],
    "ce": ["cost element ma", "ma cost element", "ma khoan muc", "cost element", "ma ce"] + ELEMENT_WORDS,
    "ce_ten": ["cost element ten", "ten cost element", "ten khoan muc"],
    "tk_no": ["tk no", "tai khoan no", "no tk", "debit account"],
    "tk_co": ["tk co", "tai khoan co", "co tk", "credit account"],
    "tk_du": ["tk doi ung", "tai khoan doi ung", "tkdu", "tk du", "doi ung", "contra account", "offset account"],
    "tk": ["tai khoan", "tk", "account", "so hieu tk", "ma tai khoan"],
    "tien": ["so tien vnd", "so tien", "thanh tien", "amount vnd", "amount", "gia tri"],
    # A ledger export with the two sides in their own columns instead of one amount: amount = Nợ - Có.
    "ps_no": ["phat sinh no", "ps no", "so phat sinh no", "so tien no", "no", "debit", "debit amount", "accounted dr",
              "dr"],
    "ps_co": ["phat sinh co", "ps co", "so phat sinh co", "so tien co", "co", "credit", "credit amount", "accounted cr",
              "cr"],
    "nguyen_te": ["nguyen te", "so tien nguyen te", "amount fc", "usd"],
    "ma_tt": ["ma tien te", "loai tien", "currency"],
    "ty_gia": ["ty gia", "rate", "exchange rate"],
    "dien_giai": ["dien giai", "noi dung", "description", "dien giai chi tiet"],
}

PLAN_SPEC = {
    "phien_ban": ["phien ban", "version", "kich ban", "scenario", "loai so lieu"],
    "nam": ["nam", "nam tai chinh", "year", "fy", "nien do"],
    "thang": ["thang", "period", "month", "ky"],
    "site": ["site", "phap nhan", "nha may", "don vi", "plant", "chi nhanh", "entity"],
    "khoi": ["khoi", "division", "khoi kinh doanh", "nganh hang"],
    "pb": ["phong ban ma", "ma phong ban", "ma pb", "phong ban", "cost center", "ma bo phan"],
    "ce": ["cost element ma", "ma cost element", "ma ce", "cost element", "ma khoan muc"] + ELEMENT_WORDS,
    "dong": ["dong bod", "dong bao cao", "chi tieu", "khoan muc bod", "khoan muc", "hang muc", "noi dung", "line item"],
    "kenh": ["kenh", "channel", "kenh phan phoi"],
    "tien": ["so tien vnd", "so tien", "gia tri", "amount", "thanh tien"],
    "ty_gia": ["ty gia gia dinh", "ty gia"],
    "ngay_duyet": ["ngay duyet", "ngay phe duyet", "approved", "approval date", "ngay ban hanh", "duyet ngay"],
}

FX_SPEC = {
    "thang": ["thang", "ky", "month", "period"],
    "nam": ["nam", "year"],
    "loai": ["loai ty gia", "loai", "type", "kieu", "phuong phap"],
    "ma_tt": ["ma tien te", "loai tien", "currency", "tien te"],
    "ty_gia": ["ty gia", "rate", "gia tri"],
}

SKU_WORDS = ["ma hang", "ma sp", "ma san pham", "sku", "ma hang hoa", "ma hh", "item code", "product code", "ma vat tu",
             "ma vt", "ma thanh pham"]
SKU_NAME_WORDS = ["ten hang", "ten san pham", "ten sp", "ten hang hoa", "product name", "item name", "ten vat tu"]
QTY_WORDS = ["so luong", "sl", "san luong", "qty", "quantity", "volume", "san luong ban"]
UNIT_WORDS = ["dvt", "don vi tinh", "dv tinh", "uom", "unit of measure"]
UNIT_COST_WORDS = ["don gia von", "gia von don vi", "unit cost", "gia thanh don vi", "cost per unit", "gia von chuan",
                   "gia thanh chuan", "standard cost", "gia von dinh muc", "gia thanh dinh muc"]
UNIT_PRICE_WORDS = ["don gia", "gia ban", "gia ban thuan", "unit price", "net price", "gia ban binh quan", "price"]
COGS_WORDS = ["gia von", "gia von hang ban", "tri gia von", "cogs", "cost of goods sold", "tien von", "gvhb"]

SALES_SPEC = {
    "ngay": ["ngay hd", "ngay hoa don", "ngay ct", "ngay chung tu", "ngay", "date", "invoice date"],
    "thang": ["thang", "ky", "period", "month"],
    "nam": ["nam", "nam tai chinh", "year", "fy", "nien do"],
    "phap_nhan": ["phap nhan", "site", "don vi", "cong ty", "nha may", "plant", "chi nhanh", "entity"],
    "khoi": ["khoi", "division", "khoi kinh doanh", "nganh hang", "bu"],
    "kenh": ["kenh", "channel", "kenh ban", "kenh phan phoi", "kenh bh", "sales channel", "nhom khach hang",
             "loai khach hang", "customer group"],
    "dt_thuan": ["doanh thu thuan", "dtt", "net revenue", "net sales", "dt thuan", "net amount", "doanh thu sau chiet khau",
                 "doanh thu sau ck", "revenue net"],
    "dt": ["doanh thu", "thanh tien", "doanh so", "gross", "gia tri", "revenue", "sales amount", "tien hang", "gross sales",
           "doanh thu gop", "doanh thu ban hang", "amount"],
    "ck": ["chiet khau", "giam gia", "discount", "ck thuong mai"],
    "tra_lai": ["tra lai", "hang ban bi tra lai", "returns", "hang tra lai"],
    "ma_hang": SKU_WORDS,
    "ten_hang": SKU_NAME_WORDS,
    "sl": QTY_WORDS,
    "dvt": UNIT_WORDS,
    "don_gia_von": UNIT_COST_WORDS,           # before gia_von: 'Đơn giá vốn' is a cost per unit, not the cost
    "gia_von": COGS_WORDS,
}

# The plan by SKU: volume with a price and a cost per unit, or revenue and cost of goods sold.
SKU_PLAN_SPEC = {
    "phien_ban": PLAN_SPEC["phien_ban"],
    "nam": PLAN_SPEC["nam"],
    "thang": PLAN_SPEC["thang"],
    "kenh": PLAN_SPEC["kenh"],
    "ma_hang": SKU_WORDS,
    "ten_hang": SKU_NAME_WORDS,
    "sl": QTY_WORDS,
    "dvt": UNIT_WORDS,
    "don_gia_von": UNIT_COST_WORDS,
    "don_gia": UNIT_PRICE_WORDS,
    "dt": ["doanh thu thuan", "dtt", "net revenue", "net sales", "doanh thu", "revenue", "doanh so"],
    "gia_von": COGS_WORDS,
    "ngay_duyet": PLAN_SPEC["ngay_duyet"],
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
    if not m:
        f_ = re.sub(r"\s+", " ", fold(s).lower()).strip()
        en = re.fullmatch(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ \-/.']*(20\d\d|\d\d)", f_)
        if en:
            y = int(en.group(2)) + (2000 if len(en.group(2)) == 2 else 0)
            mo = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec").index(en.group(1)) + 1
            return f"{y:04d}-{mo:02d}"
        vi = re.fullmatch(r"(?:thang|th|t|ky)\s*(\d{1,2})\s*(?:[-/.]|nam|,)?\s*(20\d\d)", f_)
        if vi and 1 <= int(vi.group(1)) <= 12:
            return f"{int(vi.group(2)):04d}-{int(vi.group(1)):02d}"
        s = f_ if re.fullmatch(r"(?:thang|th|t)\s*\d{1,2}(?:\.0)?", f_) else s
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(?:T|th|thang\s*)?(\d{1,2})[-/.](20\d\d)", s, re.I)
    if m:
        # '13-2026' is a 13th (adjusting) period, never a month
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}" if 1 <= int(m.group(1)) <= 12 else None
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
              "khong_cat": ["khong cat", "mien cat", "bao ve", "khong cat giam", "danh sach khong cat"],
              "email": ["email", "e mail", "dia chi email", "thu dien tu", "email nguoi nhan", "nguoi nhan"],
              "cc": ["cc", "email cc", "cc email", "dong gui", "gui kem"]},
    "elements": {"ma": ["cost element ma", "ma cost element", "ma ce", "ma khoan muc", "ma", "khoan muc"]
                 + [w for w in ELEMENT_WORDS if not w.startswith("nhom")],     # 'Nhóm chi phí' is the group here
                 "ten": ["cost element ten", "ten cost element", "ten khoan muc", "ten"],
                 "tk": ["tai khoan", "tk", "so hieu tk", "account"], "nhom": ["nhom", "group cost", "nhom chi phi"],
                 "dong": ["dong bod", "dong bao cao"]},
    "channels": {"kenh": ["kenh", "channel", "ma kenh", "channel code", "kenh ma"],
                 "ten_kenh": ["ten kenh", "channel name", "kenh ten", "dien giai kenh"],
                 "mau_so": ["mau so", "mau so dtt", "mau so %dtt", "co so tinh", "pham vi mau so", "dtt dung de chia"]},
    "bod": {"dong": ["dong bod", "dong bao cao", "chi tieu bod", "chi tieu"],
            "tk": ["tai khoan", "tk", "dau tk"], "ce": ["cost element", "ma ce", "ma cost element"],
            "nhom": ["nhom", "group cost"]},
    "rules": {"tham_so": ["tham so", "quy tac", "ten tham so", "khoa", "muc", "noi dung"],
              "gia_tri": ["gia tri", "value", "thiet lap"]},
}
# SKUs and their units: 'Mã hàng | ĐVT chuẩn' and, where a SKU is sold in other units, 'ĐVT | Hệ số quy đổi'
# (1 ĐVT = hệ số ĐVT chuẩn). A row without a SKU converts that unit for every SKU with that standard unit.
SKU_SPEC = {"ma_hang": SKU_WORDS, "ten_hang": SKU_NAME_WORDS,
            "dvt_chuan": ["dvt chuan", "don vi chuan", "don vi tinh chuan", "dvt co so", "dvt quy doi", "base unit",
                          "standard unit"],
            "dvt": UNIT_WORDS + ["dvt ban", "dvt khac", "don vi ban"],
            "he_so": ["he so quy doi", "he so", "ty le quy doi", "quy doi", "conversion factor", "factor"],
            # The pack size: 'Quy cách' = '12 chai/thùng', or a number under a header that names both units.
            "quy_cach": ["quy cach", "quy cach dong goi", "dong goi", "pack size", "units per carton", "units per case",
                         "so chai thung", "so lon thung", "so goi thung", "so hop thung", "so chai/thung", "so lon/thung",
                         "chai/thung", "lon/thung", "goi/thung", "hop/thung", "chai thung", "lon thung"]}

_N = r"(\d+(?:[.,]\d+)?)"
PACK_RE = (re.compile(_N + r"\s*([a-z]+)\s*(?:/|tren|moi|per)\s*([a-z]+)"),                  # 12 chai/thung -> n small big
           re.compile(r"^1\s*([a-z]+)\s*(?:=|:|gom|co)\s*" + _N + r"\s*([a-z]+)"),         # 1 thung = 24 chai -> big n small
           re.compile(r"^([a-z]+)\s+" + _N + r"\s*([a-z]+)\b"),                             # thung 24 lon -> big n small
           re.compile(r"^" + _N + r"\s*([a-z]+)\b"))                                         # 24 lon x 330ml -> n small
UNIT_PAIR_RE = re.compile(r"\b([a-z]+)\s*(?:/|tren|moi|per)\s*([a-z]+)\b")                # so chai/thung


@dataclass
class Config:
    depts: dict = field(default_factory=dict)        # code -> {"ten", "khoi", "cha", "khong_cat": bool, "email", "cc"}
    elements: dict = field(default_factory=dict)     # code -> {"ten", "tk", "nhom", "dong"}
    channels: dict = field(default_factory=dict)     # folded code or name -> (name as printed, base text)
    channel_aliases: dict = field(default_factory=dict)  # folded name -> {folded code, folded name}
    meanings: dict = field(default_factory=dict)     # folded term -> texts printed beside it anywhere in the catalogue

    def explain(self, term: str) -> str:
        """A rule name with what the catalogue says it means: 'DTT của site' -> '... (DTT của chính site, mọi khối)'."""
        said = [m for m in self.meanings.get(key(term), []) if key(m) != key(term)]
        return f"{term} ({'; '.join(said)})" if said else term
    bod: list = field(default_factory=list)          # [{"dong", "tk", "ce", "nhom"}]
    skus: dict = field(default_factory=dict)         # SKU code -> {"ten", "dvt": standard unit as printed}
    units: dict = field(default_factory=dict)        # (SKU or "", folded unit) -> (factor to the standard, folded standard)
    rules: dict = field(default_factory=dict)        # folded key -> value
    rule_text: dict = field(default_factory=dict)    # folded key -> key as printed
    # Every sheet keyed by account: [{"sheet", "accounts": [account as printed], "columns": {header: [text]}}].
    # Which column holds the cost element is decided with the plan in hand (pair_codes), not by its header.
    account_maps: list = field(default_factory=list)

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


def _read_skus(cfg: Config, rows: list, sheet: str) -> bool:
    """The SKU catalogue and unit conversions in one sheet; True when the sheet is one."""
    t = read_table(rows, sheet, SKU_SPEC, 2)
    if t is None or not ({"dvt_chuan", "dvt"} & set(t.columns)) or not ({"ma_hang", "he_so"} & set(t.columns)):
        return False
    if "ma_hang" in t.columns and any(w in key(t.headers[t.columns["ma_hang"]]).split() for w in ("khach", "kh", "ncc")):
        return False
    pack_head = str(t.headers[t.columns["quy_cach"]]) if "quy_cach" in t.columns else ""
    for n, v in t.rows:
        sku = str(t.get(v, "ma_hang", "") or "").strip()
        std = str(t.get(v, "dvt_chuan", "") or "").strip()
        unit = str(t.get(v, "dvt", "") or "").strip()
        factor = num(t.get(v, "he_so"))
        if not std and unit and factor in (None, 1):
            std = unit                                      # a catalogue with one unit column: it is the standard
        if sku and std:
            cfg.skus.setdefault(sku, {"ten": str(t.get(v, "ten_hang", "") or "").strip(), "dvt": std})
        if unit and std and factor and key(unit) != key(std):
            cfg.units[(sku, key(unit))] = (factor, key(std))
        elif sku and not std:
            t.skipped.append((n, "không có đơn vị tính chuẩn"))
        if sku and std:
            _pack(cfg, sku, key(std), t.get(v, "quy_cach"), pack_head)
    return True


def pack_size(value, header: str = "") -> Optional[tuple]:
    """(count, small unit, big unit) from a pack size: '12 chai/thùng', 'Thùng 24 lon', or a number under a header
    that names both units ('Số chai/thùng' = 12). None when it does not say both."""
    if value is None or str(value).strip() == "":
        return None
    from engine.trial_balance import fold
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        pair = UNIT_PAIR_RE.search(fold(header))
        n = num(value)
        return (n, pair.group(1), pair.group(2)) if pair and n and n > 0 else None
    k = fold(str(value))
    n = lambda text: num(text.replace(",", "."))               # noqa: E731
    m = PACK_RE[0].search(k)
    if m:
        return n(m.group(1)), m.group(2), m.group(3)
    for rx in PACK_RE[1:3]:
        m = rx.search(k)
        if m and m.group(1) != m.group(3):
            return n(m.group(2)), m.group(3), m.group(1)
    m = PACK_RE[3].search(k)
    if m:
        return n(m.group(1)), m.group(2), ""                 # the big unit is the catalogue's standard
    return None


def _pack(cfg, sku: str, std: str, value, header: str) -> None:
    """The pack size converts the small unit to the standard (or the standard to the big one). Kept exact: a
    count divides, it is never stored as a rounded fraction. An explicit conversion factor is never replaced."""
    got = pack_size(value, header)
    if got is None or not got[0]:
        return
    n, small, big = got
    big = big or (std if small != std else "")
    if big == std and small != std:
        cfg.units.setdefault((sku, small), (Decimal(1), std, n))     # 24 lon = 1 thùng: lon / 24
    elif small == std and big != std:
        cfg.units.setdefault((sku, big), (n, std))                    # 1 thùng = 24 lon


ACCOUNT_HEAD = ["so hieu tk", "so hieu tai khoan", "tai khoan", "tk", "ma tk", "ma tai khoan", "account",
                "account code", "gl account", "tai khoan ke toan"]
# The account's own name is never the cost element, whatever else the sheet calls it.
ACCOUNT_NAME_HEAD = ["ten tai khoan", "ten tk", "account name", "ten tai khoan ke toan"]


def account_name_head(header: str, sheet_by_account: bool = True) -> bool:
    """'Tên tài khoản', or a bare 'Tên' / 'Diễn giải' on a sheet keyed by account."""
    return matches(header, ACCOUNT_NAME_HEAD) >= 2 or (sheet_by_account and key(header) in ("ten", "dien giai"))
ACCOUNT_RE = re.compile(r"^\d{3,}[x*]*$")


def account_prefix(text) -> str:
    """'6411xx' -> '6411': an account written with x for 'any sub-account'."""
    return re.sub(r"[x*]+$", "", str(text or "").strip().lower())


def _read_account_map(cfg: Config, rows: list, sheet: str) -> None:
    """Keep a sheet whose rows are keyed by account, with its other text columns, for pair_codes to choose from."""
    for r, values in enumerate(rows[:HEADER_SCAN]):
        heads = ["" if v is None else str(v).strip() for v in (values or [])]
        acc = next((c for c, h in enumerate(heads) if h and matches(h, ACCOUNT_HEAD) == 3), None)
        if acc is None:
            continue
        data = [list(x or []) for x in rows[r + 1:]]
        data = [x for x in data if acc < len(x) and ACCOUNT_RE.match(str(x[acc] or "").strip().lower())]
        if len(data) < 2:
            continue
        cols = {}
        for c, h in enumerate(heads):
            if c == acc or not h or account_name_head(h) or matches(h, ACCOUNT_HEAD) == 3:
                continue
            texts = ["" if c >= len(x) or x[c] is None else str(x[c]).strip() for x in data]
            if any(texts) and not any(isinstance(x[c], (int, float)) for x in data if c < len(x) and x[c] is not None):
                cols[h] = texts
        if cols:
            cfg.account_maps.append({"sheet": sheet, "accounts": [str(x[acc]).strip() for x in data], "columns": cols})
        return


def read_config(path: Path) -> Config:
    cfg = Config()
    for sheet, rows in sheets_of(path).items():
        _read_account_map(cfg, rows, sheet)
        for r in rows:                       # every 'term | what it means' pair, for rules named elsewhere
            texts = [str(v).strip() for v in (r or []) if isinstance(v, str) and str(v).strip()]
            for a, b in zip(texts, texts[1:]):
                cfg.meanings.setdefault(key(a), []).append(b)
        if _read_skus(cfg, rows, sheet):     # a SKU sheet's 'Mã hàng | Tên | Nhóm hàng' is not a cost element list
            continue
        for kind, spec in CONFIG_SPECS.items():
            need = 2
            t = read_table(rows, sheet, spec, need)
            if t is None:
                continue
            if kind == "depts" and "ma" in t.columns and "ten" in t.columns and not ({"tk", "mau_so"} & set(t.columns)):
                # a cost element sheet also has ma/ten: departments need a division, parent or no-cut column
                if not ({"khoi", "cha", "khong_cat", "email"} & set(t.columns)) or "cost element" in key(sheet) or "khoan muc" in key(sheet):
                    continue
                for _, v in t.rows:
                    code = str(t.get(v, "ma", "")).strip()
                    if code:
                        cfg.depts[code] = {"ten": str(t.get(v, "ten", code)).strip(), "khoi": str(t.get(v, "khoi", "")).strip(),
                                           "cha": str(t.get(v, "cha", "")).strip(), "khong_cat": _truthy(t.get(v, "khong_cat", "")),
                                           "email": str(t.get(v, "email", "") or "").strip(),
                                           "cc": str(t.get(v, "cc", "") or "").strip()}
            elif kind == "elements" and "ma" in t.columns and ("tk" in t.columns or "nhom" in t.columns) and "mau_so" not in t.columns:
                if "phong ban" in key(sheet) or "kenh" in key(sheet):
                    continue
                # 'Tên tài khoản' names the account, never the element: such a sheet maps accounts to elements
                account_name = "ten" in t.columns and account_name_head(t.headers[t.columns["ten"]], False)
                for _, v in t.rows:
                    code = str(t.get(v, "ma", "")).strip()
                    if code and code in cfg.elements and account_name:
                        continue                          # one element, many accounts: the account map pairs them
                    if code:
                        name = code if account_name else str(t.get(v, "ten", code)).strip()
                        cfg.elements[code] = {"ten": name, "tk": str(t.get(v, "tk", "")).strip(),
                                              "nhom": str(t.get(v, "nhom", "")).strip(), "dong": str(t.get(v, "dong", "")).strip()}
            elif kind == "channels" and {"kenh", "mau_so"} <= set(t.columns):
                for _, v in t.rows:
                    code, name = str(t.get(v, "kenh", "") or "").strip(), str(t.get(v, "ten_kenh", "") or "").strip()
                    canonical = name or code
                    if canonical:
                        rule = str(t.get(v, "mau_so", "")).strip()
                        aliases = {key(x) for x in (code, name) if x}
                        for a in aliases:                 # a channel is known by its code and by its name
                            cfg.channels[a] = (canonical, rule)
                        cfg.channel_aliases[key(canonical)] = aliases
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

UNASSIGNED_RE = re.compile(r"^(n ?a|na|none|null|nan|0+|khong|khong co|chua gan|chua phan bo|chua xac dinh|khong xac dinh|"
                           r"unassigned|unallocated|chua co|trong|blank|\?+|x+)$")


UNASSIGNED_WORDS = re.compile(r"\b(chua (gan|phan bo|xac dinh|co ma)|khong (xac dinh|phan bo|gan|ro)|unassigned|"
                              r"unallocated|not assigned|no department)\b")


def _named_unassigned(name) -> bool:
    """A department name that says there is none ('Chưa gán phòng ban'); a blank name says nothing."""
    k = key(str(name or "").replace("#", " "))
    return bool(k) and unassigned(name)


def unassigned(code) -> bool:
    """'', '-', '#N/A', 'N/A', 'Chưa gán', '0': no department."""
    k = key(str(code or "").replace("#", " "))
    return not k or bool(UNASSIGNED_RE.match(k) or UNASSIGNED_WORDS.search(k))


# The month-end closing entry (Nợ 911 / Có 6xx) moves the month's cost to the result account. It is not a cost
# written down; read as one it would wipe out the month's cost. Left out, counted and named.
CLOSING_ACCOUNT = "911"
CLOSING_WHY = "bút toán kết chuyển cuối kỳ sang TK 911, không phải chi phí phát sinh"


def used_rows(t: Table) -> int:
    """Rows of the table that gave a figure: its data rows less the ones a reader left out. With the left-out rows
    (each with its reason) it adds up to the table's lines (rule 4)."""
    left = {n for n, _why in t.skipped}
    return sum(1 for n, _v in t.rows if n not in left)


def _cost_account(acct: str, prefixes) -> bool:
    return any(acct.startswith(p) for p in prefixes)


def read_gl(t: Table, cost_prefixes=("6",), fy_start: int = 7) -> list:
    """Cost detail lines. With debit and credit account columns, a cost account on the credit side is a negative cost.
    An export with one account column and Phát sinh Nợ / Phát sinh Có: amount = Nợ - Có, the side kept (a cost
    written down is on Có); lines of other accounts and the closing entry against 911 are left out with the reason."""
    out, rows = [], {}
    two_sided = "tien" not in t.columns
    for n, v in t.rows:
        amount, side = _amount(t, v)
        if amount is None:
            t.skipped.append((n, "không có số tiền"))
            continue
        acct = str(t.get(v, "tk", "") or "").strip()
        dr, cr = str(t.get(v, "tk_no", "") or "").strip(), str(t.get(v, "tk_co", "") or "").strip()
        contra = str(t.get(v, "tk_du", "") or "").strip()
        if acct and not (dr or cr):
            if contra.startswith(CLOSING_ACCOUNT) and _cost_account(acct, cost_prefixes):
                t.skipped.append((n, CLOSING_WHY))
                continue
            if two_sided and not _cost_account(acct, cost_prefixes):
                t.skipped.append((n, f"dòng của TK {acct}, không phải tài khoản chi phí"))
                continue
        else:
            side = ""
            if two_sided:
                amount = abs(amount)            # the account columns say the side; the amount is the posting's
        if (dr.startswith(CLOSING_ACCOUNT) and _cost_account(cr, cost_prefixes)) or \
                (cr.startswith(CLOSING_ACCOUNT) and _cost_account(dr, cost_prefixes)):
            t.skipped.append((n, CLOSING_WHY))
            continue
        if dr or cr:
            if _cost_account(dr, cost_prefixes):
                acct, side = dr, "dr"
            elif _cost_account(cr, cost_prefixes):
                acct, amount, side = cr, -amount, "cr"
            else:
                acct = dr or cr
        period = month_key(t.get(v, "thang"), t.get(v, "ngay"), t.get(v, "nam"), fy_start)
        fc = num(t.get(v, "nguyen_te"))
        cur = str(t.get(v, "ma_tt", "") or "").strip().upper()
        out.append(Entry(period or "", amount, division=str(t.get(v, "khoi", "") or "").strip(),
                         site=str(t.get(v, "phap_nhan", "") or "").strip(), channel=str(t.get(v, "kenh", "") or "").strip(),
                         dept="" if unassigned(t.get(v, "pb")) or _named_unassigned(t.get(v, "pb_ten"))
                         else str(t.get(v, "pb")).strip(),
                         element=str(t.get(v, "ce", "") or "").strip(),
                         account=acct, usd=fc if fc is not None and (cur in ("", "USD")) else None,
                         ref=str(t.get(v, "so_ct", "") or "").strip(), text=str(t.get(v, "dien_giai", "") or "").strip(),
                         where=f"{t.sheet}!{n}", side=side))
        rows[id(out[-1])] = (n, day_of(t.get(v, "ngay")) if t.get(v, "ngay") else "")
    if two_sided and "tk" in t.columns and not ({"tk_du", "tk_no", "tk_co"} & set(t.columns)):
        out = _closing_without_contra(t, out, rows)
    return out


CLOSING_TEXT = re.compile(r"\b(k ?c|ket chuyen)\b.*\b911\b|\b911\b.*\b(k ?c|ket chuyen)\b")
CLOSING_NETS_WHY = ("bút toán kết chuyển cuối kỳ: dòng Có cuối tháng này bằng đúng chi phí còn lại của tài khoản "
                    "trong tháng, không phải chi phí ghi giảm")


def _closing_without_contra(t: Table, out: list, rows: dict) -> list:
    """A one-account export with no contra column still carries the month-end closing entry, as a Có line on each
    cost account. It is that entry when its text names the transfer to 911 ('K/C 911', 'kết chuyển sang 911'; never
    'kết chuyển' alone, which is also how an allocation is written), or when it is the account's largest Có line on
    the month's last posting day and the account nets to exactly zero with it. Left out with the reason, never
    taken as a cost written down."""
    closing = set()
    for e in out:
        if e.side == "cr" and CLOSING_TEXT.search(key(e.text)):
            closing.add(id(e))
    by = {}
    for e in out:
        if e.period and id(e) not in closing:
            by.setdefault((e.account, e.period), []).append(e)
    for group in by.values():
        credits = [e for e in group if e.side == "cr"]
        if not credits or sum((e.amount for e in group), ZERO) != 0:
            continue
        last = max(rows[id(e)][1] for e in group)
        on_last = [e for e in credits if rows[id(e)][1] == last]
        if last and on_last:
            closing.add(id(min(on_last, key=lambda e: e.amount)))       # the largest Có (amounts are negative)
    for e in out:
        if id(e) in closing:
            t.skipped.append((rows[id(e)][0], CLOSING_WHY if CLOSING_TEXT.search(key(e.text)) else CLOSING_NETS_WHY))
    return [e for e in out if id(e) not in closing]


@dataclass
class Posting:
    """One side of a ledger line: the account, the amount on its Nợ or Có side and the account on the other side.
    A 'TK Nợ | TK Có | Số tiền' row is two postings; a single-account row is one (a negative amount is a Có)."""
    period: str
    account: str
    dr: Decimal = ZERO
    cr: Decimal = ZERO
    contra: str = ""
    where: str = ""
    text: str = ""


def _amount(t: Table, v) -> tuple:
    """(amount, side) of a ledger row: the amount column, else Phát sinh Nợ - Phát sinh Có with the side the row
    was posted on ('dr', 'cr', '' when it does not say). (None, '') when the row has no amount at all."""
    if "tien" in t.columns:
        return num(t.get(v, "tien")), ""
    d, c = num(t.get(v, "ps_no")), num(t.get(v, "ps_co"))
    if d is None and c is None:
        return None, ""
    d, c = d or ZERO, c or ZERO
    return d - c, "cr" if c and not d else "dr" if d and not c else ""


def read_postings(t: Table, fy_start: int = 7) -> list:
    """Every posting of a ledger table, both sides, for matching to the books. Rows without an amount are left to
    read_gl, which names them on the receipt."""
    out = []
    for n, v in t.rows:
        amount, _side = _amount(t, v)
        if amount is None:
            continue
        period = month_key(t.get(v, "thang"), t.get(v, "ngay"), t.get(v, "nam"), fy_start) or ""
        text, where = str(t.get(v, "dien_giai", "") or "").strip(), f"{t.sheet}!{n}"
        dr, cr = str(t.get(v, "tk_no", "") or "").strip(), str(t.get(v, "tk_co", "") or "").strip()
        if dr or cr:
            if dr:
                out.append(Posting(period, dr, dr=amount, contra=cr, where=where, text=text))
            if cr:
                out.append(Posting(period, cr, cr=amount, contra=dr, where=where, text=text))
            continue
        acct = str(t.get(v, "tk", "") or "").strip()
        if acct:
            out.append(Posting(period, acct, dr=max(amount, ZERO), cr=max(-amount, ZERO),
                               contra=str(t.get(v, "tk_du", "") or "").strip(), where=where, text=text))
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
    site: str = ""
    month_no: Optional[int] = None      # month without a year yet: resolved by load() from the file's fiscal year
    fy: Optional[int] = None            # first year of the fiscal year, when the file or header says it
    approved: str = ""                  # 'YYYY-MM-DD' the version was approved, when the file says it
    table_division: str = ""            # folded words after 'khối' the table names once for every row (header, title)
    file: str = ""                      # the file the row was read from, so a finding on it names file, sheet and cell


def cell_ref(sheet: str, col: int, row: int) -> str:
    """'Sheet!F24' (quoted when the name needs it) for a 0-based column and a sheet row number."""
    from openpyxl.utils import get_column_letter
    name = sheet if re.fullmatch(r"[A-Za-z0-9_]+", sheet or "") else "'" + str(sheet).replace("'", "''") + "'"
    return f"{name}!{get_column_letter(col + 1)}{row}"


def day_of(value) -> str:
    """'YYYY-MM-DD' from a date cell or text ('2026-11-03', '03/11/2026'), else ''."""
    import datetime as dt
    if isinstance(value, (dt.date, dt.datetime)):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    s = str(value or "").strip()
    m = re.match(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d)", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _dims(t: Table, v) -> dict:
    out = {f: str(t.get(v, c, "") or "").strip() for f, c in (("division", "khoi"), ("dept", "pb"), ("element", "ce"),
                                                              ("line", "dong"), ("channel", "kenh"), ("site", "site"))}
    out["approved"] = day_of(t.get(v, "ngay_duyet"))
    if unassigned(out["dept"]):
        out["dept"] = ""
    return out


def _plan_month(thang, nam, fy_start: int) -> tuple:
    """('YYYY-MM' or '', month number when only the month is known, fiscal year when a year is given)."""
    m = month_key(thang, None, nam, fy_start)
    years = fiscal_years(nam)
    fy = years[0] if years and years[1] else None
    if m:
        return m, None, fy
    mk = month_key(thang, None, "2000/01", fy_start)
    return "", (int(mk[5:]) if mk else None), fy


def table_division(t: Table, rows: Optional[list] = None) -> str:
    """The division a table names once for all its rows, when it has no division column: in the label column's
    header ('Chỉ tiêu (khối Thương mại)') or in a title row above the header ('Chi phí năm trước - khối TM').
    The folded words after 'khối' ('thuong mai gia lap'): the caller keeps only a division it knows. '' when the
    table names none, or names several."""
    if "khoi" in t.columns:
        return ""
    texts = [t.headers[c] for f, c in t.columns.items() if f in ("dong", "pb", "ce") and c < len(t.headers)]
    for r in (rows or [])[:max(t.header_row - 1, 0)]:
        texts += [v for v in r if isinstance(v, str)]
    found = {m.group(1).strip() for text in texts for m in [re.search(r"(?:^| )khoi ([a-z0-9&].*)$", key(text))] if m}
    return found.pop() if len(found) == 1 else ""


def read_plan(t: Table, default_version: str = "", fy_start: int = 7, rows: Optional[list] = None,
              file_fy: Optional[int] = None) -> list:
    """Long tables (one amount column) and wide ones: one column per version ('Thực tế', 'AOP', 'AOPR1') or per
    month ('T7' .. 'T6', '07/2026'), each column read into its own rows."""
    td = table_division(t, rows)
    if "tien" in t.columns:
        out = []
        for n, v in t.rows:
            amount = num(t.get(v, "tien"))
            if amount is None:
                t.skipped.append((n, "không có số tiền"))
                continue
            month, month_no, fy = _plan_month(t.get(v, "thang"), t.get(v, "nam"), fy_start)
            out.append(PlanRow(str(t.get(v, "phien_ban", default_version) or default_version).strip(), month, amount,
                               rate=num(t.get(v, "ty_gia")), where=cell_ref(t.sheet, t.columns["tien"], n), month_no=month_no,
                               fy=fy or file_fy, table_division=td, **_dims(t, v)))
        check_totals(t, "", out, [t.columns["tien"]], None)
        return out
    from engine.bod_pack import fy_of, month_of, version_of
    from openpyxl.utils import get_column_letter
    headers = header_texts(rows, t.header_row - 1) if rows is not None else t.headers
    value_cols = []
    used = set(t.columns.values())
    for c, h in enumerate(headers):
        k = key(h)
        if c in used or not k:
            continue
        ver, mon = version_of(k), month_of(k)
        if not ver and mon is None:
            continue
        value_cols.append((c, ver, mon, fy_of(k)))
    t.month_order = [mon for _c, _v, mon, _f in value_cols if mon is not None]
    total_col = getattr(t, "year_total", None)
    total_col = total_col if total_col is not None else total_column(t, rows)
    month_cols = [c for c, _v, mon, _f in value_cols if mon is not None]
    out = []
    for n, v in t.rows:
        dims, hit = _dims(t, v), False
        for c, ver, mon, col_fy in value_cols:
            amount = num(v[c] if c < len(v) else None)
            if amount is None:
                continue
            hit = True
            version = ver or str(t.get(v, "phien_ban", default_version) or default_version).strip()
            thang = mon if mon is not None else t.get(v, "thang")
            month, month_no, fy = _plan_month(thang, t.get(v, "nam"), fy_start)
            out.append(PlanRow(version, month, amount, rate=num(t.get(v, "ty_gia")),
                               where=cell_ref(t.sheet, c, n), month_no=month_no,
                               fy=fy or col_fy or file_fy, table_division=td, **dims))
        if not hit:
            t.skipped.append((n, "không có số tiền"))
    if len(month_cols) == len(value_cols):              # months only: a full-year cell is the sum of the row
        check_totals(t, "", out, month_cols, total_col)
    return out


NOT_YEAR_WORDS = {"ca", "tong", "total", "luy", "cong", "so", "tien", "gia", "tri", "ngan", "sach", "amount", "kh",
                  "ke", "hoach", "budget", "plan"}
TOTAL_HEADER = re.compile(r"^(kh |ke hoach )?(ca nam|tong cong|tong|total|full year|fy total|cong|tong ca nam|"
                          r"tong nam|tong ke hoach)( \d{4})?$")


def year_column(t: Table) -> Optional[int]:
    """A column the reader took as the year that is really a total ('Cả năm', 'Tổng KH năm'): it leaves the year
    and is returned, to check the months against. A year column holds years, never amounts."""
    c = t.columns.get("nam")
    if c is None:
        return None
    words = set(key(t.headers[c] if c < len(t.headers) else "").split())
    if words & NOT_YEAR_WORDS:
        t.columns.pop("nam")
        return c
    return None


def total_column(t: Table, rows: Optional[list]) -> Optional[int]:
    """The plan table's own full-year column ('Cả năm', 'Tổng cộng'), when it prints one."""
    headers = header_texts(rows, t.header_row - 1) if rows is not None else t.headers
    used = set(t.columns.values())
    return next((c for c, h in enumerate(headers) if c not in used and TOTAL_HEADER.match(key(h))), None)


def check_totals(t: Table, file: str, out: list, value_cols: list, total_col: Optional[int]) -> None:
    """Rule 4 for a plan: each row's months add up to the row's own full-year cell, and each month adds up to a
    printed total row. Every difference is a sentence; nothing is absorbed."""
    issues = []
    if total_col is not None and value_cols:
        for n, v in t.rows:
            stated = num(v[total_col] if total_col < len(v) else None)
            if stated is None:
                continue
            got = [num(v[c] if c < len(v) else None) for c in value_cols]
            if any(x is not None for x in got):
                made = sum((x for x in got if x is not None), ZERO)
                if made != stated:
                    issues.append(f"dòng {n}: các tháng cộng {_fmt(made)}, cột cả năm ghi {_fmt(stated)}")
    for n, v in t.totals:
        for c in value_cols:
            stated = num(v[c] if c < len(v) else None)
            if stated is None:
                continue
            made = sum((num(r[c] if c < len(r) else None) or ZERO for rn, r in t.rows), ZERO)
            if made != stated:
                issues.append(f"dòng tổng {n}, cột {c + 1}: các dòng cộng {_fmt(made)}, dòng tổng ghi {_fmt(stated)}")
    if issues:
        t.total_issues = issues


def _fmt(x: Decimal) -> str:
    from engine.monthly_report import fmt
    return f"{fmt(x)} đ"


def month_columns(t: Table, rows: list) -> list:
    """[(column, month number)] of the columns whose header is a month ('T01', 'Tháng 7', '07/2026'), in order."""
    from engine.bod_pack import month_of
    used = set(t.columns.values())
    return [(c, month_of(key(h))) for c, h in enumerate(header_texts(rows, t.header_row - 1))
            if c not in used and key(h) and month_of(key(h)) is not None]


def plan_value_columns(t: Table, rows: list) -> int:
    """How many columns of a plan-like table carry a version or a month in their header."""
    from engine.bod_pack import month_of, version_of
    used = set(t.columns.values())
    return sum(1 for c, h in enumerate(header_texts(rows, t.header_row - 1))
               if c not in used and key(h) and (version_of(key(h)) or month_of(key(h)) is not None))


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
    sku: str = ""
    qty: Optional[Decimal] = None       # in the row's unit
    unit: str = ""                      # as printed: the row's ĐVT, else the one in the quantity header ('SL (thùng)')
    cogs: Optional[Decimal] = None      # None when the ledger has no cost of goods sold
    where: str = ""                     # 'Sheet!row'
    name: str = ""
    deductions: Decimal = ZERO          # discounts and returns the row nets off its gross revenue


def header_unit(t: Table, field_name: str) -> str:
    """The unit a column header names in brackets or after a slash: 'Số lượng (thùng)' -> 'thùng'."""
    if field_name not in t.columns:
        return ""
    h = str(t.headers[t.columns[field_name]] or "")
    m = re.search(r"\(([^)]+)\)\s*$", h) or re.search(r"/\s*([^/()]+)$", h)
    return m.group(1).strip() if m else ""


def _not_a_customer(t: Table) -> None:
    """'Mã khách hàng' holds every word of 'mã hàng': it is the customer, never the SKU."""
    if "ma_hang" in t.columns and any(w in key(t.headers[t.columns["ma_hang"]]).split() for w in ("khach", "kh", "ncc")):
        del t.columns["ma_hang"]


def _per_unit(t: Table, total: str, unit: str) -> None:
    """A money column whose header says per unit ('Giá vốn/thùng', 'Giá vốn mỗi thùng') is the unit figure."""
    if total in t.columns and unit not in t.columns:
        h = str(t.headers[t.columns[total]] or "")
        if "/" in h or re.search(r"\b(moi|per|1)\b", key(h)):
            t.columns[unit] = t.columns.pop(total)


def read_sales(t: Table, fy_start: int = 7) -> list:
    _not_a_customer(t)
    _per_unit(t, "gia_von", "don_gia_von")
    qty_unit = header_unit(t, "sl")
    out = []
    for n, v in t.rows:
        net = num(t.get(v, "dt_thuan"))
        deductions = abs(num(t.get(v, "ck")) or ZERO) + abs(num(t.get(v, "tra_lai")) or ZERO)
        if net is None:
            gross = num(t.get(v, "dt"))
            if gross is None:
                t.skipped.append((n, "không có doanh thu"))
                continue
            net = gross - deductions
        qty = num(t.get(v, "sl"))
        cogs = num(t.get(v, "gia_von"))
        unit_cost = num(t.get(v, "don_gia_von"))
        if cogs is None and unit_cost is not None and qty is not None:
            cogs = qty * unit_cost
        out.append(Sale(month_key(t.get(v, "thang"), t.get(v, "ngay"), t.get(v, "nam"), fy_start) or "", net,
                        str(t.get(v, "phap_nhan", "") or "").strip(), str(t.get(v, "khoi", "") or "").strip(),
                        str(t.get(v, "kenh", "") or "").strip(), str(t.get(v, "ma_hang", "") or "").strip(), qty,
                        str(t.get(v, "dvt", "") or "").strip() or qty_unit, cogs, f"{t.sheet}!{n}",
                        str(t.get(v, "ten_hang", "") or "").strip(), deductions))
    return out


@dataclass
class SkuPlanRow:
    """One SKU (x channel) of the plan for one month, in the row's unit."""
    version: str
    month: str
    sku: str
    qty: Decimal
    revenue: Decimal
    cogs: Decimal
    channel: str = ""
    unit: str = ""
    price: Optional[Decimal] = None      # per unit, when the file gives it
    cost: Optional[Decimal] = None       # per unit, when the file gives it
    where: str = ""
    file: str = ""
    approved: str = ""
    month_no: Optional[int] = None
    fy: Optional[int] = None
    name: str = ""
    no_cost: bool = False                # the file gives no cost for it: its planned cost is 0 and it is said


def read_sku_plan(t: Table, default_version: str = "", fy_start: int = 7, file_fy: Optional[int] = None) -> list:
    """A long plan table by SKU: volume, and a price and cost per unit or the revenue and cost they make."""
    _not_a_customer(t)
    _per_unit(t, "gia_von", "don_gia_von")
    _per_unit(t, "dt", "don_gia")
    qty_unit = header_unit(t, "sl")
    out, held = [], t.held                               # [(where, version, month, revenue, cogs)] rows without volume
    for n, v in t.rows:
        qty, price, cost = num(t.get(v, "sl")), num(t.get(v, "don_gia")), num(t.get(v, "don_gia_von"))
        revenue, cogs = num(t.get(v, "dt")), num(t.get(v, "gia_von"))
        if qty is None:
            t.skipped.append((n, "không có sản lượng"))
            if revenue is not None:                      # kept aside: the plan's stated totals may count it
                month, _no, _fy = _plan_month(t.get(v, "thang"), t.get(v, "nam"), fy_start)
                held.append((f"{t.sheet}!{n}", str(t.get(v, "phien_ban", default_version) or default_version).strip(),
                             month, revenue, cogs or ZERO))
            continue
        if revenue is None and price is None:
            t.skipped.append((n, "không có doanh thu hay giá bán"))
            continue
        month, month_no, fy = _plan_month(t.get(v, "thang"), t.get(v, "nam"), fy_start)
        out.append(SkuPlanRow(
            str(t.get(v, "phien_ban", default_version) or default_version).strip(), month,
            str(t.get(v, "ma_hang", "") or "").strip(), qty,
            revenue if revenue is not None else qty * price,
            cogs if cogs is not None else qty * cost if cost is not None else ZERO,
            str(t.get(v, "kenh", "") or "").strip(), str(t.get(v, "dvt", "") or "").strip() or qty_unit,
            price if revenue is None else None, cost if cogs is None else None,
            f"{t.sheet}!{n}", approved=day_of(t.get(v, "ngay_duyet")) if t.get(v, "ngay_duyet") else "",
            month_no=month_no, fy=fy or file_fy, name=str(t.get(v, "ten_hang", "") or "").strip()))
        out[-1].no_cost = cogs is None and cost is None
    return out


def read_sku_plan_wide(t: Table, months: list, default_version: str = "", file_fy: Optional[int] = None) -> list:
    """A plan by SKU with one volume column per month ('T01' .. 'T12') and a price and a standard cost per unit:
    each month cell is its own row, its revenue volume x price and its cost volume x cost. A money column beside
    monthly volumes can only be per unit. A row with no volume in any month, or no price, is left out with why."""
    _not_a_customer(t)
    for total, unit in (("dt", "don_gia"), ("gia_von", "don_gia_von")):
        if total in t.columns and unit not in t.columns:
            t.columns[unit] = t.columns.pop(total)
    t.month_order = [mon for _c, mon in months]
    out = []
    for n, v in t.rows:
        price, cost = num(t.get(v, "don_gia")), num(t.get(v, "don_gia_von"))
        cells = [(c, mon, num(v[c] if c < len(v) else None)) for c, mon in months]
        cells = [(c, mon, q) for c, mon, q in cells if q is not None]
        if not cells:
            t.skipped.append((n, "không có sản lượng tháng nào"))
            continue
        if price is None:
            t.skipped.append((n, "không có giá bán"))
            continue
        version = str(t.get(v, "phien_ban", default_version) or default_version).strip()
        for c, mon, qty in cells:
            out.append(SkuPlanRow(
                version, "", str(t.get(v, "ma_hang", "") or "").strip(), qty, qty * price,
                qty * cost if cost is not None else ZERO, str(t.get(v, "kenh", "") or "").strip(),
                str(t.get(v, "dvt", "") or "").strip(), price, cost, cell_ref(t.sheet, c, n),
                approved=day_of(t.get(v, "ngay_duyet")) if t.get(v, "ngay_duyet") else "", month_no=mon, fy=file_fy,
                name=str(t.get(v, "ten_hang", "") or "").strip()))
            out[-1].no_cost = cost is None
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
    postings: list = field(default_factory=list)       # Posting, both sides of every ledger line (match to the books)
    segment_map: object = None                         # engine.segment_gl.SegmentMap, when the folder has one
    segment_gls: list = field(default_factory=list)  # SegmentGL of each segmented ledger read (its unknown codes)
    company: object = None                             # engine.company_config.Company, when the PC has one
    sku_plans: list = field(default_factory=list)      # SkuPlanRow, every version
    sku_plan_totals: list = field(default_factory=list)  # (file, Table, [SkuPlanRow]) of each plan-by-SKU table
    cut: list = field(default_factory=list)            # (sheet, rows) of the cut parameters, read by the reviewer
    packs: list = field(default_factory=list)          # paths of BOD drafts
    receipt: list = field(default_factory=list)        # [(file, role, used rows, skipped [(row, reason)])]
    unused: list = field(default_factory=list)         # [(file, reason)]
    month_orders: list = field(default_factory=list)   # [(file, [month numbers])] of plans printed with month columns
    plan_checks: list = field(default_factory=list)    # sentences: a plan file that disagrees with its own totals
    working: list = field(default_factory=list)        # names of working files (bản nháp, working) left out
    plan_notes: dict = field(default_factory=dict)     # plan file -> its 'Ghi chú' / 'Phạm vi' texts, as printed


PACK_WORDS = ("pack", "bod", "ban nhap", "bao cao hdqt", "trinh")

# What a file name says it holds. A hint only orders the readers and words the reason when nothing fits.
HINTS = (("tb", r"\btb\b|can doi|trial balance|cdps|cdtk|bang cdtk"),
         ("sales", r"mastersale|master sale|ban hang|doanh thu|doanh so|\bsales?\b|\bdt\b|hoa don|don hang"),
         ("prior", r"nam truoc|\bly\b|prior|last year|cung ky"),
         ("plan", r"ke hoach|\baopr? ?\d*\b|\bmuf\b|budget|ngan sach|\bplan\b|forecast"),
         ("fx", r"ty gia|\bfx\b|exchange"),
         ("cut", r"cat giam|\bcut\b"),
         ("gl", r"\bgl\b|so chi tiet|chi tiet chi phi|so cai|ledger|chi phi"))
NEED = {"sales": "nhóm 2 (mẫu số %DTT theo kênh) cần doanh thu theo site, khối, kênh",
        "prior": "nhóm 4 (phiên bản kế hoạch khi so năm trước) cần số năm trước",
        "plan": "nhóm 4 và 5 cần số kế hoạch theo tháng",
        "fx": "nhóm 7 (ảnh hưởng tỷ giá) cần bảng tỷ giá theo tháng và loại",
        "gl": "nhóm 1 (tổng chi phí) cần sổ chi tiết có tài khoản và phòng ban hoặc cost element"}
WHAT = {"sales": ("doanh thu (thành tiền, doanh thu thuần)", "kênh, site hoặc khối", "tháng hoặc ngày"),
        "prior": ("số tiền, hoặc cột theo phiên bản (Thực tế, AOP, AOPR)", "tháng", "phòng ban, cost element hoặc dòng BOD"),
        "plan": ("số tiền, hoặc cột theo phiên bản hay theo tháng", "tháng", "phòng ban, cost element hoặc dòng BOD"),
        "fx": ("tỷ giá", "tháng", "loại tỷ giá"),
        "gl": ("số tiền", "tài khoản", "phòng ban hoặc cost element")}


def hint_of(stem: str) -> str:
    k = name_key(stem)
    for name, rx in HINTS:
        if re.search(rx, k):
            return name
    return ""


def name_key(stem: str) -> str:
    """'DanhMuc_CauHinh' -> 'danh muc cau hinh': file names are often written in CamelCase."""
    from engine.tables import _split_camel             # by letter case: 'nháp' is one word, 'ChiPhí' two
    return key(_split_camel(str(stem)))


def _try_fx(rows, sheet, hint, fy_start):
    t = read_table(rows, sheet, FX_SPEC, 2, required=("ty_gia",))
    if t is None:
        return None
    named = "loai" in t.columns and "ty gia" in key(t.headers[t.columns["loai"]])
    if hint != "fx" and not named:
        return None
    rates = read_fx(t, fy_start)
    return (t, rates) if rates else None


def _try_gl(rows, sheet, fy_start):
    t = read_table(rows, sheet, GL_SPEC, 4)
    if t is None:
        return None
    amounts = "tien" in t.columns or {"ps_no", "ps_co"} <= set(t.columns)
    if amounts and ({"tk", "tk_no", "tk_co"} & set(t.columns)) and ({"pb", "ce"} & set(t.columns)):
        return t, read_gl(t, fy_start=fy_start)
    return None


def _try_plan(rows, sheet, version, fy_start, file_fy, planned: bool = False):
    t = read_table(rows, sheet, PLAN_SPEC, 2)
    if t is None and planned:
        # A file named as a plan whose only labelled column is the line ('Chỉ tiêu' | T01 .. T12): one field is enough
        # when the other columns are months.
        t = read_table(rows, sheet, PLAN_SPEC, 1)
        if t is not None and not (({"dong", "pb", "ce"} & set(t.columns)) and len(month_columns(t, rows)) >= 2):
            t = None
    if t is None or not ({"phien_ban", "dong", "pb", "ce"} & set(t.columns)):
        return None
    if "pb" in t.columns and "dong" in t.columns and "ce" not in t.columns:
        t.columns["ce"] = t.columns.pop("dong")       # a plan by department: its line column is the cost element
    t.year_total = year_column(t)
    wide = 0 if "tien" in t.columns else plan_value_columns(t, rows)
    if "tien" not in t.columns and not wide:
        return None
    if "thang" not in t.columns and not wide:
        return None
    out = read_plan(t, version, fy_start, rows, file_fy)
    return (t, out) if out else None


def _try_sku_plan(rows, sheet, version, fy_start, file_fy, planned: bool):
    """A plan by SKU: a SKU, a volume and a revenue or price, in a file named as a plan or with a version column.
    Without either it is a sales ledger, read by _try_sales."""
    t = read_table(rows, sheet, SKU_PLAN_SPEC, 3)
    if t is None or "ma_hang" not in t.columns or not (planned or "phien_ban" in t.columns):
        return None
    wide = month_columns(t, rows) if "sl" not in t.columns and "thang" not in t.columns else []
    if len(wide) >= 2 and ({"dt", "don_gia", "gia_von", "don_gia_von"} & set(t.columns)):
        out = read_sku_plan_wide(t, wide, version, file_fy)     # volume by month, a price and a cost per unit
        return (t, out) if out else None
    if "sl" not in t.columns or not ({"dt", "don_gia"} & set(t.columns)) or "thang" not in t.columns:
        return None
    out = read_sku_plan(t, version, fy_start, file_fy)
    return (t, out) if out else None


def _try_sales(rows, sheet, fy_start):
    t = read_table(rows, sheet, SALES_SPEC, 3)
    if t is None or not ({"dt_thuan", "dt"} & set(t.columns)) or not ({"kenh", "phap_nhan", "khoi"} & set(t.columns)):
        return None
    if not ({"thang", "ngay"} & set(t.columns)):
        return None
    out = read_sales(t, fy_start)
    return (t, out) if out else None


def _missing(rows, spec_name: str, what: Optional[dict] = None) -> str:
    """Which columns a file lacked for what its name says it is, in the user's words."""
    spec = {"sales": SALES_SPEC, "prior": PLAN_SPEC, "plan": PLAN_SPEC, "fx": FX_SPEC, "gl": GL_SPEC}[spec_name]
    found = find_header(rows, spec, 1)
    have = set(found[1]) if found else set()
    groups = {"sales": [{"dt_thuan", "dt"}, {"kenh", "phap_nhan", "khoi"}, {"thang", "ngay"}],
              "prior": [{"tien"}, {"thang"}, {"pb", "ce", "dong", "phien_ban"}],
              "plan": [{"tien"}, {"thang"}, {"pb", "ce", "dong", "phien_ban"}],
              "fx": [{"ty_gia"}, {"thang"}, {"loai"}],
              "gl": [{"tien", "ps_no"}, {"tk", "tk_no", "tk_co"}, {"pb", "ce"}]}[spec_name]
    lacking = [(what or WHAT)[spec_name][i] for i, g in enumerate(groups) if not (g & have)]
    return ", ".join(lacking)


def load(folder: Path, fy_start: int = 7, purpose: str = "bod") -> Bundle:
    """Every file of the folder read for what it holds. purpose words the receipt: "bod" for the review of a BOD
    draft, "fpa" for the FP&A month, the forecast and the opex split."""
    folder = Path(folder)
    b = Bundle(folder)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xlsm")
                   and not p.name.startswith("~$"))
    rest = []
    from engine import segment_gl
    maps = []                                                  # the segment map first: it says how to read the GL
    for p in files:
        if segment_gl.is_map_file(p.stem):
            try:
                found = segment_gl.read_map(p)
            except Exception:                                  # noqa: BLE001 - not a map: it goes on as any file
                continue
            if found.segments:                                 # 'mapping TK cũ mới' maps accounts, not segments
                maps.append((p, found))
    for p, found in maps:
        n = len(found.segments) + sum(len(x) for x in found.codes.values())
        if len(maps) == 1:
            b.segment_map = found
            b.receipt.append((p.name, "Ánh xạ phân đoạn", n, []))
        else:                                                  # two maps: never pick one by name or date
            b.receipt.append((p.name, f"Ánh xạ phân đoạn, chưa dùng: thư mục có {len(maps)} file ánh xạ, chị chọn "
                                      "một file", n, []))
    files = [p for p in files if p not in [x for x, _m in maps]]
    for p in files:                                            # configuration first: its rules shape the rest
        if any(w in name_key(p.stem) for w in ("danh muc", "cau hinh", "config")):
            b.config = read_config(p)
            b.receipt.append((p.name, "Cấu hình", len(b.config.depts) + len(b.config.elements) + len(b.config.channels)
                              + len(b.config.skus), []))
        else:
            rest.append(p)
    # The company's own configuration (kept on the PC, outside the folder): its variance threshold, and its account
    # segments when the folder has no map file that says them.
    from engine import company_config
    b.company = company_config.find()
    company_config.apply(b.company, b.config)
    if b.segment_map is None and b.company is not None and b.company.segments:
        b.segment_map = company_config.segment_map(b.company)
    fy = b.config.rule("thang bat dau") or b.config.rule("nien do")
    fy_start = int(num(fy)) if num(fy) is not None and 1 <= int(num(fy)) <= 12 else fy_start
    pending = []                                                # (plan rows, prior?) whose year comes from the detail
    sales_sources = []                                          # (path, sheet, named as sales?, rows, receipt entry)
    for p in rest:
        k = name_key(p.stem)
        hint = hint_of(p.stem)
        if purpose == "fpa" and working_name(p.stem):
            b.unused.append((p.name, WORKING_WHY))
            b.working.append(p.name)
            continue
        if any(w in k for w in PACK_WORDS) and p.suffix.lower() != ".csv":
            b.packs.append(p)
            if purpose == "fpa":
                b.unused.append((p.name, "bản trình Ban Giám đốc: báo cáo FP&A không lấy số từ bản trình"))
            else:
                b.receipt.append((p.name, "Bộ BOD cần soát", 0, []))
            continue
        try:
            sheets = sheets_of(p)
        except Exception as exc:                               # noqa: BLE001 - say it, never skip in silence
            b.unused.append((p.name, f"không đọc được: {exc}"))
            continue
        if purpose == "fpa" and working_inside(sheets):
            b.unused.append((p.name, WORKING_WHY))
            b.working.append(p.name)
            continue
        file_years = fiscal_years(p.stem) or fiscal_years(k)
        file_fy = file_years[0] if file_years and file_years[1] else None
        prior = hint == "prior"
        version = _version_from_name(p.stem)
        if prior and version not in ("AOP", "AOPR", "AOPR1", "AOPR2", "MUF"):
            version = ""                        # a last-year file that names no version: its rows are last year's actual
        used = False
        info = file_info(sheets)
        notes = scope_notes(sheets)
        first_plan, first_sku = len(b.plans), len(b.sku_plans)
        if hint == "cut":
            for sheet, rows in sheets.items():
                b.cut.append((sheet, rows))
            b.receipt.append((p.name, "Tham số cắt giảm", sum(len(r) for r in sheets.values()), []))
            continue
        order = {"sales": ("sales", "gl", "plan", "fx"), "prior": ("plan", "gl", "sales", "fx"),
                 "plan": ("plan", "gl", "sales", "fx"), "fx": ("fx", "plan", "gl", "sales"),
                 "gl": ("gl", "plan", "sales", "fx")}.get(hint, ("fx", "gl", "plan", "sales"))
        for sheet, rows in sheets.items():
            for kind in ("segment_gl", "sku_plan") + order:
                if kind == "segment_gl":
                    got = segment_gl.read_segment_gl(rows, sheet, b.segment_map, fy_start) if b.segment_map else None
                    if got:
                        b.gl += got.entries
                        b.postings += got.postings
                        b.segment_gls.append(got)
                        b.receipt.append((p.name, "Sổ cái theo tổ hợp tài khoản", used_rows(got.table), got.table.skipped))
                elif kind == "sku_plan":
                    got = _try_sku_plan(rows, sheet, version, fy_start, file_fy, hint in ("plan", "prior"))
                    if got:
                        t, plan_rows = got
                        for r in plan_rows:
                            r.file = p.name
                            if prior:
                                r.version = r.version if r.version and r.version != "LY" else "thuc te"
                        pending.append((plan_rows, prior))
                        b.sku_plans += plan_rows
                        b.sku_plan_totals.append((p.name, t, plan_rows))
                        b.receipt.append((p.name, plan_role(p.stem, plan_rows) + " theo mã hàng", used_rows(t),
                                          t.skipped))
                        if getattr(t, "month_order", None):
                            b.month_orders.append((p.name, t.month_order))
                elif kind == "fx":
                    got = _try_fx(rows, sheet, hint, fy_start)
                    if got:
                        b.fx.update(got[1])
                        b.receipt.append((p.name, "Tỷ giá", used_rows(got[0]), got[0].skipped))
                elif kind == "gl":
                    got = _try_gl(rows, sheet, fy_start)
                    if got:
                        b.gl += got[1]
                        b.postings += read_postings(got[0], fy_start)
                        b.receipt.append((p.name, "Sổ chi tiết chi phí", used_rows(got[0]), got[0].skipped))
                elif kind == "plan":
                    got = _try_plan(rows, sheet, "LY" if prior and version == "" else version, fy_start, file_fy,
                                    hint in ("plan", "prior"))
                    if got:
                        t, plan_rows = got
                        for r in plan_rows:
                            r.file = p.name
                        if prior:
                            for r in plan_rows:
                                r.version = r.version if r.version and r.version != "LY" else "thuc te"
                        pending.append((plan_rows, prior))
                        b.plans += plan_rows
                        b.receipt.append((p.name, "Năm trước" if prior else plan_role(p.stem, plan_rows),
                                          used_rows(t), t.skipped))
                        if getattr(t, "month_order", None) and not prior:
                            b.month_orders.append((p.name, t.month_order))
                        if getattr(t, "total_issues", None):
                            b.plan_checks.append(f"File kế hoạch {p.name} (sheet {sheet}) không tự khớp: "
                                                 + "; ".join(t.total_issues[:5])
                                                 + (f"; và {len(t.total_issues) - 5} chỗ khác" if len(t.total_issues) > 5
                                                    else "") + ". Em dùng số từng tháng, chị kiểm lại file")
                else:
                    got = _try_sales(rows, sheet, fy_start)
                    if got:
                        entry = (p.name, "Bán hàng", used_rows(got[0]), got[0].skipped)
                        sales_sources.append((p, sheet, hint == "sales", got[1], entry))
                        b.receipt.append(entry)
                if got:
                    used = True
                    break
        if info and (len(b.plans) > first_plan or len(b.sku_plans) > first_sku):
            apply_info(info, b.plans[first_plan:] + b.sku_plans[first_sku:])
            b.receipt.append((p.name, "Thông tin phiên bản (trạng thái, phiên bản, ngày duyệt)", len(info), []))
        if notes and (len(b.plans) > first_plan or len(b.sku_plans) > first_sku):
            b.plan_notes[p.name] = notes
        if not used:
            b.unused.append((p.name, _unused_reason(hint, sheets, purpose)))
    if b.segment_map:                                           # the map names departments and cost elements
        depts, elements = segment_gl.catalogue(b.segment_map)
        for code, name in depts.items():
            b.config.depts.setdefault(code, {"ten": name})
        for code, name in elements.items():
            b.config.elements.setdefault(code, {"ten": name})
    b.sales = _drop_sales_copies(b, sales_sources)
    _resolve_years(b, pending, fy_start)
    pair_codes(b)
    return b


def element_column(cfg: Config, plan_values: set) -> dict:
    """{account prefix: cost element} from the catalogue's account maps. The element column is found by its role,
    not its header: its values repeat across accounts and are the plan's (or the catalogue's) cost elements. The
    account's own name is never it. A column that matches nothing the plan or the catalogue knows maps nothing."""
    known = {key(c) for c in cfg.elements} | {key(e.get("ten", "")) for e in cfg.elements.values() if e.get("ten")}
    best, best_score = None, (0, 0, 0)
    for m in cfg.account_maps:
        for header, texts in m["columns"].items():
            values = {key(t) for t in texts if t}
            score = (len(values & plan_values), len(values & known), len([t for t in texts if t]) - len(values))
            if (score[0] or (not plan_values and score[1])) and score > best_score:
                best, best_score = (m, header), score
    if best is None:
        return {}
    m, header = best
    out = {}
    for account, text in zip(m["accounts"], m["columns"][header]):
        if text:
            out.setdefault(account_prefix(account), set()).add(text)
    return out


def pair_codes(b: Bundle) -> None:
    """Actual and plan meet on the same department x cost element codes. A ledger line with no cost element takes
    the one the catalogue gives its account (an account map sheet, its element column chosen by element_column;
    else sheet 'Khoản mục' with 'Tài khoản'; the longest account prefix, and only when one element has it). A
    department or element written by its name, in the plan or the ledger, takes the catalogue's code for that
    name. Nothing is guessed: a name or account the catalogue does not hold stays."""
    cfg = b.config
    by_account = {}
    for code, e in cfg.elements.items():
        tk = account_prefix(e.get("tk", ""))
        if tk:
            by_account.setdefault(tk, set()).add(code)
    by_account.update(element_column(cfg, {key(r.element) for r in b.plans if r.element}))
    element_names = {key(e.get("ten", "")): c for c, e in cfg.elements.items() if e.get("ten")}
    dept_names = {key(d.get("ten", "")): c for c, d in cfg.depts.items() if d.get("ten")}

    def element_of_account(account: str) -> str:
        hits = sorted((tk for tk in by_account if account.startswith(tk)), key=len, reverse=True)
        return next(iter(by_account[hits[0]])) if hits and len(by_account[hits[0]]) == 1 else ""

    def code(value: str, known: dict, names: dict) -> str:
        return value if not value or value in known else names.get(key(value), value)
    for e in b.gl:
        if not e.element and e.account:
            e.element = element_of_account(e.account)
        e.element = code(e.element, cfg.elements, element_names)
        e.dept = code(e.dept, cfg.depts, dept_names)
    for r in b.plans:
        if r.dept or r.element:
            r.element = code(r.element, cfg.elements, element_names)
            r.dept = code(r.dept, cfg.depts, dept_names)


def _sale_key(s: Sale) -> tuple:
    return s.month, s.net, key(s.site), key(s.division), key(s.channel), key(s.sku), s.qty, s.cogs


def _drop_sales_copies(b: Bundle, sources: list) -> list:
    """Sales rows, each source counted once. A table whose every row already sits in another table is a copy,
    for example the order table Clicky's month close loads into the report workbook from the same export.
    Counting it again would double every revenue base. The copy is named in the receipt with its reason."""
    from collections import Counter
    # Bigger tables first; between equal ones, keep the file whose name says sales, then a csv export.
    order = sorted(sources, key=lambda x: (-len(x[3]), not x[2], x[0].suffix.lower() != ".csv", x[0].name, x[1]))
    kept, out = [], []
    for p, sheet, _, rows, entry in order:
        mine = Counter(_sale_key(r) for r in rows)
        home = next(((q, qs) for q, qs, c in kept if not mine - c), None)
        if home is not None:
            b.receipt = [e for e in b.receipt if e is not entry]
            there = f"sheet '{home[1]}' cùng file" if home[0] == p else f"file {home[0].name}"
            b.unused.append((p.name, f"sheet '{sheet}' có {len(rows)} dòng bán hàng, dòng nào cũng đã có trong {there}: "
                                     "đây là bản sao, em không cộng doanh thu lần hai"))
            continue
        kept.append((p, sheet, mine))
        out += rows
    return out


# What the FP&A month needs each kind of file for, in the receipt's words.
FPA_NEED = {"sales": "doanh thu và giá vốn theo mã hàng, kênh cần sổ bán hàng có doanh thu và tháng",
            "prior": "cột năm trước cần số năm trước theo tháng",
            "plan": "cột kế hoạch cần số kế hoạch theo tháng",
            "fx": "quy đổi ngoại tệ cần bảng tỷ giá theo tháng",
            "gl": "chi phí theo bộ phận cần sổ chi tiết có tài khoản và bộ phận hoặc yếu tố chi phí"}
FPA_WHAT = {"sales": ("doanh thu (thành tiền, doanh thu thuần)", "kênh, đơn vị hoặc khối", "tháng hoặc ngày"),
            "prior": ("số tiền, hoặc cột theo tháng (T01 … T12)", "tháng", "bộ phận, yếu tố chi phí hoặc chỉ tiêu"),
            "plan": ("số tiền, hoặc cột theo tháng (T01 … T12)", "tháng", "bộ phận, yếu tố chi phí hoặc chỉ tiêu"),
            "fx": ("tỷ giá", "tháng", "loại tỷ giá"),
            "gl": ("số tiền (hoặc phát sinh Nợ và Có)", "tài khoản", "bộ phận hoặc yếu tố chi phí")}
FPA_KIND = {"sales": "sổ bán hàng", "prior": "số năm trước", "plan": "kế hoạch", "fx": "tỷ giá", "gl": "sổ chi tiết chi phí"}


def _unused_reason(hint: str, sheets: dict, purpose: str = "bod") -> str:
    if purpose == "fpa":
        return _unused_reason_fpa(hint, sheets)
    if hint == "tb":
        return "bảng cân đối tài khoản: dùng cho phần chốt số, phần soát bộ BOD không cần"
    if hint in NEED:
        rows = max(sheets.values(), key=len) if sheets else []
        lacking = _missing(rows, hint)
        return (f"tên file cho thấy đây là file {'năm trước' if hint == 'prior' else hint} nhưng em không đọc được"
                + (f": không thấy cột {lacking}" if lacking else ": không có dòng số liệu nào dùng được")
                + f". Vì vậy {NEED[hint]}").replace("file sales", "file doanh thu").replace("file plan", "file kế hoạch") \
            .replace("file fx", "file tỷ giá").replace("file gl", "file sổ chi tiết")
    cols = []
    for rows in sheets.values():
        for r in rows[:5]:
            cols += [str(v) for v in r or [] if isinstance(v, str) and v.strip()]
    return "em không nhận ra bảng nào cần cho soát bộ BOD trong file này" + (
        f" (các cột đầu: {', '.join(cols[:6])})" if cols else "")


def _unused_reason_fpa(hint: str, sheets: dict) -> str:
    if hint == "tb":
        return "bảng cân đối số phát sinh: em đọc riêng cho số chính thức của tháng"
    if hint in FPA_NEED:
        rows = max(sheets.values(), key=len) if sheets else []
        lacking = _missing(rows, hint, FPA_WHAT)
        return (f"tên file cho thấy đây là {FPA_KIND[hint]} nhưng em chưa đọc được"
                + (f": không thấy cột {lacking}" if lacking else ": không có dòng số liệu nào dùng được")
                + f". Vì vậy {FPA_NEED[hint]}")
    cols = []
    for rows in sheets.values():
        for r in rows[:5]:
            cols += [str(v) for v in r or [] if isinstance(v, str) and v.strip()]
    return "em không nhận ra bảng số liệu nào dùng cho báo cáo FP&A trong file này" + (
        f" (các cột đầu: {', '.join(cols[:6])})" if cols else "")


def _resolve_years(b: Bundle, pending: list, fy_start: int) -> None:
    """Plan rows that name only a month get the year from the file, else from the detail's current year
    (a last-year file: one year before)."""
    from collections import Counter
    counts = Counter(e.period for e in b.gl if e.period) or Counter(s.month for s in b.sales if s.month)
    current = None
    if counts:
        busy = [p for p, n in counts.items() if n >= max(counts.values()) / 4]
        last = max(busy)
        current = int(last[:4]) if int(last[5:]) >= fy_start else int(last[:4]) - 1
    for rows, prior in pending:
        for r in rows:
            if r.month or r.month_no is None:
                continue
            fy = r.fy if r.fy else (current - 1 if prior else current) if current else None
            if fy is None:
                continue
            r.month = f"{fy if r.month_no >= fy_start else fy + 1:04d}-{r.month_no:02d}"


# A working file of the user's ('Working T10', 'Bản nháp', 'Draft', 'Làm việc', 'WIP'): never a source of figures.
# A file name that says it is the user's own working copy: 'Working', 'Workings', 'WIP', 'Draft2', '(bản nháp)',
# 'nháp', 'đang làm', 'bản sao', 'Copy of'. 'Nhập' as in stock in, import, data entry or income is not a draft.
WORKING_RE = re.compile(
    r"\b(?:workings?|work ?file|work ?papers?|wip|drafts?|ban ?nhap|dang ?lam|lam ?viec|file ?tam|tam ?tinh|ban ?sao"
    r"|copy|backup"
    r"|(?<!thu )(?<!xuat )(?<!phieu )(?<!hang )(?<!so )(?<!tk )nhap"
    r"(?! (?:kho|khau|lieu|hang|du lieu|vat tu|nguyen lieu|xuat|ton)\b))(?:\d+|v\d+)?\b")
# Said inside the file, in a sheet name or a title cell: unambiguous words only ('nháp' alone is also a plan's status).
WORKING_INSIDE_RE = re.compile(r"\b(?:working ?file|work ?papers?|working papers?|file ?lam ?viec|ban ?lam ?viec"
                               r"|khong gui|do not send|wip)\b")


def working_name(stem: str) -> bool:
    """The file name says it is the user's working copy. 'T10Working', 'BCQT_T10_lamviec' count too."""
    k = name_key(stem)
    spaced = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", k)
    return bool(WORKING_RE.search(k) or WORKING_RE.search(spaced))


def working_inside(sheets: dict) -> bool:
    """A sheet name or a title cell in the first rows says it: 'FILE LÀM VIỆC - không gửi', sheet 'Working'."""
    for name, rows in sheets.items():
        if WORKING_INSIDE_RE.search(key(name)) or key(name) in ("working", "workings"):
            return True
        for r in rows[:5]:
            for v in (r or [])[:3]:
                if isinstance(v, str) and WORKING_INSIDE_RE.search(key(v)):
                    return True
    return False


WORKING_WHY = "file làm việc (bản nháp) của chị: báo cáo FP&A không lấy số từ file nháp"

# The key-value info sheet a plan file often carries: 'Trạng thái | Đã duyệt', 'Phiên bản | v4', 'Ngày duyệt | ...'.
INFO_KEYS = {"status": ("trang thai", "tinh trang", "status"),
             "version": ("phien ban", "version", "ban so", "lan"),
             "approved": ("ngay duyet", "ngay phe duyet", "approved", "approval date", "duyet ngay", "ngay ban hanh")}
APPROVED_NO_DATE = "da duyet"          # approved, the file names no date: never ranked against a dated approval


def file_info(sheets: dict) -> dict:
    """{'status', 'version', 'approved'} from rows of exactly two cells, a known key then its value, in any sheet."""
    out = {}
    for rows in sheets.values():
        for r in rows[:40]:
            cells = [v for v in (r or []) if v is not None and str(v).strip()]
            if len(cells) != 2 or not isinstance(cells[0], str):
                continue
            k = key(cells[0])
            for name, words in INFO_KEYS.items():
                if name not in out and k in words:
                    out[name] = cells[1]
    if "approved" not in out:
        when = resolution_date(sheets)
        if when:
            out["approved"] = when
    return out if ({"version", "approved"} & set(out)) or key(out.get("status", "")).find("duyet") >= 0 else {}


NOTE_KEYS = ("ghi chu", "pham vi", "pham vi ngan sach", "luu y", "note", "notes", "scope", "budget scope")


def scope_notes(sheets: dict) -> list:
    """The plan's own notes on what it covers: 'Ghi chú: ...', 'Phạm vi | ...' in the first rows, or the text rows
    of a sheet named 'Ghi chú' / 'Phạm vi'. As printed, at most five."""
    out = []
    for name, rows in sheets.items():
        whole = key(name) in NOTE_KEYS
        for r in rows[:40]:
            texts = [str(v).strip() for v in (r or []) if isinstance(v, str) and str(v).strip()]
            if not texts:
                continue
            first = key(texts[0])
            if whole and len(texts) == 1 and first not in NOTE_KEYS:
                out.append(texts[0])
            elif first in NOTE_KEYS and len(texts) >= 2:
                out.append(" ".join(texts[1:]))
            elif any(first.startswith(k + " ") for k in NOTE_KEYS) and re.match(r"^[^:]{2,25}:\s*\S", texts[0]):
                out.append(texts[0].split(":", 1)[1].strip())
    return list(dict.fromkeys(out))[:5]


RESOLUTION_RE = re.compile(r"\b(can cu|nghi quyet|quyet dinh|bien ban hop|resolution|decision)\b")
APPROVAL_WORDS_RE = re.compile(r"\b(phe duyet|duyet|thong qua|ban hanh|approv\w*|ke hoach|ngan sach|aop|budget)\b")
_DATE_AFTER_NGAY = (re.compile(r"\bngay\s+(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})\b"),
                    re.compile(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})\b"),
                    re.compile(r"(?<![\d/])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?![\d/])"))


def resolution_date(sheets: dict) -> str:
    """The approval date a plan states in its 'Căn cứ Nghị quyết số 12/2026/NQ-HĐQT ngày 15/09/2026 ...' or
    'Quyết định ... ngày 15 tháng 9 năm 2026' line, in the first rows of any sheet: 'YYYY-MM-DD', else ''. A line
    that names the approval or the plan wins over one that does not; then the latest date."""
    import datetime as dt
    found = []
    for rows in sheets.values():
        for r in rows[:40]:
            text = key(" ".join(str(v) for v in (r or []) if isinstance(v, str) and v.strip()))
            if not text or not RESOLUTION_RE.search(text):
                continue
            raw = " ".join(fold(str(v)).lower() for v in (r or []) if isinstance(v, str) and v.strip())
            for rx in _DATE_AFTER_NGAY:
                hit = rx.search(raw)
                if hit:
                    try:
                        when = dt.date(int(hit.group(3)), int(hit.group(2)), int(hit.group(1)))
                    except ValueError:
                        continue
                    found.append((bool(APPROVAL_WORDS_RE.search(text)), when.isoformat()))
                    break
    return max(found)[1] if found else ""


def apply_info(info: dict, rows: list) -> None:
    """The info sheet names the file's version ('v4': 'AOP v4' beside another AOP) and its approval."""
    stated = str(info.get("version", "") or "").strip()
    status = key(info.get("status", ""))
    date = day_of(info["approved"]) if info.get("approved") else ""
    approved = date or (APPROVED_NO_DATE if re.search(r"\b(da duyet|approved|phe duyet)\b", status) else "")
    if re.search(r"\b(chua duyet|nhap|draft|cho duyet)\b", status):
        approved = ""
    for r in rows:
        if stated:
            if re.search(r"\b(aopr?|le|fc|muf|rf|forecast|budget|ke hoach|du bao)\b", key(stated)):
                r.version = stated
            elif key(stated) not in key(r.version or "").split():
                r.version = f"{r.version} {stated}".strip()
        if approved and not r.approved:
            r.approved = approved


def plan_role(stem: str, rows: list) -> str:
    """'Dự báo' for a file or its versions named as a forecast or revised estimate (LE, FC, ước), else 'Kế hoạch'."""
    from engine import roles
    versions = {r.version for r in rows if r.version}
    if roles.is_forecast(stem) or (versions and all(roles.is_forecast(v) for v in versions)):
        return roles.FORECAST
    return roles.PLAN


def _version_from_name(stem: str) -> str:
    k = key(stem)
    for word, name in (("aopr2", "AOPR2"), ("aopr1", "AOPR1"), ("aopr", "AOPR"), ("aop", "AOP"), ("muf", "MUF"),
                       ("nam truoc", "LY")):
        if word in k.replace(" ", ""):
            return name
    return stem
