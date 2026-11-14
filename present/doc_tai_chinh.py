"""How a Vietnamese FP&A person reads a report aloud. Captions keep the report's own text; only the voice
gets these words.

- Finance abbreviations are said the way people say them in a Vietnamese finance team: "AOPR" is
  "ây-ô-pi điều chỉnh", "DTT" is "doanh thu thuần", "FP&A" is "ép-pi-en-ây", "YoY" is "so với cùng kỳ".
- Any other short all-caps word (a channel, a department code) is spelled letter by letter, English style,
  as people do ("MT" = "em-ti", "SKU" = "ét-cây-giu"), never read as a Vietnamese syllable. An all-caps
  Vietnamese heading ("TỔNG CHI PHÍ") is read as words, not spelled.
- Periods: "Q3/2026" is "quý 3 năm 2026", "20/11/2026" is "ngày 20 tháng 11 năm 2026", "T10" is "tháng 10",
  "9M" is "9 tháng", "H1" is "sáu tháng đầu năm", "FY26" is "năm tài chính 2026".
- Signs: a minus is "âm" ("Lợi nhuận là −2,1 tỷ"); it is "giảm" only for a change ("−5,4 tỷ so với AOP",
  "tức −18%", "(−1,4pp)"). "+12,6%" is "tăng". An accounting "(1.234)" is "âm" only as a whole cell or after
  ":"; "(35%)" after a figure stays a share. "(1) tăng…" is "thứ nhất". "3-5" is "đến"; "2026–2027" is
  "hai nghìn không trăm hai mươi sáu – hai mươi bảy". "≥", "<5%", "±", "x2" are said in words.
- Units: "149,4 triệu/năm" is "mỗi năm", "nhân sự/DTT" is "trên", "GT/MT" is "GT và MT", "1,2x" is "lần",
  "120 bps" is "điểm cơ bản", "~3%" is "khoảng 3%".
- Figures are then turned into words by present.vn_format.speakable ("186,4" = "một trăm tám mươi sáu phẩy bốn").

The owner can add his company's own words in a file (CLICKY_TU_DOC, one "VIẾT TẮT = cách đọc" per line).
A line there wins over the table here: "AOP = kế hoạch năm" makes the voice say "kế hoạch năm" for AOP."""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from present.vn_format import int_words, join_groups, slash_kind, speakable

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
    "KPI": "cây-pi-ai", "KPIs": "các cây-pi-ai", "LE": "số ước thực hiện", "FC": "dự báo", "PY": "năm trước",
    "ROI": "a-ô-ai", "ROE": "a-ô-i", "ROA": "a-ô-ây", "SKU": "ét-cây-giu", "ERP": "i-a-pi",
    "CFO": "xi-ép-ô", "CEO": "xi-i-ô", "FM": "ép-em", "HR": "nhân sự", "tr": "triệu", "tr.": "triệu",
    "HRC": "hô-rê-ca", "HORECA": "hô-rê-ca", "VAT": "vát", "USD": "đô la Mỹ",
    "COGS": "giá vốn hàng bán", "CAGR": "tăng trưởng kép bình quân", "D&A": "khấu hao",
    "B2B": "bi-tu-bi", "B2C": "bi-tu-xi", "GM": "biên lợi nhuận gộp", "QoQ": "so với quý trước",
    "SG&A": "chi phí bán hàng và quản lý", "R&D": "nghiên cứu và phát triển", "NPAT": "lợi nhuận sau thuế",
    "PBT": "lợi nhuận trước thuế", "GSV": "doanh thu gộp", "NSV": "doanh thu thuần",
    # Vietnamese accounting and HR abbreviations ("HC" alone is a department code here: it is spelled)
    "BHXH": "bảo hiểm xã hội", "BHYT": "bảo hiểm y tế", "BHTN": "bảo hiểm thất nghiệp",
    "KPCĐ": "kinh phí công đoàn", "TNCN": "thu nhập cá nhân", "TNDN": "thu nhập doanh nghiệp",
    "GTGT": "giá trị gia tăng", "TSCĐ": "tài sản cố định", "CCDC": "công cụ dụng cụ", "NVL": "nguyên vật liệu",
    "HTK": "hàng tồn kho", "KQKD": "kết quả kinh doanh", "BCTC": "báo cáo tài chính", "KD": "kinh doanh",
    "KT": "kế toán", "NS": "nhân sự", "HCNS": "hành chính nhân sự", "HC-NS": "hành chính nhân sự",
    "MKT": "ma-két-tinh", "TP.HCM": "thành phố Hồ Chí Minh", "TP. HCM": "thành phố Hồ Chí Minh",
    "TPHCM": "thành phố Hồ Chí Minh", "HCM": "Hồ Chí Minh", "HN": "Hà Nội", "kg": "ki-lô-gam",
    "GP": "lợi nhuận gộp",
    # report columns: "% TH/KH" is "phần trăm thực hiện trên kế hoạch". "KH" in "Mã KH" is a customer.
    "TH": "thực hiện", "KH": "kế hoạch", "LK": "lũy kế", "CK": "cùng kỳ", "NT": "năm trước",
    "Mã KH": "Mã khách hàng", "mã KH": "mã khách hàng", "CKTM": "chiết khấu thương mại",
    "CK thương mại": "chiết khấu thương mại",
}
# English finance words a Vietnamese voice cannot read: what the team says instead (any letter case)
ENGLISH = {"aop revised": "ây-ô-pi điều chỉnh", "revised": "điều chỉnh", "headcount": "số nhân sự",
           "forecast": "dự báo", "budget": "ngân sách", "actual": "thực tế", "actuals": "số thực tế",
           "variance": "chênh lệch", "gap": "chênh lệch", "bridge": "cầu chênh lệch", "run-rate": "tốc độ hiện tại",
           "run rate": "tốc độ hiện tại", "target": "mục tiêu", "baseline": "mức gốc", "outlook": "triển vọng",
           "margin": "biên lợi nhuận", "vs": "so với", "clicky": "Cờ-lích-ki", "logistics": "lô-gít-tích",
           "working capital": "vốn lưu động", "cash conversion cycle": "chu kỳ chuyển đổi tiền mặt",
           "cash flow": "dòng tiền", "sales": "bán hàng",
           # HR and FP&A words the team writes in English
           "bonus": "thưởng", "training": "đào tạo", "recruitment": "tuyển dụng", "turnover": "nghỉ việc",
           "attrition": "nghỉ việc", "overhead": "chi phí chung", "promotion": "khuyến mãi",
           "online": "trực tuyến", "offline": "ngoại tuyến", "payroll": "quỹ lương", "benefit": "phúc lợi",
           "benefits": "phúc lợi", "freelancer": "cộng tác viên", "freelancers": "cộng tác viên",
           "one-off": "một lần", "one off": "một lần", "upside": "kịch bản tốt", "downside": "kịch bản xấu",
           "sell-in": "bán vào", "sell in": "bán vào", "sell-out": "bán ra", "sell out": "bán ra",
           "trade spend": "chi phí thương mại", "accrual": "trích trước", "accruals": "trích trước",
           "break-even": "hòa vốn", "breakeven": "hòa vốn", "break even": "hòa vốn",
           "rolling forecast": "dự báo cuốn chiếu", "deep dive": "phân tích sâu", "deep-dive": "phân tích sâu",
           "net sales": "doanh thu thuần", "sales target": "mục tiêu bán hàng", "e-commerce": "thương mại điện tử",
           "ecommerce": "thương mại điện tử", "outsourcing": "thuê ngoài", "opex": "chi phí hoạt động",
           "capex": "chi phí đầu tư", "driver-based forecast": "dự báo theo động lực",
           "driver based forecast": "dự báo theo động lực", "driver-based": "theo động lực", "driver based": "theo động lực"}
