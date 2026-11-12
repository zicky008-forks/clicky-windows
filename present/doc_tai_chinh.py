"""How a Vietnamese FP&A person reads a report aloud. Captions keep the report's own text; only the voice
gets these words.

- Finance abbreviations are said the way people say them in a Vietnamese finance team: "AOPR" is
  "ây-ô-pi điều chỉnh", "DTT" is "doanh thu thuần", "FP&A" is "ép-pi-en-ây", "YoY" is "so với cùng kỳ".
- Any other short all-caps word (a channel, a department code) is spelled letter by letter, English style,
  as people do ("MT" = "em-ti", "SKU" = "ét-cây-giu"), never read as a Vietnamese syllable. An all-caps
  Vietnamese heading ("TỔNG CHI PHÍ") is read as words, not spelled.
- Periods: "Q3/2026" is "quý 3 năm 2026", "20/11/2026" is "ngày 20 tháng 11 năm 2026", "T10" is "tháng 10",
  "9M" is "9 tháng", "H1" is "sáu tháng đầu năm", "FY26" is "năm tài chính 2026".
- Signs: "+12,6% so với" is "tăng", "−5,4 tỷ so với" is "giảm", but a level after ":" ("Lợi nhuận: −5,4 tỷ")
  and an accounting "(1.234)" are "âm". "(+6,2%)" is ", tức tăng 6,2%,". "2026-2027" and "3-5" are "đến".
- Units: "149,4 triệu/năm" is "mỗi năm", "nhân sự/DTT" is "trên", "GT/MT" is "GT và MT", "1,2x" is "lần",
  "120 bps" is "điểm cơ bản", "~3%" is "khoảng 3%".
- Figures are then turned into words by present.vn_format.speakable ("186,4" = "một trăm tám mươi sáu phẩy bốn").

The owner can add his company's own words in a file (CLICKY_TU_DOC, one "VIẾT TẮT = cách đọc" per line).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from present.vn_format import speakable

# said as words: what the abbreviation means in a Vietnamese finance team
WORDS = {
    "AOPR": "ây-ô-pi điều chỉnh", "AOP": "ây-ô-pi", "FP&A": "ép-pi-en-ây", "P&L": "báo cáo lãi lỗ",
    "DTT": "doanh thu thuần", "DT": "doanh thu", "LNG": "lợi nhuận gộp", "LNTT": "lợi nhuận trước thuế",
    "LNST": "lợi nhuận sau thuế", "GVHB": "giá vốn hàng bán", "CPBH": "chi phí bán hàng",
    "CPQL": "chi phí quản lý", "CP": "chi phí", "CNTT": "công nghệ thông tin", "BGĐ": "Ban Giám đốc",
    "HĐQT": "Hội đồng quản trị", "BOD": "Ban Giám đốc", "NV": "nhân viên", "SL": "sản lượng",
    "VNĐ": "đồng", "VND": "đồng", "YoY": "so với cùng kỳ năm trước", "MoM": "so với tháng trước",
    "YTD": "lũy kế từ đầu năm", "MTD": "lũy kế trong tháng", "QTD": "lũy kế trong quý",
    "EBITDA": "ê-bít-đa", "EBIT": "ê-bít", "OPEX": "chi phí hoạt động", "Opex": "chi phí hoạt động",
    "OpEx": "chi phí hoạt động", "CAPEX": "chi phí đầu tư", "Capex": "chi phí đầu tư", "CapEx": "chi phí đầu tư",
    "KPI": "cây-pi-ai", "KPIs": "các cây-pi-ai", "LE": "ước thực hiện", "FC": "dự báo", "PY": "năm trước",
    "ROI": "a-ô-ai", "ROE": "a-ô-i", "ROA": "a-ô-ây", "SKU": "ét-cây-giu", "ERP": "i-a-pi",
    "CFO": "xi-ép-ô", "CEO": "xi-i-ô", "FM": "ép-em", "HR": "nhân sự", "tr": "triệu", "tr.": "triệu",
    "HC": "số nhân sự", "HRC": "hô-re-ca", "HORECA": "hô-re-ca", "VAT": "vát", "USD": "đô la Mỹ",
    "COGS": "giá vốn hàng bán", "CAGR": "tăng trưởng kép bình quân", "D&A": "khấu hao",
    "B2B": "bi-tu-bi", "B2C": "bi-tu-xi",
}
# English finance words a Vietnamese voice cannot read: what the team says instead (any letter case)
ENGLISH = {"aop revised": "ây-ô-pi điều chỉnh", "revised": "điều chỉnh", "headcount": "số nhân sự",
           "forecast": "dự báo", "budget": "ngân sách", "actual": "thực tế", "actuals": "số thực tế",
           "variance": "chênh lệch", "gap": "chênh lệch", "bridge": "cầu chênh lệch", "run-rate": "tốc độ hiện tại",
           "run rate": "tốc độ hiện tại", "target": "mục tiêu", "baseline": "mức gốc", "outlook": "triển vọng",
           "margin": "biên lợi nhuận", "vs": "so với", "clicky": "Cờ-lích-ki", "logistics": "lô-gít-tích",
           "working capital": "vốn lưu động", "cash conversion cycle": "chu kỳ chuyển đổi tiền mặt",
           "cash flow": "dòng tiền"}
# English letter names written so a Vietnamese voice says them right
LETTERS = {"A": "ây", "B": "bi", "C": "xi", "D": "đi", "E": "i", "F": "ép", "G": "di", "H": "hát", "I": "ai",
           "J": "giây", "K": "cây", "L": "e-lờ", "M": "em", "N": "en", "O": "ô", "P": "pi", "Q": "kiêu", "R": "a",
           "S": "ét", "T": "ti", "U": "giu", "V": "vi", "W": "đáp-bờ-liu", "X": "ích", "Y": "oai", "Z": "dét"}
VN_WORD = re.compile(r"[ĂÂĐÊÔƠƯÁÀẢÃẠẮẰẲẴẶẤẦẨẪẬÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ]")
# an all-caps word that is a Vietnamese syllable written without marks ("CHI", "THEO", "DOANH")
_SYLLABLE = re.compile(r"(?:NGH|CH|GH|GI|KH|NG|NH|PH|QU|TH|TR|[BCDGHKLMNPRSTVX])?[AEIOUY]{1,3}(?:CH|NG|NH|[CMNPT])?")
_SHORT_VN = {"DO", "BA", "TO"}
_MONEY = r"đồng|đ|triệu|tr|tỷ|nghìn|USD|VND|VNĐ"


# a shorter way people also ask for the same thing ("chi phí nhân sự trên doanh thu" for ".../DTT")
SHORTER = {"DTT": ["doanh thu"], "LNST": ["lợi nhuận"], "AOPR": ["AOP điều chỉnh"], "GVHB": ["giá vốn"]}


def aliases() -> dict:
    """{abbreviation: [what people say instead]}, for matching a reader's question to the report's labels.
    Only meanings in words: a reading spelled letter by letter is not something a reader types."""
    out = {}
    for k, v in {**WORDS, **owner_words()}.items():
        if "-" not in v and not k.islower() and (len(k) >= 3 or k in ("DT", "CP", "NV", "SL", "HR")):  # "LE" is a word
            out[k] = [v] + SHORTER.get(k, [])
    out["AOPR"] = ["AOP revised"] + SHORTER["AOPR"]
    return out


def owner_words() -> dict:
    """Extra "ABBR = reading" lines from the owner's file, if any."""
    path = os.environ.get("CLICKY_TU_DOC")
    out = {}
    if path and Path(path).is_file():
        from present.tri_nho import read_text                     # Notepad: BOM, no BOM or ANSI
        for line in read_text(path).lstrip("\ufeff").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
    return out


