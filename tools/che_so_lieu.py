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
NUM = re.compile(r"(?<![\w#&])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\d)")
KEEP_BEFORE = re.compile(r"(?:\b(?P<word>tháng|thang|quý|quy|ngày|ngay|kỳ|lần|phần|mục|bước)\s+|(?<!\w)(?:T|Q|H|FY)\s?)$", re.I)
TEXT_ATTRS = re.compile(r"""(\b(?:title|aria-label|alt|data-[\w-]+|value|placeholder)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
NUM_ARRAY = re.compile(r"\[\s*-?\d[\d.,\s\-eE+]*\]")
KEY_NUMBER = re.compile(r"""((?:["'][^"'\n]{1,60}["']|\b[A-Za-z_À-ỹ][\wÀ-ỹ]*)\s*:\s*-?)(\d+(?:\.\d+)?)(?![\w.])""")
LIBRARY = re.compile(r"/\*!|@license|Copyright|Chart\.js|echarts|plotly|Highcharts|jQuery|d3js\.org|d3\.js", re.I)
YEAR = re.compile(r"20(1[5-9]|2\d|3[0-5])")                    # a period, not a figure: 2015 to 2035 only
CODE_FIGURE = re.compile(r"(?<![\w.#$\\])(?:\d+\.\d+|\d{4,})(?![\w])")
DATA_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\d)")     # in CSV a comma separates fields
SVG_SHAPE = re.compile(r"<\s*(?:rect|line|circle|ellipse|path|polyline|polygon|text|tspan|use|image)\b", re.I)
GEOMETRY = re.compile(r"""(\s(?:x|y|x1|y1|x2|y2|cx|cy|r|rx|ry|dx|dy|width|height|d|points)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
SHOWN_TEXT = re.compile(r"[A-Za-zÀ-ỹ]{2,}\s+.*\d", re.S)        # words and a figure: text the chart shows
CODE_TEXT = re.compile(r"\s*(?:translate|rotate|scale|matrix|rgba?|hsla?|calc|url|[Mm]\s*-?\d|#)", re.I)
DOT_DATE = re.compile(r"(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])")
STYLE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
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


def code_constant(v: str) -> bool:
    """0.5, 1.15 (opacity, padding), 1000, 1200 (durations, screen widths): the code's own settings, kept so the
    copy lays out like the report. A typed figure (672.7, 41258) is neither."""
    if "." in v:
        return float(v) < 10 and len(v.split(".")[1]) <= 2
    return int(v) % 50 == 0


def js_strings(js: str) -> list:
    """(start, end) of the text of every string in a script: '...', "..." and each literal part of a `...${x}...`
    template. Comments, regular expressions and the code inside ${...} are skipped, so a quote in one string never
    pairs with a quote in the next and the code between them is never read as text."""
    out, n = [], len(js)

    def code(i, in_expr):
        depth, prev = 0, "("
        while i < n:
            ch = js[i]
            if ch in "'\"":
                j = i + 1
                while j < n and js[j] != ch and js[j] != "\n":
                    j += 2 if js[j] == "\\" else 1
                out.append((i + 1, min(j, n)))
                i, prev = j + 1, "a"
                continue
            if ch == "`":
                i, prev = template(i + 1), "a"
                continue
            if ch == "/" and js.startswith("//", i):
                j = js.find("\n", i)
                i = n if j < 0 else j
                continue
            if ch == "/" and js.startswith("/*", i):
                j = js.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if ch == "/" and prev in "(,=:[!&|?{};+-*%<>~^":       # a regular expression, not a division
                j, cls = i + 1, False
                while j < n and js[j] != "\n":
                    if js[j] == "\\":
                        j += 2
                        continue
                    if js[j] == "/" and not cls:
                        break
                    cls = True if js[j] == "[" else False if js[j] == "]" else cls
                    j += 1
                i, prev = j + 1, "a"
                continue
            if in_expr and ch == "{":
                depth += 1
            elif in_expr and ch == "}":
                if not depth:
                    return i + 1
                depth -= 1
            if not ch.isspace():
                prev = "a" if ch.isalnum() or ch in "_$" else ch
            i += 1
        return i

    def template(i):
        start = i
        while i < n:
            if js[i] == "\\":
                i += 2
                continue
            if js[i] == "`":
                out.append((start, i))
                return i + 1
            if js.startswith("${", i):
                out.append((start, i))
                i = start = code(i + 2, True)
                continue
            i += 1
        out.append((start, n))
        return n

    code(0, False)
    return [(a, b) for a, b in out if b > a]


def script_kind(js: str) -> str:
    """JSON, "thư viện" (a bundled library), "dữ liệu" (a big block of data, almost no code) or "mã" (report code)."""
    head = js[:js.find(">") + 1]
    if re.search(r"""type\s*=\s*["']?application/(?:ld\+)?json""", head, re.I):
        return "JSON"
    code = js.count("function") + js.count("=>")
    if LIBRARY.search(js[:400]) or (code > 200 and LIBRARY.search(js[:20000])):   # a banner after a small shim
        return "thư viện"
    if len(js) > 20000 and code <= 20:
        return "dữ liệu"
    return "mã"


class Masker:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.map: dict = {}
        self.count = 0
        self.vn_marks = False                                     # set by mask(): decimals written 12,5

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
            keep = KEEP_BEFORE.search(before)
            if keep and re.fullmatch(r"\d{1,2}" if keep.group("word") else r"\d{1,4}", s):
                return s                                          # a period: tháng 10, T10, Q3, FY2627; not "kỳ 672,7"
            if (len(plain) <= 2 or YEAR.fullmatch(plain)) and \
                    (re.match(r"\s*[/\-]\s*\d", t[m.end():m.end() + 3]) or re.search(r"\d\s*/\s*$", before)):
                return s                                          # a date: 20/11, 11/2026; never 672,7/700,0
            if self.vn_marks and DOT_DATE.fullmatch(s) and not re.match(r"\s*(?:%|tỷ|tr|triệu|nghìn|k\b|[MBK]\b|lần|x\b)",
                                                                       t[m.end():m.end() + 8], re.I):
                return s                                          # 29.10 in a report with 12,5 decimals: a date
            if len(s) <= 2 and re.match(r"\.\s", t[m.end():m.end() + 2]) and not t[:m.start()].strip():
                return s                                          # a section number: "2. Chi tiết"
            return self.figure(s)
        return NUM.sub(sub, t)

    def shape(self, value: str) -> str:
        """Chart geometry (bar heights, line points) moved by up to 15%: the chart keeps its look, not its ratios."""
        def sub(m):
            v = m.group(0)
            dec = len(v.split(".")[1]) if "." in v else 0
            if abs(float(v)) < 100 and not dec:
                return v                                          # paddings, offsets, arc flags
            new = f"{float(v) * self.rng.uniform(0.85, 1.15):.{dec}f}"
            if new == v:
                new = f"{float(v) + 10 ** -dec:.{dec}f}"
            self.count += 1
            return new
        return re.sub(r"-?\d+(?:\.\d+)?", sub, value)

    def style(self, css: str) -> str:
        """Bar widths and heights written as width:87.65%: the decimals carry a ratio, so they move like geometry."""
        return re.sub(r"(?<![\w#.-])\d+\.\d+(?=\s*%)",
                      lambda m: self.shape(m.group(0)), css)

    def script(self, js: str) -> str:
        """Every figure of a script is found on the original text by each rule below, then masked once."""
        spans: dict = {}

        def add(a, b, how=None):
            spans.setdefault(a, (b, how or self.figure))

        kind = script_kind(js)
        if kind == "thư viện":                                # code only: never touched, or the charts break
            return js
        for m in NUM_ARRAY.finditer(js):                      # chart data as arrays: [148, 162, 186.4]
            for n in re.finditer(r"\d+(?:\.\d+)?", m.group(0)):
                add(m.start() + n.start(), m.start() + n.end())
        head = js[:js.find(">") + 1]
        if kind == "JSON":
            for n in re.finditer(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", js[len(head):]):   # data only: every number
                add(len(head) + n.start(), len(head) + n.end())
        elif kind == "dữ liệu":
            # a data block (window.DATA = {...} of CSV text, almost no code): every figure, years and ISO parts kept
            for n in re.finditer(r"(?<![^\W\d_])(?<![\d.#])\d+(?:\.\d+)?(?!\d)", js):   # 120tr, 500k too
                v = n.group(0)
                if not (YEAR.fullmatch(v) or re.fullmatch(r"\d\d", v) and
                        re.search(r"(19|20)\d\d-(\d\d-)?$", js[max(0, n.start() - 8):n.start()])):
                    add(n.start(), n.end())
        elif kind == "mã":                                     # a bundled chart library is code, no report data
            # data kept as text, CSV or JSON in a long string (window.DATA = {"t": "cột,cột\n...,12.5,..."})
            strings = js_strings(js)
            for a, b in strings:
                if b - a < 200:
                    continue
                t = js[a:b]
                for n in DATA_NUM.finditer(t):
                    s = n.group(0)
                    if not (YEAR.fullmatch(s) or re.fullmatch(r"\d\d", s) and
                            re.search(r"(19|20)\d\d-(\d\d-)?$", t[max(0, n.start() - 8):n.start()])):
                        add(a + n.start(), a + n.end())   # years and ISO date parts stay
            for m in KEY_NUMBER.finditer(js):                 # data as objects: {value: 186.4}, "doanh_thu": 412580
                if m.group(1)[0] in "\"'" or "." in m.group(2):   # {t: 20, l: 120} is a margin: kept
                    add(m.start(2), m.end(2))
            # figures typed into chart code (a target line at 672.7, a total of 41258): decimals and 4+ digits.
            # Small whole numbers are sizes, margins and indexes: kept, so the charts keep their layout.
            # words shown on screen ('Tỷ lệ 12,5', 'Ngưỡng 150'): masked like the page's text, periods kept
            for a, b in strings:
                t = js[a:b]
                if 3 <= b - a < 200 and SHOWN_TEXT.search(t) and not CODE_TEXT.match(t):
                    add(a, b, self.text)
            for m in CODE_FIGURE.finditer(js):
                if not (YEAR.fullmatch(m.group(0)) or code_constant(m.group(0))):
                    add(m.start(), m.end())
        out, pos = [], 0
        for a in sorted(spans):
            if a < pos:
                continue
            b, how = spans[a]
            out += [js[pos:a], how(js[a:b])]
            pos = b
        return "".join(out) + js[pos:]


def mask(html: str, swaps: dict, seed=None) -> tuple:
    mk = Masker(seed)
    words = " ".join(x for x in SEGMENT.split(html) if x and not x.startswith("<"))
    toks = [m.group(1) for m in NUM.finditer(words)]
    vn = sum(1 for x in toks if re.search(r",\d{1,2}$|\.\d{3}(?:,|$)", x))
    en = sum(1 for x in toks if re.search(r"\.\d{1,2}$|,\d{3}(?:\.|$)", x) and not DOT_DATE.fullmatch(x))
    mk.vn_marks = vn > en
    parts, untouched = [], 0
    for seg in SEGMENT.split(html):
        if not seg:
            continue
        low = seg[:8].lower()
        if low.startswith("<script"):
            new = mk.script(seg)
            if new == seg and re.search(r"\d{2,}", re.sub(r"<[^>]+>", "", seg)) and script_kind(seg) != "thư viện":
                untouched += 1
            parts.append(new)
        elif low.startswith("<style") or seg.startswith("<!--"):
            parts.append(seg)
        elif seg.startswith("<"):
            seg = TEXT_ATTRS.sub(lambda m: m.group(1) + m.group(2) + mk.text(m.group(3)) + m.group(2), seg)
            seg = STYLE.sub(lambda m: m.group(1) + m.group(2) + mk.style(m.group(3)) + m.group(2), seg)
            if SVG_SHAPE.match(seg):
                seg = GEOMETRY.sub(lambda m: m.group(1) + m.group(2) + mk.shape(m.group(3)) + m.group(2), seg)
            parts.append(seg)
        else:
            parts.append(mk.text(seg))
    out = "".join(parts)
    for real, fake in sorted(swaps.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(re.escape(real), fake, out, flags=re.I)
    out = re.sub(r"(<meta[^>]+charset=[\"']?)[\w-]+", r"\1utf-8", out, count=1, flags=re.I)
    return out, mk.count, untouched


AUDIT_NUM = re.compile(r"(?<![A-Za-zÀ-ỹ_\d.,])\d(?:[\d.,]*\d)?")


def _shape(t: str, a: int, b: int) -> str:
    """Where a figure sits, with letters as a and digits as 9: safe to print, tells a label from a figure."""
    s = t[max(0, a - 30):a] + "[" + t[a:b] + "]" + t[b:b + 10]
    return "".join("a" if c.isalpha() else "9" if c.isdigit() else " " if c.isspace() else c for c in s)


def audit(real: str, masked: str):
    """Figures of 3+ digits (not a year) still the same at the same place, as (place, shape). Bundled libraries are
    left out. Masking keeps every figure's shape, so the pieces of the page and their figures line up one to one.
    Returns None when they do not (a name swap added or removed a tag)."""
    a, b = SEGMENT.split(real), SEGMENT.split(masked)
    if len(a) != len(b):
        return None
    found, k = [], 0
    for x, y in zip(a, b):
        low = x[:8].lower()
        if low.startswith("<script"):
            k += 1
            if script_kind(x) == "thư viện":
                continue
            place = f"script {k} ({script_kind(x)})"
        elif low.startswith("<style") or x.startswith("<!--") or not x:
            continue
        elif x.startswith("<"):
            place = "thẻ <" + (re.match(r"<\s*/?\s*([\w-]*)", x).group(1) or "?").lower()
        else:
            place = "chữ"
        ta, tb = list(AUDIT_NUM.finditer(x)), list(AUDIT_NUM.finditer(y))
        if len(ta) != len(tb):
            found += [(place, "(số lượng số khác nhau, so như tập hợp)")] * sum(
                1 for m in tb if len(re.sub(r"\D", "", m.group())) >= 5 and m.group() in {n.group() for n in ta})
            continue
        for m, n in zip(ta, tb):
            v = m.group()
            if v == n.group() and len(re.sub(r"\D", "", v).lstrip("0")) >= 3 and not YEAR.fullmatch(v) and not (
                    "dữ liệu" in place and YEAR.fullmatch(v.split(",")[0])) and not (
                    place.endswith("(mã)") and re.fullmatch(r"\d+(?:\.\d+)?", v) and code_constant(v)) and not (
                    re.fullmatch(r"[1-9]\d?00", v) and place != "chữ" and "dữ liệu" not in place):
                where = place
                if place.startswith("thẻ"):
                    attr = re.findall(r"([\w:-]+)\s*=\s*[\"']?[^\"'=]*$", x[:m.start()])
                    if attr and attr[-1].lower() == "viewbox":
                        continue                                  # a chart's frame size, kept on purpose
                    where += (" " + attr[-1].lower() if attr else "") + ">"
                found.append((where, _shape(x, m.start(), m.end())))
    return found


def leftovers(real: str, masked: str) -> int:
    """How many figures of 3+ digits (not a year) are still the same, at the same place, in the masked copy, outside
    bundled libraries. 0 to a few (made-up digits can match by chance) is clean."""
    found = audit(real, masked)
    if found is not None:
        return len(found)
    def toks(t):                               # pieces do not line up: compare long figures as sets instead
        return AUDIT_NUM.findall(" ".join(seg for seg in SEGMENT.split(t) if seg and not (
            seg[:8].lower().startswith("<script") and script_kind(seg) == "thư viện")))
    have = {x for x in toks(real) if len(re.sub(r"\D", "", x)) >= 5}
    return sum(1 for x in toks(masked) if x in have)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tạo bản che số của báo cáo HTML để thử Clicky mà số thật không rời máy.")
    ap.add_argument("bao_cao")
    ap.add_argument("--doi", action="append", default=[], help='đổi tên: "Tên thật=Tên thay"')
    ap.add_argument("--tu-file", help="file mỗi dòng: Tên thật = Tên thay")
    ap.add_argument("--soi", type=int, default=0, metavar="N",
                    help="in dạng của tối đa N số còn trùng ở mỗi chỗ (chữ thành a, số thành 9, không lộ số)")
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
        if line.strip() and not line.lstrip().startswith("#"):
            k, v = (x.strip() for x in line.split("=", 1)) if "=" in line else (line.strip(), "")
            if k:
                swaps[k] = v or f"Tên {chr(65 + len(swaps) % 26)}"
    real = read_html(src)
    hits = {k: len(re.findall(re.escape(k), real, flags=re.I)) for k in swaps}
    out, n, untouched = mask(real, swaps)
    found = audit(real, out)
    left = leftovers(real, out) if found is None else len(found)
    dst = src.with_name(src.stem + "_CHE.html")
    dst.write_text(out, encoding="utf-8")
    print(f"Đã tạo bản che số: {dst}")
    print(f"  Đổi {n} con số sang số giả cùng dạng (giữ năm, tháng, quý, ngày, số mục). Đổi {len(swaps)} tên.")
    if swaps:
        print(f"  Tên đã đổi: {sum(hits.values())} chỗ."
              + (f" {sum(1 for v in hits.values() if not v)} tên không thấy trong báo cáo (tên thứ "
                 + ", ".join(str(i + 1) for i, v in enumerate(hits.values()) if not v) + ")." if 0 in hits.values() else ""))
    else:
        print("  Chưa đổi tên nào: tên công ty, người, khách hàng vẫn giữ nguyên. Muốn che thì ghi vào TEN_CAN_CHE.txt.")
    print(f"  Kiểm lại: còn {left} số (từ 3 chữ số, không kể năm) giống hệt bản thật ở cùng chỗ (0 đến vài số là sạch, do trùng ngẫu nhiên).")
    if found:
        tally: dict = {}
        for where, _ in found:
            tally[where] = tally.get(where, 0) + 1
        print("  Số còn trùng nằm ở: " + "; ".join(f"{w}: {n}" for w, n in sorted(tally.items(), key=lambda kv: -kv[1])))
        if a.soi:
            k = 0
            for seg in SEGMENT.split(real):
                if seg[:8].lower().startswith("<script"):
                    k += 1
                    print(f"    script {k}: {script_kind(seg)}, {len(seg) // 1024} KB, "
                          f"{seg.count('function') + seg.count('=>')} hàm, {len(AUDIT_NUM.findall(seg))} số")
            shown: dict = {}
            for where, shape in found:
                shown[where] = shown.get(where, 0) + 1
                if shown[where] <= a.soi:
                    print(f"    {where}  {shape}")
    if untouched:
        print(f"  Lưu ý: {untouched} đoạn script có số mà em không chắc là dữ liệu, nên giữ nguyên. Anh mở bản che xem lướt.")
    print("  Trước khi gửi: mở bản _CHE.html xem lướt. Còn số thật hay tên nhạy cảm thì thêm vào TEN_CAN_CHE.txt rồi chạy lại.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
