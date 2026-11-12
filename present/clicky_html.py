"""Put Clicky inside an HTML report the owner already shares (SharePoint/OneDrive link by e-mail).

Run on the owner's PC. Nothing is sent anywhere and no AI is used:
1. The report is read with the standard library parser, in its own encoding (UTF-8, or the Windows-1258 an Excel
   "Save as Web Page" writes). Sections (h2, else h3), the author's own notes (callouts, first paragraphs, bullet
   points), number cards (KPI tiles), tables and charts are found. The number marks (412,580.5 or 412.580,5) are
   read from the tables; a report that mixes both stops the build.
2. A talk is drafted the way a finance person presents to the Board: greeting, key message, the two or three
   largest variances from the table, what it means, one request to the Board, thanks. Every sentence is made of
   text and figures already on the page; nothing is computed or invented. The draft is written next to the report
   as YAML (<name>_kich_ban_Clicky.yaml); the owner edits it and drops the report again. An edited script is kept
   and a fresh draft goes to <name>_kich_ban_nhap.yaml. Each place Clicky frames is stored with its text ("neo"),
   so a report with an added card is re-anchored by text, not by position.
3. A new file "<name>_Clicky.html" is written: the original page, each target tagged with data-clicky="…",
   plus Clicky's layer (bar, frames, nón lá pointer, captions, "Hỏi Clicky"). The original file is never touched.
4. The voice is the offline Vietnamese voice; clips are embedded, so the page plays offline.
5. Number lock: a sentence may only say a figure that is at the place it frames, with the same direction
   (tăng/giảm/âm) and unit. A figure found only elsewhere on the page must be confirmed ("xac_nhan"); a figure the
   owner typed himself must be listed in "so_rieng" and is shown as his. Anything else stops the build.
6. A machine-readable record ("biên bản") is embedded: every spoken figure, where it is in the report, the
   report's SHA-256 and a seal over the record. present/kiem_bien_ban.py re-checks it from the output file.

On the page, a reader can also ask about any table cell ("chi phí lương khối bán hàng AOPR?"), or simply click a
figure: Clicky finds the row and the column by their labels, frames the real cell and reads it out. Questions it
cannot place are kept and e-mailed to the owner only when the reader presses Send.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import functools
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

import yaml

from present import doc_tai_chinh, goi_html, tri_nho, vn_format
from present.goi_html import PackError, audio_mime, offline_voice

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
        "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template"}
NOTE_CLASS = re.compile(r"insight|callout|note|highlight|conclusion|ket-?luan|nhan-?xet|takeaway|alert|key-?point|"
                        r"summary|message|thong-?diep", re.I)
CARD_CLASS = re.compile(r"kpi|metric|stat|tile|card|figure-box|so-lieu", re.I)
NOTE_START = re.compile(r"^\s*(kết luận|nhận xét|đề xuất|thông điệp|lưu ý|insight|điểm chính|tóm tắt|key)", re.I)
HAS_NUMBER = re.compile(r"\d")
CHART_CLASS = re.compile(r"chart|plot|graph|bieu-?do|echarts|apex|highcharts|plotly", re.I)
# a figure: not glued to a letter (FY2627, T10, Q1 are labels) or to another figure
NUM_TOKEN = re.compile(r"(?<![A-Za-zÀ-ỹ\d.,])\d[\d.,]*")
LABEL_ATTRS = ("aria-label", "alt", "title")
RECORD_VERSION = 2


@dataclass
class Node:
    tag: str
    attrs: dict
    start: int                   # offset of '<' in the source
    parent: Optional["Node"] = None
    children: list = field(default_factory=list)
    texts: list = field(default_factory=list)      # direct text pieces, in order with children (None = child)

    def text(self) -> str:
        out, it = [], iter(self.children)
        for piece in self.texts:
            if piece is None:
                out.append(" " + next(it).text() + " ")
            else:
                out.append(piece)
        return re.sub(r"\s+", " ", "".join(out)).strip()

    @property
    def cls(self) -> str:
        return (self.attrs.get("class") or "") + " " + (self.attrs.get("id") or "")

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def has_ancestor(self, pred) -> bool:
        p = self.parent
        while p is not None:
            if pred(p):
                return True
            p = p.parent
        return False


class _Builder(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, 0)
        self.stack = [self.root]
        self._lines = [0]
        for m in re.finditer("\n", source):
            self._lines.append(m.end())

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self._offset(), self.stack[-1])
        parent = self.stack[-1]
        parent.children.append(node)
        parent.texts.append(None)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self._offset(), self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack[-1].texts.append(None)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if any(n.tag in SKIP_TEXT for n in self.stack):
            return
        self.stack[-1].texts.append(data)


def parse(source: str) -> Node:
    b = _Builder(source)
    b.feed(source)
    b.close()
    return b.root


# --- reading the file ------------------------------------------------------------------------------------

META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.I)


def decode_report(raw: bytes) -> tuple:
    """(text, encoding). BOM first, then the page's own <meta charset>, then UTF-8, then Windows-1258 (what Excel
    writes for a Vietnamese workbook saved as a web page). Never decodes with replacement characters silently."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8"
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16"
    m = META_CHARSET.search(raw[:8192])
    declared = m.group(1).decode("ascii", "ignore").lower() if m else ""
    if declared and declared not in ("utf-8", "utf8"):
        try:
            return unicodedata.normalize("NFC", raw.decode(declared)), declared
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    text = raw.decode("utf-8", errors="replace")       # one stray byte in a UTF-8 page is not a whole other page
    bad = text.count("\ufffd")
    good = sum(1 for ch in text if ord(ch) > 127) - bad
    if bad <= 3 and good >= 10 * bad:
        return text, "utf-8"
    return unicodedata.normalize("NFC", raw.decode("cp1258", errors="replace")), "cp1258"


def utf8_meta(source: str) -> str:
    """The output is written in UTF-8, so the page must say so (an Excel page says windows-1258)."""
    out, n = re.subn(r"""(<meta[^>]+charset\s*=\s*["']?\s*)[\w-]+""", r"\1utf-8", source, count=1, flags=re.I)
    if n:
        return out
    m = re.search(r"<head[^>]*>", source, re.I)
    at = m.end() if m else 0
    return source[:at] + '<meta charset="utf-8">' + source[at:]


# --- finding what to talk about ------------------------------------------------------------------------