def _spell(word: str) -> str:
    return "-".join(LETTERS.get(c, c) for c in word)


def _without_repeat(before: str, said: str) -> str:
    """Drop the words of a reading that only repeat what was just said: "khấu hao D&A" is "khấu hao",
    "tăng trưởng CAGR" is "tăng trưởng kép bình quân"."""
    prev = before.lower().split()[-6:]
    words = said.split()
    for k in range(min(len(prev), len(words)), 0, -1):
        if [w.lower() for w in words[:k]] == prev[-k:]:
            return " ".join(words[k:])
    return said


def _abbr(m: re.Match, table: dict) -> str:
    w = m.group(0)
    if w in table:
        return _without_repeat(m.string[:m.start()], table[w])
    code = re.fullmatch(r"([A-Z]{1,4})(\d{1,3})", w)                  # BU1: letters, then the number
    if code:
        letters = code.group(1)
        return f"{table.get(letters) or _spell(letters)} {code.group(2)}"
    if 2 <= len(w) <= 5 and w.isupper() and w.isalpha() and not VN_WORD.search(w):
        return _spell(w)
    return w


def _caps_heading(text: str, table: dict) -> str:
    """'TỔNG CHI PHÍ NHÂN SỰ TĂNG 12,6%' -> words, so 'CHI' is not spelled 'xi-hát-ai'. A run of two or
    more all-caps words counts as a heading when one has Vietnamese marks (or all are long Vietnamese
    syllables, 'DOANH THU'); known abbreviations and codes inside it stay as they are."""
    toks = re.split(r"(\s+)", text)

    def core(w):
        return re.sub(r"[\W\d_]", "", w)

    def caps(w):
        return core(w).isupper()

    def said_as_word(c):
        return bool(VN_WORD.search(c)) or c in _SHORT_VN or (len(c) >= 3 and bool(_SYLLABLE.fullmatch(c)))

    i = 0
    while i < len(toks):
        if not caps(toks[i]):
            i += 2
            continue
        j = i
        while j + 2 < len(toks) and (caps(toks[j + 2]) or (not core(toks[j + 2]) and re.search(r"\d", toks[j + 2]))):
            j += 2
        run = [k for k in range(i, j + 1, 2) if caps(toks[k])]
        words = [core(toks[k]) for k in run]
        known = [toks[k].strip(",.;:!?()") in table for k in run]
        heading = len(run) >= 2 and (any(VN_WORD.search(c) for c in words) or
                                     all(len(c) >= 3 and _SYLLABLE.fullmatch(c) for c in words) and not any(known))
        if heading:
            for k, c, kn in zip(run, words, known):
                if not kn and said_as_word(c):
                    toks[k] = toks[k].lower()
        i = j + 2
    return "".join(toks)


