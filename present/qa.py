"""Answer questions from a question bank without AI: keyword match, parameters, then a template.

A bank entry (YAML) has: id, hoi_mau, tu_khoa {va: [[phrase, ...], ...]}, tra_loi, optional drill,
what_if, tham_so, nhay_slide. Every group in 'va' must match at least one phrase. Phrases match whole
words on accent-folded text, so 'aop' does not match inside 'aopr'.

When two entries match equally well, the asker is shown both and picks; Clicky never guesses.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional


def fold(text) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9%,.\-+ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(fold(phrase)) + r"(?![a-z0-9])", text) is not None


@dataclass
class Match:
    entry: dict
    score: float
    params: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)   # missing parameters, said back to the asker


@dataclass
class Answer:
    best: Optional[Match]
    tied: list = field(default_factory=list)       # other entries with the same score
    no_match: bool = False


_NUMBER_WORDS = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "lam": 5, "sau": 6, "bay": 7,
                 "tam": 8, "chin": 9, "muoi": 10}


def parse_percent(text: str) -> Optional[float]:
    """'giảm 5%', 'tăng 2,5 phần trăm', 'giảm năm phần trăm', 'giam mười phần trăm' -> signed fraction."""
    t = fold(text)
    sign = -1 if re.search(r"\b(giam|xuong|bot|hut|am)\b", t) else 1
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*(%|phan tram)", t)
    if m:
        value = float(m.group(1).replace(",", "."))
        return value / 100 if value < 0 else sign * value / 100
    m = re.search(r"((?:(?:mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin|muoi|muoi)\s?){1,3})\s*phan tram", t)
    if m:
        tens, cur = None, 0
        for w in m.group(1).split():
            n = _NUMBER_WORDS.get(w)
            if n is None:
                return None
            if n == 10:
                tens, cur = (cur or 1), 0
            else:
                cur = n
        value = (tens or 0) * 10 + cur
        return sign * value / 100
    return None


def pick_option(text: str, options: list, aliases: Optional[dict] = None) -> list:
    """Return every option named in the text (whole words, accent-folded). aliases: option -> [other names]."""
    t = fold(text)
    found = []
    for opt in options:
        names = [opt] + list((aliases or {}).get(opt, []))
        if any(_has(t, n) for n in names):
            found.append(opt)
    return found


_STOP = {"phong", "ban", "khoi", "xin", "bao", "nhieu", "cat", "bi", "duoc", "con", "the", "nao", "sao", "cua",
         "chi", "phi", "tang", "giam", "nam", "nay", "truoc", "khong", "may", "lam", "voi"}


def fuzzy_names(text: str, names: list, cutoff: float = 0.8) -> list:
    """Names (e.g. departments read from the workbook) the question mentions. Several hits = ask which."""
    t = fold(text)
    hits = []
    for name in names:
        f = re.sub(r"^(p\.?|phong|ban|khoi|bo phan)\s+", "", fold(name)).strip(" .")
        if not f:
            continue
        if _has(t, f):
            hits.append((1.0, name))
            continue
        words = t.split()
        n = len(f.split())
        best = max((SequenceMatcher(None, " ".join(words[i:i + n]), f).ratio()
                    for i in range(max(1, len(words) - n + 1))), default=0)
        if best >= cutoff:
            hits.append((best, name))
    hits.sort(key=lambda h: -h[0])
    if hits and hits[0][0] == 1.0:
        return [n for s, n in hits if s == 1.0]
    if hits:
        return [n for _, n in hits]
    # "phòng MKT": a word that only some names carry. Several names = the asker picks.
    words = [w for w in t.split() if len(w) >= 3 and w not in _STOP]
    partial = [n for n in names if any(_has(fold(n), w) for w in words)]
    return partial if len(partial) < len(names) else []


class Bank:
    def __init__(self, data: dict):
        self.entries = list(data.get("cau_hoi") or [])
        self.fallback = data.get("khong_khop") or {}

    def _score(self, text: str, entry: dict) -> float:
        groups = (entry.get("tu_khoa") or {}).get("va") or []
        if not groups:
            return 0.0
        score = 0.0
        for group in groups:
            hits = [p for p in group if _has(text, p)]
            if not hits:
                return 0.0
            score += max(len(fold(p)) for p in hits)    # longer phrases are stronger evidence
        return score * len(groups)

    def ask(self, question: str, boost=None) -> Answer:
        """boost(entry) -> extra score, e.g. when the question names a department and the entry takes one."""
        t = fold(question)
        def total(e):
            base = self._score(t, e)
            return base + (boost(e) if boost and base > 0 else 0)
        scored = sorted(((total(e), i, e) for i, e in enumerate(self.entries)), key=lambda x: (-x[0], x[1]))
        scored = [s for s in scored if s[0] > 0]
        if not scored:
            return Answer(None, no_match=True)
        top = scored[0][0]
        tied = [Match(e, s) for s, _, e in scored if s == top]
        return Answer(tied[0], tied=tied[1:])