def _clean(text: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end > 80:
        return cut[:end + 1].strip()
    if " " in cut:                       # never stop in the middle of a word or a number
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "…"


CONTAINER_CLASS = re.compile(r"grid|row|list|container|wrapper|group|deck|cards\b", re.I)


def _card_like(n: Node) -> bool:
    if n.tag not in ("div", "section", "article", "li", "td"):
        return False
    # a grid of cards: two or more children that are themselves label + value blocks
    blocks = sum(1 for c in n.children if len(c.children) >= 2 and HAS_NUMBER.search(c.text()))
    if CONTAINER_CLASS.search(n.cls) or blocks >= 2:
        return False
    t = n.text()
    return 3 <= len(t) <= 120 and bool(HAS_NUMBER.search(t)) and bool(CARD_CLASS.search(n.cls))


def _is_card(n: Node) -> bool:
    """The outermost number card: a value <div> inside a card is part of that card; a grid of cards is not one."""
    return _card_like(n) and not n.has_ancestor(_card_like)


def _card_parts(n: Node) -> list:
    return [c.text() for c in n.children if c.text()]


def card_text(n: Node) -> str:
    """'Tổng chi phí: 186,4 tỷ, tăng 12,6% so với AOP gốc.' from label / value / delta children."""
    parts = _card_parts(n)
    if len(parts) < 2:
        return n.text()
    rest = [re.sub(r"^\+\s*", "tăng ", re.sub(r"^[-−]\s*(?=\d)", "giảm ", p)) for p in parts[1:]]
    return parts[0].rstrip(":") + ": " + ", ".join(rest)


def card_label(n: Node) -> str:
    parts = _card_parts(n)
    if len(parts) >= 2:
        return parts[0].rstrip(": ")
    m = re.match(r"\D+", n.text())
    return (m.group(0) if m else n.text()).strip(" :")


MONEY_LABEL = re.compile(r"chi phí|doanh thu|lợi nhuận|ngân sách|tiền|giá|trần|quỹ|lương|thưởng|aop|kế hoạch|"
                         r"\bcost|revenue|profit|budget|cần (tăng|giảm)|tiết kiệm", re.I)
NOT_MONEY_LABEL = re.compile(r"biên|tỷ lệ|tỉ lệ|margin|sản lượng|headcount|số người|nhân viên|khách|đơn hàng|"
                             r"\bfte\b|ngày|vòng quay", re.I)
CHANGE_LABEL = re.compile(r"^\s*((?:cần|phải|nên)\s+)?(tăng|giảm|tiết kiệm|cắt giảm|cắt)\s+(?:so với|vs\.?)\s+(.+)$",
                          re.I)


def card_line(n: Node, unit: Optional[str] = None, said: Optional[set] = None, en: bool = False) -> str:
    """How a person says a card: 'Tổng chi phí nhân sự AOPR là 186,4 tỷ, tăng 12,6% so với AOP gốc.'
    The value keeps its own sign ("−5,4 tỷ" is "âm 5,4 tỷ"); a change after it says tăng or giảm. A money card
    without a unit gets the report's "Đơn vị:" unit. A change whose figures were already said is left out."""
    parts = _card_parts(n)
    if len(parts) < 2:
        return _sentence(n.text())
    label = parts[0].rstrip(": ")
    money = unit if unit in MONEY and MONEY_LABEL.search(label) and not NOT_MONEY_LABEL.search(label) else None

    def with_unit(p):                  # "+1,2 vs AOP" → "+1,2 tỷ vs AOP"
        f = figures(p)
        if not (money and len(f) == 1 and not f[0].unit):
            return p
        end = f[0].at + len(f[0].tok)
        end += len(re.match(r"\)?", p[end:]).group(0))
        return p[:end] + " " + money + p[end:]

    value_part = parts[1].strip()
    value_part = re.sub(r"^\+\s*(?=\d)", "", value_part)
    value_part = re.sub(r"^[-−–]\s*(?=\d)", "âm ", value_part)
    value_part = re.sub(r"^\((\d[\d.,]*%?)\)", r"âm \1", value_part)       # (9,870): accounting negative
    value_part = with_unit(value_part)
    rest = []
    for p in parts[2:]:
        if said is not None and figures(p) and {value(f.tok, en) for f in figures(p)} <= said:
            continue                                                  # "12,6%" was just said in the key message
        p = re.sub(r"^\+\s*(?=\d)", "tăng ", p.strip())
        p = re.sub(r"^[-−–]\s*(?=\d)", "giảm ", p)
        p = re.sub(r"^\((\d[\d.,]*%?)\)", r"giảm \1", p)
        rest.append(re.sub(r"\bvs\.?(?=\s|$)", "so với", with_unit(p)))
    m = CHANGE_LABEL.match(label)
    if m:                                         # "Cần giảm so với AOPR 8,9 tỷ" → "So với AOPR, cần giảm 8,9 tỷ."
        verb = ((m.group(1) or "") + m.group(2)).strip()
        value_part = re.sub(r"^âm\s+", "", value_part)
        return _sentence(f"So với {m.group(3).strip()}, {verb[:1].lower() + verb[1:]} " + ", ".join([value_part] + rest))
    return _sentence(label + " là " + ", ".join([value_part] + rest))


def _is_note(n: Node) -> bool:
    if n.tag in ("blockquote", "aside"):
        return True
    if n.tag in ("p", "div", "li") and NOTE_CLASS.search(n.cls) and 20 <= len(n.text()) <= 1200:
        return not n.has_ancestor(lambda p: NOTE_CLASS.search(p.cls) and p.tag in ("p", "div", "li"))
    return n.tag == "p" and bool(NOTE_START.search(n.text()))


PAGE_HEAD = re.compile(r"\b(header|banner|hero|masthead|top-?bar|subtitle)\b", re.I)


def _in_table_or_list(n: Node) -> bool:
    """Inside a table, a list, a menu or the page banner (a subtitle line is not commentary)."""
    return n.has_ancestor(lambda p: p.tag in ("td", "th", "table", "li", "nav", "header", "footer")
                          or bool(PAGE_HEAD.search(p.cls)))


def _is_paragraph(n: Node) -> bool:
    """Commentary written as plain paragraphs: a <p> of real length that is not inside a table or a list."""
    return n.tag == "p" and 20 <= len(n.text()) <= 1200 and not _in_table_or_list(n)


def _is_bullet(n: Node) -> bool:
    """A 'Key messages' bullet: an <li> of a list that is not a menu and not nested in another bullet."""
    return (n.tag == "li" and 10 <= len(n.text()) <= 600 and n.parent is not None and n.parent.tag in ("ul", "ol")
            and not n.has_ancestor(lambda p: p.tag in ("li", "nav", "table", "header", "footer")))


def _is_chart(n: Node) -> bool:
    if n.tag in ("canvas", "svg"):
        return not n.has_ancestor(lambda p: p.tag == "svg")
    if n.tag == "img":
        return True
    return n.tag == "div" and bool(CHART_CLASS.search(n.cls)) and not n.has_ancestor(
        lambda p: CHART_CLASS.search(p.cls) and p.tag == "div")


@dataclass
class Item:
    kind: str            # title | section | note | card | table | chart
    node: Node
    text: str
    section: Optional[int] = None      # index (in items) of the section heading above it; None before the first


@dataclass
class Row:
    id: str
    node: Node
    table: Item
    cells: list           # cell texts, in order
    label: str            # first cell
    header: bool


def outline(root: Node) -> list:
    """Items in reading order. A heading level splits sections: h2 if the report has any, else h3. Inside each
    section the first paragraphs and the bullet points are the author's commentary too."""
    nodes = list(root.walk())
    level = "h2" if any(n.tag == "h2" for n in nodes) else "h3"
    items: list = []
    title = next((n for n in nodes if n.tag == "h1"), None)
    if title is not None:
        items.append(Item("title", title, _clean(title.text(), 160)))
    seen_inside: set = set()
    section, paras, bullets = None, 0, 0
    for n in nodes:
        if id(n) in seen_inside or n is title:
            continue
        kind = None
        if n.tag == level:
            kind = "section"
        elif n.tag == "table":
            kind = "table"
        elif _is_card(n):
            kind = "card"
        elif _is_note(n):
            kind = "note"
        elif _is_chart(n):
            kind = "chart"
        elif _is_paragraph(n) and paras < 3 and (paras == 0 or HAS_NUMBER.search(n.text())):
            kind, paras = "note", paras + 1
        elif _is_bullet(n) and bullets < 6:
            kind, bullets = "note", bullets + 1
        if kind is None:
            continue
        text = card_text(n) if kind == "card" else n.text()
        if kind == "chart":
            text = next((d.attrs.get("aria-label") or d.attrs.get("alt") or d.attrs.get("title")
                         for d in n.walk() if d.attrs.get("aria-label") or d.attrs.get("alt") or d.attrs.get("title")),
                        "")
        if kind == "section":
            section, paras, bullets = len(items), 0, 0
        items.append(Item(kind, n, _clean(text, 160 if kind in ("section", "card") else 400),
                          None if kind == "section" else section))
        for d in n.walk():
            seen_inside.add(id(d))
    return items


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…:" else text + "."


# --- figures: how a number is written, its direction and its unit -----------------------------------------

UNITS = {"%": "%", "nghìn tỷ": "nghìn tỷ", "ngàn tỷ": "nghìn tỷ", "tỷ": "tỷ", "tỉ": "tỷ", "triệu": "triệu",
         "tr": "triệu", "nghìn": "nghìn", "ngàn": "nghìn", "đồng": "đồng", "người": "người", "điểm": "điểm",
         "lần": "lần"}
MONEY = {"nghìn tỷ", "tỷ", "triệu", "nghìn", "đồng"}
UNIT_AFTER = re.compile(r"\s*(%|nghìn tỷ|ngàn tỷ|tỷ|tỉ|triệu|tr\b\.?|nghìn|ngàn|đồng|người|điểm|lần)", re.I)
NEG_WORD = re.compile(r"(?<!\w)(giảm|thấp hơn|âm|hụt|lỗ|kém|ít hơn|ít đi|sụt|tiết kiệm|đi xuống|thu hẹp|thấp đi)(?!\w)",
                      re.I)
POS_WORD = re.compile(r"(?<!\w)(tăng|cao hơn|vượt|nhiều hơn|nhiều lên|đi lên|mở rộng)(?!\w)", re.I)
CLAUSE = re.compile(r"[,;:.!?](?=\s)|[()]|(?<!\w)(?:nhưng|trong khi|còn|song|tuy nhiên|mặc dù|riêng)(?!\w)", re.I)
SUBJECTS = [(re.compile(p, re.I), k) for p, k in (
    (r"tỷ lệ|tỉ lệ|\bratio\b", "tỷ lệ"), (r"doanh thu|revenue|\bsales\b|\bdtt\b", "doanh thu"),
    (r"giá vốn|\bcogs\b|gvhb", "giá vốn"), (r"lợi nhuận|\bebitda\b|\bebit\b|lntt|lnst|profit|net income", "lợi nhuận"),
    (r"headcount|số người|nhân viên|\bfte\b", "số người"), (r"chi phí|\bcost|opex|expense", "chi phí"))]
DECLARED = re.compile(r"đơn vị(?: tính)?\s*:?\s*(nghìn tỷ|ngàn tỷ|tỷ|tỉ|triệu|tr\b|nghìn|ngàn)", re.I)


@dataclass
class Fig:
    tok: str               # as written, without sign: "22,630"
    sign: Optional[str]    # "+", "-" or None
    sym: bool              # the sign is a symbol (+, −, parentheses), not a word
    unit: Optional[str]
    ctx: str = ""          # the words before it in its clause ("Khối Bán hàng chênh lệch ")
    how: str = ""          # "sym" (+ − parentheses), "am" (the word âm: the cell's own sign), "word" (tăng, giảm…)
    subj: str = ""         # what the clause is about: "doanh thu", "chi phí"… (first one named)
    at: int = -1           # where the figure starts in the text
    spaced: bool = False   # written with spaces between thousands ("1 234 567"): says nothing about the marks


PAREN_NEG = re.compile(r"(?:^|[:=]|(?<!\w)(?:là|was|is|were|are|đạt|ở mức|at))\s*$", re.I)
PAREN_SHARE = re.compile(r"(?:\d[\d.,]*\s*(?:%|nghìn tỷ|ngàn tỷ|tỷ|tỉ|triệu|tr\b\.?|nghìn|ngàn|đồng|người|điểm|lần)?|"
                         r"(?<!\w)(?:chiếm|tỷ trọng|tỉ trọng|bằng|tương đương|khoảng|gồm|trong đó|tức)(?:\s+\S+)?)\s*$",
                         re.I)


def paren_kind(text: str, s: int) -> str:
    """What "(x)" means, from the words before the bracket. "neg": an accounting negative, when the bracket stands
    alone (a cell, a card value) or right after ":", "=" or "là". "share": a figure set beside another figure
    ("186,4 tỷ (12,6%)") or after "chiếm". "?": it could be either; the owner says which in words."""
    start = max(0, s - 80)
    pre = text[start:s].rstrip()
    pre = pre[:-1].rstrip() if pre.endswith("(") else pre
    if start and len(pre) == 0:
        pre = "x"
    if PAREN_NEG.search(pre):
        return "neg"
    if PAREN_SHARE.search(pre) or NEG_WORD.search(CLAUSE.split(pre)[-1]) or POS_WORD.search(CLAUSE.split(pre)[-1]):
        return "share"
    return "?"


def subject(ctx: str) -> str:
    hits = [(m.start(), k) for rx, k in SUBJECTS for m in [rx.search(ctx or "")] if m]
    return min(hits)[1] if hits else ""


def figures(text: str) -> list:
    """Figures as written, each with its direction and unit. Years (2026), periods glued to a letter (FY2627, T10,
    Q1), dates (11/11) and list markers "(1)" are not figures."""
    return list(_figures(text or ""))


_YEAR = re.compile(r"(19|20)\d\d")
_SLASH_AFTER = re.compile(r"/\d")
_SLASH_BEFORE = re.compile(r"\d/$")
_OPEN = re.compile(r"\(\s*$")
_CLOSE = re.compile(r"\s*%?\s*\)")
_MINUS = re.compile(r"(?:^|[^\w%)])[−–-]\s?$")
_PLUS = re.compile(r"(?:^|[^\w])\+\s?$")
_TUC = re.compile(r"\s*tức\b", re.I)


@functools.lru_cache(maxsize=8192)
def _figures(raw: str) -> tuple:
    text = vn_format.join_groups(raw)       # "1 234 567" is one figure, as the voice reads it (same length)
    out, prev = [], 0
    for m in NUM_TOKEN.finditer(text):
        tok = m.group(0).rstrip(".,")
        if not tok:
            continue
        s, e = m.start(), m.start() + len(tok)
        before, after = text[max(prev, s - 400):s], text[e:e + 40]
        prev = e
        if _YEAR.fullmatch(tok) or _SLASH_AFTER.match(after) or _SLASH_BEFORE.search(text[max(0, s - 2):s]):
            continue
        if _OPEN.search(before) and after.startswith(")") and len(tok) <= 2 and tok.isdigit():
            continue                                            # "(1) tăng ngân sách": a list marker
        sign, sym, how = None, False, ""
        clause = CLAUSE.split(before)[-1]
        paren = paren_kind(text, s) if _OPEN.search(before) and _CLOSE.match(after) else ""
        if paren == "neg":
            sign, sym, how = "-", True, "sym"
        elif paren == "?":
            how = "paren?"
        elif _MINUS.search(text[max(0, s - 3):s]):
            sign, sym, how = "-", True, "sym"
        elif _PLUS.search(text[max(0, s - 3):s]):
            sign, sym, how = "+", True, "sym"
        else:
            words = [(w.start(), "-", w.group(1).lower()) for w in NEG_WORD.finditer(clause)] + \
                    [(w.start(), "+", w.group(1).lower()) for w in POS_WORD.finditer(clause)]
            if paren:
                pass                                            # "186,4 tỷ (12,6%)": no direction of its own
            elif words:
                _, sign, word = max(words)
                how = "am" if word == "âm" else "word"
            elif out and _TUC.match(clause):     # "giảm 14,1 tỷ, tức 18,0%": same direction
                sign, how = out[-1].sign, out[-1].how
        u = UNIT_AFTER.match(after)
        if paren:
            u = UNIT_AFTER.match(re.sub(r"^\s*%?\s*\)", lambda m: m.group(0).replace(")", ""), after, count=1))
        out.append(Fig(tok, sign, sym, UNITS.get(u.group(1).lower().rstrip(".")) if u else None, clause, how,
                       subject(clause), s, raw[s:e] != tok))
    return tuple(out)


NUM_WORDS = ("không một hai ba bốn năm sáu bảy tám chín mười mươi mốt lăm tư trăm nghìn ngàn triệu tỷ tỉ phẩy linh "
             "lẻ").split()
NUM_WORD_RUN = re.compile(r"(?<!\w)((?:(?:%s)\s+){1,}(?:%s))(?!\w)" % ("|".join(NUM_WORDS), "|".join(NUM_WORDS)),
                          re.I)


def spelled_number(text: str) -> Optional[str]:
    """A figure written in words ("một trăm sáu mươi tám phẩy bốn"): the lock cannot check it."""
    for m in NUM_WORD_RUN.finditer(text or ""):
        words = m.group(1).lower().split()
        if len(words) >= 3 or set(words) & {"trăm", "mươi", "mười", "phẩy", "linh", "lẻ"}:
            return m.group(1)
    return None


def numbers(text: str) -> list:
    """Figures as written (186,4 · 12,6 · 2.300), without sign, a sentence's closing dot, years or dates."""
    return [f.tok for f in figures(text)]


def value(tok: str, en: bool = False) -> Optional[Decimal]:
    if tok.count(".") >= 2 and "," not in tok:          # 1.234.567: thousands in any report
        tok = tok.replace(".", "")
    t = tok.replace(",", "") if en else tok.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _mark_kind(tok: str) -> tuple:
    """('en'|'vn'|None, strong). 412,580.5 and 5.2 are English marks; 412.580,5 and 12,6 are Vietnamese."""
    if "," in tok and "." in tok:
        return ("en" if tok.rfind(".") > tok.rfind(",") else "vn"), True
    for sep, decimal, thousands in ((".", "en", "vn"), (",", "vn", "en")):
        if sep in tok:
            head, *groups = tok.split(sep)
            if len(groups) > 1:
                return thousands, True
            if len(groups[0]) != 3 or head == "0":
                return decimal, True
            return thousands, False                 # 1.248 or 412,580: most likely thousands, but not sure
    return None, False


def number_marks(texts: list) -> bool:
    """True when the report writes English marks (412,580.5). A report that mixes both stops the build: a figure
    read with the wrong marks is 1000 times off."""
    seen = {("en", True): [], ("vn", True): [], ("en", False): [], ("vn", False): []}
    for t in texts:
        for f in figures(t):
            if f.spaced:
                continue
            kind, strong = _mark_kind(f.tok)
            if kind:
                seen[(kind, strong)].append(f.tok)
    en, vn = seen[("en", True)], seen[("vn", True)]
    if not en and not vn:
        en, vn = seen[("en", False)], seen[("vn", False)]
    if en and vn:
        raise PackError(f"Báo cáo viết số theo hai kiểu lẫn nhau, ví dụ «{en[0]}» (kiểu Anh: dấu phẩy ngăn nghìn, dấu chấm thập phân) và "
                        f"«{vn[0]}» (kiểu Việt: dấu chấm ngăn nghìn, dấu phẩy thập phân). Em không chắc số nào là phần nghìn, số nào là "
                        "phần thập phân, nên chưa đọc. Anh sửa báo cáo về một kiểu rồi chạy lại.")
    return bool(en)


def declared_unit(text: str) -> Optional[str]:
    m = DECLARED.search(text or "")
    return UNITS.get(m.group(1).lower()) if m else None


SIGN_WORD = {"-": "giảm/âm", "+": "tăng"}


def check_figure(f: Fig, place: list, en: bool, declared: Optional[str]) -> Optional[str]:
    """Why a spoken figure does not match the place it frames, or None when it does: same value, same direction
    (tăng/giảm/âm) and same unit."""
    v = value(f.tok, en)
    same = [p for p in place if v is not None and value(p.tok, en) == v]
    if not same:
        return "missing"
    if f.subj and all(p.subj and p.subj != f.subj for p in same) and any(p.subj == f.subj for p in place):
        return f"trong báo cáo, số {f.tok} là của «{same[0].subj}», không phải «{f.subj}»"
    signs = {p.sign for p in same}
    if f.sign and None not in signs and f.sign not in signs:
        return f"báo cáo ghi {SIGN_WORD[next(iter(signs))]}, câu nói lại là {SIGN_WORD[f.sign]}"
    if f.sign == "-" and f.how in ("sym", "am") and signs == {None} and all(p.how != "paren?" for p in same):
        return "báo cáo ghi số dương, câu nói lại là số âm"
    if f.sign is None and all(p.sign == "-" and p.sym for p in same):
        return "báo cáo ghi số âm, câu nói thiếu chữ «giảm» hoặc «âm»"
    if f.unit:
        units = {p.unit or (declared if f.unit in MONEY else None) for p in same}
        if None not in units and f.unit not in units:
            return f"báo cáo ghi «{next(iter(units))}», câu nói lại là «{f.unit}»"
    return None


# --- the places Clicky can frame ---------------------------------------------------------------------------

@dataclass
class Col:
    header: str            # every header row's text for this column ("Chênh lệch Tỷ đồng")
    text: str              # the cell
    figs: list
    unit: Optional[str]    # from the cell, the header, the table's money unit, or "Đơn vị:" on the page
    kind: str              # label | value | var | pct | pctvar
    name: str = ""         # the lowest header cell ("AOP" under "Kế hoạch so Thực hiện"): how a person names it


@dataclass
class Place:
    id: str
    kind: str              # title | section | note | card | table | chart | row
    text: str              # what a reader sees there (with chart labels)
    anchor: str            # the text that finds it again in a changed report
    section: Optional[str]
    label: str = ""
    cols: Optional[list] = None    # a table row: its cells by column
    figs: Optional[list] = None    # a card: the figures of each part on its own, so "(5,4) tỷ" alone is negative
    neg_book: bool = False         # the row writes its amounts negative, e.g. costs in brackets in a P&L
    cost: bool = False             # a cost line: more is worse


HEAD_UNIT = re.compile(r"(nghìn tỷ|ngàn tỷ|tỷ|tỉ|triệu|\btr\b|nghìn|ngàn)", re.I)
HEAD_PEOPLE = re.compile(r"headcount|\bhc\b|người|nhân viên|số lượng|\bsl\b|lao động|\bfte\b", re.I)
COST_WORDS = re.compile(r"chi phí|giá vốn|\bcost|cogs|opex|expense|lương|thưởng|phụ cấp|khấu hao|capex|\bcp\b|"
                        r"\bgvhb\b|cpbh|cpql", re.I)
INCOME_WORDS = re.compile(r"doanh thu|revenue|sales|lợi nhuận|ebitda|\bebit\b|biên|margin|thu nhập|income|sản lượng|"
                          r"\bdtt\b|lntt|lnst|tiền", re.I)


def _cell_sign(g: Fig) -> str:
    """A table cell without a sign is a positive figure."""
    return g.sign if g.sym and g.sign else "+"


def column_kinds(trs: list) -> tuple:
    """(header text, kind) per column of a table."""
    hc = header_texts(trs)
    data = [r for r in trs if not r.header]
    kinds = []
    for i, h in enumerate(hc):
        cells = [r.cells[i] for r in data if i < len(r.cells) and r.cells[i].strip()]
        if not cells or not any(FIGURE_CELL.match(x) for x in cells):
            kinds.append("label")
        elif "%" in h or (cells and all("%" in x for x in cells if FIGURE_CELL.match(x))):
            kinds.append("pctvar" if VAR_HEAD.search(h) or re.search(r"tăng|giảm|var", h, re.I) else "pct")
        elif VAR_HEAD.search(h):
            kinds.append("var")
        else:
            kinds.append("value")
    return hc, kinds


def row_places(trs: list, section: Optional[str], page_figs: list, en: bool, declared: Optional[str],
               cost_page: bool) -> dict:
    """Places for the rows of one table, each with its cells by column, the column's unit and kind."""
    if not trs:
        return {}
    hc, kinds = column_kinds(trs)
    money = {}                                  # the table's money unit, from the same figure written elsewhere
    page = {}
    for g in page_figs:
        if g.unit in MONEY:
            page.setdefault(value(g.tok, en), set()).add(g.unit)
    for r in trs:
        for i, x in enumerate(r.cells):
            if i < len(kinds) and kinds[i] in ("value", "var") and not HEAD_PEOPLE.search(hc[i]):
                for g in figures(x):
                    for u in page.get(value(g.tok, en), ()):
                        money[u] = money.get(u, 0) + 1
    # the unit the same figures carry elsewhere on the page: twice, or once with no other unit seen
    table_unit = max(money, key=money.get) if money and (max(money.values()) >= 2 or len(money) == 1) else declared
    units = []
    for i, h in enumerate(hc):
        m = HEAD_UNIT.search(h)
        if kinds[i].startswith("pct"):
            units.append("%")
        elif HEAD_PEOPLE.search(h):
            units.append("người")
        elif m:
            units.append(UNITS.get(m.group(1).lower(), m.group(1).lower()))
        else:
            units.append(table_unit if kinds[i] != "label" else None)
    heads = [r for r in trs if r.header and len({x for x in r.cells if x}) > 1]
    names = [next((h.cells[i] for h in reversed(heads) if i < len(h.cells) and h.cells[i].strip()), "").strip()
             for i in range(len(hc))]
    out = {}
    for r in trs:
        cols = [Col(hc[i] if i < len(hc) else "", x, figures(x), units[i] if i < len(units) else None,
                    kinds[i] if i < len(kinds) else "value", names[i] if i < len(names) else "")
                for i, x in enumerate(r.cells)]
        vals = [g for c in cols if c.kind == "value" for g in c.figs]
        neg_book = bool(vals) and sum(1 for g in vals if _cell_sign(g) == "-") > len(vals) / 2
        cost = bool(COST_WORDS.search(r.label)) or (cost_page and not INCOME_WORDS.search(r.label))
        out[r.id] = Place(r.id, "row", r.node.text(), r.label.strip()[:80], section, r.label.strip(), cols=cols,
                          neg_book=neg_book, cost=cost)
    return out


def _wanted(f: Fig, cols: list) -> tuple:
    """The columns a spoken figure must come from, by the words before it, and how to name them."""
    ctx = tri_nho.fold(f.ctx)
    named, best, diff = [], 0, False
    for c in cols:
        h = tri_nho.fold(c.header)
        m = re.search(rf"(?<![\w%]){re.escape(h)}(?![\w%])", ctx) if c.kind != "label" and len(h) >= 2 else None
        if m is None and c.kind != "label" and len(tri_nho.fold(c.name)) >= 2:
            h = tri_nho.fold(c.name)
            m = re.search(rf"(?<![\w%]){re.escape(h)}(?![\w%])", ctx)
        if m and len(h) >= best:
            named = [x for x in named if len(tri_nho.fold(x.header)) >= len(h)] + [c]
            best = len(h)
            # "vượt AOP gốc 14,1 tỷ": the figure is the difference from that column, not the column
            diff = any(w.start() < m.start() and w.group(1) != "am"
                       for w in re.finditer(r"(?<!\w)(giam|thap hon|cao hon|vuot|hut|kem|it hon|tang|tiet kiem|"
                                            r"nhieu hon|am)(?!\w)", ctx))
    if named and not diff:
        return named, named[0].header
    if f.unit == "%" or re.match(r"\s*tuc\b", ctx):
        pct = [c for c in cols if c.kind.startswith("pct")]
        if pct:
            return pct, " / ".join(dict.fromkeys(c.header for c in pct))
    if f.how in ("word", "am") or re.search(r"chenh lech|\blech\b", ctx):
        var = [c for c in cols if c.kind == "var"] or [c for c in cols if c.kind == "pctvar"]
        if var:
            return var, " / ".join(dict.fromkeys(c.header for c in var))
    return None, ""


def check_row(f: Fig, place: Place, en: bool) -> Optional[str]:
    """A spoken figure against a table row: the right column, the right direction, the right unit."""
    v = value(f.tok, en)
    hits = [(c, g) for c in place.cols if c.kind != "label" for g in c.figs if v is not None and value(g.tok, en) == v]
    if not hits:
        named = any(value(g.tok, en) == v for c in place.cols if c.kind == "label" for g in c.figs)
        return None if named and f.sign is None and not f.unit else "missing"     # "Dòng 1", "Chi nhánh 6"
    want, name = _wanted(f, place.cols)
    if want is not None:
        inside = [(c, g) for c, g in hits if any(c is w for w in want)]
        if not inside:
            return f"số {f.tok} là cột «{hits[0][0].header}», không phải cột «{name}»"
        hits = inside
    why = None
    for c, g in hits:
        raw = _cell_sign(g)
        change = c.kind in ("var", "pctvar")
        size = ("-" if raw == "+" else "+") if place.neg_book else raw     # is the amount bigger or smaller
        if f.sign is None:
            if raw == "-" and not (place.neg_book and not change):
                why = why or f"cột «{c.header}» ghi số âm ({c.text.strip()}), câu nói thiếu chữ «giảm» hoặc «âm»"
                continue
        elif f.how == "word" and change and f.sign != size:
            why = why or (f"cột «{c.header}» ghi {'tăng' if size == '+' else 'giảm'} ({c.text.strip()}), câu nói lại là "
                          f"{'tăng' if f.sign == '+' else 'giảm'}")
            continue
        elif f.how in ("am", "sym") and f.sign != raw:
            why = why or f"cột «{c.header}» ghi {c.text.strip()}, câu nói lại là {SIGN_WORD[f.sign]}"
            continue
        unit = g.unit or c.unit
        if f.unit and unit and f.unit != unit:
            why = why or f"cột «{c.header}» tính bằng «{unit}», câu nói lại là «{f.unit}»"
            continue
        return None
    return why


def other_row(noi: str, chi: str, places: dict) -> Optional[str]:
    """The label of another row of the same table, named in a sentence that frames this row (and does not name
    this row): Clicky would circle «Khối Bán hàng» while saying «Khối Marketing»."""
    p = places.get(chi)
    if p is None or p.kind != "row" or not p.label:
        return None
    text, own = tri_nho.fold(noi), tri_nho.fold(p.label)
    if re.search(rf"(?<!\w){re.escape(own)}(?!\w)", text):
        return None
    table = chi.rsplit("r", 1)[0] + "r"
    for q in places.values():
        lab = tri_nho.fold(q.label)
        if q.kind == "row" and q.id != chi and q.id.startswith(table) and re.fullmatch(r"\d+", q.id[len(table):]) \
                and len(lab) >= 4 and lab not in own and re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", text):
            return q.label
    return None


def check_at(f: Fig, place: Optional[Place], en: bool, declared: Optional[str]) -> Optional[str]:
    if place is None:
        return "missing"
    if place.cols is not None:
        return check_row(f, place, en)
    return check_figure(f, place.figs if place.figs is not None else figures(place.text), en, declared)


def card_figs(n: Node) -> list:
    """A card's figures read part by part (label, value, change): its value stands alone in its part."""
    parts = _card_parts(n)
    if len(parts) < 2:
        return figures(n.text() + " " + _labels(n))
    out = [g for p in parts for g in figures(p)]
    words = [(w.start(), "-", w.group(1).lower()) for w in NEG_WORD.finditer(parts[0])] + \
            [(w.start(), "+", w.group(1).lower()) for w in POS_WORD.finditer(parts[0])]
    if words:                        # "Cần giảm so với AOPR" / "8,9 tỷ": the label gives the value its direction
        _, sign, word = max(words)
        for g in figures(parts[1]):
            if g.sign is None and g.how == "":
                out[out.index(g)] = Fig(g.tok, sign, False, g.unit, parts[0], "am" if word == "âm" else "word",
                                        subject(parts[0]), g.at)
    return out + figures(_labels(n))


def _labels(n: Node) -> str:
    return " ".join(d.attrs[a] for d in n.walk() for a in LABEL_ATTRS if d.attrs.get(a))


def tag_targets(source: str, root: Node) -> tuple:
    """Give every candidate element a data-clicky id; return (new source, items, table rows)."""
    items = outline(root)
    for i, it in enumerate(items, 1):
        it.node.attrs["data-clicky"] = f"c{i}"
    inserts = [(it.node.start, it.node.tag, it.node.attrs["data-clicky"]) for it in items]
    rows: list = []
    for it in items:
        if it.kind != "table":
            continue
        for row in table_rows(it.node, it.node.attrs["data-clicky"], it):
            row.node.attrs["data-clicky"] = row.id
            inserts.append((row.node.start, "tr", row.id))
            rows.append(row)
    pieces, pos = [], 0
    for start, tag, cid in sorted(inserts, key=lambda x: x[0]):
        at = start + 1 + len(tag)
        if source[start:at].lower() != "<" + tag:
            raise PackError(f"Không gắn được dấu vào thẻ <{tag}> ở vị trí {start}.")
        pieces += [source[pos:at], f' data-clicky="{cid}"']
        pos = at
    return "".join(pieces) + source[pos:], items, rows


FIGURE_CELL = re.compile(r"^[\s(+\-−–]*\d[\d.,]*\s*%?\)?\s*$")


def table_rows(table: Node, tid: str, item=None) -> list:
    """The rows of a table with their cells laid out by column; ids are "<table id>r<n>"."""
    trs = [n for n in table.walk() if n.tag == "tr"]
    grid = table_grid(trs)
    first_data = next((k for k, g in enumerate(grid) if sum(1 for x in g if FIGURE_CELL.match(x)) >= 2), len(grid))
    rows = []
    for r, row in enumerate(trs):
        cells = grid[r]
        lead = []
        for x in cells:                     # the label columns, before the first figure
            if FIGURE_CELL.match(x):
                break
            if x and x not in lead:
                lead.append(x)
        label = " ".join(reversed(lead)) if len(lead) > 1 else (lead[0] if lead else "")
        header = r < first_data or all(c.tag == "th" for c in row.children if c.tag in ("td", "th")) or \
            row.has_ancestor(lambda p: p.tag == "thead")
        rows.append(Row(f"{tid}r{r}", row, item, cells, label, header))
    return rows


def cost_context(root: Node) -> bool:
    """The report is about costs (its title says so): a line without a revenue word is a cost line."""
    head = " ".join(n.text() for n in root.walk() if n.tag in ("title", "h1"))[:300]
    return bool(re.search(r"chi phí|\bcost|expense|opex", head, re.I))


def page_places(root: Node, en: bool, declared: Optional[str]) -> dict:
    """Row and card places read back from a built page (kiem_bien_ban): the same columns, units, kinds and card
    parts as the build."""
    page_figs = figures(root.text())
    cost_page = cost_context(root)
    out = {}
    for t in root.walk():
        cid = t.attrs.get("data-clicky")
        if t.tag == "table" and cid:
            out.update(row_places(table_rows(t, cid), None, page_figs, en, declared, cost_page))
        elif cid and _is_card(t):
            out[cid] = Place(cid, "card", t.text() + " " + _labels(t), card_label(t), None, card_label(t),
                             figs=card_figs(t))
    return out


def table_grid(trs: list) -> list:
    """Cell texts by column, with colspan and rowspan spread out the way the browser lays them out (Excel)."""
    grid, carry = [], {}                       # carry: column -> (rows left, text)
    for row in trs:
        out, col = [], 0
        cells = [c for c in row.children if c.tag in ("td", "th")]
        for c in cells + [None]:
            while col in carry:
                left, text = carry[col]
                out.append(text)
                carry[col] = (left - 1, text)
                if left - 1 <= 0:
                    del carry[col]
                col += 1
            if c is None:
                break
            span = _int(c.attrs.get("colspan"))
            down = _int(c.attrs.get("rowspan"))
            for k in range(span):
                out.append(c.text())
                if down > 1:
                    carry[col + k] = (down - 1, c.text())
            col += span
        grid.append(out)
    width = max((len(g) for g in grid), default=0)
    return [g + [""] * (width - len(g)) for g in grid]


def _int(v) -> int:
    try:
        return max(1, min(50, int(str(v).strip())))
    except ValueError:
        return 1


def make_places(items: list, rows: list, root: Optional[Node] = None, en: bool = False,
                declared: Optional[str] = None) -> dict:
    out = {}
    for it in items:
        cid = it.node.attrs["data-clicky"]
        sec = items[it.section].node.attrs["data-clicky"] if it.section is not None else None
        if it.kind == "section":
            sec = cid
        label = card_label(it.node) if it.kind == "card" else ""
        if it.kind == "card":
            anchor = label
        elif it.kind == "table":
            head = next((r for r in rows if r.table is it), None)
            anchor = "Bảng " + " | ".join(head.cells if head else [])[:80]
        elif it.kind == "chart":
            anchor = it.text or "biểu đồ " + (it.node.attrs.get("id") or "")
        else:
            anchor = it.text[:80]
        out[cid] = Place(cid, it.kind, it.node.text() + " " + _labels(it.node), anchor.strip(), sec, label,
                         figs=card_figs(it.node) if it.kind == "card" else None)
    page_figs = figures(root.text()) if root is not None else []
    cost_page = cost_context(root) if root is not None else False
    for trs in tables_of(rows):
        tp = out[trs[0].table.node.attrs["data-clicky"]]
        out.update(row_places(trs, tp.section, page_figs, en, declared, cost_page))
    return out


def target_texts(root: Node) -> dict:
    """What a reader can read at each tagged place: its text plus chart labels (aria-label, alt, title)."""
    return {n.attrs["data-clicky"]: n.text() + " " + _labels(n) for n in root.walk() if n.attrs.get("data-clicky")}


def page_numbers(root: Node) -> set:
    return set(numbers(root.text() + " " + _labels(root)))


def _masked(text: str) -> str:
    return re.sub(r"\d[\d.,]*", "#", text)[:60]


def reanchor(script: dict, places: dict) -> list:
    """Point each line back at its place by the text stored with it ("neo"), so a card added above does not shift
    every frame. Returns the lines that moved. A place whose text is gone stops the build."""
    by_anchor: dict = {}
    for p in places.values():
        by_anchor.setdefault(tri_nho.fold(p.anchor), []).append(p)
    moved = []
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            chi, neo = str(c.get("chi") or ""), str(c.get("neo") or "").strip()
            if not chi or not neo:
                continue
            key = tri_nho.fold(neo)
            if chi in places and tri_nho.fold(places[chi].anchor) == key:
                continue
            found = by_anchor.get(key) \
                or [p for k, ps in by_anchor.items() for p in ps if _masked(k) == _masked(key)] \
                or [p for k, ps in by_anchor.items() for p in ps if k and len(key) >= 8 and
                    min(len(k), len(key)) >= 0.8 * max(len(k), len(key)) and (k.startswith(key) or key.startswith(k))]
            if not found:
                raise PackError(f"Kịch bản trỏ tới chỗ «{neo}» ('{chi}') nhưng báo cáo không còn chỗ đó. Anh sửa câu "
                                f"«{str(c.get('noi'))[:60]}» hoặc xóa file kịch bản để Clicky soạn lại.")
            kind = places[chi].kind if chi in places else ("row" if "r" in chi[1:] else None)
            same = [p for p in found if (p.kind == "row") == (kind == "row")] or found
            new = next((p for p in same if p.id == chi), same[0]).id
            if new != chi:
                c["chi"] = new
                moved.append({"noi": str(c.get("noi")), "tu": chi, "den": new})
    return moved


# --- the draft: a talk to the Board -------------------------------------------------------------------------

VAR_HEAD = re.compile(r"chênh|lệch|\bvar\b|variance|\bdiff|Δ|delta|\+/-", re.I)
SKIP_ROW = re.compile(r"^\s*(tổng|total|cộng|grand)|lợi nhuận|ebitda|\bebit\b|lntt|lnst|\blãi\b|net income|"
                      r"gross profit", re.I)
SUB_ROW = re.compile(r"\b(sub|total|subtotal|tong|sum)\b", re.I)
KEY_HEAD = re.compile(r"key message|thông điệp|tóm tắt|summary|highlight|điểm chính|executive|kết luận", re.I)
RECO_HEAD = re.compile(r"đề xuất|kiến nghị|đề nghị|recommend|hành động|action|next step|giải pháp", re.I)
RECO_START = re.compile(r"^\s*(đề xuất|kiến nghị|đề nghị|recommendations?)\s*(của [^:]{1,30})?\s*:\s*", re.I)
KEY_START = re.compile(r"^\s*(thông điệp chính|thông điệp|key messages?|kết luận|tóm tắt|nhận xét|điểm chính|insight|"
                       r"takeaway|summary)\s*:\s*", re.I)
KEY_CLASS = re.compile(r"message|thong-?diep|key|summary|takeaway|insight|conclusion|ket-?luan", re.I)
GREETING = re.compile(r"^\s*(kính gửi|kính chào|trân trọng|thân gửi|dear|best regards|regards)", re.I)
EN_HEADINGS = {"key messages": "Thông điệp chính", "key message": "Thông điệp chính", "summary": "Tóm tắt",
               "executive summary": "Tóm tắt", "highlights": "Điểm nổi bật", "recommendations": "Đề xuất",
               "recommendation": "Đề xuất", "next steps": "Bước tiếp theo", "appendix": "Phụ lục",
               "overview": "Tổng quan", "risks": "Rủi ro", "outlook": "Triển vọng", "actions": "Hành động",
               "commentary": "Nhận xét", "key takeaways": "Điểm chính"}
ORDINAL = ["Thứ nhất", "Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm"]
REASON = re.compile(r"(?<!\w)(do|vì|bởi|nhờ|chủ yếu do|chủ yếu vì|nguyên nhân là|nguyên nhân do)\s+(.+?)(?:[.;!]\s|[.;!]?$)",
                    re.I)
COUNT_WORD = {1: "một", 2: "hai", 3: "ba"}
NET_CHART = re.compile(r"""<script[^>]+src\s*=\s*["']https?://[^"']*(chart|echarts|highcharts|apexcharts|plotly|"""
                       r"""d3|amcharts|google)""", re.I)


SECTION_NUMBER = re.compile(r"^\s*(?:(?:phần|mục|part|section)\s+)?(?:\d+(?:\.\d+)*|[ivxlc]+|[a-h])\s*[.):\-–]\s*",
                            re.I)


def heading_title(text: str) -> str:
    """A section title as Clicky says it and shows it in the bar: English headings in Vietnamese, and without the
    report's numbering ("2. Chi tiết theo khối"): Clicky presents only some parts, so "phần hai" would follow no
    "phần một"."""
    t = re.sub(r"\s+", " ", text).strip().rstrip(".:")
    t = SECTION_NUMBER.sub("", t, count=1) or t
    return EN_HEADINGS.get(t.lower(), t)


def chart_series(source: str) -> dict:
    """{canvas id: (x labels, series names)} read from a Chart.js setup in the report, when it is readable."""
    out = {}
    for m in re.finditer(r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)([\s\S]{0,3000}?)\}\s*\)", source):
        body = m.group(2)
        labels = re.search(r"labels\s*:\s*\[([^\]]*)\]", body)
        names = re.findall(r"\blabel\s*:\s*['\"]([^'\"]+)['\"]", body)
        if names:
            xs = re.findall(r"['\"]([^'\"]+)['\"]", labels.group(1)) if labels else []
            out[m.group(1)] = (xs, names)
    return out


