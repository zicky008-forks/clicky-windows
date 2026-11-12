"""Put Clicky inside an HTML report the owner already shares (SharePoint/OneDrive link by e-mail).

Run on the owner's PC. Nothing is sent anywhere and no AI is used:
1. The report is read with the standard library parser. Sections (h2, else h3), the author's own notes
   (callouts, conclusion paragraphs), number cards (KPI tiles), tables and charts are found.
2. A talk is drafted from the report's OWN words and numbers: every sentence Clicky says is text that is
   already on the page, so no figure is computed or invented here. The draft is written next to the report as
   YAML; the owner edits it and builds again (--kich-ban).
3. A new file "<name>_Clicky.html" is written: the original page, each target tagged with data-clicky="…",
   plus Clicky's layer (bar, frames, nón lá pointer, captions, "Hỏi Clicky"). The original file is never touched.
4. The voice is the offline Vietnamese voice; clips are embedded, so the page plays offline.
5. Number lock: a sentence may only say a figure that is written in the report. A figure the owner typed
   himself must be listed in that sentence's "so_rieng", and is shown as his. Anything else stops the build.
6. A machine-readable record ("biên bản") is embedded: every spoken figure, where it is in the report, the
   report's SHA-256 and a seal over the record. present/kiem_bien_ban.py re-checks it from the output file.

On the page, a reader can also ask about any table cell ("chi phí lương khối bán hàng AOPR?"), or simply click a
figure: Clicky finds the row and the column by their labels, frames the real cell and reads it out. Questions it
cannot place are kept and e-mailed to the owner only when the reader presses Send.
While Clicky talks, the rest of the page fades, and each figure in the caption shows its source from the record
(the framed place, elsewhere in the report, or added by the owner). present/kiem_hinh.py opens the result in a
headless browser and checks that nothing of Clicky covers a figure.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

import yaml

from present import doc_tai_chinh, tri_nho
from present.goi_html import PackError, audio_mime, offline_voice, wav_to_mp3

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
        "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template"}
NOTE_CLASS = re.compile(r"insight|callout|note|highlight|conclusion|ket-?luan|nhan-?xet|takeaway|alert|key-?point|"
                        r"summary|message|thong-?diep", re.I)
CARD_CLASS = re.compile(r"kpi|metric|stat|tile|card|figure-box|so-lieu", re.I)
NOTE_START = re.compile(r"^\s*(kết luận|nhận xét|đề xuất|thông điệp|lưu ý|insight|điểm chính|tóm tắt|key)", re.I)
HAS_NUMBER = re.compile(r"\d")
CHART_CLASS = re.compile(r"chart|plot|graph|bieu-?do|echarts|apex|highcharts|plotly", re.I)
NUM_TOKEN = re.compile(r"\d[\d.,]*")
LABEL_ATTRS = ("aria-label", "alt", "title")
RECORD_VERSION = 1


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


def card_text(n: Node) -> str:
    """'Tổng chi phí: 186,4 tỷ, tăng 12,6% so với AOP gốc.' from label / value / delta children."""
    parts = [c.text() for c in n.children if c.text()]
    if len(parts) < 2:
        return n.text()
    rest = [re.sub(r"^\+\s*", "tăng ", re.sub(r"^[-−]\s*(?=\d)", "giảm ", p)) for p in parts[1:]]
    return parts[0].rstrip(":") + ": " + ", ".join(rest)


def _is_note(n: Node) -> bool:
    if n.tag in ("blockquote", "aside"):
        return True
    if n.tag in ("p", "div", "li") and NOTE_CLASS.search(n.cls) and 20 <= len(n.text()) <= 1200:
        return not n.has_ancestor(lambda p: NOTE_CLASS.search(p.cls) and p.tag in ("p", "div", "li"))
    return n.tag == "p" and bool(NOTE_START.search(n.text()))


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


def outline(root: Node) -> list:
    """Items in reading order. A heading level splits sections: h2 if the report has any, else h3."""
    nodes = list(root.walk())
    level = "h2" if any(n.tag == "h2" for n in nodes) else "h3"
    items: list = []
    title = next((n for n in nodes if n.tag == "h1"), None)
    if title is not None:
        items.append(Item("title", title, _clean(title.text(), 160)))
    seen_inside: set = set()
    for n in nodes:
        if id(n) in seen_inside:
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
        if kind is None:
            continue
        text = card_text(n) if kind == "card" else n.text()
        if kind == "chart":
            text = next((d.attrs.get("aria-label") or d.attrs.get("alt") or d.attrs.get("title")
                         for d in n.walk() if d.attrs.get("aria-label") or d.attrs.get("alt") or d.attrs.get("title")),
                        "")
        items.append(Item(kind, n, _clean(text, 160 if kind in ("section", "card") else 400)))
        for d in n.walk():
            seen_inside.add(id(d))
    return items


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…:" else text + "."


def draft_script(items: list, owner: str, max_cards: int = 4) -> dict:
    """A talk made of the report's own words: one scene per section, the author's notes first, then up to
    max_cards number cards, then the table or chart to look at. Owner edits the YAML afterwards."""
    scenes, current = [], None
    title = next((i for i in items if i.kind == "title"), None)
    intro = f"Kính chào anh chị. Em là Clicky, trợ lý AI của {owner}."
    if title:
        intro += f" Em xin trình bày {title.text.rstrip('.')}."

    def new(head: Optional[Item]):
        return {"muc": head.text if head else "Tổng quan",
                "cau": ([{"noi": f"Phần {head.text.rstrip('.')}.", "chi": head.node.attrs['data-clicky']}]
                        if head else []), "cards": 0, "seen": False}

    current = new(None)
    for it in items:
        if it.kind == "title":
            continue
        k = it.node.attrs["data-clicky"]
        if it.kind == "section":
            if current["cau"]:
                scenes.append(current)
            current = new(it)
        elif it.kind == "note" and it.text:
            current["cau"].append({"noi": _sentence(it.text), "chi": k})
        elif it.kind == "card" and current["cards"] < max_cards:
            current["cau"].append({"noi": _sentence(it.text), "chi": k})
            current["cards"] += 1
        elif it.kind in ("table", "chart") and not current["seen"]:
            if it.kind == "table":
                line = "Chi tiết ở bảng này. Anh chị có thể hỏi em bất kỳ ô nào trong bảng."
            elif it.text:
                line = f"Biểu đồ này cho thấy {it.text.rstrip('.')}."
            else:
                line = "Anh chị xem thêm biểu đồ này."
            current["cau"].append({"noi": line, "chi": k})
            current["seen"] = True
    if current["cau"]:
        scenes.append(current)
    for s in scenes:
        s.pop("cards"), s.pop("seen")
    if scenes:
        scenes[0]["cau"].insert(0, {"noi": intro, "chi": (title.node.attrs["data-clicky"] if title else "")})
        scenes[-1]["cau"].append({"noi": f"Em xin hết phần trình bày. Câu nào em chưa trả lời được, em ghi lại "
                                         f"để {owner} phản hồi anh chị.", "chi": ""})
    return {"ghi_chu": "Bản nháp do Clicky soạn từ chính chữ và số trong báo cáo. Anh sửa câu 'noi', giữ 'chi' "
                       "(chỗ Clicky khoanh), rồi chạy lại với --kich-ban.", "canh": scenes}


def tag_targets(source: str, root: Node) -> tuple:
    """Give every candidate element a stable data-clicky id and return (new source, items)."""
    items = outline(root)
    for i, it in enumerate(items, 1):
        it.node.attrs["data-clicky"] = f"c{i}"
    # tables: tag every row too, so a question can frame the row or the cell
    inserts = [(it.node.start, it.node.tag, it.node.attrs["data-clicky"]) for it in items]
    for it in items:
        if it.kind != "table":
            continue
        rows = [n for n in it.node.walk() if n.tag == "tr"]
        for r, row in enumerate(rows):
            rid = f"{it.node.attrs['data-clicky']}r{r}"
            row.attrs["data-clicky"] = rid
            inserts.append((row.start, "tr", rid))
    out = source
    for start, tag, cid in sorted(inserts, key=lambda x: -x[0]):
        at = start + 1 + len(tag)
        if out[start:at].lower() != "<" + tag:
            raise PackError(f"Không gắn được dấu vào thẻ <{tag}> ở vị trí {start}.")
        out = out[:at] + f' data-clicky="{cid}"' + out[at:]
    return out, items


# --- number lock and record ------------------------------------------------------------------------------

def numbers(text: str) -> list:
    """Figures as written (186,4 · 12,6 · 2.300), without a sentence's closing dot or comma."""
    return [t for t in (m.rstrip(".,") for m in NUM_TOKEN.findall(text or "")) if t]