def _date(m: re.Match) -> str:
    lead, d, mo, y = m.group(1), int(m.group(2)), int(m.group(4)), m.group(5)
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return m.group(0)
    return f"{lead or 'ngày '}{d} tháng {mo} năm {y}"


def _day_month(m: re.Match) -> str:
    lead, a, b = m.group(1), int(m.group(2)), int(m.group(3))
    if not lead and a < b <= 4 and len(m.group(2)) == len(m.group(3)) == 1:   # 1/2, 2/3, 3/4: a fraction
        return f"{a} phần {b}"
    if not (1 <= a <= 31 and 1 <= b <= 12):
        return m.group(0)
    return f"{lead or 'ngày '}{a} tháng {b}"


def _month_year(m: re.Match) -> str:
    mo = int(m.group(2))
    return f"tháng {mo} năm {m.group(3)}" if 1 <= mo <= 12 else m.group(0)


def _paren_negative(m: re.Match) -> str:
    """Accounting brackets: '(1.234) triệu', '(5,4)' and 'Chênh lệch: (2,1%)' are negatives. A share
    '(35%)', a year '(2026)' or a note '(1)' is not."""
    num, pct = m.group(1), m.group(2) or ""
    after_label = bool(re.search(r"[:=]\s*$", m.string[:m.start()]))
    if after_label or (not pct and re.search(r"[.,]", num)):
        return f"âm {num}{pct}"
    return m.group(0)