def header_texts(trs: list) -> list:
    """One header text per column, from every header row except full-width banners (a title, "Đơn vị: tỷ")."""
    heads = [r for r in trs if r.header and len({x for x in r.cells if x}) > 1]
    width = len(trs[0].cells) if trs else 0
    return [" ".join(dict.fromkeys(h.cells[i] for h in heads if i < len(h.cells) and h.cells[i])) for i in range(width)]


def tables_of(rows: list) -> list:
    tables: dict = {}
    for r in rows:
        tables.setdefault(id(r.table), []).append(r)
    return list(tables.values())


def drivers(rows: list, en: bool, limit: int = 3) -> Optional[tuple]:
    """The rows with the largest absolute variance in the first table that has a variance column:
    (table rows, [(row, variance figure, percent figure or None)])."""
    for trs in tables_of(rows):
        hc = header_texts(trs)
        var = next((i for i, h in enumerate(hc) if i and VAR_HEAD.search(h) and "%" not in h), None)
        if var is None:
            continue
        pct = next((i for i, h in enumerate(hc) if i and "%" in h and (VAR_HEAD.search(h) or
                                                                         re.search(r"tăng|giảm", h, re.I))), None)
        cand = []
        for r in trs:
            if r.header or not r.label or SKIP_ROW.search(r.label) or SUB_ROW.search(r.node.attrs.get("class") or ""):
                continue
            f = figures(r.cells[var])
            v = value(f[0].tok, en) if len(f) == 1 else None
            if not v:
                continue
            pf = figures(r.cells[pct]) if pct is not None else []
            cand.append((abs(v), r, f[0], pf[0] if len(pf) == 1 else None))
        if len(cand) >= 2:
            cand.sort(key=lambda x: -x[0])
            return trs, [c[1:] for c in cand[:limit]]
    return None