# English letter names written so a Vietnamese voice says them right
LETTERS = {"A": "ây", "B": "bi", "C": "xi", "D": "đi", "E": "i", "F": "ép", "G": "di", "H": "hát", "I": "ai",
           "J": "giây", "K": "cây", "L": "e-lờ", "M": "em", "N": "en", "O": "ô", "P": "pi", "Q": "kiêu", "R": "a",
           "S": "ét", "T": "ti", "U": "giu", "V": "vi", "W": "đáp-bờ-liu", "X": "ích", "Y": "oai", "Z": "dét"}
VN_WORD = re.compile(r"[ĂÂĐÊÔƠƯÁÀẢÃẠẮẰẲẴẶẤẦẨẪẬÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ]")
# an all-caps word that is a Vietnamese syllable written without marks ("CHI", "THEO", "DOANH")
_SYLLABLE = re.compile(r"(?:NGH|CH|GH|GI|KH|NG|NH|PH|QU|TH|TR|[BCDGHKLMNPRSTVX])?[AEIOUY]{1,3}(?:CH|NG|NH|[CMNPT])?")
_SHORT_VN = {"DO", "BA", "TO"}
_MONEY = r"đồng|đ|triệu|tr|tỷ|nghìn|USD|VND|VNĐ|ơ-rô|đô la"


# a shorter way people also ask for the same thing ("chi phí nhân sự trên doanh thu" for ".../DTT")
SHORTER = {"DTT": ["doanh thu"], "LNST": ["lợi nhuận"], "AOPR": ["AOP điều chỉnh"], "GVHB": ["giá vốn"]}


_SHORT_ALIAS = ("DT", "CP", "NV", "SL", "HR", "TH", "KH", "LK", "CK", "NT")