def _labels(n: Node) -> str:
    return " ".join(d.attrs[a] for d in n.walk() for a in LABEL_ATTRS if d.attrs.get(a))


def target_texts(root: Node) -> dict:
    """What a reader can read at each tagged place: its text plus chart labels (aria-label, alt, title)."""
    return {n.attrs["data-clicky"]: n.text() + " " + _labels(n) for n in root.walk() if n.attrs.get("data-clicky")}


def page_numbers(root: Node) -> set:
    return set(numbers(root.text() + " " + _labels(root)))


def seal(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "ma_bam"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def make_record(script: dict, root: Node, meta: dict) -> dict:
    """Check every spoken figure against the report and write down where it is. Raises on a figure that is
    neither in the report nor declared by the owner in "so_rieng"."""
    texts, on_page = target_texts(root), page_numbers(root)
    rows, bad = [], []
    for s in script.get("canh") or []:
        for c in s.get("cau") or []:
            noi = str(c.get("noi") or "").strip()
            if not noi:
                continue
            chi = c.get("chi") or ""
            own = {str(x).strip() for x in (c.get("so_rieng") or [])}
            here = set(numbers(texts.get(chi, "")))
            found = []
            for tok in numbers(noi):
                if tok in here:
                    found.append({"so": tok, "nguon": chi})
                elif tok in own:                     # declared by the owner: his, even if the page has it too
                    found.append({"so": tok, "nguon": "nguoi_soan"})
                elif tok in on_page:
                    found.append({"so": tok, "nguon": "bao_cao"})
                else:
                    bad.append((noi, tok))
            rows.append({"canh": s.get("muc", ""), "noi": noi, "chi": chi, "so": found})
    if bad:
        shown = "; ".join(f"số {tok} trong câu «{noi[:70]}»" for noi, tok in bad[:5])
        more = f" (và {len(bad) - 5} chỗ nữa)" if len(bad) > 5 else ""
        raise PackError(f"Lời nói có số không có trong báo cáo: {shown}{more}. Hãy sửa cho đúng số trong báo cáo. "
                        "Nếu đó là số anh chủ động thêm, hãy ghi nó vào dòng so_rieng của câu đó.")
    flat = [x["nguon"] for r in rows for x in r["so"]]
    record = {"phien_ban": RECORD_VERSION, "bao_cao": meta["report"], "sha256": meta["report_sha256"],
              "lap_luc": meta["built_iso"], "nguoi_chiu_trach_nhiem": meta["owner"], "cau": rows,
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
            data = voiced(doc_tai_chinh.doc(piece, en_numbers))
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
        scenes.append({"title": s.get("muc", ""), "line": text, "chi": [c.get("chi") or "" for c in lines],
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


def memory_scene(mem, report: str, owner: str, on_page: set) -> tuple:
    """A short scene that answers last time's parked questions, read once per report. Figures in the owner's
    answers that are not on this page are recorded as his ("nguoi_soan")."""
    items = mem.for_talk(report)[:5] if mem else []
    if not items:
        return None, []
    who = owner[:1].upper() + owner[1:]
    own = lambda text: [t for t in numbers(text) if t not in on_page]
    cau = [{"noi": "Trước hết, em xin trả lời các câu anh chị đã hỏi lần trước.", "chi": ""}]
    for e in items:
        q, a = _sentence(e.text), _sentence(e.answer)
        cau += [{"noi": f"Câu hỏi: {q}", "chi": "", "so_rieng": own(q)},
                {"noi": f"{who} trả lời: {a}", "chi": "", "so_rieng": own(a)}]
    return {"muc": "Trả lời câu hỏi lần trước", "cau": cau}, items


def ready_answers(mem, on_page: set, limit: int = 30) -> list:
    """Answered questions kept on the page as ready answers, each figure with its source."""
    out = []
    for e in (mem.bank(limit) if mem else []):
        out.append({"cau": e.text, "tra_loi": e.answer, "ngay": e.fields.get("ngay", ""),
                    "so": [{"so": t, "nguon": "bao_cao" if t in on_page else "nguoi_soan"} for t in numbers(e.answer)]})
    return out


def _blob(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def inject(source: str, data: dict, record: dict) -> str:
    layer = Path(__file__).with_name("clicky_html_layer.html").read_text(encoding="utf-8")
    layer = layer.replace("__DATA__", _blob(data)).replace("__BIEN_BAN__", _blob(record))
    m = None
    for m in re.finditer(r"</body\s*>", source, re.I):
        pass
    if m is None:
        return source + layer
    return source[:m.start()] + layer + source[m.start():]


def build(report: str, out: Optional[str] = None, owner: str = "anh Tuấn", contact: str = "",
          script_path: Optional[str] = None, label: str = "",
          voice: Optional[Callable[[str], bytes]] = None, memory: Optional[str] = None) -> dict:
    src_path = Path(report)
    raw = src_path.read_bytes()
    source = raw.decode("utf-8-sig", errors="replace")
    if 'data-clicky-layer="1"' in source:
        raise PackError("File này đã có Clicky. Hãy chạy trên file báo cáo gốc.")
    root = parse(source)
    tagged, items = tag_targets(source, root)
    known = {it.node.attrs["data-clicky"] for it in items}
    draft = draft_script(items, owner)
    draft_path = src_path.with_name(src_path.stem + "_kich_ban_Clicky.yaml")
    if script_path:
        script = yaml.safe_load(Path(script_path).read_text(encoding="utf-8"))
    else:
        draft_path.write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=110),
                              encoding="utf-8")
        script = draft
    mem = tri_nho.load(memory) if memory else None
    on_page = page_numbers(root)
    extra, told = memory_scene(mem, src_path.name, owner, on_page)
    if extra:                                          # after the greeting scene, before the first section
        script = dict(script, canh=list(script.get("canh") or []))
        script["canh"].insert(1 if script["canh"] else 0, extra)
    scenes = scenes_from_script(script, known)
    now = dt.datetime.now()
    meta = {"owner": owner, "contact": contact, "label": label, "report": src_path.name,
            "report_sha256": hashlib.sha256(raw).hexdigest(), "built": now.strftime("%d/%m/%Y %H:%M"),
            "built_iso": now.isoformat(timespec="seconds")}
    record = make_record(script, root, meta)          # before the voice: a wrong figure costs no waiting
    bank = ready_answers(mem, on_page)
    if bank:
        record["tra_loi_san"] = bank
        record["ma_bam"] = seal(record)
    attach_evidence(scenes, record)
    say = voice or offline_voice()
    cache: dict = {}

    def clip(text, sentences=None):
        key = "\n".join(sentences) if sentences else text
        if key not in cache:
            wav, starts = voice_lines(sentences or [text], say)
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
    fallback = f"Câu này em chưa tìm thấy trong báo cáo. Em ghi lại để {owner} trả lời anh chị."
    meta["minutes"] = round(sum(s["secs"] + 0.8 for s in scenes) / 60, 1)
    meta["pack_id"] = meta["report_sha256"][:16]
    meta["record"] = dict(record["tong"], ma=record["ma_bam"][:12])
    data = {"meta": meta, "scenes": scenes,
            "cards": [{"id": it.node.attrs["data-clicky"], "text": it.text} for it in items if it.kind == "card"],
            "fallback": fallback, "fallback_clip": clip(fallback), "aliases": doc_tai_chinh.aliases(),
            "bank": [dict(clip(f"{owner[:1].upper() + owner[1:]} trả lời: {_sentence(b['tra_loi'])}"),
                          q=b["cau"], a=b["tra_loi"], ngay=b["ngay"]) for b in bank]}
    page = inject(tagged, data, record)
    out_path = Path(out) if out else src_path.with_name(src_path.stem + "_Clicky.html")
    if out_path.resolve() == src_path.resolve():
        raise PackError("Không ghi đè file báo cáo gốc.")
    out_path.write_text(page, encoding="utf-8")
    if mem:
        tri_nho.mark_said(src_path.name, told, memory)
    return {"out": str(out_path), "draft": None if script_path else str(draft_path), "scenes": len(scenes),
            "minutes": meta["minutes"], "bytes": len(page.encode("utf-8")), "record": meta["record"],
            # figures found in the report but not at the framed place: weaker evidence, the owner should look
            "xem_lai": [{"cau": r["noi"], "so": [x["so"] for x in r["so"] if x["nguon"] == "bao_cao"]}
                        for r in record["cau"] if any(x["nguon"] == "bao_cao" for x in r["so"])],
            "found": {k: sum(1 for i in items if i.kind == k) for k in ("section", "note", "card", "table", "chart")},
            "tri_nho": None if not mem else {
                "thu_muc": str(mem.path), "doc_dau": len(told), "tra_loi_san": len(bank),
                "cau_chua_tra_loi": [e.text for e in mem.open_questions()],
                "hua_chua_xong": [e.text + (f" (hạn {e.fields['han']})" if e.fields.get("han") else "")
                                  for e in mem.open_promises()],
                "bai_hoc": mem.lessons}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gắn Clicky vào báo cáo HTML: trình bày bằng giọng, khoanh đúng chỗ, hỏi–đáp.")
    ap.add_argument("bao_cao", help="file báo cáo .html (không bị sửa)")
    ap.add_argument("--ra", help="file kết quả, mặc định <tên>_Clicky.html")
    ap.add_argument("--kich-ban", help="kịch bản đã sửa (YAML); bỏ trống thì Clicky soạn nháp")
    ap.add_argument("--chu", default="anh Tuấn")
    ap.add_argument("--email", default="")
    ap.add_argument("--nhan", default="")
    ap.add_argument("--tri-nho", default=os.environ.get("CLICKY_TRI_NHO") or None,
                    help="thư mục trí nhớ (câu treo, sếp hay hỏi, bài học); mặc định CLICKY_TRI_NHO")
    a = ap.parse_args(argv)
    try:
        rep = build(a.bao_cao, a.ra, a.chu, a.email, a.kich_ban, a.nhan, memory=a.tri_nho)
    except PackError as exc:
        print(f"Chưa làm được: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in rep.items() if k != "tri_nho"}, ensure_ascii=False, indent=1))
    print(remind(rep))
    return 0


def remind(rep: dict) -> str:
    """What the owner should know before sending, in plain words."""
    m = rep.get("tri_nho")
    if not m:
        return ""
    lines = [f"Trí nhớ ({m['thu_muc']}): đọc {m['doc_dau']} câu trả lời ở đầu bài, "
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