EN_COLUMNS = {"var": "chênh lệch", "variance": "chênh lệch", "var %": "chênh lệch phần trăm", "ly": "cùng kỳ năm trước",
              "py": "năm trước", "act": "thực tế", "bud": "ngân sách", "plan": "kế hoạch"}


def row_line(r: Row, hc: list, unit: Optional[str]) -> Optional[str]:
    """'Dòng Tổng có AOP là 18,4, Thực hiện là 21,3.' for a table that has nothing else said about it."""
    parts = []
    for i, cell in enumerate(r.cells):
        f = figures(cell)
        if i >= len(hc) or not hc[i] or len(f) != 1 or not FIGURE_CELL.match(cell):
            continue
        name = EN_COLUMNS.get(hc[i].strip().lower(), hc[i].strip())
        tail = "%" if f[0].unit == "%" else (" " + f[0].unit if f[0].unit else (" " + unit if unit else ""))
        parts.append(f"{name} là {_signed(f[0])}{tail}")
        if len(parts) == 3:
            break
    head = r.label if re.match(r"dòng\b", r.label, re.I) else "Dòng " + r.label
    return f"{head} có " + ", ".join(parts) + "." if parts else None


def _signed(f: Fig) -> str:
    return ("âm " if f.sign == "-" else "") + f.tok


PLAN_HEAD = re.compile(r"\baop\b|kế hoạch|\bkh\b|plan|budget|ngân sách|cùng kỳ|\bly\b|\bpy\b|năm trước|tháng trước",
                       re.I)


def driver_line(i: int, r: Row, f: Fig, pf: Optional[Fig], unit: Optional[str], place: Optional[Place] = None,
                said_before: bool = False) -> str:
    """'Thứ nhất, khối Bán hàng vượt AOP gốc 14,1 tỷ, tức 18,0%.' The direction comes from the variance cell
    (a P&L that writes costs in brackets is read the same way as the lock reads it), the unit from its column,
    the words from the line: a cost above plan is "vượt", below plan "tiết kiệm so với"; revenue is "cao hơn" or
    "thấp hơn". The percent is said without a second minus ("tức 5,2%", never "thấp hơn … âm 5,2%")."""
    raw = _cell_sign(f)
    size = ("-" if raw == "+" else "+") if place is not None and place.neg_book else raw
    col = next((c for c in (place.cols if place and place.cols else []) if c.kind == "var" and f in c.figs), None)
    u = f.unit or (col.unit if col and col.unit != "%" else None) or unit
    ref = None
    if place is not None and place.cols:
        values = [c for c in place.cols if c.kind == "value" and c.unit != "người"]
        ref = next(((c.name or c.header) for c in values if PLAN_HEAD.search(c.name or c.header)), None)
    label = r.label.strip()
    label = label[:1].lower() + label[1:] if re.match(r"(khối|phòng|ban|bộ phận|kênh|vùng|miền|chi nhánh)\b", label, re.I) \
        else label
    amount = f.tok + (" " + u if u else "")
    if ref and place.cost:
        verb = f"vượt {ref}" if size == "+" else f"tiết kiệm so với {ref}"
    elif ref:
        verb = f"cao hơn {ref}" if size == "+" else f"thấp hơn {ref}"
    else:
        verb = "tăng" if size == "+" else "giảm"
    if said_before and pf:                 # the amount was in the key message: say the rate only, not 22,630 twice
        return f"{ORDINAL[i]}, {label} {verb} {pf.tok}%."
    line = f"{ORDINAL[i]}, {label} {verb} {amount}"
    if pf:
        line += f", tức {pf.tok}%"
    return line + "."


def _vals(text: str, en: bool) -> set:
    return {value(f.tok, en) for f in figures(text)} - {None}


def draft_script(items: list, rows: list, places: dict, owner: str, en: bool = False,
                 declared: Optional[str] = None, charts: Optional[dict] = None, net_chart: bool = False,
                 memo: Optional[dict] = None, doc_title: str = "", max_parts: int = 5) -> dict:
    """A talk to the Board made of the report's own words and figures: greeting, (last time's answers), key
    message with the number cards, the largest variances in the table, what else it means, one request to the
    Board, thanks. Every line keeps the place it frames and that place's text, so the owner can edit safely."""
    said: set = set()
    used: set = set()
    cid = lambda it: it.node.attrs["data-clicky"]                                  # noqa: E731

    def line(noi, chi=""):
        said.update(_vals(noi, en))
        out = {"noi": noi, "chi": chi}
        if chi:
            out["neo"] = places[chi].anchor
        return out

    unclear: list = []

    def speakable(text):                   # nothing the lock or the voice would stop on in a first draft
        if any(f.how == "paren?" for f in figures(text)) or spelled_number(text) or \
                doc_tai_chinh.unsaid(doc_tai_chinh.doc(for_voice(text), en)):
            if text not in unclear:
                unclear.append(text)
            return False
        return True

    def fresh(text):                       # has a figure, and not every figure was just said
        v = _vals(text, en)
        return bool(v) and not v <= said and speakable(text)

    title = next((i for i in items if i.kind == "title"), None)
    notes = [i for i in items if i.kind == "note" and i.text and not GREETING.search(i.text)]
    cards = [i for i in items if i.kind == "card"]

    reco = next((n for n in notes if n.section is not None and RECO_HEAD.search(items[n.section].text)), None) \
        or next((n for n in notes if RECO_START.search(n.text)), None)
    reco_sec = reco.section if reco else -1
    fig_notes = [n for n in notes if n is not reco and _vals(n.text, en) and speakable(n.text)]
    headline = next((n for n in fig_notes if KEY_START.search(n.text) or KEY_CLASS.search(n.node.cls)), None) \
        or next((n for n in fig_notes if n.section is not None and KEY_HEAD.search(items[n.section].text)), None) \
        or (fig_notes[0] if fig_notes else None)

    def chart_line(it):
        if net_chart:
            return None
        xs, names = next(((charts or {})[d.attrs["id"]] for d in it.node.walk()
                          if d.attrs.get("id") in (charts or {})), ([], []))
        if not names:
            return None
        text = "Biểu đồ này cho thấy " + " và ".join(names)
        if len(xs) >= 2:
            text += f" từ {xs[0]} đến {xs[-1]}"
        return None if figures(text) else text + "."

    scenes = []
    name = title.text if title else (doc_title if doc_title and not figures(doc_title) else "báo cáo này")
    opening = f"Em kính chào anh chị ạ. Em là trợ lý AI của {owner}, xin trình bày {name.rstrip('. ')}."
    scenes.append({"muc": "Mở đầu", "cau": [line(opening, cid(title) if title else "")]})
    if memo:
        scenes.append(memo)

    # the key message, the other commentary of its section and the number cards
    key = []
    home = headline.section if headline else None
    if headline:
        used.add(id(headline))
        key.append(line("Thông điệp chính: " + _sentence(KEY_START.sub("", headline.text)), cid(headline)))
        for n in fig_notes:
            if len(key) < 3 and id(n) not in used and (n.section == home or n.section is None) and fresh(n.text):
                used.add(id(n))
                key.append(line(_sentence(n.text), cid(n)))
        for ch in (i for i in items if i.kind == "chart" and i.section == home and home is not None):
            text = chart_line(ch)
            if text:
                used.add(id(ch))
                key.append(line(text, cid(ch)))
                break
    unit = declared if declared in MONEY else None
    for c in [c for c in cards if c.section != reco_sec or reco is None][:4]:
        text = card_line(c.node, unit, said, en)
        if fresh(text):
            used.add(id(c))
            if not key:
                text = "Số liệu chính: " + text
            key.append(line(text, cid(c)))
        else:
            used.add(id(c))
    if key:
        scenes.append({"muc": "Thông điệp chính" if headline else "Số liệu chính", "cau": key})

    # the largest variances, each framed on its own row
    found = drivers(rows, en)
    if found:
        _trs, top = found
        cau = [line(f"Nguyên nhân chính nằm ở {COUNT_WORD[len(top)]} dòng có chênh lệch lớn nhất trong bảng.",
                    top[0][0].id)]
        for k, (r, f, pf) in enumerate(top):
            cau.append(line(driver_line(k, r, f, pf, unit, places.get(r.id), value(f.tok, en) in said), r.id))
            # the report's own comment on this row: say its reason here, never its figure a second time
            lab = tri_nho.fold(r.label)
            for n in fig_notes:
                if id(n) in used or n is headline or len(lab) < 4 or value(f.tok, en) not in _vals(n.text, en) \
                        or not re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", tri_nho.fold(n.text)):
                    continue
                used.add(id(n))
                why = REASON.search(n.text)
                if why:
                    cau.append(line(_sentence("Nguyên nhân là " + why.group(2).strip(" ,;")), cid(n)))
        scenes.append({"muc": "Nguyên nhân chính", "cau": cau})

    # what else it means: the other sections, in the report's order (a few: the full report is on the page)
    parts = 0
    for k, sec in enumerate(items):
        if sec.kind != "section":
            continue
        idx = items.index(sec)
        if idx in (home, reco_sec):
            continue
        cau = []
        for n in fig_notes:
            if n.section == idx and id(n) not in used and len(cau) < 2 and fresh(n.text):
                used.add(id(n))
                cau.append(line(_sentence(n.text), cid(n)))
        for c in cards:
            if c.section == idx and id(c) not in used and fresh(card_line(c.node, unit, said, en)):
                used.add(id(c))
                cau.append(line(card_line(c.node, unit, said, en), cid(c)))
        for ch in (i for i in items if i.kind == "chart" and i.section == idx):
            text = chart_line(ch)
            if text and cau:
                cau.append(line(text, cid(ch)))
                break
        if not cau:                       # only a table: its total row, else its first row
            for trs in tables_of(rows):
                if trs[0].table.section != idx or (found and trs[0].table is found[0][0].table):
                    continue
                body = [r for r in trs if not r.header and r.label]
                pick = next((r for r in body if re.match(r"\s*(tổng|total|cộng)", r.label, re.I)), body[0] if body else None)
                text = row_line(pick, header_texts(trs), unit) if pick else None
                if text and fresh(text):
                    cau.append(line(text, pick.id))
                    break
        if cau and parts < max_parts:
            parts += 1
            t = heading_title(sec.text)
            said_t = t if re.match(r".[A-ZĐ0-9]", t) else t[:1].lower() + t[1:]
            scenes.append({"muc": t, "cau": [line(f"Tiếp theo là phần {said_t}.", cid(sec))] + cau})

    # the requests to the Board: every point of the recommendation section, in order
    skipped = []
    if reco:
        recos = [n for n in notes if n.section == reco_sec and n.section is not None] or [reco]
        if reco not in recos:
            recos.insert(0, reco)
        skipped = [n.text for n in recos[5:]]
        recos = recos[:5]
        cau = []
        for k, n in enumerate(recos):
            used.add(id(n))
            body = RECO_START.sub("", n.text).strip()
            body = re.sub(r"^\s*(?:\(?\d{1,2}[.)]|[-•*–])\s*", "", body) if len(recos) > 1 else body
            word = body.split(" ", 1)[0]
            if len(word) == 1 or word[1:].islower():           # "Giao Mua hàng…" → "giao Mua hàng…", not "GT…"
                body = body[:1].lower() + body[1:]
            if len(recos) == 1:
                joint = ": " if re.match(r"[(\d]", body) else " "
                text = "Kính đề nghị Ban Giám đốc" + joint + body
            elif k == 0:
                text = "Kính đề nghị Ban Giám đốc: thứ nhất, " + body
            else:
                text = (ORDINAL[k] if k < len(ORDINAL) else "Tiếp theo") + ", " + body
            cau.append(line(_sentence(text), cid(n)))
        for c in cards:
            if c.section == reco_sec and id(c) not in used and fresh(card_line(c.node, unit, said, en)):
                used.add(id(c))
                cau.append(line(card_line(c.node, unit, said, en), cid(c)))
        scenes.append({"muc": "Đề nghị Ban Giám đốc", "cau": cau})

    scenes[-1]["cau"] += [
        line(f"Anh chị có thể hỏi em bất kỳ con số nào trong báo cáo; câu nào em chưa trả lời được, em ghi lại để "
             f"{owner} trả lời anh chị sau."),
        line("Em xin cảm ơn anh chị đã lắng nghe.")]
    return {"canh": scenes, "bo_qua": skipped, "chua_ro": unclear}


