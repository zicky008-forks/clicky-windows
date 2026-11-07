"""One-process voice check: hearing (sherpa) + Piper + VieNeu loaded together, each voice read back by the
recognizer. Catches a clash between the two onnxruntime DLLs on Windows and measures speed on this CPU.

    python tools/giong_noi/kiem_tra_giong.py            # uses CLICKY_VOICE_DIR or ./voice
Exit code 1 when a voice that is installed fails, or a read-back misses a key number.
"""
from __future__ import annotations

import io
import json
import os
import platform
import sys
import time
import wave
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CHECKS = [("Lợi nhuận sau thuế tháng 10 là 436 triệu.", ["436 triệu"]),
          ("Chênh lệch -5,42 tỷ nằm ở tài khoản 511.", ["5,42 tỷ", "TK 511"]),
          ("Biên lợi nhuận gộp quý 3 đạt 12,5%.", ["quý 3", "12,5%"])]


def to_16k(wav: bytes):
    import numpy as np
    with wave.open(io.BytesIO(wav)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    n = int(len(x) * 16000 / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def main() -> int:
    from audio.stt.sherpa_vi_stt import SherpaViSTT, tidy
    from audio.tts import vi_offline_tts as v
    report = {"cpu": platform.processor(), "vnni": v.cpu_has_vnni(), "voices": {}}
    stt = SherpaViSTT()
    stt.warm_up()
    names = [n for n, ok in (("piper", v.piper_available()), ("vieneu", v.vieneu_available())) if ok]
    import importlib.util
    if importlib.util.find_spec("vieneu") and "vieneu" not in names:
        report["voices"]["vieneu"] = {"loi": "đã cài gói vieneu nhưng không thấy voice/vieneu dùng được"}
    failed = "loi" in report["voices"].get("vieneu", {})
    for name in names:
        row = {"cau": []}
        try:
            t = time.perf_counter()
            eng = v.engine(name)
            row["nap_giay"] = round(time.perf_counter() - t, 2)
            row["precision"] = getattr(eng, "precision", "")
            for text, must in CHECKS:
                t = time.perf_counter()
                wav = eng.synthesize(text)
                sec = time.perf_counter() - t
                audio = to_16k(wav)
                heard = tidy(stt.decode(audio))
                # Also with 0.5 s of pure digital silence after it: that used to make "TK 511" into "511 tỷ".
                import numpy as np
                heard_tail = tidy(stt.decode(np.concatenate([audio, np.zeros(8000, dtype=np.float32)])))
                ok = all(m.lower() in h.lower() for m in must for h in (heard, heard_tail))
                failed |= not ok
                row["cau"].append({"doc": text, "nghe_lai": heard, "nghe_lai_co_im_lang": heard_tail, "dung": ok, "giay": round(sec, 2),
                                   "rtf": round(sec / (len(audio) / 16000), 2)})
        except Exception as exc:
            row["loi"] = f"{type(exc).__name__}: {exc}"[:400]
            failed = True
        report["voices"][name] = row
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if failed or not names else 0


if __name__ == "__main__":
    raise SystemExit(main())