def aliases() -> dict:
    """{abbreviation: [what people say instead]}, for matching a reader's question to the report's labels.
    Only meanings in words: a reading spelled letter by letter is not something a reader types."""
    out = {}
    for k, v in {**WORDS, **owner_words()}.items():
        if "-" not in v and not k.islower() and (len(k) >= 3 or k in _SHORT_ALIAS):  # "LE" is a word
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
    prev = re.sub(r"\(\s*$", " ", before).lower().split()[-6:]
    words = said.split()
    for k in range(min(len(prev), len(words)), 0, -1):
        if [w.lower() for w in words[:k]] == prev[-k:]:
            return " ".join(words[k:])
    return said


def _abbr(m: re.Match, table: dict, spelled: list | None = None) -> str:
    w = m.group(0)
    if w in table:
        return _without_repeat(m.string[:m.start()], table[w])
    code = re.fullmatch(r"([A-Z]{1,4})(\d{1,3})", w)                  # BU1: letters, then the number
    if code:
        letters = code.group(1)
        if letters not in table and spelled is not None:
            spelled.append(letters)
        return f"{table.get(letters) or _spell(letters)} {code.group(2)}"
    if 2 <= len(w) <= 5 and w.isupper() and w.isalpha() and not VN_WORD.search(w):
        if spelled is not None:
            spelled.append(w)
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


_DATE_CUE = r"(?:ngày|hạn|từ|đến|trước|sau|dịp|khuyến mãi|chương trình)"


def _slash_say(a: int, b: int, kind: str, cue: str = "") -> str:
    if kind == "date":
        lead = cue if re.match(r"(?i)ngày", cue) else cue + "ngày "
        return f"{lead}{a} tháng {b}"
    return f"{cue}{a} {'phần' if kind == 'fraction' else 'trên'} {b}"


def _day_month(m: re.Match) -> str:
    """'5/11' is a date, '3/10 khối' a fraction, '4/4 khối' and 'bậc 3/7' a ratio: vn_format.slash_kind,
    the same rule as the build's figure check (present.clicky_html.figures)."""
    cue, a, b = m.group(1) or "", int(m.group(2)), int(m.group(3))
    kind = slash_kind(a, b, m.string[:m.start(2)], m.string[m.end():])
    return _slash_say(a, b, kind, cue)


def _day_range(m: re.Match) -> str:
    """'(3/11–9/11)' is 'từ ngày 3 đến ngày 9 tháng 11'; across months each date keeps its month."""
    a, b, c, d = (int(x) for x in m.groups())
    before, after = m.string[:m.start()], m.string[m.end():]
    if slash_kind(a, b, before, "") != "date" or slash_kind(c, d, "đến ", after) != "date":
        return m.group(0)
    lead = "" if re.search(r"(?i)\btừ\s*$", before) else "từ "
    if b == d:
        return f"{lead}ngày {a} đến ngày {c} tháng {d}"
    return f"{lead}ngày {a} tháng {b} đến ngày {c} tháng {d}"


def _month_year(m: re.Match) -> str:
    mo = int(m.group(2))
    return f"tháng {mo} năm {m.group(3)}" if 1 <= mo <= 12 else m.group(0)


def _paren_negative(m: re.Match) -> str:
    """Brackets mean a negative only where an accounting cell would: the bracket is the whole line ('(1.234)'),
    or it follows ':' or '=' ('Chênh lệch: (2,1%)'). A share after a figure ('92,3 tỷ (66,2%)'), after 'chiếm'
    or a direction word ('giảm (12,6%)') and a bare '(35%)' or '(2026)' keep their brackets.
    A bracketed figure followed by a money unit ('Lợi nhuận (9,8) tỷ', 'EBIT (2,1) tỷ') is a negative too.
    The same rule as the build's figure check (present.clicky_html.figures)."""
    num, pct = m.group(1), m.group(2) or ""
    before, after = m.string[:m.start()], m.string[m.end():]
    whole = not before.strip() and not after.strip(" .;,!?")
    money = not pct and re.match(r"\s*(?:tỷ|triệu|nghìn|đồng|tr)\b", after)          # "(9,8) tỷ" in prose
    if whole or money or re.search(r"[:=]\s*$", before):
        return f"âm {num}{pct}"
    return m.group(0)


def _ordinal(n: int) -> str:
    return "thứ " + {1: "nhất", 4: "tư"}.get(n, int_words(n))


def _years(y1: str, y2: str) -> str:
    """'2026–2027' is said 'hai nghìn không trăm hai mươi sáu – hai mươi bảy': the century once."""
    short = int(y2[2:]) if y1[:2] == y2[:2] else int(y2)
    return f"{y1} – {int_words(short)}"


