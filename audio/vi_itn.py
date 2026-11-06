"""Spoken Vietnamese finance words -> the digits an accountant writes (inverse text normalization).

The offline recognizer writes what it hears as words, with no digits and no punctuation:
"chênh lệch âm năm phẩy bốn hai tỷ" / "kiểm tra tê ca năm một một". The question bank, the what-if
engine and the report all need "-5,42 tỷ" and "TK 511". This module does that one job, with rules only:
no model, no network, and it never invents a number. When a phrase is not clearly a number it stays as words.

normalize(text) returns the rewritten sentence. extract(text) also returns what was found, with exact
Decimal values (money in đồng, percent as a fraction) so code never has to parse the display string again.

Vietnamese specifics handled here:
- tens: "hai mươi lăm", "hai lăm" (25), "ba mốt" (31), "bốn tư" (44); "mười", "mươi"; "linh"/"lẻ" (105).
- scales: nghìn/ngàn, triệu, tỷ/tỉ, "nghìn tỷ"; the short forms "một tỷ hai" (1,2 tỷ), "hai triệu rưỡi" (2,5 triệu),
  "trăm rưỡi" (150), "hai trăm tư" (240).
- decimals: "phẩy"/"chấm", read digit by digit ("phẩy bốn hai") or as a number ("phẩy bốn mươi hai").
- sign: "âm" before a number.
- accounts: "tài khoản", "tê ca", "TK" (and the recognizer's "t k") + digits read one by one or as a number.
- periods: "tháng mười hai", "tháng tư", "quý ba", "năm hai không hai sáu", "năm hai nghìn không trăm hai mươi sáu".
- single small numbers ("một", "năm", "ba") stay words unless a unit or a counter word makes them a number:
  "năm phần trăm" -> "5%", "slide số mười hai" -> "slide số 12", but "năm nay", "một chút", "đầu tư" stay.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

DIGIT = {"không": 0, "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7, "bẩy": 7,
         "tám": 8, "chín": 9}
# Unit spellings that only exist after a tens digit: "hai mươi mốt", "ba lăm", "bốn tư".
TAIL = {"mốt": 1, "lăm": 5, "nhăm": 5, "tư": 4}
SCALE = {"nghìn": 10 ** 3, "ngàn": 10 ** 3, "triệu": 10 ** 6, "tỷ": 10 ** 9, "tỉ": 10 ** 9}
SCALE_NAME = {10 ** 9: "tỷ", 10 ** 6: "triệu"}
STRUCT = {"mười", "mươi", "trăm", "linh", "lẻ", "rưỡi"}
DECIMAL = {"phẩy", "chấm"}

# A lone small number becomes digits only before one of these ...
UNIT_AFTER = ("phần trăm", "điểm phần trăm", "đồng", "điểm", "lần", "tấn", "người")
# ... or after one of these counters.
COUNTER_BEFORE = ("số", "slide", "trang", "dòng", "cột", "hàng", "bước", "mục", "câu", "phương án", "kịch bản",
                  "ngày", "k", "phần")
ACCOUNT_WORDS = ("tài khoản", "tê ca", "tê ka", "tê k", "t k", "tk", "tê", "t")
MONTH_WORDS = {"giêng": 1, "chạp": 12}
# Words that make the following "năm" mean "year", or "năm" followed by them mean "year".
YEAR_AFTER = {"nay", "ngoái", "trước", "sau", "tới", "qua", "tài", "kế", "đó", "này", "rồi"}

# What the recognizer mishears in this domain, fixed before parsing. Keep this list short and specific.
FIXES = (
    (r"\bquý bà\b", "quý ba"),           # "quý ba" (Q3) heard as "quý bà"
    (r"\bt k\b", "tk"),
)


@dataclass
class Found:
    kind: str                 # "amount" | "percent" | "account" | "month" | "quarter" | "year" | "number"
    value: object             # Decimal (amount in đồng, percent as fraction, number) | int | str (account)
    text: str                 # what replaced the words in the sentence, e.g. "5,42 tỷ", "TK 511"


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _tokens(text: str) -> list:
    t = _nfc(text).lower()
    t = re.sub(r"[^\w%\s]", " ", t)
    for pat, rep in FIXES:
        t = re.sub(pat, rep, t)
    return t.split()


def _is_num_word(w: str) -> bool:
    return w in DIGIT or w in STRUCT or w in SCALE or w in TAIL


# ── parsing a run of number words ────────────────────────────────────────────

def _below_thousand(ws: list) -> Optional[int]:
    """0..999 from words like ['hai', 'trăm', 'linh', 'năm'] or ['ba', 'mươi', 'mốt'] or ['hai', 'lăm']."""
    if not ws:
        return None
    if "trăm" in ws:
        i = ws.index("trăm")
        head = ws[:i]
        if len(head) > 1 or (head and head[0] not in DIGIT):
            return None
        h = DIGIT[head[0]] if head else 1
        rest = ws[i + 1:]
        if not rest:
            return h * 100
        if rest == ["rưỡi"]:
            return h * 100 + 50
        if rest[0] in ("linh", "lẻ"):
            if len(rest) == 2 and rest[1] in DIGIT and rest[1] != "không":
                return h * 100 + DIGIT[rest[1]]
            return None
        if len(rest) == 1 and rest[0] in DIGIT and rest[0] != "không":
            return h * 100 + DIGIT[rest[0]] * 10       # "hai trăm năm" = 250
        if len(rest) == 1 and rest[0] == "tư":
            return h * 100 + 40
        t = _tens(rest)
        return None if t is None else h * 100 + t
    return _tens(ws)


def _tens(ws: list) -> Optional[int]:
    if len(ws) == 1:
        w = ws[0]
        if w in DIGIT:
            return DIGIT[w]
        if w == "mười":
            return 10
        return None
    if ws[0] == "mười":
        if len(ws) == 2 and (ws[1] in DIGIT or ws[1] in ("lăm", "nhăm")) and ws[1] != "không":
            return 10 + (DIGIT.get(ws[1]) or TAIL[ws[1]])
        return None
    if ws[0] in DIGIT and ws[0] not in ("không", "một"):
        d = DIGIT[ws[0]]
        rest = ws[1:]
        if rest and rest[0] == "mươi":
            rest = rest[1:]
            if not rest:
                return d * 10
        if len(rest) == 1:
            u = rest[0]
            if u in TAIL:
                return d * 10 + TAIL[u]
            if u in DIGIT and u != "không":
                return d * 10 + DIGIT[u]
        return None
    return None


def _integer(ws: list) -> Optional[int]:
    """Whole number with scales. Short forms: 'một tỷ hai' = 1,2 tỷ, 'triệu rưỡi' = 1,5 triệu."""
    if not ws:
        return None
    scales = [i for i, w in enumerate(ws) if w in SCALE]
    if not scales:
        return _below_thousand(ws)
    # Split at the largest scale (its last occurrence handles "một nghìn tỷ").
    top = max(SCALE[ws[i]] for i in scales)
    i = max(j for j in scales if SCALE[ws[j]] == top)
    left, right = ws[:i], ws[i + 1:]
    # "nghìn tỷ": the scale word just before tỷ multiplies it.
    lv = _integer(left) if left else 1
    if lv is None:
        return None
    base = lv * top
    if not right:
        return base
    if right == ["rưỡi"]:
        return base + top // 2
    if len(right) == 1 and right[0] in DIGIT and right[0] != "không":
        return base + DIGIT[right[0]] * top // 10      # "một tỷ hai" = 1,2 tỷ
    if len(right) == 1 and right[0] == "tư":
        return base + 4 * top // 10
    rv = _integer(right)
    if rv is None or rv >= top:
        return None
    return base + rv


def _fraction(ws: list) -> Optional[str]:
    """Digits after 'phẩy': 'bốn hai' -> '42', 'bốn mươi hai' -> '42', 'không năm' -> '05'."""
    if not ws:
        return None
    if all(w in DIGIT for w in ws):
        return "".join(str(DIGIT[w]) for w in ws)
    if ws[0] == "không":
        rest = _fraction(ws[1:])
        return None if rest is None else "0" + rest
    v = _below_thousand(ws)
    return None if v is None else str(v)


def parse_number(ws: list) -> Optional[Decimal]:
    """Decimal value of a run of number words, or None when it is not one clean number."""
    if not ws:
        return None
    neg = ws[0] == "âm"
    if neg:
        ws = ws[1:]
    dec_at = next((i for i, w in enumerate(ws) if w in DECIMAL), None)
    if dec_at is None:
        v = _integer(ws)
        if v is None:
            return None
        out = Decimal(v)
    else:
        # The fraction belongs to the leftmost group; a scale may follow it: "năm phẩy bốn hai tỷ".
        after = ws[dec_at + 1:]
        scale_at = next((i for i, w in enumerate(after) if w in SCALE), None)
        frac_words = after if scale_at is None else after[:scale_at]
        tail = [] if scale_at is None else after[scale_at:]
        whole = _integer(ws[:dec_at]) if ws[:dec_at] else None
        frac = _fraction(frac_words)
        if whole is None or frac is None:
            return None
        out = Decimal(f"{whole}.{frac}")
        if tail:
            if len(tail) != 1:
                return None
            out *= SCALE[tail[0]]
    return -out if neg else out


# ── formatting ───────────────────────────────────────────────────────────────

def _group(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    whole, _, frac = format(abs(d).normalize(), "f").partition(".")
    whole = f"{int(whole):,}".replace(",", ".")
    return sign + whole + ("," + frac if frac else "")


def show_amount(value: Decimal, spoken_scale: Optional[int]) -> str:
    """Money in the unit the speaker used: 5420000000 said in tỷ -> '5,42 tỷ'; 18500 -> '18.500'."""
    if spoken_scale in SCALE_NAME and abs(value) >= spoken_scale:
        return _group(value / spoken_scale) + " " + SCALE_NAME[spoken_scale]
    if spoken_scale == 10 ** 9 and abs(value) >= 10 ** 6:
        return _group(value / 10 ** 6) + " triệu"
    return _group(value)


# ── sentence rewriting ───────────────────────────────────────────────────────

def _starts(ws: list, i: int, phrase: str) -> bool:
    p = phrase.split()
    return ws[i:i + len(p)] == p


def _ends(ws: list, i: int, phrase: str) -> bool:
    """True when the words just before position i spell phrase."""
    p = phrase.split()
    return i >= len(p) and ws[i - len(p):i] == p


def _run(ws: list, i: int) -> int:
    """End (exclusive) of the longest run of number words from i, with one 'phẩy' and an optional leading 'âm'."""
    j = i
    if j < len(ws) and ws[j] == "âm":
        j += 1
    seen_dec = False
    start = j
    while j < len(ws):
        w = ws[j]
        if w in DECIMAL and not seen_dec and j > start and j + 1 < len(ws) and ws[j + 1] in DIGIT:
            seen_dec = True
            j += 1
            continue
        if not _is_num_word(w):
            break
        if w in TAIL and (j == start or not (ws[j - 1] in DIGIT or ws[j - 1] in ("mươi", "mười", "trăm") or ws[j - 1] in SCALE)):
            break                                        # "đầu tư", "thứ tư": not a number
        j += 1
    return j if j > start else i


def _digits(ws: list, i: int, max_len: int = 6) -> tuple:
    """Digits read one by one ('năm một một'), allowing 'mốt'/'lăm'/'tư' after the first digit. -> (str, end)."""
    out, j = "", i
    while j < len(ws) and len(out) < max_len:
        w = ws[j]
        if w in DIGIT:
            out += str(DIGIT[w])
        elif w in TAIL and out:
            out += str(TAIL[w])
        else:
            break
        j += 1
    return out, j


def _account(ws: list, i: int) -> tuple:
    """Account code from position i: digit by digit ('sáu ba hai') or a number ('năm trăm mười một')."""
    code, j = _digits(ws, i)
    if len(code) >= 3:
        return code, j
    end = _run(ws, i)
    v = _integer(ws[i:end]) if end > i else None
    if v is not None and 100 <= v <= 999999:
        return str(v), end
    return None, i


def extract(text: str) -> tuple:
    """(normalized sentence, [Found, ...])."""
    ws = _tokens(text)
    out, found = [], []
    i = 0
    while i < len(ws):
        w = ws[i]

        # Accounts: "tài khoản năm một một", "tê ca sáu ba hai", "tk năm hai mốt", and "... và năm hai mốt".
        acct = next((p for p in ACCOUNT_WORDS if _starts(ws, i, p)), None)
        if acct:
            k = i + len(acct.split())
            code, end = _account(ws, k)
            if code:
                out.append(f"TK {code}")
                found.append(Found("account", code, f"TK {code}"))
                i = end
                while i + 1 < len(ws) and ws[i] in ("và", "với", "hoặc"):
                    nxt = next((p for p in ACCOUNT_WORDS if _starts(ws, i + 1, p)), None)
                    k = i + 1 + (len(nxt.split()) if nxt else 0)
                    code, end = _account(ws, k)
                    if not code:
                        break
                    out += [ws[i], f"TK {code}"]
                    found.append(Found("account", code, f"TK {code}"))
                    i = end
                continue

        # Months and quarters.
        if w in ("tháng", "quý") and i + 1 < len(ws):
            nxt = ws[i + 1]
            if w == "tháng" and nxt in MONTH_WORDS:
                out.append(f"tháng {MONTH_WORDS[nxt]}")
                found.append(Found("month", MONTH_WORDS[nxt], out[-1]))
                i += 2
                continue
            if nxt in DIGIT or nxt in ("mười", "tư"):
                end = min(_run(ws, i + 1), i + 3)
                if nxt == "tư":
                    v, end = 4, i + 2
                else:
                    v = _integer(ws[i + 1:end])
                    while v is not None and end > i + 2 and not (1 <= v <= (12 if w == "tháng" else 4)):
                        end -= 1
                        v = _integer(ws[i + 1:end])
                limit = 12 if w == "tháng" else 4
                if v is not None and 1 <= v <= limit:
                    out.append(f"{w} {v}")
                    found.append(Found("month" if w == "tháng" else "quarter", v, out[-1]))
                    i = end
                    continue

        # Years: "năm hai không hai sáu", "năm hai nghìn không trăm hai mươi sáu", "năm hai sáu".
        if w == "năm" and i + 1 < len(ws) and ws[i + 1] in DIGIT:
            year, end = _year(ws, i + 1)
            if year is not None:
                out.append(f"năm {year}")
                found.append(Found("year", year, out[-1]))
                i = end
                continue

        # Numbers, with an optional sign, decimal and unit.
        if w in DIGIT or w == "mười" or ((w == "trăm" or w in SCALE) and i + 1 < len(ws) and ws[i + 1] == "rưỡi") or (w == "âm" and i + 1 < len(ws) and
                                                                (ws[i + 1] in DIGIT or ws[i + 1] == "mười")):
            end = _run(ws, i)
            if end > i:
                part = ws[i:end]
                val = parse_number(part)
                # Trim from the right until it parses: "bốn trăm ba mươi sáu triệu chứ" never gets here,
                # but "ba hai" + noise might.
                while val is None and end - i > 1:
                    end -= 1
                    part = ws[i:end]
                    val = parse_number(part)
                if val is not None and _keep_as_number(ws, i, end, part):
                    pct = _starts(ws, end, "phần trăm")
                    pp = _starts(ws, end, "điểm phần trăm")
                    scales = [SCALE[x] for x in part if x in SCALE]
                    if pct or pp:
                        shown = _group(val) + ("%" if pct else " điểm %")
                        found.append(Found("percent", val / 100, shown))
                        out.append(shown)
                        end += 2 if pct else 3
                    elif scales or _starts(ws, end, "đồng"):
                        spoken = max((s for s in scales if s >= 10 ** 6), default=None)
                        shown = show_amount(val, spoken)
                        found.append(Found("amount", val, shown))
                        out.append(shown)
                    else:
                        shown = _group(val)
                        found.append(Found("number", val, shown))
                        out.append(shown)
                    i = end
                    continue

        out.append(w)
        i += 1
    return " ".join(out), found


def _year(ws: list, i: int) -> tuple:
    """Year spoken after 'năm', from position i. -> (year or None, end)."""
    code, end = _digits(ws, i, 4)
    if len(code) == 4 and code[0] in "12":
        return int(code), end
    run = _run(ws, i)
    v = _integer(ws[i:run]) if run > i else None
    if v is not None and 1900 <= v <= 2199:
        return v, run
    after = ws[i + 2] if i + 2 < len(ws) else ""
    if len(code) == 2 and end == i + 2 and run == end and not any(_starts(ws, end, u) for u in UNIT_AFTER) \
            and after not in SCALE and after not in DECIMAL:
        return int(code), end                          # "năm hai sáu" = năm 26
    return None, i


def _keep_as_number(ws: list, i: int, end: int, part: list) -> bool:
    """Is this run really a number? A lone digit word, or two bare digits ('ba năm'), stay words
    unless a unit follows or a counter precedes: 'năm phần trăm', 'slide số ba' -> digits; 'năm nay',
    'một chút', 'sau ba năm' stay words."""
    core = part[1:] if part[0] == "âm" else part
    if not any(x in DIGIT or x == "mười" for x in core) and "rưỡi" not in core:
        return False                                   # "tỷ giá", "tỷ suất", "phần trăm": no digit, no number
    unit_after = any(_starts(ws, end, u) for u in UNIT_AFTER)
    if any(x in STRUCT or x in SCALE or x in DECIMAL for x in core) or core[-1] in TAIL:
        return True
    if end < len(ws) and ws[end] in YEAR_AFTER:
        return False
    if unit_after:
        return True
    if core == ["không"]:
        return False
    return any(_ends(ws, i, c) for c in COUNTER_BEFORE)


def normalize(text: str) -> str:
    return extract(text)[0]
