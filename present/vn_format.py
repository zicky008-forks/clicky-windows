"""Vietnamese number formats for slides and speech.

Display: 1234.56 -> "1.234,6 tỷ"; 0.1234 -> "12,34%". Speech: numbers are turned into words here,
so a voice never has to guess how to read "1.234,6" (offline voices read it digit by digit or in English).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re

KINDS = ("bn", "bn0", "pct", "pct1", "pct2", "pp", "x", "num")


def _q(value, places: int) -> Decimal:
    exp = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(exp, rounding=ROUND_HALF_UP)


def _group(d: Decimal, places: int) -> str:
    sign = "-" if d < 0 else ""
    text = f"{abs(d):,.{places}f}"                    # 1,234.5
    return sign + text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt(value, kind: str = "bn") -> str:
    """Format a value read from Excel. 'bn' values are already in tỷ (billions of VND)."""
    if value is None or value == "":
        raise ValueError("ô trống")
    if isinstance(value, str) and value.startswith("#"):
        raise ValueError(f"ô lỗi {value}")
    kind = (kind or "bn").strip().lower()
    if kind == "bn":
        return _group(_q(value, 1), 1) + " tỷ"
    if kind == "bn0":
        return _group(_q(value, 0), 0) + " tỷ"
    if kind in ("pct", "pct2"):
        return _group(_q(Decimal(str(value)) * 100, 2), 2) + "%"
    if kind == "pct1":
        return _group(_q(Decimal(str(value)) * 100, 1), 1) + "%"
    if kind == "pp":
        return _group(_q(value, 2), 2) + " điểm %"
    if kind == "x":
        return _group(_q(value, 1), 1) + " lần"
    if kind == "num":
        return _group(_q(value, 0), 0)
    raise ValueError(f"không biết định dạng {kind!r}")


_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _three(n: int, full: bool) -> str:
    """Read 0..999. full=True reads leading zeros ('không trăm linh năm') inside a bigger number."""
    h, t, u = n // 100, n // 10 % 10, n % 10
    words = []
    if h or full:
        words += [_DIGITS[h], "trăm"]
    if t == 0:
        if u and (h or full):
            words.append("linh")
    elif t == 1:
        words.append("mười")
    else:
        words += [_DIGITS[t], "mươi"]
    if u:
        if u == 1 and t > 1:
            words.append("mốt")
        elif u == 5 and t > 0:
            words.append("lăm")
        elif u == 4 and t > 1:
            words.append("tư")
        else:
            words.append(_DIGITS[u])
    return " ".join(words)


def int_words(n: int) -> str:
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + int_words(-n)
    units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g == 0:
            continue
        words.append(_three(g, full=idx != len(groups) - 1))
        if units[idx]:
            words.append(units[idx])
    return " ".join(words)


def number_words(text: str) -> str:
    """'1.234,56' -> 'một nghìn hai trăm ba mươi tư phẩy năm sáu'."""
    s = text.strip()
    neg = s.startswith("-")
    s = s.lstrip("-")
    whole, _, frac = s.partition(",")
    out = int_words(int(whole.replace(".", "") or 0))
    if frac:
        frac = frac.rstrip("0")
        if frac:
            out += " phẩy " + (int_words(int(frac)) if len(frac) == 2 and frac[0] != "0" else " ".join(_DIGITS[int(c)] for c in frac))
    return ("âm " if neg else "") + out


# 1.234,5 · 2026 · 12,6 · -2,1 (a minus only when it is not between two figures: "3-5" is a range)
_NUM = re.compile(r"(?:(?<![\w.,])-)?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)")


_GAP = "[\u00a0\u202f\u2009]"          # no-break space, narrow no-break space, thin space


def join_groups(text: str) -> str:
    """'1 234 567' (thousands split by a space, a no-break space or a thin space) is one number: '1.234.567'.
    A plain space needs two groups ('1 234 567') or a decimal part ('1 234,5'), so '3 120' stays two numbers."""
    def one(m):
        return re.sub(r"[ \u00a0\u202f\u2009]", ".", m.group(0))
    text = re.sub(rf"(?<![\d.,])\d{{1,3}}(?:{_GAP}\d{{3}})+(?![\d])", one, text)
    return re.sub(r"(?<![\d.,])\d{1,3}(?:(?: \d{3}){2,}| \d{3}(?=,\d))(?!\d)", one, text)


def speakable(text: str) -> str:
    """Turn every displayed number in a sentence into Vietnamese words for an offline voice.
    Number words always get a space on each side, so "3%so" or "Q3" never glue into one unknown word."""
    text = join_groups(text)
    text = re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " đến ", text)            # 3-5 người, 2026–2027
    # "12.6%": a dot with one or two digits after it cannot group thousands, so it is a decimal point
    text = re.sub(r"(?<![\d.,])(\d+)\.(\d{1,2})(?![\d.,]|\.\d)", r"\1,\2", text)
    text = re.sub(r"\s*%", " phần trăm ", text)
    text = _NUM.sub(lambda m: " " + number_words(m.group(0)) + " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" (?=[,.;:!?)])", "", text)
    return re.sub(r"(?<=\() ", "", text).strip()


# a/b followed by one of these counts things ("4/4 khối", "2/5 vị trí"): never a date
COUNTED = (r"khối|phần|vị trí|nhân viên|nhân sự|người|chỉ tiêu|tiêu chí|số|bậc|phòng|ban|chi nhánh|mục|hạng mục|"
           r"cửa hàng|kênh|dự án|sản phẩm|đơn vị|nhà máy|NV|lần|tháng|quý|tuần|vùng|miền|tỉnh|đại lý")
_FRACTION_CUE = re.compile(r"(?i)\b(?:tỷ lệ|chiếm|chỉ(?!\s+(?:tiêu|số))|bậc)\b")
_DATE_CUE = re.compile(r"(?i)\b(?:ngày|hạn|hạn chót|từ|đến|trước|sau|dịp|khuyến mãi|chương trình|lúc|cập nhật|"
                       r"chốt|tính đến|kể từ|hết)\s*$")


def slash_kind(a, b, before: str = "", after: str = "") -> str:
    """How 'a/b' (two small whole numbers) is read: 'date' ('5/11', 'cập nhật 3/11'), 'fraction'
    ('3/10 khối' = 'ba phần mười', 'tỷ lệ 2/5'), or 'ratio' ('4/4 khối', '8/8 chỉ tiêu', 'bậc 3/7' = 'trên').
    before/after: the text around it. Shared by the voice (present.doc_tai_chinh) and the build's figure
    check (present.clicky_html.figures), so both see the same date.
    - A counted noun right after ('khối', 'vị trí', 'người', 'chỉ tiêu'...) makes it a count: a < b is a
      fraction, a >= b is 'a trên b'.
    - A date cue right before ('ngày', 'hạn', 'từ', 'cập nhật'...) makes it a date.
    - 'tỷ lệ', 'chiếm', 'chỉ' or 'bậc' within the last few words makes it a fraction ('bậc' a ratio).
    - Otherwise it is a date when 1 <= a <= 31 and 1 <= b <= 12; else a fraction when a < b, else a ratio."""
    a, b = int(a), int(b)
    valid = 1 <= a <= 31 and 1 <= b <= 12
    count = "fraction" if a < b else "ratio"
    if re.match(rf"\s*(?:{COUNTED})\b", after):
        return count
    if valid and _DATE_CUE.search(before):
        return "date"
    near = " ".join(re.split(r"[,;:(]", before)[-1].split()[-4:])
    cue = _FRACTION_CUE.search(near)
    if cue:
        return "ratio" if cue.group(0).lower() == "bậc" else count
    return "date" if valid else count