def _minus(m: re.Match, bridge: bool = False) -> str:
    """A minus is 'âm' (a level: 'là −2,1 tỷ', 'Dòng tiền thuần −12,5 tỷ', 'Tăng trưởng −2,3%'). It is 'giảm'
    for a change: 'so với' later in the same clause, just after 'tức', 'tương đương' or '(', in a bridge line
    that also has a '+', or when the sentence already said 'tăng' ('MT tăng 11,2%, GT −3,4%'). Right after 'giảm' the sign is dropped: 'giảm −5%' is 'giảm 5%'."""
    before, after = m.string[:m.start()], m.string[m.end():]
    clause = re.split(r"[,;:.!?](?=\s|$)", after, maxsplit=1)[0]
    if re.search(r"(?i)\btăng trưởng\s*$", before):
        return "âm "
    if re.search(r"(?i)\b(?:giảm|sụt|thấp hơn|lỗ)\s*$", before):
        return ""
    sentence = re.split(r"[.!?](?=\s)", before)[-1]
    if (bridge or re.search(r"(?i)(?:\btức|\btương đương|\()\s*$", before) or re.search(r"\bso với\b", clause)
            or re.search(r"(?i)\btăng\b(?!\s+trưởng)", sentence)):
        return "giảm "
    return "âm "


_PERIOD_TAG = {"A": "thực hiện", "B": "kế hoạch", "F": "dự báo", "E": "ước tính"}


def _periods(t: str) -> str:
    """Dates and periods, before any '/' is read as 'trên' or 'mỗi' and before '-' is read as 'đến'."""
    yr = r"((?:19|20)\d{2})"
    t = re.sub(rf"\bFY\s?{yr}\s*[-–—/]\s*{yr}\b", lambda m: "năm tài chính " + _years(m.group(1), m.group(2)), t)
    t = re.sub(r"\bFY\s?(\d{2})\s*[-–—/]\s*(\d{2})\b",                                        # FY26-27
               lambda m: "năm tài chính " + _years("20" + m.group(1), "20" + m.group(2)), t)
    t = re.sub(r"\bFY\s?(\d{2})(\d{2})\b", lambda m: f"năm tài chính {m.group(1)}{m.group(2)}" if m.group(1) in
               ("19", "20") else "năm tài chính " + _years("20" + m.group(1), "20" + m.group(2)), t)  # FY2026, FY2627
    t = re.sub(r"\bFY\s?(\d{2})\b", r"năm tài chính 20\1", t)                                    # FY26
    t = re.sub(r"(?i)(\bngày\s+)?(?<![\d.,])\b(\d{1,2})\s*([/.-])\s*(\d{1,2})\s*\3\s*((?:19|20)\d{2})\b", _date, t)
    t = re.sub(rf"(?i)(?:\btháng\s+|\bT)(1[0-2]|0?[1-9])\s*[-.]\s*{yr}\b", r"tháng \1 năm \2", t)  # tháng 11-2026
    t = re.sub(rf"(?<![\d.,])\b(1[0-2]|0?[1-9])\s*[-.]\s*{yr}\b(?![.,]\d)", r"tháng \1 năm \2", t)   # 11.2026
    t = re.sub(rf"(?<![\d.,]){yr}\s*[-–—]\s*{yr}\b", lambda m: _years(m.group(1), m.group(2)), t)  # 2026-2027
    t = re.sub(rf"\b{yr}([ABFE])\b", lambda m: f"{_PERIOD_TAG[m.group(2)]} năm {m.group(1)}", t)   # 2025A, 2026B
    t = re.sub(rf"\bQ([1-4])\s*[-–—]\s*Q([1-4])(?:\s*[/-]\s*{yr}\b)?",                          # Q1–Q3/2026
               lambda m: f"quý {m.group(1)} đến quý {m.group(2)}" + (f" năm {m.group(3)}" if m.group(3) else ""), t)
    t = re.sub(rf"\bT(1[0-2]|[1-9])\s*[-–—]\s*T(1[0-2]|[1-9])(?:\s*[/-]\s*{yr}\b)?",            # T1–T10/2026
               lambda m: f"tháng {m.group(1)} đến tháng {m.group(2)}" + (f" năm {m.group(3)}" if m.group(3) else ""), t)
    t = re.sub(rf"\bH([12])\s*[/-]\s*{yr}\b",                                                   # H1/2026
               lambda m: f"6 tháng {'đầu' if m.group(1) == '1' else 'cuối'} năm {m.group(2)}", t)
    t = re.sub(rf"\bQ([1-4])\s*[/-]\s*{yr}\b", r"quý \1 năm \2", t)                              # Q3/2026, Q3-2026
    t = re.sub(r"\bQ([1-4])\s*/\s*(\d{2})\b(?![\d/])", r"quý \1 năm 20\2", t)                    # Q3/26
    t = re.sub(rf"\bQ([1-4])\s+{yr}\b", r"quý \1 năm \2", t)
    t = re.sub(r"\bQ([1-4])\b", r"quý \1", t)
    t = re.sub(r"\bH1\b", "6 tháng đầu năm", t)
    t = re.sub(r"\bH2\b", "6 tháng cuối năm", t)
    t = re.sub(r"\b(3|6|9|12)M\b(?!\s*(?:USD|VND|\$))", r"\1 tháng", t)        # lũy kế 9M
    t = re.sub(rf"\b(1[0-2]|[1-9])T\s*/\s*{yr}\b", r"\1 tháng năm \2", t)       # 9T/2026
    t = re.sub(r"\b(1[0-2]|[1-9])T\b", r"\1 tháng", t)                          # 6T đầu năm
    t = re.sub(rf"\bT(1[0-2]|[1-9])\s*/\s*{yr}\b", r"tháng \1 năm \2", t)
    t = re.sub(r"\bT(1[0-2]|[1-9])\b", r"tháng \1", t)
    t = re.sub(r"(?i)(\btháng\s+)?(?<![\d.,])\b(\d{1,2})\s*/\s*((?:19|20)\d{2})\b(?!\s*/)", _month_year, t)
    t = re.sub(r"(?<![\d.,])\b(\d{1,2})/(\d{1,2})\b(?![/%]|[.,]\d)",              # 30/70: a split, not a date
               lambda m: f"{int_words(int(m.group(1)))} – {int_words(int(m.group(2)))}"
               if int(m.group(1)) + int(m.group(2)) == 100 else m.group(0), t)
    t = re.sub(r"(?<![\d.,/])\b(\d{1,2})/(\d{1,2})\s*[-–—]\s*(\d{1,2})/(\d{1,2})\b(?![/%]|[.,]\d)",
               _day_range, t)                                                          # 3/11–9/11
    t = re.sub(rf"(?i)(\b{_DATE_CUE}\s+)?(?<![\d.,])\b(\d{{1,2}})/(\d{{1,2}})\b(?![/%]|[.,]\d|\s*/)",
               _day_month, t)                                                          # 15/11, 3/10
    t = re.sub(rf"(?i)(\b{_DATE_CUE}\s+)(?<![\d.,])\b(\d{{1,2}})\.(\d{{1,2}})\b(?=\s*(?:năm\b|[,;:!?]|\.(?!\d)|$))",
               _day_month, t)                                                          # đến 31.10 năm nay
    # "6 tháng cuối năm (H2)": the bracket only repeats the words just said
    t = re.sub(r"(?i)\b((?:\w+\s+){0,5}\w+)\s*\(\s*\1\s*\)", r"\1", t)
    t = re.sub(r"\b(?:TK|[Tt]ài khoản)\s*(\d{3,})",                                # TK 6421: digit by digit
               lambda m: "tài khoản " + " ".join(int_words(int(c)) for c in m.group(1)), t)
    return t