def _periods(t: str) -> str:
    """Dates and periods, before any '/' is read as 'trên' or 'mỗi'."""
    t = re.sub(r"\bFY\s?((?:19|20)\d{2})\s*[-–—/]\s*((?:19|20)\d{2})\b", r"năm tài chính \1 đến \2", t)
    t = re.sub(r"\bFY\s?(\d{2})\s*[-–—/]\s*(\d{2})\b", r"năm tài chính 20\1 đến 20\2", t)     # FY26-27
    t = re.sub(r"\bFY\s?(\d{2})(\d{2})\b", lambda m: f"năm tài chính {m.group(1)}{m.group(2)}" if m.group(1) in
               ("19", "20") else f"năm tài chính 20{m.group(1)} đến 20{m.group(2)}", t)          # FY2026, FY2627
    t = re.sub(r"\bFY\s?(\d{2})\b", r"năm tài chính 20\1", t)                                    # FY26
    t = re.sub(r"(?i)(\bngày\s+)?(?<![\d.,])\b(\d{1,2})\s*([/.-])\s*(\d{1,2})\s*\3\s*((?:19|20)\d{2})\b", _date, t)
    t = re.sub(r"\bQ([1-4])\s*/\s*((?:19|20)\d{2})\b", r"quý \1 năm \2", t)
    t = re.sub(r"\bQ([1-4])\s+((?:19|20)\d{2})\b", r"quý \1 năm \2", t)
    t = re.sub(r"\bQ([1-4])\b", r"quý \1", t)
    t = re.sub(r"\bH1\b", "sáu tháng đầu năm", t)
    t = re.sub(r"\bH2\b", "sáu tháng cuối năm", t)
    t = re.sub(r"\b(3|6|9|12)M\b(?!\s*(?:USD|VND|\$))", r"\1 tháng", t)        # lũy kế 9M
    t = re.sub(r"\bT(1[0-2]|[1-9])\s*/\s*((?:19|20)\d{2})\b", r"tháng \1 năm \2", t)
    t = re.sub(r"\bT(1[0-2]|[1-9])\b", r"tháng \1", t)
    t = re.sub(r"(?i)(\btháng\s+)?(?<![\d.,])\b(\d{1,2})\s*/\s*((?:19|20)\d{2})\b(?!\s*/)", _month_year, t)
    t = re.sub(r"(?i)(\bngày\s+)?(?<![\d.,])\b(\d{1,2})/(\d{1,2})\b(?![/%]|[.,]\d|\s*/)", _day_month, t)  # 11/11
    return t


def _signs(t: str) -> str:
    t = re.sub(r"\s*\(\s*\+\s*(\d[\d.,]*\s*%?)\s*\)", r", tức tăng \1,", t)       # (+6,2%)
    t = re.sub(r"\s*\(\s*[−–-]\s*(\d[\d.,]*\s*%?)\s*\)", r", tức giảm \1,", t)    # (−6,2%)
    t = re.sub(r"\(\s*(\d[\d.,]*)\s*(%)?\s*\)", _paren_negative, t)
    t = re.sub(r"([:=])\s*[−–-]\s?(?=\d)", r"\1 âm ", t)                          # a level after ':' is negative
    t = re.sub(r"(\d)\s*[-–—]\s*(\d)", r"\1 đến \2", t)                         # 2026-2027, 3–5 người
    t = re.sub(r"(?<![\w,.])\+\s?(?=\d)", "tăng ", t)                             # +12,6% so với AOP
    t = re.sub(r"(?<![\w,.])[−–]\s?(?=\d)", "giảm ", t)                           # −5,4 tỷ written with a real minus
    t = re.sub(r"(?<![\w,.])-\s?(?=\d[\d.,]*[^\d,;:!?.][^,;:!?.]*\bso với)", "giảm ", t)  # -5,4 tỷ so với AOP
    t = re.sub(r"[~≈]\s*(?=\d)", "khoảng ", t)
    return t


def _units(t: str) -> str:
    t = re.sub(r"\bGM\s*%", "biên lợi nhuận gộp", t)
    t = re.sub(r"\b(EBITDA|EBIT)\s+margin\b", r"biên \1", t, flags=re.I)
    t = re.sub(r"\bUSD\s*/\s*VND\b", "USD", t)                                     # tỷ giá USD/VND
    t = re.sub(r"\bvs\.(?=\s)", "vs", t)
    t = re.sub(r"(\d)\s*x\b", r"\1 lần", t)                                        # 1,2x
    t = re.sub(r"(\d)\s*pp\b", r"\1 điểm phần trăm", t)
    t = re.sub(r"(\d)\s*bps?\b", r"\1 điểm cơ bản", t)
    t = re.sub(r"(\d)\s*đ\b", r"\1 đồng", t)                                       # 1.250.000 đ
    t = re.sub(r"%\s*/\s*(?=[\d−+-])", "% và ", t)                                 # 65%/35%
    t = re.sub(rf"\b({_MONEY})\s*/\s*(?=[^\W\d_])", r"\1 mỗi ", t)                 # đồng/thùng, tr/người
    t = re.sub(r"(?<=\w)\s*/\s*(?=năm|tháng|quý|người|ngày|tuần)", " mỗi ", t)
    t = re.sub(r"(?<=[A-Za-zÀ-ỹ])\s*\+\s*(?=[A-Za-zÀ-ỹ])", " cộng ", t)             # HRC+KA: one channel
    return t


