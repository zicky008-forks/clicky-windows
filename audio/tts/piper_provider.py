"""Offline Vietnamese voice with Piper: a stand-alone piper.exe plus a vi_VN .onnx voice model.

No admin rights, no network, nothing installed system-wide: the files sit in the user's Clicky data folder
(%LOCALAPPDATA%\\Clicky\\piper by default, CLICKY_PIPER_DIR overrides) and piper.exe runs as a child process.
Numbers must already be words (present.vn_format.speakable): voices misread "1.234,5".
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from audio.tts.base_tts import BaseTTS

DEFAULT_MODEL = "vi_VN-vais1000-medium.onnx"


def piper_dir() -> Path:
    env = os.environ.get("CLICKY_PIPER_DIR", "").strip()
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Clicky" / "piper"


def find_piper(folder: Path | None = None) -> tuple:
    """(piper executable, model) or raise FileNotFoundError naming what is missing, in Vietnamese."""
    folder = folder or piper_dir()
    exe = next((p for p in (folder / "piper.exe", folder / "piper" / "piper.exe", folder / "piper") if p.is_file()), None)
    models = sorted(folder.rglob("vi_VN-*.onnx"))
    preferred = [m for m in models if m.name == DEFAULT_MODEL]
    model = (preferred or models or [None])[0]
    if exe is None or model is None:
        missing = ("chương trình piper" if exe is None else "") + (" và " if exe is None and model is None else "") + \
                  ("giọng vi_VN (.onnx + .onnx.json)" if model is None else "")
        raise FileNotFoundError(f"Chưa có {missing} trong {folder}")
    return exe, model


class PiperTTSProvider(BaseTTS):
    offline = True

    def __init__(self, folder: Path | None = None, length_scale: float = 1.1):
        self.exe, self.model = find_piper(folder)
        self.length_scale = length_scale        # >1 = slower, clearer for numbers
        self._process = None

    def stop(self):
        p = self._process
        if p and p.poll() is None:
            p.terminate()

    async def synthesize(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "say.wav"
            p = subprocess.Popen([str(self.exe), "--model", str(self.model), "--output_file", str(out),
                                  "--length_scale", str(self.length_scale)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._process = p
            try:
                _, err = await asyncio.to_thread(p.communicate, text.encode("utf-8"), timeout=120)
                if p.returncode or not out.exists():
                    raise RuntimeError("Piper không đọc được: " + err.decode(errors="replace")[-300:])
                return out.read_bytes()
            finally:
                if p.poll() is None:
                    p.terminate()
                self._process = None

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        from audio.playback import play_mp3_async
        await play_mp3_async(await self.synthesize(text))