_UNIT = r"(?:%|pp|điểm phần trăm|nghìn tỷ|tỷ|triệu|nghìn|đồng|người|tr\b)"
# a figure, a sign, a figure: "12 tỷ + 3 tỷ", "245,3 tỷ +3,2%", "12 tỷ − 3 tỷ thuế"
_ARITH = re.compile(rf"(\d[\d.,]*)(\s*{_UNIT})?(\s*)([+−]|(?<=\s)[-–](?=\s))(\s*)(\d[\d.,]*)(\s*{_UNIT})?")


def _value(text: str):
    try:
        return Decimal(text.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _arith(m: re.Match) -> str:
    """'+' between two figures is 'cộng' only when both have the same unit and no comparison follows
    ('12 tỷ + 3 tỷ'); '245,3 tỷ +3,2% YoY' is a change: 'tăng'. A minus with spaces between two figures of
    the same unit is 'trừ' ('12 tỷ − 3 tỷ thuế'); a spaced '-' or '–' only when the second is smaller, else
    it is a range: '5 tỷ – 7 tỷ' is 'đến'."""
    a, ua, gap1, sign, gap2, b, ub = m.groups()
    same = (ua or "").strip() == (ub or "").strip()
    compare = re.match(r"\s*(?:so với|vs\b|cùng kỳ)", m.string[m.end():])
    left = a + (ua or "") + gap1
    if sign == "+":
        if same and not compare:
            return f"{a}{ua or ''} cộng {b}{ub or ''}"
        return f"{a}{ua or ''}{',' if gap1 or ua else ''} +{b}{ub or ''}"
    spaced = gap1 and gap2
    smaller = sign == "−" or (_value(a) or 0) > (_value(b) or 0)
    if same and spaced and not compare:
        return f"{a}{ua or ''} {'trừ' if smaller else 'đến'} {b}{ub or ''}"
    return m.group(0)


def _signs(t: str) -> str:
    bridge = bool(re.search(r"(?<![\w,.])\+\s?\d", t)) and bool(re.search(r"(?<![\w,.])[−–-]\s?\d", t))
    t = re.sub(_ARITH, _arith, t)                                                   # 12 tỷ + 3 tỷ, 12 tỷ − 3 tỷ
    t = re.sub(r"(\b(?:tháng|năm|quý|tuần|ngày|tỷ|triệu|nghìn|đồng|người)|%)\s*[x×]\s*(?=\d)", r"\1 nhân ", t)
    t = re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 nhân ", t)                                # 3 x 4
    # a list marker: "Kính đề nghị: (1) tăng…, (2) giảm…" is "thứ nhất", never "âm một"
    t = re.sub(r"\((\d{1,2})\)(?=\s+[^\W\d_])(?!\s+(?:tỷ|triệu|nghìn|đồng|tr|điểm)\b)",
               lambda m: _ordinal(int(m.group(1))) + ",", t)
    t = re.sub(r"(?i)\b(giảm|tăng)\s*\(\s*[−–+-]?\s*(\d[^()]*?)\s*\)", r"\1 \2", t)     # giảm (−12,5%): giảm 12,5%
    t = re.sub(r"\s*\(\s*\+\s*(\d[^()]*?)\s*\)", r", tức tăng \1,", t)                 # (+6,2%)
    t = re.sub(r"\s*\(\s*[−–-]\s*(\d[^()]*?)\s*\)", r", tức giảm \1,", t)              # (−1,4pp)
    t = re.sub(r"\(\s*(\d[\d.,]*)\s*(%)?\s*\)", _paren_negative, t)
    t = re.sub(r"(\d)\s*[-–—]\s*(\d)", r"\1 đến \2", t)                         # 3–5 người
    t = re.sub(r"(?<![\w,.])\+\s?(?=\d)", "tăng ", t)                             # +12,6% so với AOP
    t = re.sub(r"(?<![\w,.])[−–-]\s?(?=\d)", lambda m: _minus(m, bridge), t)       # −2,1 tỷ: âm, unless a change
    t = re.sub(r"[~≈]\s*(?=\d)", "khoảng ", t)
    t = re.sub(r"\s*≥\s*", " lớn hơn hoặc bằng ", t)
    t = re.sub(r"\s*≤\s*", " nhỏ hơn hoặc bằng ", t)
    t = re.sub(r"\s*<\s*(?=\d)", " dưới ", t)
    t = re.sub(r"\s*>\s*(?=\d)", " trên ", t)
    t = re.sub(r"\s*±\s*", " cộng trừ ", t)
    t = re.sub(r"(?:\bgấp\s+)?(?<!\w)[x×]\s*(?=\d)", "gấp ", t)                    # x2, gấp x2
    t = re.sub(r"\s*=\s*", " bằng ", t)
    return t


def _units(t: str) -> str:
    t = re.sub(r"\bGM\s*%", "biên lợi nhuận gộp", t)
    t = re.sub(r"\b(EBITDA|EBIT)\s+margin\b", r"biên \1", t, flags=re.I)
    t = re.sub(r"\bUSD\s*/\s*VND\b", "USD", t)                                     # tỷ giá USD/VND
    t = re.sub(r"\bvs\.(?=\s)", "vs", t)
    t = re.sub(r"(\d)\s*x\b", r"\1 lần", t)                                        # 1,2x
    t = re.sub(r"(\d)\s*pp\b", r"\1 điểm phần trăm", t)
    t = re.sub(r"(\d)\s*bps?\b", r"\1 điểm cơ bản", t)
    t = re.sub(r"(\d)\s*đ\b", r"\1 đồng", t)
    t = re.sub(r"(\d)\s*tr\b(?:\.(?=\s+[a-zđà-ỹ]))?", r"\1 triệu", t)                                  # 18,5tr                                       # 1.250.000 đ
    t = re.sub(r"(\d)\s*HC\b", r"\1 người", t)                                     # 1.248 HC
    t = re.sub(r"%\s*/\s*(?=[\d−+-])", "% và ", t)                                 # 65%/35%
    t = re.sub(rf"\b({_MONEY})\s*/\s*(?=[^\W\d_])", r"\1 mỗi ", t)                 # đồng/thùng, tr/người
    t = re.sub(r"(?<=[\w%])\s*/\s*(?=năm|tháng|quý|người|ngày|tuần)", " mỗi ", t)    # 7,5%/năm
    t = re.sub(r"(?<=[A-Za-zÀ-ỹ])\s*\+\s*(?=[A-Za-zÀ-ỹ])", " cộng ", t)             # HRC+KA: one channel
    # "gốc" is lost at a sentence end, so the original plan is "ban đầu" everywhere: one wording per talk
    t = re.sub(r"\b(AOP|[Kk]ế hoạch|[Nn]gân sách)\s+gốc\b", r"\1 ban đầu", t)
    return t


# what the offline voice can say once doc() is done; anything else would be silently skipped
SAYABLE = re.compile(r"[\w\s,.;:!?()–-]")


def unsaid(text: str) -> list:
    """Characters left in a spoken line that the voice would skip (a sign like '≠' or '€')."""
    return sorted({c for c in text if not SAYABLE.match(c)})


def vn_numbers(text: str) -> str:
    """A report written with English number marks (412,580.5) rewritten the Vietnamese way (412.580,5)."""
    swap = {",": ".", ".": ","}
    return re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: "".join(swap.get(c, c) for c in m.group(0)), str(text))


