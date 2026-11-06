"""Offline Vietnamese speech-to-text: sherpa-onnx + Zipformer-30M (about 32 MB, CPU only, nothing leaves the PC).

The model is an offline transducer, so it decodes once the user lets go of the talk key: a 5 s question
takes well under a second on an office laptop. It writes lowercase words with no digits, so the text goes
through audio.vi_itn before anyone reads it ("năm phẩy bốn hai tỷ" -> "5,42 tỷ", "tê ca năm một một" -> "TK 511").

Hotwords bias the decoder toward finance terms it would otherwise mishear ("âm" heard as "ở", "slide" as "dây").
The default list ships next to this file; callers can add names from the open workbook or deck per question.

Where the model lives, first match wins:
  CLICKY_VOICE_DIR/asr-vi, <app folder>/voice/asr-vi, %LOCALAPPDATA%/Clicky/voice/asr-vi
The folder holds tokens.txt, encoder*.onnx, decoder*.onnx, joiner*.onnx and (for hotwords) bpe.vocab.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from audio.stt.base_stt import BaseSTT

MODEL_SUBDIR = "asr-vi"
HOTWORDS_FILE = Path(__file__).with_name("hotwords_vi.txt")


def voice_roots() -> list:
    roots = []
    env = os.environ.get("CLICKY_VOICE_DIR", "").strip()
    if env:
        roots.append(Path(env))
    base = Path(getattr(sys, "_MEIPASS", "") or Path(sys.argv[0] if sys.argv and sys.argv[0] else ".").resolve().parent)
    roots.append(base / "voice")
    roots.append(Path(__file__).resolve().parents[2] / "voice")
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    roots.append(Path(local) / "Clicky" / "voice")
    return roots


def _pick(folder: Path, stem: str) -> Optional[Path]:
    files = sorted(folder.glob(f"{stem}*.onnx"))
    int8 = [f for f in files if ".int8." in f.name]
    return (int8 or files or [None])[0]


def find_model(folder: Optional[Path] = None) -> Optional[dict]:
    """Paths of the model files, or None when no complete model is installed."""
    candidates = [folder] if folder else [r / MODEL_SUBDIR for r in voice_roots()]
    for d in candidates:
        if not d or not (d / "tokens.txt").is_file():
            continue
        enc, dec, joi = _pick(d, "encoder"), _pick(d, "decoder"), _pick(d, "joiner")
        if enc and joi and dec:
            # The decoder stays fp32 in the int8 packages; prefer a non-int8 decoder when both exist.
            plain = [f for f in sorted(d.glob("decoder*.onnx")) if ".int8." not in f.name]
            return {"dir": d, "encoder": enc, "decoder": plain[0] if plain else dec, "joiner": joi,
                    "tokens": d / "tokens.txt", "bpe_vocab": d / "bpe.vocab" if (d / "bpe.vocab").is_file() else None}
    return None


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("sherpa_onnx") is not None and find_model() is not None
    except Exception:
        return False


def default_hotwords() -> list:
    """Lines 'PHRASE :score' or 'PHRASE' from hotwords_vi.txt; '#' starts a comment."""
    try:
        lines = HOTWORDS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def _hotword_line(phrase: str) -> str:
    # Tokens in this model are upper case; lower case would split into unknown pieces.
    return " ".join(phrase.split()).upper()


_cache = {}
_cache_lock = threading.Lock()


def _recognizer(model: dict, threads: int):
    key = (str(model["dir"]), threads)
    with _cache_lock:
        rec = _cache.get(key)
        if rec is None:
            import sherpa_onnx
            kw = dict(encoder=str(model["encoder"]), decoder=str(model["decoder"]), joiner=str(model["joiner"]),
                      tokens=str(model["tokens"]), num_threads=threads, decoding_method="modified_beam_search",
                      max_active_paths=4)
            words = default_hotwords()
            if model.get("bpe_vocab") and words:
                hot = model["dir"] / ".hotwords.txt"
                try:
                    hot.write_text("\n".join(_hotword_line(w) for w in words) + "\n", encoding="utf-8")
                except OSError:
                    import tempfile
                    hot = Path(tempfile.gettempdir()) / "clicky-hotwords-vi.txt"
                    hot.write_text("\n".join(_hotword_line(w) for w in words) + "\n", encoding="utf-8")
                kw.update(hotwords_file=str(hot), hotwords_score=1.5, modeling_unit="bpe",
                          bpe_vocab=str(model["bpe_vocab"]))
            rec = sherpa_onnx.OfflineRecognizer.from_transducer(**kw)
            _cache[key] = rec
        return rec


def tidy(text: str) -> str:
    """Normalized sentence for people: digits, 'TK 511', first letter upper case."""
    from audio.vi_itn import normalize
    out = normalize(text)
    return out[:1].upper() + out[1:] if out else out


class SherpaViSTT(BaseSTT):
    """Push-to-talk Vietnamese transcription on the CPU. No key, no network."""

    offline = True

    def __init__(self, folder: Optional[Path] = None, threads: Optional[int] = None):
        self.model = find_model(folder)
        if self.model is None:
            raise FileNotFoundError("Chưa có bộ nghe tiếng Việt (asr-vi) trong thư mục giọng nói của Clicky")
        self.threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        self.extra_hotwords: list = []        # names from the open workbook or deck, set per question
        self.last_raw = ""
        self.last_seconds = 0.0

    def warm_up(self) -> None:
        """Load the model before the first question so the first answer is not slower than the rest."""
        _recognizer(self.model, self.threads)

    def decode(self, samples, sample_rate: int = 16000) -> str:
        """Raw recognizer words for float32 samples in [-1, 1]."""
        rec = _recognizer(self.model, self.threads)
        extra = "/".join(_hotword_line(w) for w in self.extra_hotwords if w.strip())
        stream = rec.create_stream(hotwords=extra) if extra and self.model.get("bpe_vocab") else rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        t = time.perf_counter()
        rec.decode_stream(stream)
        self.last_seconds = time.perf_counter() - t
        self.last_raw = stream.result.text.strip().lower()
        return self.last_raw

    def transcribe_sync(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        import numpy as np
        from audio.capture import trim_silence
        pcm = trim_silence(pcm_bytes)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size < sample_rate // 5:            # under 0.2 s: nothing was said
            return ""
        return tidy(self.decode(samples, sample_rate))

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        return await asyncio.to_thread(self.transcribe_sync, pcm_bytes, sample_rate)
