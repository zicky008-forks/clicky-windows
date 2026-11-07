"""Vietnamese text -> phonemes for the Piper vi_VN voices, from a table (no espeak-ng code in Clicky).

Vietnamese is written syllable by syllable, so a table of syllables covers the language. The table
(vi_phonemes.tsv, built by tools/giong_noi/tao_bang_am.py) holds two forms per syllable: mid-clause and
clause-final. On the 30 gate sentences this matches espeak-ng's own sentence output on 29 of 30.

Numbers must already be words (present.vn_format.speakable). A word missing from the table is reported
in `missing`, never dropped silently by the caller.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).with_name("vi_phonemes.tsv")
_PUNCT = ",.;:!?"
# Words people say in English in a finance meeting, spelled the way a Vietnamese voice should say them.
LOANWORDS = {"slide": "xờ lai", "marketing": "ma két tinh", "excel": "ếch xeo", "power": "pao ơ",
             "bi": "bi ai", "pivot": "pi vốt", "dashboard": "đét bọt", "file": "phai", "email": "i meo",
             "ok": "ô kê", "kpi": "kê pê i", "aop": "a ô pê", "fy": "ép oai", "ebitda": "i bít đa"}


@lru_cache(maxsize=1)
def table() -> dict:
    out = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        word, mid, end = line.split("\t")
        out[word] = (mid, end)
    return out


def _words(clause: str) -> list:
    words = []
    for w in re.findall(r"\w+", clause):
        words += LOANWORDS.get(w, w).split()
    return words


def phonemize(text: str, missing: list | None = None) -> list:
    """Sentences of phoneme strings for sherpa-onnx (one string per sentence)."""
    t = table()
    text = unicodedata.normalize("NFC", text or "").lower()
    sentences, current = [], []
    for part in re.split(r"([" + re.escape(_PUNCT) + r"])", text):
        if part and part in _PUNCT:
            if current and current[-1] == " ":
                current.pop()
            if current:
                current.append(part)
                current.append(" ")
            if part in ".!?" and current:
                sentences.append("".join(current).strip())
                current = []
            continue
        words = _words(part)
        for i, w in enumerate(words):
            entry = t.get(w)
            if entry is None:
                if missing is not None:
                    missing.append(w)
                continue
            current.append(entry[1] if i == len(words) - 1 else entry[0])
            current.append(" ")
    if current:
        sentences.append("".join(current).strip())
    return [s for s in sentences if s.strip(_PUNCT + " ")]