def _arrow(m: re.Match) -> str:
    """'15% → 13%' is 'xuống', '200 → 245 tỷ' and '165,5 tỷ → 186,4 tỷ' are 'lên'; between words
    ('AOP → LE') it is 'sang'."""
    a = re.search(r"(\d[\d.,]*)\s*(?:%|\w+)?\s*$", m.string[:m.start()])
    b = re.match(r"\s*(\d[\d.,]*)", m.string[m.end():])
    if not (a and b):
        return " sang "
    return " xuống " if (_value(b.group(1)) or 0) < (_value(a.group(1)) or 0) else " lên "


def _colon_figure(m: re.Match) -> str:
    """'9:30' is a time ('9 giờ 30'); '2:1' is a ratio ('2 trên 1')."""
    a, b = m.group(1), m.group(2)
    if len(b) == 2 and int(a) <= 23 and int(b) <= 59:
        return f"{int(a)} giờ {int(b)}" if int(b) else f"{int(a)} giờ"
    return f"{a} trên {b}"


def _hour(m: re.Match) -> str:
    return f"{int(m.group(1))} giờ" + (f" {int(m.group(2))}" if m.group(2) and int(m.group(2)) else "")


def doc(text: str, en_numbers: bool = False, spelled: list | None = None) -> str:
    """The sentence as it should be heard. en_numbers: the report writes 412,580.5 (English marks).
    spelled: if given, each code read letter by letter (not in the table: "MT", "KA") is appended to it,
    so the owner can see what to add to TU_DOC.txt."""
    table = {**WORDS, **owner_words()}
    t = vn_numbers(text or "") if en_numbers else str(text or "")
    t = join_groups(t)                                                 # "1 234 567 triệu" is one figure
    t = re.sub(r"_+", " ", t)                                          # a code like __BIEN_BAN__ has no sound
    t = re.sub(r"\bQ([1-4])\s?['’]\s?(\d{2})\b", r"quý \1 năm 20\2", t)       # Q3'26
    t = re.sub(r"(?<=\w)['’‘](?=\s|$)|['’‘`]", "", t)                     # Sales' / quotes have no sound
    scale = r"(\d[\d.,]*(?:\s*(?:nghìn tỷ|tỷ|triệu|nghìn))?)"
    t = re.sub(r"€\s*" + scale, r"\1 ơ-rô", t)                                    # €2,5 triệu -> 2,5 triệu ơ-rô
    t = re.sub(r"\s*€", " ơ-rô", t)
    t = re.sub(r"\$\s*" + scale, r"\1 đô la", t)                                  # $12 triệu -> 12 triệu đô la
    t = re.sub(r"(?<=\d)\s*\$", " đô la", t)
    t = re.sub(r"\s*£", " bảng Anh", t)
    t = re.sub(r"\s*¥", " yên", t)
    t = re.sub(r"(?<![\w#])#\s?(?=\d)", "số ", t)                                  # hạng #1: số một
    t = re.sub(r"[«»“”\"‘’`*•]", "", t)                                 # quotes and bullets have no sound
    t = re.sub(r"\s*…\s*", ", ", t)
    t = re.sub(r"<!--|-->|</?[A-Za-z][^<>]*>|[{}\[\]|\\^#@]", " ", t)        # markup and code marks have no sound
    t = re.sub(r"\s*(?:→|⟶|➝|[−-]+>|=>)\s*", _arrow, t)                              # 15% → 13%
    t = re.sub(r"(?<![\d:.,])(\d{1,2}):(\d{1,2})(?![\d:])", _colon_figure, t)              # 2:1, 9:30
    t = re.sub(r"(?<![\w.,])([01]?\d|2[0-3])h(?:([0-5]\d)(?:p|ph)?)?\b", _hour, t)           # 9h, 9h30
    t = re.sub(r"\s>\s", " lớn hơn ", t)                                       # Doanh thu > chi phí
    t = re.sub(r"\s<\s", " nhỏ hơn ", t)
    t = re.sub(r"\s+[–—]\s+(?=[^\W\d_])", ", ", t)                            # "T10/2026 – Khối Bán hàng": a breath
    t = re.sub(r"(?<![\w&])(YoY|MoM|QoQ)(?![\w&])", lambda m: table[m.group(1)], t)  # before the signs: a change
    t = re.sub(r"\bvs\.?(?=\s)", "so với", t)
    t = _caps_heading(t, table)
    t = _periods(t)
    t = _signs(t)
    t = _units(t)
    t = re.sub(r"\s*—\s*", ", ", t)                                      # any other long dash is a breath
    eng = sorted(ENGLISH, key=len, reverse=True)
    t = re.sub(r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in eng) + r")(?![\w-])",
               lambda m: _without_repeat(m.string[:m.start()], ENGLISH[m.group(0).lower()]), t, flags=re.I)
    t = re.sub(r"\b([A-Z]{2,5})\s*/\s*([A-Z]{2,5})\b",                            # GT/MT: two codes
               lambda m: m.group(0) if m.group(1) in table or m.group(2) in table else f"{m.group(1)} và {m.group(2)}", t)
    keys = sorted(table, key=len, reverse=True)
    pat = "|".join(re.escape(k) for k in keys)
    t = re.sub(rf"(?<![\w&])(?:{pat})(?![\w&])|\b[A-Z]{{1,4}}\d{{1,3}}\b|\b[A-Z]{{2,5}}\b",
               lambda m: _abbr(m, table, spelled), t)
    t = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " trên ", t)                   # chi phí nhân sự/DTT
    t = t.replace("&", " và ")
    t = re.sub(r"\(\s*\)", "", t)                                       # "(GP)" said just before: nothing left
    t = re.sub(r",\s*(?=[,.;:!?]|$)", "", t)                          # "(+6,2%)." leaves no ",."
    # a two-word lead sentence ("Phần 2.") voiced alone comes out garbled: read it into the next one
    t = re.sub(r"(?:^|(?<=[.!?]\s))([^\s.!?]+(?:\s+[^\s.!?]+)?)\.\s+(?=[^\W\d_])", r"\1, ", t)
    t = re.sub(r"^(\W*)[Ưư]ớc(?= thực hiện)", r"\1Số ước", t.strip())  # Piper swallows a first "Ước thực hiện"
    t = re.sub(r"^(\W*)[Ââ]m\b", r"\1Số âm", t)                        # a clip never starts on "âm" (heard "thâm")
    return re.sub(r"\s+", " ", speakable(t)).strip()