def memory_scene(entries: list, owner: str, places: dict, en: bool, declared: Optional[str]) -> Optional[dict]:
    """Last time's parked questions and the owner's answers, read right after the greeting. A figure in an answer
    counts as the report's only when it sits on the row or card the question is about; otherwise it is his."""
    if not entries:
        return None
    who = owner[:1].upper() + owner[1:]
    cau = []
    for e in entries:
        asker = e.fields.get("ai_hoi", "").strip()
        context = e.text + " " + e.answer
        for noi in (f"Lần trước {asker or 'có anh chị'} hỏi: {_sentence(e.text)}", f"{who} xin trả lời: {_sentence(e.answer)}"):
            chi, own = label_place(context, noi, places, en, declared)
            c = {"noi": noi, "chi": chi}
            if chi:
                c["neo"] = places[chi].anchor
            if own:
                c["so_rieng"] = own
            cau.append(c)
    return {"muc": "Câu hỏi lần trước", "tri_nho": True, "cau": cau}


def label_place(context: str, noi: str, places: dict, en: bool, declared: Optional[str]) -> tuple:
    """(place id, owner's own figures) for a sentence about a labelled row or card ("Khối CNTT ...")."""
    text = tri_nho.fold(context)
    best = None
    for p in places.values():
        lab = tri_nho.fold(p.label)
        if p.kind in ("row", "card") and len(lab) >= 4 and re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", text):
            if best is None or len(lab) > len(tri_nho.fold(best.label)):
                best = p
    figs = figures(noi)
    at = [f for f in figs if best and check_at(f, best, en, declared) is None]
    own = list(dict.fromkeys(f.tok for f in figs if f not in at))
    return (best.id if at else ""), own


# --- the script file ---------------------------------------------------------------------------------------

class _Q(str):
    """A string written in single quotes, so a colon or a figure in the owner's sentence never breaks the YAML."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(_Q, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", str(s), style="'"))

YES = {"co", "roi", "da duyet", "duyet", "x", "yes", "true", "ok", "dong y"}
SCRIPT_HEAD = """# Kịch bản Clicky cho «{report}».
# Anh sửa chữ trong dấu nháy sau "noi:". Giữ nguyên "chi" và "neo": đó là chỗ Clicky khoanh trên báo cáo.
# Trong câu có dấu nháy đơn thì gõ hai dấu nháy đơn liền nhau. Thụt đầu dòng bằng dấu cách, không dùng phím Tab.
# Số anh tự thêm (không có trong báo cáo): ghi vào dòng so_rieng của câu đó, ví dụ   so_rieng: ['0,12']
# Số có trong báo cáo nhưng ở chỗ khác chỗ khoanh: anh xem lại rồi ghi   xac_nhan: ['14,1']
# Đọc và sửa xong: đổi da_duyet: 'chưa' thành da_duyet: 'có', rồi kéo thả báo cáo vào Clicky lần nữa.
# Khi đó bản trình bày hết chữ SƠ BỘ.
"""


def script_hash(canh) -> str:
    return hashlib.sha256(json.dumps(canh, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def dump_script(script: dict, report: str, snap: Optional[dict] = None, draft: bool = True) -> str:
    """The script as the owner edits it. "ma_nhap" marks a draft nobody has touched; "so_cho" keeps the figures
    each framed place showed, so a report of the next month is recognised and its new figures put in place."""
    canh = script["canh"]
    out = []
    for s in canh:
        sc = {"muc": _Q(s["muc"])}
        if s.get("tri_nho"):
            sc["tri_nho"] = True
        sc["cau"] = []
        for c in s["cau"]:
            row = {"noi": _Q(c["noi"]), "chi": c.get("chi") or ""}
            if c.get("neo"):
                row["neo"] = _Q(c["neo"])
            for k in ("so_rieng", "xac_nhan"):
                if c.get(k):
                    row[k] = [_Q(str(x)) for x in c[k]]
            sc["cau"].append(row)
        out.append(sc)
    body = {"bao_cao": _Q(report), "da_duyet": _Q("chưa"),
            "ma_nhap": script_hash(json.loads(json.dumps(out))) if draft else "", "canh": out}
    return SCRIPT_HEAD.format(report=report) + yaml.dump(body, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                                                         width=4096) + dump_snap(snap)


SNAP_HEAD = "# Số ở từng chỗ khoanh lúc soạn hoặc duyệt (Clicky tự ghi, anh không cần sửa):\n"


def dump_snap(snap: Optional[dict]) -> str:
    if not snap:
        return ""
    return "\n" + SNAP_HEAD + yaml.dump({"so_cho": {_Q(k): _Q(v) for k, v in snap.items()}}, Dumper=_Dumper,
                                         allow_unicode=True, sort_keys=False, width=4096)


def write_snap(path: Path, snap: dict, reset: bool = False) -> None:
    """Rewrite only the "so_cho" block at the end of the owner's script (and, when asked, set da_duyet back to
    'chưa'); every sentence the owner wrote stays as it is."""
    text = tri_nho.read_text(path)
    cut = re.search(r"^(?:%s)?so_cho:" % re.escape(SNAP_HEAD), text, re.M)
    text = text[:cut.start()].rstrip("\n") + "\n" if cut else text.rstrip("\n") + "\n"
    if reset:
        text = re.sub(r"^da_duyet:.*$", "da_duyet: 'chưa'", text, count=1, flags=re.M)
    path.write_text(text + dump_snap(snap), encoding="utf-8")


def _tok(g: Fig) -> str:
    return ("-" if g.sign == "-" else "") + g.tok + ("%" if g.unit == "%" else "")


def place_figs(p: Place) -> list:
    if p.cols is not None:
        return [g for c in p.cols if c.kind != "label" for g in c.figs]
    return p.figs if p.figs is not None else figures(p.text)


def snapshot(script: dict, places: dict) -> dict:
    """{neo: "78,2 92,3 14,1 18,0% 712"}: the figures at each place a line frames, in reading order."""
    out = {}
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            chi, neo = str(c.get("chi") or ""), str(c.get("neo") or "").strip()
            if chi in places and neo and places[chi].kind not in ("title", "section"):
                out[neo] = " ".join(_tok(g) for g in place_figs(places[chi]))
    return out


def _tok_value(t: str, en: bool) -> Optional[Decimal]:
    return value(t.lstrip("-").rstrip("%"), en)


def next_period(script: dict, old: dict, places: dict, en: bool) -> tuple:
    """The owner's sentences with this period's figures put where last period's stood (same place, same position
    in it). Returns (script, lines whose direction flipped: their wording no longer holds)."""
    now = snapshot(script, places)
    flipped = []
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            neo = str(c.get("neo") or "").strip()
            a, b = (old.get(neo) or "").split(), (now.get(neo) or "").split()
            if not a or len(a) != len(b):
                continue
            noi = str(c.get("noi") or "")
            for f in reversed(figures(noi)):
                k = next((i for i, t in enumerate(a) if _tok_value(t, en) == value(f.tok, en)), None)
                if k is None or a[k] == b[k]:
                    continue
                if a[k].startswith("-") != b[k].startswith("-"):
                    flipped.append(c)
                noi = noi[:f.at] + b[k].lstrip("-").rstrip("%") + noi[f.at + len(f.tok):]
            c["noi"] = noi
    return script, flipped


RANK = {"thu nhat": 0, "thu hai": 1, "thu ba": 2, "lon nhat": 0, "manh nhat": 0,
        "nhieu nhat": 0, "cao nhat": 0, "nho nhat": -1, "it nhat": -1, "thap nhat": -1}


def ranking_issues(script: dict, rows: list, en: bool) -> list:
    """Lines that rank a row ("Thứ nhất, …", "… lớn nhất") whose rank in this report's variance column is not
    that one any more. [(line, the row that holds that rank now)]"""
    found = drivers(rows, en, limit=999)
    if not found:
        return []
    _trs, order = found
    ids = {r.id for r in _trs}
    out = []
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            chi = str(c.get("chi") or "")
            if chi not in ids:
                continue
            text = tri_nho.fold(str(c.get("noi") or ""))
            k = next((v for w, v in RANK.items() if re.search(rf"(?<!\w){w}(?!\w)", text)), None)
            if k is None or not order:
                continue
            want = order[k][0] if -len(order) <= k < len(order) else None
            if want is not None and want.id != chi:
                out.append((c, want))
    return out


def _quote_lists(text: str) -> str:
    """so_rieng: [0,12] is one Vietnamese figure, not the numbers 0 and 12: keep what the owner typed as text."""
    def fix(m):
        inner = m.group(2)
        if m.group(3) is None:                                     # so_rieng: 1.240  (a bare value)
            items = [inner]
        else:
            items = [x for x in re.split(r"\s*;\s*|,\s+|\s+", m.group(3).strip()) if x]
        items = [x.strip().strip("'\"") for x in items if x.strip().strip("'\"")]
        return m.group(1) + "[" + ", ".join("'" + x.replace("'", "''") + "'" for x in items) + "]"
    return re.sub(r"^([ \t]*(?:-[ \t]+)?(?:so_rieng|xac_nhan)[ \t]*:[ \t]*)(\[([^\]\n]*)\]|[^\[\s'\"#][^\n#]*?)[ \t]*$", fix, text,
                  flags=re.M)


def _inner_quotes(text: str) -> str:
    """noi: 'theo ý 'sếp' Hùng.' — an apostrophe inside the owner's sentence is doubled for him, so a quoted word
    does not break the whole script."""
    def fix(m):
        inner = m.group(3).replace("''", "'").replace("'", "''")
        return f"{m.group(1)}'{inner}'{m.group(4)}"
    return re.sub(r"^([ \t]*(?:-[ \t]+)?(noi|neo|muc)[ \t]*:[ \t]*)'(.*)'([ \t]*)$", fix, text, flags=re.M)


class _Loader(yaml.SafeLoader):
    """A key written twice ("noi:" twice in one line of the script) is an error, not the last one silently."""


def _no_twice(loader, node, deep=False):
    seen = {}
    for k, _v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"twice:{key}:{seen[key] + 1}", k.start_mark)
        seen[key] = k.start_mark.line
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                        lambda loader, node: _no_twice(loader, node, True))


def load_script(path) -> dict:
    """The owner's script, with errors in plain Vietnamese and the line they are on."""
    text = tri_nho.read_text(path)
    try:
        script = yaml.load(_inner_quotes(_quote_lists(text)), Loader=_Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f"ở dòng {mark.line + 1}" if mark else ""
        prob = str(getattr(exc, "problem", "") or exc)
        line = text.splitlines()[mark.line] if mark and mark.line < len(text.splitlines()) else ""
        if prob.startswith("twice:"):
            _, key, first = prob.split(":", 2)
            why = (f"một câu có hai dòng «{key}:» (dòng {first} và dòng này). Mỗi câu chỉ một dòng «{key}:»; muốn thêm "
                   "câu mới, bắt đầu bằng «- noi:».")
        elif "\\t" in prob or "\t" in line:
            why = "dòng này có phím Tab. Hãy xóa Tab, chỉ dùng dấu cách để thụt đầu dòng."
        elif "mapping values" in prob:
            why = ("câu có dấu hai chấm (:) nhưng chưa nằm trong dấu nháy. Hãy đặt cả câu sau noi: trong dấu nháy "
                   "đơn, ví dụ noi: 'Phần 2: Chi tiết theo khối.'")
        elif "quoted" in prob or "quote" in str(exc):
            why = "thiếu dấu nháy đóng, hoặc trong câu có dấu nháy đơn mà chưa gõ thành hai dấu nháy."
        else:
            why = "thụt đầu dòng không thẳng hàng với các dòng cùng loại ở trên, hoặc thiếu dấu nháy."
        raise PackError(f"Kịch bản lỗi {where}: {why}" + (f" Dòng đó: «{line.strip()[:80]}»." if line.strip() else "")
                        + f" File: {path}") from None
    if not isinstance(script, dict) or not isinstance(script.get("canh"), list):
        raise PackError(f"Kịch bản thiếu phần «canh:» (danh sách các phần trình bày). File: {path}")
    for i, s in enumerate(script["canh"], 1):
        if not isinstance(s, dict) or not isinstance(s.get("cau"), list):
            name = s.get("muc", "") if isinstance(s, dict) else ""
            raise PackError(f"Phần thứ {i} của kịch bản («{name}») thiếu dòng «cau:» hoặc thụt đầu dòng sai. "
                            f"File: {path}")
        for c in s["cau"]:
            if not isinstance(c, dict) or "noi" not in c:
                raise PackError(f"Phần «{s.get('muc', i)}» có một câu thiếu «noi:» hoặc thụt đầu dòng sai. File: {path}")
    return script


def approved(script: dict) -> bool:
    v = script.get("da_duyet")
    return v is True or tri_nho.fold(str(v or "")) in YES


def unedited(script: dict) -> bool:
    return bool(script.get("ma_nhap")) and script_hash(script.get("canh")) == script.get("ma_nhap")


def _toks(v) -> list:
    if v is None or v is True or v is False:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x for x in re.split(r"\s*;\s*|,\s+|\s+", str(v).strip()) if x]


