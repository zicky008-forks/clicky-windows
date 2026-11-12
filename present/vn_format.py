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


_NUM = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:,\d+)?")   # 1.234,5 · 2026 · 12,6


def speakable(text: str) -> str:
    """Turn every displayed number in a sentence into Vietnamese words for an offline voice."""
    text = text.replace("%", " phần trăm").replace("điểm  phần trăm", "điểm phần trăm")
    return _NUM.sub(lambda m: number_words(m.group(0)), text).replace("  ", " ")
