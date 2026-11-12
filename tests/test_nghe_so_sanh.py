"""The voice comparison page and the owner's pick of an offline voice."""
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "giong_noi"))

import nghe_so_sanh  # noqa: E402


def _wav(text):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 1600)
    return buf.getvalue()


def test_page_has_every_line_for_every_voice(tmp_path, monkeypatch):
    heard = []
    monkeypatch.setattr(nghe_so_sanh, "CHOICE_FILE", tmp_path / "giong_chon.txt")
    out = tmp_path / "so_sanh.html"
    nghe_so_sanh.build(out, {"vieneu": lambda t: heard.append(t) or _wav(t), "piper": _wav})
    page = out.read_text(encoding="utf-8")
    assert page.count("<audio") == 2 * len(nghe_so_sanh.LINES)
    # the voices hear the finance reading, not the raw abbreviations
    said = " ".join(heard)
    assert "ây-ô-pi điều chỉnh" in said and "AOPR" not in said and "héc-cao" in said


def test_the_pick_is_kept_and_used(tmp_path, monkeypatch):
    from audio.tts import vi_offline_tts
    monkeypatch.setattr(nghe_so_sanh, "CHOICE_FILE", tmp_path / "giong_chon.txt")
    nghe_so_sanh.choose("Piper")
    assert (tmp_path / "giong_chon.txt").read_text(encoding="utf-8").strip() == "piper"
    monkeypatch.setattr(vi_offline_tts, "vieneu_available", lambda: True)
    monkeypatch.setattr(vi_offline_tts, "piper_available", lambda: True)
    monkeypatch.setenv("CLICKY_GIONG", "piper")
    assert vi_offline_tts.preferred_order() == ["piper", "vieneu"]
    monkeypatch.setenv("CLICKY_GIONG", "vieneu")
    assert vi_offline_tts.preferred_order() == ["vieneu", "piper"]