# --- number lock and record ------------------------------------------------------------------------------

def seal(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "ma_bam"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def stale_places(script: dict, places: dict, en: bool) -> int:
    """How many framed places no longer show a figure the line says there (a script without "so_cho")."""
    gone = set()
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            chi = str(c.get("chi") or "")
            own = {value(t, en) for t in _toks(c.get("so_rieng"))}
            if chi in places and any(value(f.tok, en) not in own and check_at(f, places[chi], en, None) == "missing"
                                     for f in figures(str(c.get("noi") or ""))):
                gone.add(chi)
    return len(gone)


def make_record(script: dict, places: dict, page_text: str, meta: dict, en: bool = False,
                declared: Optional[str] = None, earlier: Optional[dict] = None) -> dict:
    """Check every spoken figure against the place it frames and write down where it is. Stops on a figure that
    is not there with the same direction and unit, unless the owner declared it (so_rieng) or confirmed it is the
    report's figure from another place (xac_nhan)."""
    on_page = _vals(page_text, en)
    rows, bad = [], []
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            noi = str(c.get("noi") or "").strip()
            if not noi:
                continue
            chi = str(c.get("chi") or "")
            own = {value(t, en) for t in _toks(c.get("so_rieng"))}
            conf = c.get("xac_nhan")
            conf_all = conf is True or tri_nho.fold(str(conf or "")) in YES
            confirmed = {value(t, en) for t in _toks(conf)}
            found = []
            words = spelled_number(noi)
            if words:
                bad.append((noi, words, "là số viết bằng chữ, em không đối chiếu được với báo cáo. Anh viết lại "
                                        "bằng chữ số, ví dụ «12,6%»"))
            other = other_row(noi, chi, places)
            if other and figures(noi):
                bad.append((noi, figures(noi)[0].tok, f"câu nói về «{other}» nhưng Clicky khoanh dòng "
                                                       f"«{places[chi].label}». Sửa chi cho đúng dòng"))
                rows.append({"canh": s.get("muc", ""), "noi": noi, "chi": chi, "so": []})
                continue
            for f in figures(noi):
                v = value(f.tok, en)
                why = check_at(f, places.get(chi), en, declared)
                if f.how == "paren?":
                    why = (f"số trong ngoặc, em chưa rõ là số âm hay tỷ trọng. Anh viết «âm {f.tok}» nếu là số âm, "
                           f"hoặc «chiếm {f.tok}» nếu là tỷ trọng")
                entry = {"so": f.tok, "gia_tri": str(v), "dau": f.sign or ""}   # gia_tri: the value as read aloud
                if v in own:                         # declared by the owner: his, even if the page has it too
                    found.append(dict(entry, nguon="nguoi_soan"))
                elif why is None:
                    found.append(dict(entry, nguon=chi))
                elif why != "missing":
                    bad.append((noi, f.tok, why))
                elif v in (earlier or {}).get(chi, ()):       # last time's figure at this very place
                    now = " ".join(g.tok for g in place_figs(places[chi])) if chi in places else ""
                    bad.append((noi, f.tok, f"là số cũ ở chỗ «{places[chi].anchor[:40] if chi in places else chi}» "
                                            f"lúc soạn kịch bản; báo cáo bây giờ ghi {now[:60]} ở đó. Anh sửa câu theo "
                                            "số mới"))
                elif v in on_page and (conf_all or v in confirmed):
                    found.append(dict(entry, nguon="bao_cao"))
                elif v in on_page:
                    where = f"chỗ Clicky khoanh («{places[chi].anchor[:40]}»)" if chi in places else "câu này"
                    bad.append((noi, f.tok, f"có trong báo cáo nhưng không ở {where}. Sửa chi cho đúng chỗ, hoặc "
                                            f"nếu anh chắc chắn thì ghi xac_nhan: ['{f.tok}']"))
                else:
                    bad.append((noi, f.tok, f"không có trong báo cáo. Nếu là số anh tự thêm, ghi so_rieng: "
                                            f"['{f.tok}']"))
            rows.append({"canh": s.get("muc", ""), "noi": noi, "chi": chi, "so": found})
    if bad:
        shown = "\n".join(f" - số {tok} trong câu «{noi[:70]}»: {why}." for noi, tok, why in bad[:6])
        more = f"\n   (và {len(bad) - 6} chỗ nữa)" if len(bad) > 6 else ""
        raise PackError(f"Lời nói có số chưa khớp với báo cáo:\n{shown}{more}")
    flat = [x["nguon"] for r in rows for x in r["so"]]
    record = {"phien_ban": RECORD_VERSION, "bao_cao": meta["report"], "sha256": meta["report_sha256"],
              "lap_luc": meta["built_iso"], "nguoi_chiu_trach_nhiem": meta["owner"], "dau_so": "en" if en else "vn",
              "don_vi": declared or "", "trang_thai": meta.get("trang_thai", ""), "cau": rows,
              "tong": {"cau": len(rows), "so": len(flat),
                       "dung_cho_khoanh": sum(1 for x in flat if x not in ("bao_cao", "nguoi_soan")),
                       "cho_khac_trong_bao_cao": flat.count("bao_cao"), "nguoi_soan_them": flat.count("nguoi_soan")}}
    record["ma_bam"] = seal(record)
    return record


# --- building ----------------------------------------------------------------------------------------

PAUSE_PIECE, PAUSE_SENTENCE, LEAD = 0.3, 0.65, 0.5    # seconds: a breath, a full stop, frame before the words


def _wav(data: bytes):
    import io
    import wave
    with wave.open(io.BytesIO(data)) as w:
        return w.getparams(), w.readframes(w.getnframes())


LEAD_IN = 0.25    # seconds of silence before the first word: Bluetooth and Windows audio wake-up eat the first syllable


def voice_lines(sentences: list, say: Callable[[str], bytes], en_numbers: bool = False) -> tuple:
    """Voice each sentence piece by piece, the way a person reads a report: a short breath inside a long
    sentence, a longer pause between sentences. Returns (wav bytes, start second of each sentence).
    A word the offline voice cannot say is spelled letter by letter instead of being dropped; if even that
    fails the build stops (a dropped word can change a figure). A voice that does not return WAV (tests)
    gives one clip and no timings."""
    import io
    import wave
    engine = getattr(say, "__self__", None)

    def voiced(text):
        data = say(text)
        missed = list(getattr(engine, "missing", None) or [])
        if missed:                                  # e.g. a brand name: spell it rather than skip it
            for w in sorted(set(missed), key=len, reverse=True):
                text = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", doc_tai_chinh._spell(w.upper()).replace("-", " "),
                              text, flags=re.I)
            data = say(text)
            still = list(getattr(engine, "missing", None) or [])
            if still:
                raise PackError("Giọng đọc chưa đọc được: " + ", ".join(sorted(set(still))) + ". Anh thêm cách đọc vào "
                                "file TU_DOC.txt trong thư mục trí nhớ (mỗi dòng: CHỮ = cách đọc) rồi chạy lại.")
        return data

    params, frames, starts = None, [], []
    t = LEAD_IN
    for k, sentence in enumerate(sentences):
        starts.append(round(t, 3))
        parts = doc_tai_chinh.pieces(sentence) or [sentence]
        for j, piece in enumerate(parts):
            said = doc_tai_chinh.doc(piece, en_numbers)
            odd = doc_tai_chinh.unsaid(said)       # a sign the voice would skip ("≠", "€") can change the meaning
            if odd:
                raise PackError(f"Câu «{sentence}» có ký hiệu giọng đọc không đọc được: {' '.join(odd)}. "
                                "Anh viết ký hiệu đó bằng chữ trong kịch bản (ví dụ «khác», «euro») rồi chạy lại.")
            data = voiced(said)
            if not data.startswith(b"RIFF"):
                return b"".join(say(doc_tai_chinh.doc(x, en_numbers)) for x in sentences), None
            pr, fr = _wav(data)
            if params is None:
                params = pr
                frames.append(b"\0" * (int(pr.framerate * LEAD_IN) * pr.sampwidth * pr.nchannels))
            rate, width = params.framerate, params.sampwidth * params.nchannels
            frames.append(fr)
            t += len(fr) / width / rate
            gap = PAUSE_PIECE if j < len(parts) - 1 else (PAUSE_SENTENCE if k < len(sentences) - 1 else 0.2)
            frames.append(b"\0" * (int(rate * gap) * width))
            t += gap
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return out.getvalue(), starts


def wav_to_mp3(wav: bytes) -> tuple:
    """(audio bytes, seconds). ffmpeg when the PC has it; otherwise the MP3 encoder installed with the kit (lameenc),
    so a normal office PC does not embed six times bigger WAV clips. WAV stays the last resort."""
    if shutil.which("ffmpeg"):
        return goi_html.wav_to_mp3(wav)
    import io
    import wave
    with wave.open(io.BytesIO(wav)) as w:
        p, frames = w.getparams(), w.readframes(w.getnframes())
    secs = round(p.nframes / float(p.framerate), 2)
    try:
        import lameenc
        if p.sampwidth != 2:
            raise ValueError("lameenc takes 16-bit samples")
        enc = lameenc.Encoder()
        enc.set_bit_rate(48)
        enc.set_in_sample_rate(p.framerate)
        enc.set_channels(p.nchannels)
        enc.set_quality(5)
        mp3 = bytes(enc.encode(frames) + enc.flush())
        if mp3:
            return mp3, secs
    except Exception:                                   # noqa: BLE001 - keep the WAV, it sounds the same
        pass
    return wav, secs


