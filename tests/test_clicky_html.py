"""Clicky inside an HTML report: the talk uses only the report's own words, the original file is never touched."""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from present import clicky_html, goi_html

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"
NUM = re.compile(r"\d[\d.,]*")


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path / "bao_cao.html"
    shutil.copyfile(FIXTURE, dst)
    return dst


def voice(text):
    return text.encode("utf-8")


def test_outline_finds_sections_notes_cards_table_chart():
    items = clicky_html.outline(clicky_html.parse(FIXTURE.read_text(encoding="utf-8")))
    kinds = [i.kind for i in items]
    assert kinds.count("section") == 3 and kinds.count("table") == 1 and kinds.count("chart") == 1
    cards = [i.text for i in items if i.kind == "card"]
    # a grid of cards is not one card, and a value <div> inside a card is not a card of its own
    assert len(cards) == 6
    assert cards[0] == "Tổng chi phí nhân sự AOPR: 186,4 tỷ, tăng 12,6% so với AOP gốc"


def test_draft_speaks_only_numbers_that_are_on_the_page(report):
    before = report.read_bytes()
    rep = clicky_html.build(str(report), voice=voice)
    assert report.read_bytes() == before                      # the original report is never touched
    page_text = re.sub(r"<[^>]+>", " ", before.decode("utf-8"))
    on_page = set(NUM.findall(page_text))
    draft = yaml.safe_load(Path(rep["draft"]).read_text(encoding="utf-8"))
    said = " ".join(c["noi"] for s in draft["canh"] for c in s["cau"])
    assert said and set(NUM.findall(said)) <= on_page
    assert rep["scenes"] == 4 and rep["found"]["card"] == 6


def test_output_tags_targets_and_adds_one_layer(report):
    rep = clicky_html.build(str(report), voice=voice)
    out = Path(rep["out"]).read_text(encoding="utf-8")
    assert out.count('data-clicky-layer="1"') == 1
    assert 'data-clicky="c1"' in out and re.search(r'<tr data-clicky="c\d+r1">', out)
    assert "Trợ lý AI của" in out
    # the layer goes before </body>, so the report's own scripts still run first
    assert out.index('id="clicky-host"') < out.lower().rindex("</body>")
    with pytest.raises(goi_html.PackError, match="đã có Clicky"):
        clicky_html.build(rep["out"], voice=voice)


def test_edited_script_is_used_and_a_stale_target_stops_the_build(report, tmp_path):
    script = {"canh": [{"muc": "A", "cau": [{"noi": "Tổng chi phí nhân sự 186,4 tỷ.", "chi": "c2"}]}]}
    path = tmp_path / "kb.yaml"
    path.write_text(yaml.safe_dump(script, allow_unicode=True), encoding="utf-8")
    rep = clicky_html.build(str(report), script_path=str(path), voice=voice)
    assert rep["scenes"] == 1 and rep["draft"] is None
    script["canh"][0]["cau"][0]["chi"] = "c999"
    path.write_text(yaml.safe_dump(script, allow_unicode=True), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="c999"):
        clicky_html.build(str(report), script_path=str(path), voice=voice)


def test_voice_reads_like_a_finance_person():
    from present.doc_tai_chinh import doc, pieces
    assert doc("Tổng chi phí nhân sự AOPR: 186,4 tỷ, +12,6% so với AOP gốc.") == (
        "Tổng chi phí nhân sự ây-ô-pi điều chỉnh: một trăm tám mươi sáu phẩy bốn tỷ, "
        "tăng mười hai phẩy sáu phần trăm so với ây-ô-pi gốc.")
    assert doc("149,4 triệu/năm; nhân sự/DTT; FP&A") == (
        "một trăm bốn mươi chín phẩy bốn triệu mỗi năm; nhân sự trên doanh thu thuần; ép-pi-en-ây")
    assert doc("AOP Revised FY2026–2027, kênh MT và HRC+KA, Headcount") == (
        "ây-ô-pi điều chỉnh năm tài chính hai nghìn không trăm hai mươi sáu đến hai nghìn không trăm hai mươi bảy, "
        "kênh em-ti và hát-a-xi và cây-ây, héc-cao")
    assert pieces("Đề xuất: giữ 2,40%; giãn tuyển 30 vị trí.") == ["Đề xuất:", "giữ 2,40%;", "giãn tuyển 30 vị trí."]


def test_every_word_the_finance_reader_adds_is_known_to_the_offline_voice():
    from audio.tts.vi_g2p import phonemize
    from present.doc_tai_chinh import ENGLISH, LETTERS, WORDS
    missing = []
    phonemize(" ".join([*LETTERS.values(), *WORDS.values(), *ENGLISH.values()]).replace("-", " "), missing)
    assert not missing


def test_frames_come_just_before_the_sentence_that_names_them(report, tmp_path):
    import io
    import wave

    def wav_voice(text):              # 0.05 s of silence per character: a real WAV with a known length
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
            w.writeframes(b"\0\0" * int(400 * len(text)))
        return out.getvalue()

    path = _script(tmp_path, [{"noi": "Kính chào anh chị.", "chi": ""},
                              {"noi": "Tổng chi phí nhân sự AOPR: 186,4 tỷ.", "chi": "c2"}])
    rep = clicky_html.build(str(report), script_path=path, voice=wav_voice)
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>',
                                Path(rep["out"]).read_text(encoding="utf-8"), re.S).group(1))
    s = data["scenes"][0]
    first = 0.05 * len(clicky_html.doc_tai_chinh.doc("Kính chào anh chị."))
    lead_in = clicky_html.LEAD_IN                 # a breath of silence so audio wake-up never eats the first word
    assert s["starts"][0] == lead_in and abs(s["starts"][1] - (lead_in + first + clicky_html.PAUSE_SENTENCE)) < 0.01
    assert s["points"] == [{"id": "c2", "t": round(s["starts"][1] - clicky_html.LEAD, 3)}]