def _sentences(text: str) -> list:
    """Split after '.', '!' or '?' followed by a space; '1.248', 'tr. mỗi' and a short lead 'Phần 2.' stay whole."""
    out = []
    for part in re.split(r"(?<=[.!?])\s+", str(text).strip()):
        joined = out and (len(out[-1].split()) <= 2 or out[-1].endswith(("TP.", "Tp.")) or
                          not (part[:1].isupper() or part[:1].isdigit() or part[:1] in "\"“('"))
        if part and joined:                                           # "Phần 2." stays with what follows
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def _colon(part: str) -> list:
    """Breathe after ':' only after a long lead-in, before a lower-case clause, or before a title when the
    whole piece is long when said. 'Đề xuất: giữ 2,40%', 'Tổng chi phí: 186,4 tỷ' and 'Kính đề nghị…: …'
    stay one breath."""
    m = re.search(r":\s+", part)
    if not m or len(part[:m.start()]) <= 25 or re.match(r"(?i)\W*kính đề nghị", part):
        return [part]
    nxt = part[m.end():m.end() + 1]
    if nxt.islower() or (nxt.isupper() and len(doc(part)) > 100):
        return [part[:m.start() + 1]] + _colon(part[m.end():])
    return [part]


_GLUE = re.compile(r"(?i)\W*(?:tức|âm\b|[−–+-]\s?\d)")      # a piece never starts on these: they finish the last one
LIMIT = 90                                                     # spoken characters in one breath
LONG = 120                                  # past this, a breath before "tức" beats running on (8 s in one go)
_SOFT_GLUE = re.compile(r"(?i)\W*tức\b")


