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
$wav = $null
try {
  $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$inputData.rate))
  if ($inputData.voice) { $speaker.SelectVoice([string]$inputData.voice) }
  elseif ($inputData.lang) {
    # A voice in the text's language, never the default English one reading Vietnamese.
    $match = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $inputData.lang } | Select-Object -First 1
    if ($match) { $speaker.SelectVoice($match.VoiceInfo.Name) }
    else {
      # Voices added in Settings > Time & language > Speech (e.g. Microsoft An, vi-VN) are OneCore voices,
      # reachable only through the WinRT speech API.
      try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
        $rtVoice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.Language -eq $inputData.lang } | Select-Object -First 1
        if ($rtVoice) {
          $rt = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
          $rt.Voice = $rtVoice
          $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
          $op = $rt.SynthesizeTextToStreamAsync([string]$inputData.text)
          $stream = $asTask.MakeGenericMethod([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]).Invoke($null, @($op)).Result
          $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream.GetInputStreamAt(0))
          $copy = New-Object System.IO.MemoryStream
          $netStream.CopyTo($copy)
          $wav = $copy.ToArray()
        }
      } catch { [Console]::Error.Write("WinRT speech: " + $_.Exception.Message + " ") }
      if (-not $wav) { [Console]::Error.Write("NO_VOICE_FOR_LANG"); exit 3 }
    }
  }
  if (-not $wav) {
    $audio = New-Object System.IO.MemoryStream
    $speaker.SetOutputToWaveStream($audio)
    $speaker.Speak([string]$inputData.text)
    $wav = $audio.ToArray()
    $audio.Dispose()
  }
  [Console]::Out.Write([Convert]::ToBase64String($wav))
} finally { $speaker.Dispose() }
"""


class NoVoiceForLanguage(RuntimeError):
    """Windows has no voice for the text's language; Clicky shows the words instead of reading them wrongly."""
    # A Windows toast shows about two lines: keep it to what to do.
    ADVICE = "Chưa có giọng tiếng Việt nên Clicky chỉ hiện chữ. Giọng online (gửi lên Microsoft): Cài đặt > Voice."


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
            if process.returncode == 3 and b"NO_VOICE_FOR_LANG" in errors:
                raise NoVoiceForLanguage(errors.decode(errors="replace")[-300:])
            if process.returncode:
                raise RuntimeError("Windows speech failed: " + errors.decode(errors="replace")[-400:])
            return base64.b64decode(output, validate=True)
        finally:
            if process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=5)
            if self._process is process:
                self._process = None