def vn_numbers(text: str) -> str:
    """A report written with English number marks (412,580.5) rewritten the Vietnamese way (412.580,5)."""
    swap = {",": ".", ".": ","}
    return re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: "".join(swap.get(c, c) for c in m.group(0)), str(text))


def doc(text: str, en_numbers: bool = False) -> str:
    """The sentence as it should be heard. en_numbers: the report writes 412,580.5 (English marks)."""
    table = {**WORDS, **owner_words()}
    t = vn_numbers(text or "") if en_numbers else str(text or "")
    t = re.sub(r"_+", " ", t)                                          # a code like __BIEN_BAN__ has no sound
    t = _caps_heading(t, table)
    t = _periods(t)
    t = _signs(t)
    t = _units(t)
    eng = sorted(ENGLISH, key=len, reverse=True)
    t = re.sub(r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in eng) + r")(?![\w-])",
               lambda m: _without_repeat(m.string[:m.start()], ENGLISH[m.group(0).lower()]), t, flags=re.I)
    t = re.sub(r"\b([A-Z]{2,5})\s*/\s*([A-Z]{2,5})\b",                            # GT/MT: two codes
               lambda m: m.group(0) if m.group(1) in table or m.group(2) in table else f"{m.group(1)} và {m.group(2)}", t)
    keys = sorted(table, key=len, reverse=True)
    pat = "|".join(re.escape(k) for k in keys)
    t = re.sub(rf"(?<![\w&])(?:{pat})(?![\w&])|\b[A-Z]{{1,4}}\d{{1,3}}\b|\b[A-Z]{{2,5}}\b",
               lambda m: _abbr(m, table), t)
    t = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " trên ", t)                   # chi phí nhân sự/DTT
    t = t.replace("&", " và ")
    t = re.sub(r",\s*(?=[,.;:!?]|$)", "", t)                          # "(+6,2%)." leaves no ",."
    # a two-word lead sentence ("Phần 2.") voiced alone comes out garbled: read it into the next one
    t = re.sub(r"(?:^|(?<=[.!?]\s))([^\s.!?]+(?:\s+[^\s.!?]+)?)\.\s+(?=[^\W\d_])", r"\1, ", t)
    return re.sub(r"\s+", " ", speakable(t)).strip()


def _sentences(text: str) -> list:
    """Split after '.', '!' or '?' followed by a space; '1.248', 'tr. mỗi' and a short lead 'Phần 2.' stay whole."""
    out = []
    for part in re.split(r"(?<=[.!?])\s+", str(text).strip()):
        joined = out and (len(out[-1].split()) <= 2 or not (part[:1].isupper() or part[:1].isdigit() or part[:1] in "\"“('"))
        if part and joined:                                           # "Phần 2." stays with what follows
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def _colon(part: str) -> list:
    """Breathe after ':' only after a long lead-in and before a lower-case clause. 'Đề xuất: giữ 2,40%',
    'Tổng chi phí: 186,4 tỷ' and a title 'AOP Revised FY2026–2027: Chi phí nhân sự' stay one breath."""
    m = re.search(r":\s+", part)
    if m and len(part[:m.start()]) > 25 and part[m.end():m.end() + 1].islower():
        return [part[:m.start() + 1]] + _colon(part[m.end():])
    return [part]


def pieces(text: str) -> list:
    """A sentence cut where a speaker breathes: at each sentence end, after ';', after a long lead-in
    before ':', and before a long tail after a comma. Each piece is voiced on its own with a short pause
    between, so a figure-heavy line never runs on."""
    out = []
    for sentence in _sentences(text):
        for semi in re.split(r"(?<=;)\s+", sentence):
            for part in _colon(semi):
                if len(part) > 110:                                   # a long line: breathe at commas too
                    sub, cur = [], ""
                    for seg in re.split(r"(?<=,)\s+(?=\D)", part):
                        cur = (cur + " " + seg).strip() if cur else seg
                        if len(cur) > 45:
                            sub.append(cur)
                            cur = ""
                    if cur:
                        sub.append(cur)
                    out += sub
                elif part:
                    out.append(part)
    return out
