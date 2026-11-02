"""Offline Windows voice; no key, downloads or network requests."""
import asyncio
import json
import subprocess
import base64

from audio.tts.base_tts import BaseTTS
from storage import setting
from audio.playback import play_mp3_async

_SCRIPT = """[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Speech
$inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$inputData.rate))
  if ($inputData.voice) { $speaker.SelectVoice([string]$inputData.voice) }
  elseif ($inputData.lang) {
    # Prefer an installed voice in the text's language (e.g. Microsoft An, vi-VN).
    $match = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $inputData.lang } | Select-Object -First 1
    if ($match) { $speaker.SelectVoice($match.VoiceInfo.Name) }
  }
  $audio = New-Object System.IO.MemoryStream
  $speaker.SetOutputToWaveStream($audio)
  $speaker.Speak([string]$inputData.text)
  [Console]::Out.Write([Convert]::ToBase64String($audio.ToArray()))
  $audio.Dispose()
} finally { $speaker.Dispose() }
"""


_VI_LETTERS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")


def text_language(text: str) -> str:
    """'vi-VN' when the text is Vietnamese, else '' (keep the default Windows voice)."""
    return "vi-VN" if any(ch in _VI_LETTERS for ch in text.lower()) else ""


class SapiTTSProvider(BaseTTS):
    def __init__(self):
        self._voice = setting("voice", "")
        self._process = None

    def set_voice(self, voice):
        self._voice = voice

    def stop(self):
        process = self._process
        if process and process.poll() is None:
            process.terminate()

    async def speak(self, text):
        if not text.strip():
            return
        await play_mp3_async(await self.synthesize(text))

    async def synthesize(self, text) -> bytes:
        """WAV bytes of Windows speech for text; raises when Windows speech is unavailable."""
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._process = process
        try:
            output, errors = await asyncio.to_thread(process.communicate, json.dumps({
                "text": text, "voice": self._voice, "rate": setting("speech_rate", 0),
                "lang": text_language(text),
            }).encode("utf-8"), timeout=120)
            if process.returncode:
                raise RuntimeError("Windows speech failed: " + errors.decode(errors="replace")[-400:])
            return base64.b64decode(output, validate=True)
        finally:
            if process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=5)
            if self._process is process:
                self._process = None
