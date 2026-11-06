"""Offline Vietnamese voice: phoneme table, engine fallback, provider choice."""
from pathlib import Path

import pytest

from audio.tts import vi_g2p, vi_offline_tts


def test_table_covers_every_loanword_spelling():
    t = vi_g2p.table()
    assert len(t) > 15000
    assert [w for v in vi_g2p.LOANWORDS.values() for w in v.split() if w not in t] == []


def test_clause_final_level_tone_uses_the_final_form():
    # espeak marks the level tone 1 mid-clause and 7 at the end of a clause; the table keeps both.
    assert vi_g2p.phonemize("hai ba") == ["hˈaː1j bˈaː7"]
    assert vi_g2p.phonemize("ba, hai.") == ["bˈaː7, hˈaː7j."]


def test_sentences_split_and_missing_words_are_reported():
    missing = []
    out = vi_g2p.phonemize("Doanh thu tăng. Chi phí zzqx giảm!", missing)
    assert len(out) == 2
    assert missing == ["zzqx"]


def test_slide_is_read_the_vietnamese_way():
    assert vi_g2p.phonemize("slide") == vi_g2p.phonemize("xờ lai")


class _Engine:
    def __init__(self, name, fail=False):
        self.name, self.fail = name, fail

    def synthesize(self, text):
        if self.fail:
            raise RuntimeError("int8 garbled")
        return b"RIFF" + self.name.encode()


def test_falls_back_to_piper_and_says_so(monkeypatch):
    notes = []
    monkeypatch.setattr(vi_offline_tts, "preferred_order", lambda: ["vieneu", "piper"])
    engines = {"vieneu": _Engine("vieneu", fail=True), "piper": _Engine("piper")}
    monkeypatch.setattr(vi_offline_tts, "engine", lambda n: engines[n])
    tts = vi_offline_tts.OfflineViTTS(on_fallback=notes.append)
    assert tts.synthesize_sync("Xin chào") == b"RIFFpiper"
    assert tts.used == "piper" and len(notes) == 1


def test_no_voice_raises_instead_of_staying_silent(monkeypatch):
    monkeypatch.setattr(vi_offline_tts, "preferred_order", lambda: [])
    with pytest.raises(RuntimeError, match="Không có giọng tiếng Việt"):
        vi_offline_tts.OfflineViTTS().synthesize_sync("Xin chào")


def test_setting_puts_piper_first(monkeypatch):
    import storage
    monkeypatch.setattr(storage, "setting", lambda k, d=None: "piper" if k == "tts_vi_engine" else d)
    monkeypatch.setattr(vi_offline_tts, "vieneu_available", lambda: True)
    monkeypatch.setattr(vi_offline_tts, "piper_available", lambda: True)
    assert vi_offline_tts.preferred_order() == ["piper", "vieneu"]


def test_offline_vietnamese_voice_is_chosen_even_in_local_only(monkeypatch):
    from config import cfg
    import office.outgoing as outgoing
    monkeypatch.setattr(vi_offline_tts, "available", lambda: True)
    monkeypatch.setattr(outgoing, "local_only", lambda: True)
    assert cfg.tts_provider() == "vi_offline"
    monkeypatch.setattr(outgoing, "local_only", lambda: False)
    assert cfg.tts_provider() == "vi_offline"


def test_find_piper_reads_the_voice_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKY_VOICE_DIR", str(tmp_path))
    d = tmp_path / "tts-piper-vi"
    d.mkdir()
    (d / "vi_VN-vais1000-medium.onnx").write_bytes(b"x")
    (d / "tokens.txt").write_text("x", encoding="utf-8")
    assert vi_offline_tts.find_piper()["model"].name == "vi_VN-vais1000-medium.onnx"


def test_no_gpl_piper_program_is_used():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "audio" / "tts" / "piper_provider.py").exists()
    assert "espeak" not in (root / "audio" / "tts" / "vi_offline_tts.py").read_text(encoding="utf-8").replace(
        "no espeak-ng (GPL) code", "")


def test_wav_header_and_rate():
    import io
    import wave
    data = vi_offline_tts.to_wav([0.0, 0.5, -0.5], 22050)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 22050 and w.getnframes() == 3