def _millions(m, en: bool) -> str:
    """"2.000 tr" is said "2 tỷ", "1.234,5 triệu" is "1 tỷ 234,5 triệu": nobody reads two thousand million."""
    v = value(m.group(1), en)
    if v is None or v < 1000:
        return m.group(1) + " triệu"
    whole, rest = int(v // 1000), v - (v // 1000) * 1000
    text = f"{whole} tỷ"
    if rest:
        r = format(rest.normalize(), "f")
        text += " " + (r if en else r.replace(".", ",")) + " triệu"
    return text


def for_voice(sentence: str, en: bool = False) -> str:
    """What the voice gets besides the caption, read with the same bracket rule as the number lock: "(9,870)" that
    stands for a negative is "âm 9,870"; "186,4 tỷ (12,6%)" is said "186,4 tỷ, 12,6%"; "vs" is "so với"."""
    t = sentence
    for f in reversed(figures(sentence)):
        m = re.search(r"\s*\(\s*$", t[:f.at])
        close = re.match(r"\s*%?\s*\)", t[f.at + len(f.tok):])
        if not m or not close:
            continue
        inner = f.tok + ("%" if "%" in close.group(0) else "")
        tail = t[f.at + len(f.tok) + close.end():]
        if f.sign == "-" and f.sym:
            mid = (" " if m.start() else "") + "âm " + inner
        else:
            mid = (", " if m.start() else "") + inner + ("," if re.match(r"\s*\w", tail) else "")
        t = t[:m.start()] + mid + tail
    t = re.sub(r"(?<![\d.,])(\d[\d.,]*\d|\d)\s*(?:tr\b|triệu)", lambda m: _millions(m, en), t)
    return re.sub(r"(?<!\w)vs\.?(?=\s|$)", "so với", t)


def scenes_from_script(script: dict, known: set) -> list:
    scenes = []
    for s in script.get("canh") or []:
        lines = [c for c in (s.get("cau") or []) if str(c.get("noi") or "").strip()]
        if not lines:
            continue
        for c in lines:
            if c.get("chi") and c["chi"] not in known:
                raise PackError(f"Kịch bản trỏ tới '{c['chi']}' nhưng báo cáo không còn chỗ đó. Hãy soạn lại kịch bản.")
        text = " ".join(str(c["noi"]).strip() for c in lines)
        scenes.append({"title": str(s.get("muc", "")), "line": text, "chi": [c.get("chi") or "" for c in lines],
                       "sentences": [str(c["noi"]).strip() for c in lines]})
    if not scenes:
        raise PackError("Kịch bản không có câu nào để nói.")
    return scenes


def attach_evidence(scenes: list, record: dict) -> None:
    """Give each spoken sentence the source of each of its figures, taken from the sealed record, so the page can
    show it beside the caption: k = the framed place, b = elsewhere in the report, o = added by the owner."""
    rows = iter(record["cau"])
    for s in scenes:
        s["ev"] = []
        for sentence in s["sentences"]:
            r = next(rows)
            if r["noi"] != sentence:                 # both come from the same script in the same order
                raise PackError("Biên bản và lời nói lệch nhau. Hãy dựng lại.")
            s["ev"].append([[x["so"], "o" if x["nguon"] == "nguoi_soan" else "b" if x["nguon"] == "bao_cao" else "k"]
                            for x in r["so"]])


def ready_answers(mem, places: dict, en: bool, declared: Optional[str], limit: int = 30) -> list:
    """Answered questions kept on the page as ready answers. A figure is the report's only when it sits on the row
    or card the question is about; any other figure is the owner's."""
    out = []
    for e in (mem.bank(limit) if mem else []):
        chi, own = label_place(e.text + " " + e.answer, e.answer, places, en, declared)
        out.append({"cau": e.text, "tra_loi": e.answer, "ngay": e.fields.get("ngay", ""),
                    "so": [{"so": t, "nguon": "nguoi_soan" if t in own else "bao_cao"} for t in numbers(e.answer)]})
    return out


def _blob(obj) -> str:
    """JSON safe inside <script>: no "<" at all, so neither "</script>" nor "<!--" in a report's text can end it."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


CSP_META = re.compile(r"<meta[^>]+http-equiv\s*=\s*[\"']?content-security-policy[^>]*>", re.I)


def inject(source: str, data: dict, record: dict) -> str:
    layer = Path(__file__).with_name("clicky_html_layer.html").read_text(encoding="utf-8")
    blobs = {"__DATA__": _blob(data), "__BIEN_BAN__": _blob(record)}
    layer = re.sub(r"__DATA__|__BIEN_BAN__", lambda m: blobs[m.group(0)], layer)   # one pass: report text is data
    source = CSP_META.sub("<!-- Clicky: thẻ Content-Security-Policy được bỏ để Clicky chạy; file gốc giữ nguyên -->",
                          source)                     # a CSP meta blocks the layer's inline script outright
    scripts = [(x.start(), x.end()) for x in re.finditer(r"<script\b[\s\S]*?</script\s*>", source, re.I)]
    m = None
    for x in re.finditer(r"</body\s*>", source, re.I):
        if not any(a < x.start() < b for a, b in scripts):    # "</body>" written inside a script is not the end
            m = x
    if m is None:
        return source + layer
    return source[:m.start()] + layer + source[m.start():]


FRAMESET = re.compile(r"<frameset|<frame\s", re.I)
JS_RENDER = re.compile(r"innerHTML|document\.write|createElement|appendChild|ReactDOM|createApp|new Vue|\.render\(",
                       re.I)


def _english(text: str) -> bool:
    """A sentence written in English: it uses several common English words."""
    return len(re.findall(r"(?<![\w-])(the|of|and|was|were|is|are|by|to|in|for|with|from|versus|than|due|driven|"
                          r"decline|increase|million|billion|this|that|which|an|on|at|our|we)(?![\w-])", text, re.I)) >= 3


def _nothing_to_say(source: str, items: list, why: str) -> PackError:
    n = {k: sum(1 for i in items if i.kind == k) for k in ("section", "table", "card", "note")}
    seen = (f"Em đọc được {n['section']} phần, {n['table']} bảng, {n['card']} thẻ số, {n['note']} đoạn nhận xét, "
            f"{why}.")
    scripts = " ".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", source, re.I))
    if JS_RENDER.search(scripts):
        return PackError(seen + " Báo cáo này vẽ bảng và số bằng JavaScript lúc mở, nên trong file chưa có số để em "
                                "đọc. Anh mở báo cáo trong Edge, bấm Ctrl+S, chọn «Trang web, hoàn chỉnh», rồi kéo "
                                "thả file vừa lưu vào Clicky.")
    return PackError(seen + " Clicky cần báo cáo có số nằm sẵn trong file: thẻ số, bảng, hoặc đoạn nhận xét có số.")


def _first_caption(root) -> str:
    """An Excel export has no <title> or <h1>: its title is the first cell of words, e.g. "BÁO CÁO CHI PHÍ T10"."""
    for n in root.walk():
        if n.tag in ("td", "th", "p", "div", "caption") and not any(
                d is not n and d.tag in ("td", "th", "p", "div", "table", "tr") for d in n.walk()):
            t = _clean(n.text(), 120)
            if len(t) >= 8 and re.search(r"[^\W\d_]{3}", t) and len(t.split()) >= 2:
                return t
    return ""


def build(report: str, out: Optional[str] = None, owner: Optional[str] = None, contact: Optional[str] = None,
          script_path: Optional[str] = None, label: str = "",
          voice: Optional[Callable[[str], bytes]] = None, memory: Optional[str] = None) -> dict:
    owner = re.sub(r"\s+", " ", owner or "").strip()
    contact = (contact or "").strip()
    if not owner:
        raise PackError("Chưa có tên người chịu trách nhiệm báo cáo (người xem gọi là gì, ví dụ «anh Tuấn»). Anh chạy "
                        "Gan_Clicky_vao_bao_cao.bat, em sẽ hỏi một lần; hoặc thêm --chu \"anh Tuấn\".")
    if not tri_nho.EMAIL.match(contact):
        raise PackError("Chưa có email nhận câu hỏi của người xem" + (f" («{contact}» chưa đúng)" if contact else "")
                        + ". Anh chạy Gan_Clicky_vao_bao_cao.bat, em sẽ hỏi một lần; hoặc thêm --email ten@congty.vn.")
    src_path = Path(report)
    raw = src_path.read_bytes()
    source, encoding = decode_report(raw)
    if 'data-clicky-layer="1"' in source:
        raise PackError("File này đã có Clicky. Hãy chạy trên file báo cáo gốc.")
    if FRAMESET.search(source):
        raise PackError(f"Đây là báo cáo Excel nhiều sheet lưu dạng trang web: số nằm trong thư mục «{src_path.stem}_files». "
                        "Anh kéo thả từng file sheet trong thư mục đó (ví dụ sheet001.htm) vào Clicky.")
    source = utf8_meta(source)
    root = parse(source)
    tagged, items, rows = tag_targets(source, root)
    page_text = root.text() + " " + _labels(root)
    places = make_places(items, rows)
    if not any(figures(p.text) for p in places.values() if p.kind not in ("title", "section")):
        raise _nothing_to_say(source, items, "nhưng không thấy con số nào ngoài tiêu đề")
    en = number_marks([p.text for p in places.values()] + [r.node.text() for r in rows])   # every place: a note too
    declared = declared_unit(page_text)
    places = make_places(items, rows, root, en, declared)
    net_chart = bool(NET_CHART.search(source)) and any(i.kind == "chart" for i in items)
    known = set(places)

    mem = tri_nho.load(memory) if memory else None
    title = next((i.text for i in items if i.kind == "title"), "")
    head_title = re.search(r"<title[^>]*>([^<]*)</title>", source, re.I)
    if head_title:
        import html as _html
        head_title = re.match(r"(.*)", re.sub(r"\s+", " ", _html.unescape(head_title.group(1))))
    period = tri_nho.report_period(src_path.name, title + " " + (head_title.group(1) if head_title else ""))
    to_tell = mem.for_talk(period, src_path.name)[:5] if mem else []
    memo = memory_scene(to_tell, owner, places, en, declared)
    draft = draft_script(items, rows, places, owner, en, declared, chart_series(source), net_chart, memo,
                         head_title.group(1).strip() if head_title else "")
    if not any(figures(c["noi"]) for s in draft["canh"] for c in s["cau"]
               if c.get("chi") not in places or places[c["chi"]].kind not in ("title", "section")):
        raise _nothing_to_say(source, items, "nhưng chưa có câu nào có số để trình bày")
    fresh = dump_script(draft, src_path.name, snapshot(draft, places))
    main_yaml = src_path.with_name(src_path.stem + "_kich_ban_Clicky.yaml")
    nhap = src_path.with_name(src_path.stem + "_kich_ban_nhap.yaml")
    notes = []
    if script_path or main_yaml.exists():
        used = Path(script_path) if script_path else main_yaml
        script = load_script(used)
        if not script_path and unedited(script) and not approved(script):
            main_yaml.write_text(fresh, encoding="utf-8")     # never edited: a fresh draft is safe to write over it
            script, kind, draft_out = load_script(main_yaml), "nhap", main_yaml
        else:
            kind, draft_out = "chu", nhap
            if used.resolve() != nhap.resolve():
                nhap.write_text(fresh, encoding="utf-8")
            else:
                draft_out = None
    else:
        main_yaml.write_text(fresh, encoding="utf-8")
        used, script, kind, draft_out = main_yaml, load_script(main_yaml), "nhap", main_yaml
    if draft.get("chua_ro") and kind == "nhap":
        notes.append(f"{len(draft['chua_ro'])} đoạn em chưa đưa vào bản nháp vì có số trong ngoặc chưa rõ âm hay tỷ "
                     "trọng, số viết bằng chữ, hoặc ký hiệu giọng không đọc được, ví dụ "
                     f"«{draft['chua_ro'][0][:60]}». Anh viết lại câu đó trong kịch bản nếu cần nói.")
    if draft.get("bo_qua"):
        notes.append(f"Phần đề xuất có {len(draft['bo_qua']) + 5} ý, bản nháp chỉ đọc 5 ý đầu; chưa đọc: "
                     + "; ".join(f"«{t[:50]}»" for t in draft["bo_qua"][:3]) + ". Anh thêm vào kịch bản nếu cần.")
    if memo and not any(s.get("tri_nho") for s in script["canh"]):
        script["canh"].insert(1 if script["canh"] else 0, memo)
        notes.append("Đã thêm phần «Câu hỏi lần trước» sau lời chào (anh có thể sửa trong file nháp).")
    moved = reanchor(script, places)
    if moved:
        notes.append(f"{len(moved)} chỗ khoanh đã dời theo chữ vì báo cáo thêm hoặc bớt phần.")
    old = {str(k).strip(): str(v) for k, v in (script.get("so_cho") or {}).items()} \
        if isinstance(script.get("so_cho"), dict) else {}
    now_snap = snapshot(script, places)
    changed = [k for k in old if k in now_snap and now_snap[k] != old[k]]
    by_chi = {str(c.get("chi")): c for s in draft["canh"] for c in s["cau"] if c.get("chi")}
    period_note = ""
    if kind == "chu" and ((old and len(changed) >= 2) or (not old and stale_places(script, places, en) >= 2)):
        # a report of the next period saved under the same name: keep what the owner approved, put in the new
        # figures, and say so first
        before = used.with_name(used.stem + " (kỳ trước)" + used.suffix)
        if before.exists():
            before = used.with_name(used.stem + f" (kỳ trước {dt.datetime.now():%d-%m-%Y %Hh%M})" + used.suffix)
        if not script_path:
            used.rename(before)
        if old:
            script, flipped = next_period(script, old, places, en)
        else:
            script, flipped = json.loads(json.dumps(draft)), []
        redo = []
        for c, want in ranking_issues(script, rows, en):     # "Thứ ba" is another row now: this period's line
            d = by_chi.get(want.id)
            redo.append((str(c.get("noi")), d["noi"] if d else ""))
            c.update(noi=d["noi"] if d else "", chi=want.id, neo=d.get("neo", "") if d else "")
        for c in flipped:                           # its tăng/giảm no longer holds: this period's draft line
            if any(c["noi"] == new for _, new in redo):
                continue
            d = by_chi.get(str(c.get("chi")))
            redo.append((str(c.get("noi")), d["noi"] if d else ""))
            c["noi"] = d["noi"] if d else ""
        script["canh"] = [dict(s, cau=[c for c in s["cau"] if str(c.get("noi") or "").strip()])
                          for s in script["canh"]]
        for s in script["canh"]:
            for c in s["cau"]:
                if c.get("chi") in places:
                    c["neo"] = places[c["chi"]].anchor
        main_yaml.write_text(dump_script(script, src_path.name, snapshot(script, places), draft=False),
                             encoding="utf-8")
        script, used, kind = load_script(main_yaml), main_yaml, "ky_moi"
        period_note = (f"Đây là báo cáo kỳ mới ({len(changed) or 'nhiều'} chỗ đổi số). Em giữ lời anh đã duyệt và thay "
                       f"số mới vào đúng chỗ; kịch bản cũ giữ ở «{before.name}».")
        if redo:
            period_note += (f" {len(redo)} câu đổi nghĩa (tăng thành giảm, hoặc đổi thứ hạng), anh đọc lại: "
                            + "; ".join(f"«{a[:50]}» → " + (f"«{b[:60]}»" if b else "bỏ") for a, b in redo[:3]) + ".")
        notes.insert(0, period_note)
    ranked = ranking_issues(script, rows, en)
    for c, want in ranked[:3]:
        d = by_chi.get(want.id)
        notes.append(f"Thứ hạng đã đổi: câu «{str(c.get('noi'))[:60]}» nhưng bây giờ dòng đứng ở thứ hạng đó là "
                     f"«{want.label}»." + (f" Câu nháp mới: «{d['noi']}»." if d else ""))
    scenes = scenes_from_script(script, known)
    ok = approved(script)
    reset = ok and bool(changed) and kind == "chu"
    status = ("SƠ BỘ: báo cáo kỳ mới, số mới đã thay vào lời đã duyệt" if kind == "ky_moi"
              else "SƠ BỘ: báo cáo đổi số sau khi duyệt" if reset
              else "SƠ BỘ: thứ tự chênh lệch đã đổi" if ok and ranked
              else "CHÍNH THỨC: kịch bản đã được duyệt" if ok else "SƠ BỘ: kịch bản nháp chưa duyệt" if kind == "nhap"
              else "SƠ BỘ: kịch bản đã sửa nhưng chưa ghi duyệt")
    if reset:
        notes.insert(0, f"Báo cáo đã đổi số ở {len(changed)} chỗ sau khi anh duyệt kịch bản ("
                     + ", ".join(f"«{k[:30]}»" for k in changed[:3]) + "). Em chuyển về SƠ BỘ; anh đọc lại rồi đổi "
                     "da_duyet thành 'có' lần nữa.")
    now = dt.datetime.now()
    meta = {"owner": owner, "contact": contact, "label": label, "report": src_path.name,
            "report_sha256": hashlib.sha256(raw).hexdigest(), "built": now.strftime("%d/%m/%Y %H:%M"),
            "built_iso": now.isoformat(timespec="seconds"), "trang_thai": status, "dau_so": "en" if en else "vn",
            "title": title or (head_title.group(1).strip() if head_title else "") or _first_caption(root)}
    earlier = {}                               # the figures each place showed when the script was written
    for sc in script.get("canh") or []:
        for c in sc.get("cau") or []:
            t = old.get(str(c.get("neo") or "").strip())
            if t and kind == "chu":
                earlier[str(c.get("chi"))] = {_tok_value(x, en) for x in t.split()} - {None}
    try:                                       # before the voice: a wrong figure costs no waiting
        record = make_record(script, places, page_text, meta, en, declared, earlier)
    except PackError as exc:
        raise PackError(f"{exc}\nKịch bản đang dùng: {used}." + (f"\n{period_note}" if period_note else "")) from None
    if kind == "chu" and used.suffix.lower() in (".yaml", ".yml") and (reset or now_snap != old):
        write_snap(used, now_snap, reset=reset)  # the owner reads this report now: these are the figures he sees
    if not any(r["so"] for r in record["cau"] if r["chi"] not in places
               or places[r["chi"]].kind not in ("title", "section")):
        raise PackError("Bài trình bày chưa nói con số nào trong báo cáo. Anh thêm ít nhất một câu có số vào kịch bản "
                        f"({used}) rồi chạy lại.")
    bank = ready_answers(mem, places, en, declared)
    if bank:
        record["tra_loi_san"] = bank
        record["ma_bam"] = seal(record)
    attach_evidence(scenes, record)

    # warnings the owner should read: a part that says no figure, a chart that needs the internet
    spoken = {r["chi"] for r in record["cau"] if r["so"]}
    silent = [re.sub(r"\s+", " ", p.anchor[:60]).strip().rstrip(".:") for sid, p in places.items() if p.kind == "section"
              and not any(places[c].section == sid and places[c].kind != "section" for c in spoken if c in places)]
    if silent:
        notes.append(f"{len(silent)} phần chưa có câu nào nói số: " + ", ".join(f"«{t}»" for t in silent[:4])
                     + (" …" if len(silent) > 4 else "") + ". Anh thêm câu vào kịch bản nếu cần nói.")
    english = [r["noi"] for r in record["cau"] if _english(r["noi"])]
    if english:
        notes.append(f"{len(english)} câu viết bằng tiếng Anh, giọng tiếng Việt đọc sẽ khó nghe, ví dụ «{english[0][:60]}». "
                     "Anh nên viết lại bằng tiếng Việt trong kịch bản.")
    if "\ufffd" in source and encoding == "utf-8":
        notes.append(f"Báo cáo có {source.count(chr(0xFFFD))} ký tự hỏng (lỗi mã hóa khi lưu); em vẫn đọc bằng UTF-8. "
                     "Anh xem lại chữ quanh dấu � trên bản trình bày.")
    if re.search(r"<meta[^>]+http-equiv\s*=\s*[\"']?content-security-policy", source, re.I):
        notes.append("Báo cáo có thẻ Content-Security-Policy chặn mã trong trang: em bỏ thẻ đó trong bản trình bày "
                     "để Clicky chạy được (file gốc giữ nguyên).")
    if net_chart:
        notes.append("Biểu đồ trong báo cáo tải thư viện vẽ từ Internet: người xem không có mạng sẽ thấy ô trống, nên "
                     "Clicky không nói về biểu đồ.")
    if record["tong"]["cho_khac_trong_bao_cao"]:
        notes.append(f"{record['tong']['cho_khac_trong_bao_cao']} con số anh đã xác nhận nằm ở chỗ khác chỗ khoanh.")

    say = voice or offline_voice()
    cache: dict = {}

    def clip(text, sentences=None):
        key = "\n".join(sentences) if sentences else text
        if key not in cache:
            wav, starts = voice_lines([for_voice(x, en) for x in (sentences or [text])], say, en)
            mp3, secs = wav_to_mp3(wav)
            if starts is None:                     # no timings from the voice: spread by length
                total, starts, pos = sum(len(x) + 1 for x in (sentences or [text])), [], 0
                for x in (sentences or [text]):
                    starts.append(round(pos / max(1, total) * secs, 3))
                    pos += len(x) + 1
            cache[key] = {"audio": base64.b64encode(mp3).decode("ascii"), "secs": secs, "mime": audio_mime(mp3),
                          "starts": starts}
        return cache[key]

    def timed(s):
        c = clip(None, s["sentences"])
        # the frame appears just before Clicky says the sentence, never after
        s["points"] = [{"id": chi, "t": max(0.0, round(c["starts"][k] - LEAD, 3))}
                       for k, chi in enumerate(s.pop("chi")) if chi]
        return c

    for s in scenes:
        s.update(timed(s))
    who = owner[:1].upper() + owner[1:]
    fallback = f"Câu này em chưa tìm thấy trong báo cáo. Em ghi lại để {owner} trả lời anh chị."
    meta["minutes"] = round(sum(s["secs"] + 0.8 for s in scenes) / 60, 1)
    meta["pack_id"] = meta["report_sha256"][:16]
    meta["record"] = dict(record["tong"], ma=record["ma_bam"][:12])
    meta["canh_bao"] = notes
    data = {"meta": meta, "scenes": scenes,
            "cards": [{"id": it.node.attrs["data-clicky"], "text": it.text, "label": card_label(it.node)}
                      for it in items if it.kind == "card"],
            "fallback": fallback, "fallback_clip": clip(fallback), "aliases": doc_tai_chinh.aliases(),
            "bank": [dict(clip(f"{who} đã trả lời: {_sentence(b['tra_loi'])}"),
                          q=b["cau"], a=b["tra_loi"], ngay=b["ngay"]) for b in bank]}
    page = inject(tagged, data, record)
    out_path = Path(out) if out else src_path.with_name(src_path.stem + "_Clicky.html")
    if out_path.resolve() == src_path.resolve():
        raise PackError("Không ghi đè file báo cáo gốc.")
    out_path.write_text(page, encoding="utf-8")
    told = []
    if mem:
        heard = tri_nho.fold(" ".join(str(c.get("noi")) for s in script["canh"] if s.get("tri_nho")
                                      for c in s.get("cau") or []))
        told = [e for e in to_tell if tri_nho.fold(e.text) in heard]
        tri_nho.mark_said(period, told, memory)
    return {"out": str(out_path), "draft": str(draft_out) if draft_out else None, "kich_ban": str(used),
            "trang_thai": status, "canh_bao": notes, "dau_so": meta["dau_so"], "ma_hoa": encoding,
            "scenes": len(scenes), "minutes": meta["minutes"], "bytes": len(page.encode("utf-8")),
            "record": meta["record"], "doi_neo": moved,
            "xem_lai": [{"cau": r["noi"], "so": [x["so"] for x in r["so"] if x["nguon"] == "bao_cao"]}
                        for r in record["cau"] if any(x["nguon"] == "bao_cao" for x in r["so"])],
            "found": {k: sum(1 for i in items if i.kind == k) for k in ("section", "note", "card", "table", "chart")},
            "tri_nho": None if not mem else {
                "thu_muc": str(mem.path), "ky": period, "doc_dau": len(told), "tra_loi_san": len(bank),
                "cau_chua_tra_loi": [e.text for e in mem.open_questions()],
                "hua_chua_xong": [e.text + (f" (hạn {e.fields['han']})" if e.fields.get("han") else "")
                                  for e in mem.open_promises()],
                "bai_hoc": mem.lessons}}


def todo_list(rep: dict) -> list:
    """What the owner should do, most important first: a new period or a changed report, then reading the draft."""
    todo = rep["canh_bao"][:]
    if rep["trang_thai"].startswith("SƠ BỘ"):
        first = 1 if todo and todo[0].startswith(("Đây là báo cáo kỳ mới", "Báo cáo đã đổi số")) else 0
        todo.insert(first, f"Đọc và sửa lời trong «{Path(rep['kich_ban']).name}», đổi da_duyet thành 'có', rồi kéo "
                           "thả báo cáo vào Clicky lần nữa.")
    return todo


def summary(rep: dict) -> str:
    """Three plain lines for the console: pass or not, what to look at, where the file is."""
    r = rep["record"]
    first = (f"ĐẠT ({rep['trang_thai']}). {rep['scenes']} phần, khoảng {str(rep['minutes']).replace('.', ',')} phút, "
             f"{r['so']} con số đều khớp với báo cáo.")
    todo = todo_list(rep)
    second = "Cần xem: " + " ".join(todo) if todo else "Không có gì cần sửa."
    return "\n".join([first, second, f"File: {rep['out']}"])


def set_aside(out_path: Path, why: str = "bản cũ") -> Optional[Path]:
    """A failed build must not leave last time's "<tên>_Clicky.html" looking like today's result: it becomes
    "<tên>_Clicky (bản cũ).html"."""
    if not out_path.is_file():
        return None
    to = out_path.with_name(f"{out_path.stem} ({why}){out_path.suffix}")
    if to.exists():
        to = out_path.with_name(f"{out_path.stem} ({why} {dt.datetime.fromtimestamp(out_path.stat().st_mtime):%d-%m-%Y %Hh%M})"
                                f"{out_path.suffix}")
    out_path.replace(to)
    return to


def dropped(argv: list, cmdline: Optional[str]) -> list:
    """The files dropped on the launcher. Explorer leaves a path without spaces unquoted, so "P&L_T10.html" splits
    at "&" before the .bat sees it; the whole command line (%cmdcmdline%) still has it."""
    paths = [a for a in argv if a]
    if cmdline:
        m = re.search(r"\.bat\"?\s*(.*)$", cmdline, re.I | re.S)
        if m:
            rest = m.group(1).strip()
            rest = rest[:-1] if rest.endswith('"') and rest.count('"') % 2 else rest
            whole = [a.strip('"') for a in re.findall(r'"[^"]*"|[^\s"]+', rest)]
            if whole and (not paths or sum(Path(a).exists() for a in whole) > sum(Path(a).exists() for a in paths)):
                paths = whole
    return paths


def run_drop(argv=None, cmdline: Optional[str] = None, ask=input, opener=None, look: bool = True) -> int:
    """What the launcher does after the files are dropped: owner, build, re-check, look at the page, open it.
    The console gets exactly three lines (pass or not, what to look at, which file); everything else goes to
    "<tên>_nhat_ky.txt" beside the report."""
    import contextlib
    import io
    argv = list(sys.argv[1:] if argv is None else argv)
    mem = os.environ.get("CLICKY_TRI_NHO") or None
    paths = dropped(argv, cmdline if cmdline is not None else os.environ.get("CLICKY_CMDLINE"))
    kb = next((a for a in paths if a.lower().endswith((".yaml", ".yml"))), None)
    report = next((a for a in paths if not a.lower().endswith((".yaml", ".yml"))), None)
    if not report:
        print("KHÔNG ĐẠT: chưa có file báo cáo.\nCần làm: kéo thả file báo cáo .html vào biểu tượng Clicky (có thể "
              "kéo kèm file kịch bản .yaml đã sửa).\nFile: chưa có.")
        return 1
    if not Path(report).is_file():
        print(f"KHÔNG ĐẠT: không tìm thấy file «{report}».\nCần làm: nếu tên file có dấu & hay ký tự lạ, anh đổi tên "
              "(chỉ giữ chữ, số, dấu cách, gạch ngang) rồi kéo thả lại.\nFile: chưa có.")
        return 1
    src = Path(report)
    out = src.with_name(src.stem + "_Clicky.html")
    log_path = src.with_name(src.stem + "_nhat_ky.txt")
    try:
        name, mail = tri_nho.ask_owner(mem, ask=ask)
    except (EOFError, KeyboardInterrupt):
        print("\nKHÔNG ĐẠT: chưa có tên và email người chịu trách nhiệm báo cáo.\nCần làm: chạy lại và gõ tên, email "
              f"khi em hỏi (hoặc điền file {tri_nho.folder(mem) / tri_nho.CHU}).\nFile: chưa có.")
        return 1
    log, console = io.StringIO(), sys.stdout
    log.write(f"Clicky {dt.datetime.now():%d/%m/%Y %H:%M}\nBáo cáo: {src}\nTrí nhớ: {tri_nho.folder(mem)}\n"
              f"Người chịu trách nhiệm: {name} <{mail}>\n\n")

    def finish(lines, code):
        log.write("\n".join(lines) + "\n")
        try:
            log_path.write_text(log.getvalue(), encoding="utf-8")
        except OSError:
            pass
        print("\n".join(lines), file=console)
        return code

    def fail(why, aside=None):
        moved = set_aside(out, aside) if aside else None
        log.write(f"KHÔNG ĐẠT:\n{why}\n" + (f"Bản trình bày lần trước đã đổi tên thành «{moved.name}» để không gửi "
                                            "nhầm.\n" if moved else ""))
        parts = why.strip().splitlines()
        first = parts[0] + (" " + parts[1].strip(" -") if parts[0].endswith(":") and len(parts) > 1 else "")
        return finish(["KHÔNG ĐẠT: chưa có bản trình bày mới để gửi.",
                       "Cần sửa: " + first[:300] + (" (chi tiết trong nhật ký)" if len(parts) > 2 or len(first) > 300
                                                     else ""),
                       f"Nhật ký: {log_path}"], 1)

    with contextlib.redirect_stdout(log):
        try:
            rep = build(str(src), None, name, mail, kb, memory=mem)
        except PackError as exc:
            return fail(str(exc), "bản cũ")
        print(summary(rep))
        extra = remind(rep)
        if extra:
            print(extra)
        from present import kiem_bien_ban
        res = kiem_bien_ban.verify(str(out), str(src))
        if not res["ok"]:
            return fail("Kiểm lại biên bản: " + "; ".join(res["errors"]), "chưa đạt")
        print(f"Kiểm lại biên bản: ĐẠT, {res['tong']['so']} con số.")
        seen = "chưa kiểm hình"
        if look:
            from present import kiem_hinh
            code = kiem_hinh.main([str(out)] + (["--tri-nho", mem] if mem else []))
            if code == 1:
                return fail("Kiểm hình: Clicky che số hoặc khoanh sai chỗ trên một số màn hình (xem nhật ký).",
                            "chưa đạt")
            seen = "đã kiểm hình" if code == 0 else "chưa kiểm hình được, anh mở file xem vài chỗ khoanh"
    todo = todo_list(rep)
    r = rep["record"]
    lines = [f"ĐẠT ({rep['trang_thai']}): {r['so']} con số khớp báo cáo, {seen}.",
             "Cần xem: " + (todo[0][:300] + (f" (và {len(todo) - 1} việc nữa trong nhật ký)" if len(todo) > 1 else "")
                            if todo else "không có gì cần sửa."),
             f"File: {out}"]
    code = finish(lines, 0)
    opener = opener if opener is not None else getattr(os, "startfile", None)
    if opener:
        try:
            opener(str(out))
        except OSError:
            pass
    return code


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--tu-bat"]:
        return run_drop(argv[1:])
    ap = argparse.ArgumentParser(description="Gắn Clicky vào báo cáo HTML: trình bày bằng giọng, khoanh đúng chỗ, hỏi–đáp.")
    ap.add_argument("bao_cao", help="file báo cáo .html (không bị sửa)")
    ap.add_argument("--ra", help="file kết quả, mặc định <tên>_Clicky.html")
    ap.add_argument("--kich-ban", help="kịch bản đã sửa (YAML); bỏ trống thì dùng <tên>_kich_ban_Clicky.yaml nếu có")
    ap.add_argument("--chu", help="người chịu trách nhiệm, như người xem gọi (anh Tuấn); mặc định lấy từ CHU_BAO_CAO.txt")
    ap.add_argument("--email", help="email nhận câu hỏi; mặc định lấy từ CHU_BAO_CAO.txt")
    ap.add_argument("--nhan", default="")
    ap.add_argument("--tri-nho", default=os.environ.get("CLICKY_TRI_NHO") or None,
                    help="thư mục trí nhớ (câu treo, sếp hay hỏi, bài học); mặc định CLICKY_TRI_NHO")
    a = ap.parse_args(argv)
    name, mail = tri_nho.owner_info(a.tri_nho)
    try:
        rep = build(a.bao_cao, a.ra, a.chu or name, a.email or mail, a.kich_ban, a.nhan, memory=a.tri_nho)
    except PackError as exc:
        src = Path(a.bao_cao)
        moved = set_aside(Path(a.ra) if a.ra else src.with_name(src.stem + "_Clicky.html"))
        print(f"KHÔNG ĐẠT. {exc}", file=sys.stderr)
        print("Chưa ghi file trình bày mới. Sửa chỗ trên rồi kéo thả báo cáo vào Clicky lần nữa."
              + (f" Bản lần trước đã đổi tên thành «{moved.name}» để không gửi nhầm." if moved else ""), file=sys.stderr)
        return 2
    print(summary(rep))
    extra = remind(rep)
    if extra:
        print(extra)
    return 0


def remind(rep: dict) -> str:
    """What the owner should know before sending, in plain words."""
    m = rep.get("tri_nho")
    if not m:
        return ""
    lines = [f"Trí nhớ ({m['thu_muc']}): đọc {m['doc_dau']} câu trả lời ngay sau lời chào, "
             f"giữ {m['tra_loi_san']} câu trả lời sẵn để ai hỏi lại thì đáp ngay."]
    if m["cau_chua_tra_loi"]:
        lines.append(f"Còn {len(m['cau_chua_tra_loi'])} câu treo anh chưa trả lời:")
        lines += [f"  ? {q}" for q in m["cau_chua_tra_loi"][:10]]
    if m["hua_chua_xong"]:
        lines.append("Lời hứa chưa xong:")
        lines += [f"  * {x}" for x in m["hua_chua_xong"][:10]]
    if m["bai_hoc"]:
        lines.append("Nhắc từ các kỳ trước:")
        lines += [f"  - {x}" for x in m["bai_hoc"][:10]]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
