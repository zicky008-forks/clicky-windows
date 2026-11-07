"""Run a talk: slides from a script, questions from a bank, what-ifs on the live scenario sheet, minutes.

Screen, voice and keys are injected (say, point, wait_key) so the same logic runs inside Clicky's overlay,
in the rehearsal robot and in tests. A line with any unresolved placeholder is never spoken: the talk stops.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from present import deck as deck_mod
from present.placeholders import Resolver
from present.qa import Bank, fuzzy_names, parse_percent, pick_option
from present.vn_format import speakable


class Stop(RuntimeError):
    """A line could not be filled with checked figures; the presenter takes over."""


@dataclass
class Minutes:
    started: float = field(default_factory=time.time)
    preflight: list = field(default_factory=list)
    slides: list = field(default_factory=list)       # (slide, seconds, text)
    questions: list = field(default_factory=list)    # (question, id or "", answer)
    whatifs: list = field(default_factory=list)      # (inputs, results, restored, problems)
    open_questions: list = field(default_factory=list)
    stops: list = field(default_factory=list)

    def markdown(self) -> str:
        out = ["# Biên bản buổi trình bày", "", "## Kiểm tra trước", *[f"- {l}" for l in self.preflight], "",
               "## Slide đã trình", *[f"- Slide {n} ({s:.0f} giây): {t}" for n, s, t in self.slides], "",
               "## Câu hỏi", *[f"- {q} → {i or 'ghi lại'}: {a}" for q, i, a in self.questions], "",
               "## Kịch bản đã chạy thử",
               *[f"- {inp} → {res}; trả ô cũ: {'có' if ok else 'KHÔNG'}{'; ' + '; '.join(p) if p else ''}" for inp, res, ok, p in self.whatifs],
               "", "## Câu hỏi mở", *[f"- {q}" for q in self.open_questions]]
        if self.stops:
            out += ["", "## Chỗ phải dừng", *[f"- {s}" for s in self.stops]]
        return "\n".join(out) + "\n"


def estimate_seconds(script: dict, syllables_per_second: float = 3.2) -> float:
    """Rough talk length: Vietnamese syllables read by an offline voice, plus pauses. Placeholders count as 6 syllables."""
    from present.placeholders import TOKEN
    total = 0.0
    meta = script.get("meta") or {}
    lines = [meta.get("mo_dau", ""), meta.get("ket_thuc", "")]
    for s in script.get("slides", []):
        lines.append(s.get("loi_noi", ""))
        total += float(s.get("dung_giay", 2)) + 1.0          # pause + slide change
    for line in lines:
        total += (len(TOKEN.sub(" ", line).split()) + 6 * len(TOKEN.findall(line))) / syllables_per_second
    return total


class Talk:
    def __init__(self, script: dict, bank: dict, resolver: Resolver, deck, say: Callable[[str, str], None],
                 point: Callable[[tuple, str], None], wait: Callable[[float], None], whatif=None,
                 minutes: Optional[Minutes] = None):
        self.script, self.bank = script, Bank(bank)
        self.resolver, self.deck, self.whatif = resolver, deck, whatif
        self.say, self.point, self.wait = say, point, wait
        self.minutes = minutes or Minutes()
        self.slide = 0
        resolver.slide_title = lambda: self.deck.title(self.slide) if self.slide else ""

    # ---- slides -------------------------------------------------------------------------
    def _speak(self, text: str, where: str, extra: Optional[dict] = None) -> str:
        r = self.resolver.fill(text, extra)
        if r.missing:
            why = "; ".join(f"{t}: {m}" for t, m in r.missing)
            self.minutes.stops.append(f"{where}: {why}")
            raise Stop(f"{where}: chưa đọc được số ({why})")
        self.say(r.text, speakable(r.text))       # screen text, voice text
        return r.text

    def targets(self, item: dict, shapes: Optional[list] = None) -> list:
        """[(screen rect, label)] for the shapes a script item points at, in order. Misses go to the minutes."""
        shapes = self.deck.slide_shapes(int(item["so"])) if shapes is None else shapes
        found = []
        for target in item.get("chi_vao") or []:
            if "excel" in target:
                continue
            contains = target.get("shape_chua")
            if contains is not None:
                contains = self.resolver.fill(contains).text
            shape = deck_mod.find(shapes, kind=target.get("shape"), contains=contains)
            if shape is None and contains:
                bare = re.sub(r"\s*(tỷ|%|điểm %)$", "", contains)    # the slide may print "123,4" without the unit
                shape = deck_mod.find(shapes, contains=bare) if bare != contains else None
            if shape is None:
                self.minutes.stops.append(f"slide {item['so']}: không thấy hình '{contains or target.get('shape')}' để chỉ")
                continue
            found.append((self.deck.screen_rect(shape), contains or target.get("shape", "")))
        return found

    def show_slide(self, item: dict) -> None:
        start = time.time()
        self.slide = self.deck.goto(int(item["so"]))
        for rect, label in self.targets(item):
            self.point(rect, label)
        text = self._speak(item.get("loi_noi", ""), f"slide {item['so']}")
        self.wait(float(item.get("dung_giay", 2)) if not item.get("cho_phim") else -1)
        self.minutes.slides.append((item["so"], time.time() - start, text))

    def present(self, only: Optional[list] = None) -> None:
        meta = self.script.get("meta") or {}
        if meta.get("mo_dau"):
            self._speak(meta["mo_dau"], "mở đầu")
        for item in self.script.get("slides", []):
            if only and item["so"] not in only:
                continue
            self.show_slide(item)
        if meta.get("ket_thuc"):
            self._speak(meta["ket_thuc"], "kết thúc")

    # ---- questions ------------------------------------------------------------------------
    def answer(self, question: str) -> dict:
        """Returns {'id', 'text', 'choices', 'drill', 'slide'}; choices non-empty = ask the asker to pick."""
        found = self.bank.ask(question, boost=lambda e: self._names_boost(e, question))
        if found.no_match:
            text = (self.bank.fallback.get("tra_loi") or "Câu này em ghi lại.")
            self.say(text, speakable(text))
            self.minutes.questions.append((question, "", text))
            self.minutes.open_questions.append(question)
            return {"id": "", "text": text, "choices": []}
        if found.tied:
            options = [found.best] + found.tied
            return {"id": "", "text": "", "choices": [(m.entry["id"], m.entry.get("hoi_mau", "")) for m in options]}
        return self.answer_entry(found.best.entry, question)

    def answer_entry(self, entry: dict, question: str) -> dict:
        extra, row = {}, None
        params = entry.get("tham_so") or {}
        if "ten_phong" in params:
            spec = params["ten_phong"] if isinstance(params["ten_phong"], dict) else {}
            names = self._names(spec)
            hits = fuzzy_names(question, list(names))
            if len(hits) != 1:
                return {"id": entry["id"], "text": "", "choices": [(entry["id"], h) for h in hits] or [],
                        "ask": "Anh chị muốn hỏi phòng nào ạ?"}
            extra["ten_phong"], row = hits[0], names[hits[0]]
        what = entry.get("what_if")
        if what:
            return self._what_if(entry, what, question, extra)
        text = entry.get("tra_loi", "")
        if row is not None:
            text = text.replace("<dong>", str(row))
        text = self._speak(text, entry["id"], extra)
        self.minutes.questions.append((question, entry["id"], text))
        return {"id": entry["id"], "text": text, "choices": [], "drill": entry.get("drill"), "slide": entry.get("nhay_slide")}

    def _names_boost(self, entry: dict, question: str) -> float:
        """A question that names a department belongs to the entry that takes a department."""
        spec = (entry.get("tham_so") or {}).get("ten_phong")
        if not isinstance(spec, dict):
            return 0.0
        try:
            return 100.0 if fuzzy_names(question, list(self._names(spec))) else 0.0
        except Exception:                   # noqa: BLE001 - a missing sheet just means no boost
            return 0.0

    def _names(self, spec: dict) -> dict:
        sheet, col, first = spec.get("sheet", "De_xuat"), spec.get("cot", 4), int(spec.get("tu_dong", 4))
        names = {}
        for i, r in enumerate(self.resolver.source.rows(sheet, first, int(col))):
            name = str(r[int(col) - 1] or "").strip()
            if name:
                names.setdefault(name, first + i)
        return names

    def _what_if(self, entry: dict, what: dict, question: str, extra: dict) -> dict:
        if self.whatif is None:
            raise Stop("chưa nối được file Excel để chạy thử")
        inputs = {}
        for w in what.get("ghi") or []:
            value = w.get("gia_tri")
            if value == "{pct}":
                pct = parse_percent(question)
                if pct is None:
                    return {"id": entry["id"], "text": "", "choices": [], "ask": "Anh chị muốn tăng hay giảm bao nhiêu phần trăm ạ?"}
                value, extra["pct"] = pct, f"{'giảm' if pct < 0 else 'tăng'} {abs(pct) * 100:g}%".replace(".", ",")
            elif isinstance(value, str) and value.startswith("{tq:"):
                value = self.resolver.summary()[value[4:-1]][0]
            target = w["o"]
            if "{dong_kenh}" in target:
                rows = what.get("dong_kenh") or {}
                picked = pick_option(question, list(rows), what.get("ten_khac_kenh"))
                if fold_all(question) or not picked:
                    if not what.get("tat_ca"):
                        return {"id": entry["id"], "text": "", "choices": [], "ask": "Kênh nào ạ?"}
                    target, extra["kenh"] = what["tat_ca"], "tất cả các kênh"
                elif len(picked) > 1:
                    return {"id": entry["id"], "text": "", "choices": [(entry["id"], p) for p in picked], "ask": "Kênh nào ạ?"}
                else:
                    target, extra["kenh"] = target.replace("{dong_kenh}", str(rows[picked[0]])), "kênh " + picked[0]
            inputs[target] = value
        run = self.whatif.run(inputs, list(what.get("doc") or []))
        self.minutes.whatifs.append((run.inputs, run.results, run.restored, run.problems))
        if not run.restored:
            self.minutes.stops.append(f"{entry['id']}: không trả được ô về giá trị cũ")
            raise Stop("không trả được ô về giá trị cũ; KHÔNG lưu file, báo anh Tuấn")
        self.resolver.overrides = dict(run.results)
        try:
            text = self._speak(entry.get("tra_loi", ""), entry["id"], extra)
        finally:
            self.resolver.overrides = {}
        self.minutes.questions.append((question, entry["id"], text))
        return {"id": entry["id"], "text": text, "choices": [], "drill": entry.get("drill")}


def fold_all(question: str) -> bool:
    from present.qa import fold
    return re.search(r"\b(tat ca|toan bo|moi kenh|ca nam|tong)\b", fold(question)) is not None
