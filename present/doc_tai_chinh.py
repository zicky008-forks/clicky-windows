"""How a Vietnamese FP&A person reads a report aloud. Captions keep the report's own text; only the voice
gets these words.

- Finance abbreviations are said the way people say them in a Vietnamese finance team: "AOPR" is
  "ây-ô-pi điều chỉnh", "DTT" is "doanh thu thuần", "FP&A" is "ép-pi-en-ây", "YoY" is "so với cùng kỳ".
- Any other short all-caps word (a channel, a department code) is spelled letter by letter, English style,
  as people do ("MT" = "em-ti", "HRC" = "hát-a-xi"), never read as a Vietnamese syllable.
- Signs and units: "+12,6%" is "tăng 12,6%", "149,4 triệu/năm" is "149,4 triệu mỗi năm", "2026–2027" is
  "2026 đến 2027", "FY2026" is "năm tài chính 2026", "1,2x" is "1,2 lần".
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
    "EBITDA": "ê-bít-đa", "OPEX": "chi phí hoạt động", "Opex": "chi phí hoạt động", "CAPEX": "chi phí đầu tư", "Capex": "chi phí đầu tư",
    "KPI": "cây-pi-ai", "KPIs": "các cây-pi-ai", "LE": "ước tính cả năm", "FC": "dự báo", "PY": "năm trước",
    "ROI": "a-ô-ai", "ROE": "a-ô-i", "ROA": "a-ô-ây", "SKU": "ét-cây-giu", "ERP": "i-a-pi",
    "CFO": "xi-ép-ô", "CEO": "xi-i-ô", "FM": "ép-em", "HR": "nhân sự", "tr": "triệu", "tr.": "triệu",
}
# English finance words a Vietnamese voice cannot read: what the team says instead (any letter case)
ENGLISH = {"aop revised": "ây-ô-pi điều chỉnh", "revised": "điều chỉnh", "headcount": "héc-cao",
           "forecast": "dự báo", "budget": "ngân sách", "actual": "thực tế", "actuals": "số thực tế",
           "variance": "chênh lệch", "gap": "chênh lệch", "bridge": "cầu chênh lệch", "run-rate": "tốc độ hiện tại",
           "run rate": "tốc độ hiện tại", "target": "mục tiêu", "baseline": "mức gốc", "outlook": "triển vọng"}
# English letter names written so a Vietnamese voice says them right
LETTERS = {"A": "ây", "B": "bi", "C": "xi", "D": "đi", "E": "i", "F": "ép", "G": "di", "H": "hát", "I": "ai",
           "J": "giây", "K": "cây", "L": "e-lờ", "M": "em", "N": "en", "O": "ô", "P": "pi", "Q": "kiêu", "R": "a",
           "S": "ét", "T": "ti", "U": "giu", "V": "vi", "W": "đáp-bờ-liu", "X": "ích", "Y": "oai", "Z": "di"}
VN_WORD = re.compile(r"[ĂÂĐÊÔƠƯÁÀẢÃẠẮẰẲẴẶẤẦẨẪẬÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ]")


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
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
    return out


def _spell(word: str) -> str:
    return "-".join(LETTERS.get(c, c) for c in word)


def _abbr(m: re.Match, table: dict) -> str:
    w = m.group(0)
    if w in table:
        return table[w]
    if 2 <= len(w) <= 5 and w.isupper() and w.isalpha() and not VN_WORD.search(w):
        return _spell(w)
    return w


def doc(text: str) -> str:
    """The sentence as it should be heard."""
    table = {**WORDS, **owner_words()}
    t = str(text or "")
    t = re.sub(r"\bFY\s?(\d{2,4})", r"năm tài chính \1", t)
    t = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1 đến \2", t)              # 2026–2027, 3–5 người
    t = re.sub(r"(?<![\w,.])\+\s?(?=\d)", "tăng ", t)                 # +12,6% so với AOP
    t = re.sub(r"(?<![\w,.])[−–]\s?(?=\d)", "giảm ", t)               # −5,4 tỷ written with a real minus
    t = re.sub(r"(\d)\s*x\b", r"\1 lần", t)                           # 1,2x
    t = re.sub(r"(\d)\s*pp\b", r"\1 điểm phần trăm", t)
    t = re.sub(r"(?<=\w)\s*/\s*(?=năm|tháng|quý|người|ngày|tuần)", " mỗi ", t)
    t = re.sub(r"(?<=[A-Za-zÀ-ỹ])\s*\+\s*(?=[A-Za-zÀ-ỹ])", " và ", t)  # HRC+KA
    eng = sorted(ENGLISH, key=len, reverse=True)
    t = re.sub(r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in eng) + r")(?![\w-])",
               lambda m: ENGLISH[m.group(0).lower()], t, flags=re.I)
    keys = sorted(table, key=len, reverse=True)
    pat = "|".join(re.escape(k) for k in keys)
    t = re.sub(rf"(?<![\w&])(?:{pat})(?![\w&])|\b[A-Z]{{2,5}}\b", lambda m: _abbr(m, table), t)
    t = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " trên ", t)                   # chi phí nhân sự/DTT
    t = t.replace("&", " và ")
    return re.sub(r"\s+", " ", speakable(t)).strip()


def pieces(text: str) -> list:
    """A sentence cut where a speaker breathes: after ';' and ':' and before a long tail after a comma.
    Each piece is voiced on its own with a short pause between, so a figure-heavy line never runs on."""
    out = []
    for part in re.split(r"(?<=[;:])\s+", str(text).strip()):
        if len(part) > 110:                                           # a long line: breathe at commas too
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
