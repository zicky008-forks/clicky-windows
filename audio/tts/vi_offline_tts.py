"""Offline Vietnamese voices: VieNeu-TTS v3 (best quality) with Piper vi_VN as the always-works fallback.

Nothing leaves the PC and no key is needed. Numbers are turned into words first (present.vn_format.speakable),
so "5,42 tỷ" is read "năm phẩy bốn hai tỷ" and never digit by digit.

- VieNeu v3 Turbo (weights Apache-2.0) runs on ONNX Runtime. int8 is about 1.6x faster but needs AVX-VNNI /
  AVX-512 VNNI; without it int8 can garble the audio, so Clicky then uses fp32. If VieNeu is missing or fails,
  Clicky falls back to Piper.
- Piper vi_VN-vais1000-medium (dataset CC BY 4.0) runs through sherpa-onnx 2.0 with phonemes from a table
  (audio/tts/vi_g2p.py), so no espeak-ng (GPL) code ships with Clicky.

Which engine is the default is decided by the blind listening test (tools/giong_noi/nghe_mu.py); the setting
"tts_vi_engine" ("vieneu" | "piper") overrides it.

Voice files, first match wins: CLICKY_VOICE_DIR, <app folder>/voice, %LOCALAPPDATA%/Clicky/voice
  tts-piper-vi/  vi_VN-vais1000-medium.onnx + tokens.txt
  vieneu/        Hugging Face cache for VieNeu (HF_HOME points here, so it never re-downloads)
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
import wave
from pathlib import Path
from typing import Optional

from audio.tts.base_tts import BaseTTS

PIPER_SUBDIR = "tts-piper-vi"
VIENEU_SUBDIR = "vieneu"
VIENEU_VOICE = os.environ.get("CLICKY_VIENEU_VOICE", "").strip() or None


def _roots() -> list:
    from audio.stt.sherpa_vi_stt import voice_roots
    return voice_roots()


def find_piper() -> Optional[dict]:
    for root in _roots():
        d = root / PIPER_SUBDIR
        models = sorted(d.glob("vi_VN-*.onnx")) if d.is_dir() else []
        if models and (d / "tokens.txt").is_file():
            return {"model": models[0], "tokens": d / "tokens.txt"}
    return None


def piper_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("sherpa_onnx") is not None and find_piper() is not None


def vieneu_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("vieneu") is not None


def cpu_has_vnni() -> bool:
    """AVX-VNNI or AVX-512 VNNI. Unknown counts as no, so int8 is only used when it is safe."""
    try:
        import cpuinfo                                     # py-cpuinfo, optional
        flags = set(cpuinfo.get_cpu_info().get("flags", []))
        return bool(flags & {"avx512_vnni", "avx512vnni", "avxvnni", "avx_vnni"})
    except Exception:
        pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            text = f.read()
        return "avx512_vnni" in text or "avx_vnni" in text
    except OSError:
        return False


def to_wav(samples, sample_rate: int) -> bytes:
    import numpy as np
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    return buf.getvalue()


def speakable(text: str) -> str:
    from present.vn_format import speakable as words
    return words(text)


class PiperVi:
    name = "piper"

    def __init__(self, files: Optional[dict] = None, speed: float = 0.95):
        files = files or find_piper()
        if files is None:
            raise FileNotFoundError("Chưa có giọng Piper tiếng Việt (tts-piper-vi)")
        import sherpa_onnx
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(model=str(files["model"]), tokens=str(files["tokens"]))
        cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            vits=vits, num_threads=max(1, min(4, (os.cpu_count() or 2) // 2))))
        self._tts = sherpa_onnx.OfflineTts(cfg)
        self.speed = speed                     # a little slower than normal: numbers are easier to follow
        self.missing: list = []

    def synthesize(self, text: str) -> bytes:
        import numpy as np
        import sherpa_onnx
        from audio.tts.vi_g2p import phonemize
        self.missing = []
        pieces, rate = [], 22050
        for sentence in phonemize(speakable(text), self.missing):
            g = sherpa_onnx.GenerationConfig()
            g.speed = self.speed
            g.phoneme_codepoints = [[ord(c) for c in sentence]]
            audio = self._tts.generate(sentence, g)
            rate = audio.sample_rate
            pieces += [np.asarray(audio.samples, dtype=np.float32), np.zeros(rate // 8, dtype=np.float32)]
        if not pieces:
            return b""
        return to_wav(np.concatenate(pieces), rate)


class VieneuVi:
    name = "vieneu"

    def __init__(self, voice: Optional[str] = VIENEU_VOICE):
        for root in _roots():
            cache = root / VIENEU_SUBDIR
            if cache.is_dir():
                os.environ.setdefault("HF_HOME", str(cache))
                os.environ.setdefault("HF_HUB_OFFLINE", "1")   # use the bundled files, never call home
                break
        from vieneu import Vieneu
        self.precision = "int8" if cpu_has_vnni() else "fp32"
        self._tts = Vieneu(backend="onnx", precision=self.precision)
        self.voice = voice

    def synthesize(self, text: str) -> bytes:
        kw = {"voice": self.voice} if self.voice else {}
        audio = self._tts.infer(speakable(text), **kw)
        return to_wav(audio, 48000)


_engines = {}
_lock = threading.Lock()


def engine(name: str):
    with _lock:
        if name not in _engines:
            _engines[name] = VieneuVi() if name == "vieneu" else PiperVi()
        return _engines[name]


def preferred_order() -> list:
    try:
        from storage import setting
        chosen = (setting("tts_vi_engine", "") or "").strip().lower()
    except Exception:
        chosen = ""
    order = ["vieneu", "piper"] if chosen != "piper" else ["piper", "vieneu"]
    return [n for n in order if (vieneu_available() if n == "vieneu" else piper_available())]


def available() -> bool:
    return bool(preferred_order())


class OfflineViTTS(BaseTTS):
    """Speak Vietnamese on the PC. Tries the preferred engine, then the other one; says which one it used."""

    offline = True

    def __init__(self, on_fallback=None):
        self.on_fallback = on_fallback
        self.used = None
        self._stopped = False

    def stop(self):
        self._stopped = True
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass

    def synthesize_sync(self, text: str) -> bytes:
        errors = []
        for name in preferred_order():
            try:
                wav = engine(name).synthesize(text)
                self.used = name
                if errors and self.on_fallback:
                    self.on_fallback("Giọng đọc chính không chạy được trên máy này, em dùng giọng dự phòng.")
                return wav
            except Exception as exc:          # one engine broken must not silence Clicky
                errors.append(f"{name}: {exc}")
        raise RuntimeError("Không có giọng tiếng Việt nào đọc được: " + "; ".join(errors or ["chưa cài giọng"]))

    async def synthesize(self, text: str) -> bytes:
        return await asyncio.to_thread(self.synthesize_sync, text)

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        self._stopped = False
        wav = await self.synthesize(text)
        if self._stopped or not wav:
            return
        from audio.playback import play_mp3_async
        await play_mp3_async(wav)