def _script(tmp_path, cau):
    path = tmp_path / "kb.yaml"
    path.write_text(yaml.safe_dump({"canh": [{"muc": "A", "cau": cau}]}, allow_unicode=True), encoding="utf-8")
    return str(path)


def test_a_figure_that_is_not_in_the_report_stops_the_build(report, tmp_path):
    # 186,4 is on the page; 187,4 is a typo the owner made while editing the talk
    path = _script(tmp_path, [{"noi": "Tổng chi phí nhân sự 187,4 tỷ.", "chi": "c2"}])
    with pytest.raises(goi_html.PackError, match="187,4"):
        clicky_html.build(str(report), script_path=path, voice=voice)
    assert not report.with_name("bao_cao_Clicky.html").exists()
    # a figure the owner adds on purpose is allowed only when he declares it, and the record says it is his
    path = _script(tmp_path, [{"noi": "Tổng chi phí nhân sự 186,4 tỷ, em có 3 ý.", "chi": "c2", "so_rieng": [3]}])
    rep = clicky_html.build(str(report), script_path=path, voice=voice)
    assert rep["record"]["so"] == 2 and rep["record"]["dung_cho_khoanh"] == 1
    assert rep["record"]["nguoi_soan_them"] == 1


def test_record_is_embedded_and_rechecked_from_the_output_file(report):
    from present import kiem_bien_ban

    rep = clicky_html.build(str(report), voice=voice)
    out = Path(rep["out"])
    res = kiem_bien_ban.verify(str(out), str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["so"] == rep["record"]["so"] > 0 and res["tong"]["nguoi_soan_them"] == 0
    assert kiem_bien_ban.main([str(out), "--goc", str(report)]) == 0
    page = out.read_text(encoding="utf-8")
    # a figure changed in the report body after the build is caught
    tampered = out.with_name("sua_so.html")
    tampered.write_text(page.replace(">186,4 tỷ<", ">196,4 tỷ<", 1), encoding="utf-8")
    assert ">196,4 tỷ<" in tampered.read_text(encoding="utf-8")
    bad = kiem_bien_ban.verify(str(tampered))
    assert not bad["ok"] and any("186,4" in e for e in bad["errors"])
    # so is a changed spoken line, and a record edited by hand
    tampered.write_text(page.replace("186,4 tỷ, tăng", "186,4 tỷ, giảm"), encoding="utf-8")
    assert not kiem_bien_ban.verify(str(tampered))["ok"]
    assert kiem_bien_ban.main([str(tampered)]) == 1
    # the wrong original is refused
    other = report.with_name("khac.html")
    other.write_text("<html><body>khác</body></html>", encoding="utf-8")
    assert not kiem_bien_ban.verify(str(out), str(other))["ok"]


def test_draft_never_cuts_a_figure_in_half():
    long = "Chi phí " + "rất dài " * 60 + "1.234.567 đồng"
    cut = clicky_html._clean(long[:395] + " 1.234.567 đồng và còn nữa", 400)
    assert all(tok in ("1.234.567",) for tok in clicky_html.numbers(cut))


def test_each_spoken_figure_carries_its_source_for_the_caption(report, tmp_path):
    path = _script(tmp_path, [{"noi": "Tổng chi phí nhân sự 186,4 tỷ, em có 3 ý.", "chi": "c2", "so_rieng": [3]},
                              {"noi": "Headcount 1.248 người.", "chi": "c2"},
                              {"noi": "Chưa có số ở câu này.", "chi": ""}])
    rep = clicky_html.build(str(report), script_path=path, voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    # framed place = k, owner's own = o, elsewhere in the report = b, and one list per spoken sentence
    assert data["scenes"][0]["ev"] == [[["186,4", "k"], ["3", "o"]], [["1.248", "b"]], []]


def test_layer_script_parses():
    # a syntax error in the layer only shows in a browser; node catches it on the Windows CI too
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    import subprocess
    layer = (Path(clicky_html.__file__).with_name("clicky_html_layer.html")).read_text(encoding="utf-8")
    js = re.findall(r"<script>(.*?)</script>", layer, re.S)[0]
    r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


def test_a_word_the_voice_cannot_say_is_spelled_not_dropped():
    import io
    import wave

    class Voice:                        # knows every word except "Xyzco"
        missing = []

        def synthesize(self, text):
            self.missing = [w for w in re.findall(r"\w+", text) if w.lower() == "xyzco"]
            self.heard = text
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
                w.writeframes(b"\0\0" * 80)
            return out.getvalue()

    v = Voice()
    clicky_html.voice_lines(["Doanh thu kênh Xyzco tăng."], v.synthesize)
    assert "Xyzco" not in v.heard and "ích oai dét xi ô" in v.heard

    class Mute(Voice):                  # even the letters fail: stop, never skip a word silently
        def synthesize(self, text):
            data = super().synthesize(text)
            self.missing = self.missing or ["ích"]
            return data

    with pytest.raises(clicky_html.PackError, match="TU_DOC"):
        clicky_html.voice_lines(["Doanh thu kênh Xyzco tăng."], Mute().synthesize)
