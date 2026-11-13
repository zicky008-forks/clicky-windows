"""Make a masked copy of an HTML report: same layout, tables, charts, labels and number formats, but every
figure replaced by a made-up one of the same shape. The owner runs it on his own PC and shares only the copy,
so Clicky can be tested on the real structure without real numbers leaving the company.

    python tools/che_so_lieu.py "<report>.html" [--doi "Tên thật=Tên thay" ...] [--tu-file TEN_CAN_CHE.txt]

Writes "<report>_CHE.html" next to the source; the source is never changed. Nothing is sent anywhere.
What is kept: years (1990-2040), periods (tháng 10, T10, Q3, FY2627, H1), dates, section numbers ("2. ...").
The same figure always gets the same replacement, so a number repeated in a card and a table still matches.
Chart data inside <script> is masked too: number arrays, key: number pairs and JSON blocks. Bundled libraries are
left as is; any other script that still has numbers is counted.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import unicodedata
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SEGMENT = re.compile(r"(<script\b.*?</script\s*>|<style\b.*?</style\s*>|<!--.*?-->|<[^>]+>)", re.S | re.I)
NUM = re.compile(r"(?<![\w#&;])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\w])")
KEEP_BEFORE = re.compile(r"(?:\b(?:tháng|thang|quý|quy|ngày|ngay|kỳ|lần|phần|mục|bước)\s+|(?<!\w)(?:T|Q|H|FY)\s?)$", re.I)
TEXT_ATTRS = re.compile(r"""(\b(?:title|aria-label|alt|data-value|data-label|value)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
NUM_ARRAY = re.compile(r"\[\s*-?\d[\d.,\s\-eE+]*\]")
KEY_NUMBER = re.compile(r"""((?:["'][^"'\n]{1,60}["']|\b[A-Za-z_À-ỹ][\wÀ-ỹ]*)\s*:\s*-?)(\d+(?:\.\d+)?)(?![\w.])""")
LIBRARY = re.compile(r"/\*!|@license|Copyright|Chart\.js|echarts|plotly|Highcharts|jQuery|d3js\.org|d3\.js", re.I)
LONG_STRING = re.compile(r'"(?:[^"\\\n]|\\.){200,}"|' + r"'(?:[^'\\\n]|\\.){200,}'|`(?:[^`\\]|\\.){200,}`", re.S)
DATA_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")     # in CSV a comma separates fields
ENC = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


def read_html(path: Path) -> str:
    """Text of a report in any encoding Excel or a browser writes (UTF-8 with or without BOM, windows-1258...)."""
    return unicodedata.normalize("NFC", _decode(path.read_bytes()))


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    m = ENC.search(raw[:4096])
    for enc in ([m.group(1).decode("ascii", "ignore")] if m else []) + ["utf-8", "cp1258", "cp1252"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class Masker:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.map: dict = {}
        self.count = 0

    def figure(self, s: str) -> str:
        """Same digits get the same made-up digits, whatever the marks: 186,4 in a card and 186.4 in a chart match."""
        digits = re.sub(r"\D", "", s)
        if digits not in self.map:
            body = digits.lstrip("0")
            lead = digits[:len(digits) - len(body)]               # 0,5 stays below one
            self.map[digits] = lead + "".join(str(self.rng.randint(1, 9) if i == 0 else self.rng.randint(0, 9))
                                              for i, _ in enumerate(body))
        self.count += 1
        fake = iter(self.map[digits])
        return "".join(next(fake) if c.isdigit() else c for c in s)

    def text(self, t: str) -> str:
        def sub(m):
            s, before = m.group(1), t[max(0, m.start() - 8):m.start()]
            plain = s.replace(".", "").replace(",", "")
            if len(plain) == 4 and plain.isdigit() and 1990 <= int(plain) <= 2040 and s == plain:
                return s                                          # a year
            if KEEP_BEFORE.search(before):
                return s                                          # a period: tháng 10, T10, Q3, FY2627
            if re.match(r"\s*[/\-]\s*\d", t[m.end():m.end() + 3]) or re.search(r"\d\s*/\s*$", before):
                return s                                          # a date: 20/11, 11/2026
            if len(s) <= 2 and re.match(r"\.\s", t[m.end():m.end() + 2]) and not t[:m.start()].strip():
                return s                                          # a section number: "2. Chi tiết"
            return self.figure(s)
        return NUM.sub(sub, t)

    def script(self, js: str) -> str:
        fig = lambda n: self.figure(n.group(0))
        out = NUM_ARRAY.sub(lambda m: re.sub(r"\d+(?:\.\d+)?", fig, m.group(0)), js)
        head = out[:out.find(">") + 1]
        if re.search(r"""type\s*=\s*["']?application/(?:ld\+)?json""", head, re.I):
            return head + re.sub(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", fig, out[len(head):])   # data only: every number
        if LIBRARY.search(out[:400]):
            return out                                            # a bundled chart library: code, no report data
        # data kept as text, CSV or JSON in a long string (window.DATA = {"t": "cột,cột\n...,12.5,..."}): every number
        out = LONG_STRING.sub(lambda m: self.data_text(m.group(0)), out)
        # data written as objects: {value: 186.4}, "doanh_thu": 412580 (a key, then a number)
        return KEY_NUMBER.sub(lambda m: m.group(1) + self.figure(m.group(2)), out)

    def data_text(self, t: str) -> str:
        """A CSV or JSON text: every figure masked; years and ISO dates (2026-10-31) kept, so periods still match."""
        def sub(m):
            s = m.group(0)
            if re.fullmatch(r"(19|20)\d\d", s) or re.fullmatch(r"\d\d", s) and \
                    re.search(r"(19|20)\d\d-(\d\d-)?$", t[max(0, m.start() - 8):m.start()]):
                return s
            return self.figure(s)
        return DATA_NUM.sub(sub, t)


def mask(html: str, swaps: dict, seed=None) -> tuple:
    mk = Masker(seed)
    parts, untouched = [], 0
    for seg in SEGMENT.split(html):
        if not seg:
            continue
        low = seg[:8].lower()
        if low.startswith("<script"):
            new = mk.script(seg)
            if new == seg and re.search(r"\d{2,}", re.sub(r"<[^>]+>", "", seg)) and not LIBRARY.search(seg[:400]):
                untouched += 1
            parts.append(new)
        elif low.startswith("<style") or seg.startswith("<!--"):
            parts.append(seg)
        elif seg.startswith("<"):
            parts.append(TEXT_ATTRS.sub(lambda m: m.group(1) + m.group(2) + mk.text(m.group(3)) + m.group(2), seg))
        else:
            parts.append(mk.text(seg))
    out = "".join(parts)
    for real, fake in sorted(swaps.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(re.escape(real), fake, out, flags=re.I)
    out = re.sub(r"(<meta[^>]+charset=[\"']?)[\w-]+", r"\1utf-8", out, count=1, flags=re.I)
    return out, mk.count, untouched


def _data_parts(html: str) -> str:
    """The report without bundled libraries and styles: where its figures can be."""
    return " ".join(seg for seg in SEGMENT.split(html) if seg and not (
        seg[:8].lower().startswith("<script") and LIBRARY.search(seg[:400])) and not seg[:7].lower().startswith("<style"))


AUDIT_NUM = re.compile(r"(?<![A-Za-zÀ-ỹ_\d.,])\d(?:[\d.,]*\d)?")


def leftovers(real: str, masked: str) -> int:
    """How many figures of 3+ digits (not a year) are still the same, at the same place, in the masked copy, outside
    bundled libraries. Masking keeps every figure's shape, so the figures line up one to one. 0 is the goal."""
    def toks(t):
        return AUDIT_NUM.findall(_data_parts(t))     # whole figures; "m104", "FY2627" are names, not figures
    a, b = toks(real), toks(masked)
    if len(a) != len(b):                       # names swapped into digits, or the like: compare as sets instead
        have = {x for x in a if len(re.sub(r"\D", "", x)) >= 5}
        return sum(1 for x in b if x in have)
    return sum(1 for x, y in zip(a, b) if x == y and len(re.sub(r"\D", "", x).lstrip("0")) >= 3
               and not re.fullmatch(r"(19|20)\d\d", x))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tạo bản che số của báo cáo HTML để thử Clicky mà số thật không rời máy.")
    ap.add_argument("bao_cao")
    ap.add_argument("--doi", action="append", default=[], help='đổi tên: "Tên thật=Tên thay"')
    ap.add_argument("--tu-file", help="file mỗi dòng: Tên thật = Tên thay")
    a = ap.parse_args(argv)
    src = Path(a.bao_cao)
    if not src.is_file():
        print(f"Không thấy file {src}")
        return 2
    swaps = {}
    lines = list(a.doi)
    if a.tu_file and Path(a.tu_file).is_file():
        lines += read_html(Path(a.tu_file)).splitlines()
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = (x.strip() for x in line.split("=", 1))
            if k:
                swaps[k] = v or "Công ty A"
    real = read_html(src)
    out, n, untouched = mask(real, swaps)
    left = leftovers(real, out)
    dst = src.with_name(src.stem + "_CHE.html")
    dst.write_text(out, encoding="utf-8")
    print(f"Đã tạo bản che số: {dst}")
    print(f"  Đổi {n} con số sang số giả cùng dạng (giữ năm, tháng, quý, ngày, số mục). Đổi {len(swaps)} tên.")
    print(f"  Kiểm lại: còn {left} số (từ 3 chữ số, không kể năm) giống hệt bản thật ở cùng chỗ (0 đến vài số là sạch, do trùng ngẫu nhiên).")
    if untouched:
        print(f"  Lưu ý: {untouched} đoạn script có số mà em không chắc là dữ liệu, nên giữ nguyên. Anh mở bản che xem lướt.")
    print("  Trước khi gửi: mở bản _CHE.html xem lướt. Còn số thật hay tên nhạy cảm thì thêm vào TEN_CAN_CHE.txt rồi chạy lại.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
