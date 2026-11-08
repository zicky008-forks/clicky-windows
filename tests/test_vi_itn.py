"""Spoken Vietnamese finance words -> digits (audio/vi_itn.py) and the offline Vietnamese recognizer wiring."""
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

from audio.vi_itn import extract, normalize, parse_number
from present.vn_format import int_words, number_words

GATE = json.loads((Path(__file__).resolve().parents[1] / "tools" / "giong_noi" / "cau_cong.json")
                  .read_text(encoding="utf-8"))["cau"]


@pytest.mark.parametrize("s", GATE, ids=lambda s: f"cau-{s['id']:02d}")
def test_every_gate_sentence_normalizes_exactly(s):
    assert normalize(s["doc"]) == s["dung"]


def test_round_trip_with_the_speech_side_for_every_integer_shape():
    random.seed(7)
    nums = list(range(0, 1200)) + [random.randrange(10 ** k) for k in range(3, 13) for _ in range(100)]
    assert [n for n in nums if parse_number(int_words(n).split()) != n] == []


def test_round_trip_for_decimals():
    random.seed(8)
    values = [f"{random.randrange(5000)},{random.randrange(1, 100)}" for _ in range(300)]
    assert [v for v in values if parse_number(number_words(v).split()) != Decimal(v.replace(",", "."))] == []


@pytest.mark.parametrize("said, written", [
    ("năm phẩy bốn hai tỷ", "5,42 tỷ"),
    ("âm năm phẩy bốn hai tỷ", "-5,42 tỷ"),
    ("một tỷ hai", "1,2 tỷ"),
    ("hai triệu rưỡi", "2,5 triệu"),
    ("trăm rưỡi", "150"),
    ("hai trăm tư", "240"),
    ("một trăm lẻ năm tỷ", "105 tỷ"),
    ("ba lăm phần trăm", "35%"),
    ("không phẩy năm phần trăm", "0,5%"),
    ("hai nghìn tỷ", "2.000 tỷ"),
    ("mười lăm nghìn", "15.000"),
    ("tê ca năm một một", "TK 511"),
    ("t k năm một một", "TK 511"),
    ("t năm hai mốt", "TK 521"),
    ("tài khoản năm trăm mười một", "TK 511"),
    ("tài khoản sáu ba lăm", "TK 635"),
    ("tài khoản một ba mốt", "TK 131"),
    ("năm hai nghìn không trăm hai mươi sáu", "năm 2026"),
    ("tháng mười năm hai không hai lăm", "tháng 10 năm 2025"),
    ("quý tư năm hai sáu", "quý 4 năm 26"),
    ("quý bà", "quý 3"),
    ("tháng giêng", "tháng 1"),
])
def test_finance_forms(said, written):
    assert normalize(said) == written


@pytest.mark.parametrize("words", [
    "năm nay lợi nhuận tăng", "đầu tư một chút", "sau ba năm", "âm thanh", "tỷ giá", "tỷ suất",
    "bao nhiêu phần trăm", "chứ không phải", "cho t xem", "nghìn tỷ",
])
def test_words_that_are_not_numbers_stay_words(words):
    assert normalize(words) == words


def test_extract_gives_exact_values_for_code():
    text, found = extract("chênh lệch âm năm phẩy bốn hai tỷ ở tê ca năm một một, giảm năm phần trăm")
    assert text == "chênh lệch -5,42 tỷ ở TK 511 giảm 5%"
    kinds = {f.kind: f.value for f in found}
    assert kinds["amount"] == Decimal("-5420000000")
    assert kinds["account"] == "511"
    assert kinds["percent"] == Decimal("0.05")


def test_several_accounts_after_and():
    assert normalize("tài khoản năm một một và năm hai mốt") == "TK 511 và TK 521"


# ── recognizer wiring ────────────────────────────────────────────────────────

def _fake_model(folder: Path) -> Path:
    folder.mkdir(parents=True)
    for name in ("tokens.txt", "encoder-epoch-12-avg-8.int8.onnx", "decoder-epoch-12-avg-8.onnx",
                 "joiner-epoch-12-avg-8.int8.onnx", "bpe.vocab"):
        (folder / name).write_text("x", encoding="utf-8")
    return folder


def test_find_model_reads_the_voice_folder(tmp_path, monkeypatch):
    from audio.stt import sherpa_vi_stt as m
    monkeypatch.setenv("CLICKY_VOICE_DIR", str(tmp_path))
    assert m.find_model() is None or m.find_model()["dir"] != tmp_path / "asr-vi"
    _fake_model(tmp_path / "asr-vi")
    found = m.find_model()
    assert found["dir"] == tmp_path / "asr-vi"
    assert found["encoder"].name.endswith(".int8.onnx")
    assert found["decoder"].name == "decoder-epoch-12-avg-8.onnx"
    assert found["bpe_vocab"] is not None


def test_offline_vietnamese_comes_before_cloud_keys(monkeypatch):
    from audio.stt import sherpa_vi_stt
    from config import cfg
    monkeypatch.delenv("CLICKY_STT", raising=False)
    monkeypatch.setattr(sherpa_vi_stt, "available", lambda: True)
    monkeypatch.setattr(cfg, "deepgram_api_key", "dg-key")
    assert cfg.stt_provider() == "sherpa_vi"


def test_tidy_capitalizes_and_normalizes():
    from audio.stt.sherpa_vi_stt import tidy
    assert tidy("kiểm tra tk năm một một") == "Kiểm tra TK 511"


def test_hotwords_are_upper_case_for_the_model():
    from audio.stt.sherpa_vi_stt import _hotword_line, default_hotwords
    words = default_hotwords()
    assert "âm :2.0" in words
    assert _hotword_line("âm :2.0") == "ÂM :2.0"


def test_windows_voice_typing_is_never_started():
    root = Path(__file__).resolve().parents[1]
    for name in ("main.py", "companion_manager.py", "config.py", "ui/settings_window.py"):
        text = (root / name).read_text(encoding="utf-8").lower()
        assert "windows+h" not in text and "'windows')" not in text, name