def _glued(cur: str, seg: str) -> bool:
    """seg must stay with cur: it starts on a glue word, unless it is 'tức' and the two together would run
    past LONG spoken characters."""
    if not _GLUE.match(seg):
        return False
    return not (_SOFT_GLUE.match(seg) and len(doc(cur + " " + seg)) > LONG)


def _commas(part: str) -> list:
    """Breathe at commas: close the piece before a segment would take it past LIMIT spoken characters."""
    out, cur = [], ""
    for seg in re.split(r"(?<=,)\s+(?=\D)", part):
        if cur and len(doc(cur + " " + seg)) > LIMIT and not _glued(cur, seg):
            out.append(cur)
            cur = seg
        else:
            cur = (cur + " " + seg).strip()
    return out + ([cur] if cur else [])


def pieces(text: str) -> list:
    """A sentence cut where a speaker breathes: at each sentence end, after ';', after a long lead-in
    before ':', and at a comma before the piece gets too long to say in one breath. Each piece is voiced on
    its own with a short pause between, so a figure-heavy line never runs on. A piece never starts with
    a minus ('âm'), and starts with 'tức' only when keeping it would run past LONG spoken characters."""
    out = []
    for sentence in _sentences(text):
        for semi in re.split(r"(?<=;)\s+", sentence):
            for part in _colon(semi):
                for piece in _commas(part):
                    if out and _glued(out[-1], piece) and not re.search(r"[.!?]$", out[-1]):
                        out[-1] += " " + piece
                    elif piece:
                        out.append(piece)
    return out
