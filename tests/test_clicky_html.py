"""Clicky inside an HTML report: the talk uses only the report's own words, the original file is never touched."""
import json
import datetime as dt
import re
import sys
import shutil
from pathlib import Path

import pytest
import yaml

from present import clicky_html, goi_html

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"
NUM = re.compile(r"\d[\d.,]*")
BUILD = clicky_html.build                  # the real one: build() refuses to run without an owner and an e-mail
OWNER = {"owner": "anh Tuấn", "contact": "tuan@congty.vn"}


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    monkeypatch.setattr(clicky_html, "build", lambda *a, **k: BUILD(*a, **{**OWNER, **k}))
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
    on_page = {t.rstrip(".,") for t in NUM.findall(page_text)}
    draft = yaml.safe_load(Path(rep["draft"]).read_text(encoding="utf-8"))
    said = " ".join(c["noi"] for s in draft["canh"] for c in s["cau"])
    assert said and {t.rstrip(".,") for t in NUM.findall(said)} <= on_page
    assert rep["found"]["card"] == 6
    # a talk to the Board: greeting, key message, the largest variances, one request, thanks. The comment of
    # "2. Chi tiết theo khối" only repeats the first driver (14,1 on Khối Bán hàng): its reason is said there
    # instead, so the part is not presented and nothing says "phần hai" after no "phần một"
    assert [s["muc"] for s in draft["canh"]] == ["Mở đầu", "Thông điệp chính", "Nguyên nhân chính",
                                                  "Đề nghị Ban Giám đốc"]
    lines = [c["noi"] for s in draft["canh"] for c in s["cau"]]
    assert sum("14,1" in x for x in lines) == 1
    assert "Nguyên nhân là kế hoạch tuyển thêm 48 nhân viên bán hàng kênh MT và HRC+KA." in lines
    assert not any(re.search(r"\bPhần \d|Phần hai", x) for x in lines)
    first = [c["noi"] for s in draft["canh"] for c in s["cau"]]
    assert first[0] == ("Em kính chào anh chị ạ. Em là trợ lý AI của anh Tuấn, xin trình bày AOP Revised "
                        "FY2026–2027: Chi phí nhân sự quản trị.")
    assert first[-1] == "Em xin cảm ơn anh chị đã lắng nghe."


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
    assert rep["scenes"] == 1 and rep["kich_ban"] == str(path) and rep["draft"].endswith("_kich_ban_nhap.yaml")
    script["canh"][0]["cau"][0]["chi"] = "c999"
    path.write_text(yaml.safe_dump(script, allow_unicode=True), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="c999"):
        clicky_html.build(str(report), script_path=str(path), voice=voice)


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
    assert "AOPR tăng 12,6%" in page
    tampered.write_text(page.replace("AOPR tăng 12,6%", "AOPR giảm 12,6%"), encoding="utf-8")
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
                              {"noi": "Headcount 1.248 người.", "chi": "c2", "xac_nhan": ["1.248"]},
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


# --- round 1 review: the talk, the lock and the edit loop ---------------------------------------------------

EN_REPORT = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>P&L T10/2026</title></head><body>
<h1>Báo cáo kết quả kinh doanh tháng 10/2026</h1><div>Đơn vị: triệu VND</div>
<div class="stat"><div class="lbl">Doanh thu thuần T10</div><div class="val">412,580</div><div class="var">(5.2%) vs AOP</div></div>
<h3>Key messages</h3><ul><li>Doanh thu T10 thấp hơn AOP 22,630 tr, chủ yếu do kênh GT giảm sản lượng 8%.</li>
<li>Biên gộp giảm 1.4 điểm % do giá đường tăng.</li></ul>
<h3>P&amp;L tháng 10</h3><table><tr><th>Chỉ tiêu</th><th>Actual</th><th>AOP</th><th>Var</th><th>Var %</th></tr>
<tr><td>Doanh thu thuần</td><td>412,580</td><td>435,210</td><td>(22,630)</td><td>(5.2%)</td></tr>
<tr><td>Giá vốn</td><td>(294,150)</td><td>(304,210)</td><td>10,060</td><td>3.3%</td></tr>
<tr class="sub"><td>EBITDA</td><td>41,250</td><td>51,120</td><td>(9,870)</td><td>(19.3%)</td></tr>
<tr><td>Chi phí quản lý</td><td>(24,880)</td><td>(24,460)</td><td>(420)</td><td>(1.7%)</td></tr></table>
<h3>Recommendations</h3><p>Đề xuất: tăng ngân sách trade kênh GT 2,000 tr trong T11-T12 để bù sản lượng.</p>
</body></html>"""


def _page(tmp_path, html, name="bc.html"):
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


def _data_of(out):
    return json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>',
                                Path(out).read_text(encoding="utf-8"), re.S).group(1))


def _record_of(out):
    return json.loads(re.search(r'<script id="clicky-bien-ban" type="application/json">(.*?)</script>',
                                Path(out).read_text(encoding="utf-8"), re.S).group(1))


def _draft(rep):
    return clicky_html.load_script(rep["kich_ban"])


def _id(report, start):
    """The data-clicky id of the place whose text starts with `start` (ids depend on the outline)."""
    root = clicky_html.parse(Path(report).read_text(encoding="utf-8"))
    _, items, rows = clicky_html.tag_targets(Path(report).read_text(encoding="utf-8"), root)
    places = clicky_html.make_places(items, rows)
    exact = [p.id for p in places.values() if start in (p.anchor, p.label)]
    return exact[0] if exact else next(p.id for p in places.values() if p.anchor.startswith(start))


def _cp1258(text):
    """Text as Excel writes it in Windows-1258: tone marks as separate combining characters."""
    import unicodedata
    out = b""
    for ch in text:
        try:
            out += ch.encode("cp1258")
        except UnicodeEncodeError:
            d = unicodedata.normalize("NFD", ch)
            tone = "".join(c for c in d if c in "\u0300\u0301\u0303\u0309\u0323")
            base = "".join(c for c in d if c not in tone)
            out += unicodedata.normalize("NFC", base).encode("cp1258") + tone.encode("cp1258")
    return out


def test_english_number_marks_are_read_as_written_and_spoken_right(report, tmp_path):
    heard = []
    rep = clicky_html.build(str(_page(tmp_path, EN_REPORT)), voice=lambda t: heard.append(t) or t.encode())
    assert rep["dau_so"] == "en"
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    # the largest variances come from the table rows, "(x)" is said as negative, subtotals are not drivers
    # revenue below plan: "thấp hơn", never a double negative ("thấp hơn … âm 5.2%"); 22,630 was in the key
    # message, so the driver says the rate only. Costs written in brackets: "vượt" above plan; below plan while
    # revenue is under plan too is no saving, just "thấp hơn" (FP&A round 3)
    assert "Thứ nhất, Doanh thu thuần thấp hơn AOP 5.2%." in said
    assert sum("22,630" in s for s in said) == 1
    assert "Thứ hai, Giá vốn thấp hơn AOP 10,060 triệu, tức 3.3%." in said
    assert not any("tiết kiệm" in s for s in said)
    assert "Thứ ba, Chi phí quản lý vượt AOP 420 triệu, tức 1.7%." in said
    assert not any("EBITDA chênh lệch" in s for s in said)
    # 412,580 triệu is 412 tỷ 580 triệu (not 412.58), and 2,000 tr is said "hai tỷ"
    assert any("bốn trăm mười hai tỷ năm trăm tám mươi triệu" in h for h in heard)
    assert any("ngân sách trade kênh di-ti hai tỷ trong" in h for h in heard)
    rec = _record_of(rep["out"])
    assert rec["dau_so"] == "en" and {"so": "412,580", "gia_tri": "412580", "dau": "", "nguon": _id(tmp_path / "bc.html", "Doanh thu thuần T10")} in [x for r in rec["cau"] for x in r["so"]]
    # English headings are said in Vietnamese, in the bar and in the voice alike
    assert "Recommendations" not in " ".join(said) and any(s["muc"] == "Đề nghị Ban Giám đốc" for s in _draft(rep)["canh"])


def test_a_report_that_mixes_number_marks_stops(report, tmp_path):
    mixed = EN_REPORT.replace("<td>412,580</td><td>435,210</td>", "<td>412,6</td><td>435,2</td>")
    with pytest.raises(goi_html.PackError, match="hai kiểu"):
        clicky_html.build(str(_page(tmp_path, mixed)), voice=voice)


def test_without_owner_and_email_the_build_stops_and_the_page_mails_the_owner(report, tmp_path):
    with pytest.raises(goi_html.PackError, match="người chịu trách nhiệm"):
        BUILD(str(report), voice=voice)
    with pytest.raises(goi_html.PackError, match="email"):
        BUILD(str(report), owner="anh Tuấn", contact="", voice=voice)
    rep = BUILD(str(report), owner="chị Lan", contact="lan@congty.vn", voice=voice)
    meta = _data_of(rep["out"])["meta"]
    assert meta["owner"] == "chị Lan" and meta["contact"] == "lan@congty.vn"
    assert "trợ lý AI của chị Lan" in _draft(rep)["canh"][0]["cau"][0]["noi"]


def test_the_lock_checks_direction_unit_and_place(report, tmp_path):
    card = _id(report, "Tổng chi phí nhân sự AOPR")
    cases = [("Tổng chi phí nhân sự AOPR là 186,4 tỷ, giảm 12,6% so với AOP gốc.", "tăng, câu nói lại là giảm"),
             ("Tổng chi phí nhân sự AOPR là 186,4 triệu.", "«tỷ», câu nói lại là «triệu»"),
             ("Tổng chi phí nhân sự AOPR là 186,4 tỷ, tăng 12,0%.", "không ở chỗ Clicky khoanh")]
    for noi, why in cases:
        with pytest.raises(goi_html.PackError, match=re.escape(why)):
            clicky_html.build(str(report), script_path=_script(tmp_path, [{"noi": noi, "chi": card}]), voice=voice)
    # a figure from elsewhere on the page passes only when the owner confirms it; years are not figures
    path = _script(tmp_path, [{"noi": "Kế hoạch FY2026–2027 tăng 12,0% ở khối CNTT.", "chi": card, "xac_nhan": ["12,0"]}])
    rep = clicky_html.build(str(report), script_path=path, voice=voice)
    assert rep["record"]["cho_khac_trong_bao_cao"] == 1 and rep["record"]["so"] == 1
    assert any("xác nhận" in n for n in rep["canh_bao"])
    # a negative figure on the page must be said with its sign
    en = _page(tmp_path, EN_REPORT, "en.html")
    row = _id(en, "Doanh thu thuần")
    with pytest.raises(goi_html.PackError, match="thiếu chữ"):
        clicky_html.build(str(en), script_path=_script(tmp_path, [{"noi": "Doanh thu thuần lệch 22,630.", "chi": row}]),
                          voice=voice)


def test_frames_follow_their_text_when_the_report_changes(report, tmp_path):
    rep = clicky_html.build(str(report), voice=voice)
    path = Path(rep["kich_ban"])
    text = path.read_text(encoding="utf-8").replace("da_duyet: 'chưa'", "da_duyet: 'có'")
    path.write_text(text, encoding="utf-8")
    # next month a card is added on top: every position shifts by one
    html = report.read_text(encoding="utf-8").replace(
        '<div class="kpi-grid">',
        '<div class="kpi-grid"><div class="kpi-card"><div class="kpi-label">Doanh thu thuần</div>'
        '<div class="kpi-value">7.398 tỷ</div><div class="kpi-delta">+7,2%</div></div>', 1)
    report.write_text(html, encoding="utf-8")
    Path(rep["out"]).unlink()
    rep2 = clicky_html.build(str(report), voice=voice)
    assert rep2["doi_neo"] and rep2["trang_thai"].startswith("CHÍNH THỨC")
    data = _data_of(rep2["out"])
    card = _id(report, "Tổng chi phí nhân sự AOPR")
    assert any(p["id"] == card for s in data["scenes"] for p in s["points"])
    # a figure that changed at its place stops the build instead of reading last month's number
    report.write_text(html.replace(">186,4 tỷ<", ">190,1 tỷ<"), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="186,4"):
        clicky_html.build(str(report), voice=voice)


def test_paragraph_and_bullet_commentary_is_spoken(report, tmp_path):
    v3 = """<html><body><h1>Báo cáo quản trị tháng 10/2026</h1><h2>1. Kết quả kinh doanh</h2>
<p>Doanh thu thuần tháng 10 đạt 412,6 tỷ, thấp hơn kế hoạch 22,6 tỷ (−5,2%).</p>
<table><tr><th>Chỉ tiêu</th><th>Thực hiện</th><th>Kế hoạch</th></tr><tr><td>Doanh thu thuần</td><td>412,6</td><td>435,2</td></tr></table>
<h2>2. Dòng tiền</h2><p>Tiền cuối kỳ 356,0 tỷ, giảm 41,2 tỷ do trả cổ tức 60,0 tỷ.</p>
<h2>3. Phụ lục</h2><p>Xem chi tiết ở file đính kèm.</p>
<h2>4. Kiến nghị</h2><ul><li>Phê duyệt tăng 2,0 tỷ trade kênh GT cho T11–T12.</li><li>Giao Mua hàng đàm phán lại giá.</li></ul>
</body></html>"""
    rep = clicky_html.build(str(_page(tmp_path, v3)), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert said[1].startswith("Thông điệp chính: Doanh thu thuần tháng 10 đạt 412,6 tỷ")
    assert "Tiền cuối kỳ 356,0 tỷ, giảm 41,2 tỷ do trả cổ tức 60,0 tỷ." in said
    # every point of the recommendation is read, in order
    assert "Kính đề nghị Ban Giám đốc: thứ nhất, phê duyệt tăng 2,0 tỷ trade kênh GT cho T11–T12." in said
    assert "Thứ hai, giao Mua hàng đàm phán lại giá." in said
    assert any("«3. Phụ lục»" in n for n in rep["canh_bao"])           # a part that says no figure is named


def test_cards_are_said_as_sentences_and_never_repeat_a_figure(report):
    rep = clicky_html.build(str(report), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    # "+12,6%" was said in the key message a moment before: the card does not say it again
    assert "Tổng chi phí nhân sự AOPR là 186,4 tỷ." in said
    assert sum("12,6" in s for s in said) == 1
    assert not any(s.startswith("Trần đề xuất") for s in said)           # 177,5 was just said in the request
    assert "So với AOPR, cần giảm 8,9 tỷ." in said
    # the drivers frame the row they talk about, not the whole table, with the column's unit and the words of a
    # cost line above plan
    row = _id(report, "Khối Bán hàng")
    assert {"noi": "Thứ nhất, khối Bán hàng vượt AOP gốc 14,1 tỷ, tức 18,0%.", "chi": row, "neo": "Khối Bán hàng"} \
        in [c for s in _draft(rep)["canh"] for c in s["cau"]]
    # the chart loads its library from the internet: no line about it, and the owner is told
    assert not any("Biểu đồ" in s for s in said) and any("Internet" in n for n in rep["canh_bao"])


def test_chart_line_uses_the_charts_own_series(report, tmp_path):
    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>', "")
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Biểu đồ này cho thấy Chi phí nhân sự từ FY2425 đến FY2627 AOPR." in said
    assert not any("doanh thu 3 năm" in s for s in said)                 # the aria-label claims a series it has not


def test_dropping_the_report_again_keeps_the_edited_script(report):
    rep = clicky_html.build(str(report), voice=voice)
    path = Path(rep["kich_ban"])
    assert rep["trang_thai"] == "SƠ BỘ: kịch bản nháp chưa duyệt"
    # an untouched draft is simply refreshed
    rep = clicky_html.build(str(report), voice=voice)
    assert rep["kich_ban"] == str(path) and rep["draft"] == str(path)
    edited = path.read_text(encoding="utf-8").replace("Em xin cảm ơn anh chị đã lắng nghe.", "Em cảm ơn anh chị.")
    path.write_text(edited, encoding="utf-8")
    rep = clicky_html.build(str(report), voice=voice)
    assert path.read_text(encoding="utf-8") == edited                     # never written over
    assert rep["draft"].endswith("bao_cao_kich_ban_nhap.yaml") and Path(rep["draft"]).exists()
    assert rep["trang_thai"] == "SƠ BỘ: kịch bản đã sửa nhưng chưa ghi duyệt"
    assert "Em cảm ơn anh chị." in _data_of(rep["out"])["scenes"][-1]["sentences"]


def test_script_errors_are_explained_in_vietnamese_with_the_line(report, tmp_path):
    rep = clicky_html.build(str(report), voice=voice)
    good = Path(rep["kich_ban"]).read_text(encoding="utf-8")
    lines = good.splitlines()
    k = next(i for i, l in enumerate(lines) if l.strip().startswith("chi: c"))
    bad = tmp_path / "tab.yaml"
    bad.write_text("\n".join(lines[:k] + ["\t" + lines[k].strip()] + lines[k + 1:]), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match=rf"Kịch bản lỗi ở dòng {k + 1}: dòng này có phím Tab"):
        clicky_html.build(str(report), script_path=str(bad), voice=voice)
    line = next(l for l in lines if "Nguyên nhân chính nằm ở" in l)
    bad.write_text(good.replace(line, line.split("- noi:")[0] + "- noi: Phần 2: Chi tiết: xem bảng."),
                   encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="dấu hai chấm"):
        clicky_html.build(str(report), script_path=str(bad), voice=voice)
    # so_rieng: [0,12] is one Vietnamese figure; the file may come with a BOM or in the old ANSI code page
    card = _id(report, "Tỷ lệ chi phí nhân sự/DTT")
    text = ("canh:\n- muc: A\n  cau:\n  - noi: 'Tỷ lệ chi phí nhân sự/DTT là 2,52%, cao hơn AOP gốc 0,12 điểm.'\n"
            f"    chi: {card}\n    so_rieng: [0,12]\n")
    bad.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert clicky_html.build(str(report), script_path=str(bad), voice=voice)["record"]["nguoi_soan_them"] == 1
    bad.write_bytes(_cp1258(text))
    assert clicky_html.build(str(report), script_path=str(bad), voice=voice)["record"]["nguoi_soan_them"] == 1
    # the draft writes every sentence in quotes, so a colon in it never breaks the file
    assert all(l.split("noi: ", 1)[1].startswith("'") for l in good.splitlines() if "- noi: " in l)


def test_console_says_pass_what_to_do_and_where_in_three_lines(report, tmp_path, capsys, monkeypatch):
    mem = tmp_path / "nho"
    from present import tri_nho
    tri_nho.save_owner("anh Tuấn", "tuan@congty.vn", str(mem))
    monkeypatch.setattr(clicky_html, "offline_voice", lambda: voice)
    monkeypatch.setattr(clicky_html, "build", BUILD)
    assert clicky_html.main([str(report), "--tri-nho", str(mem)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("ĐẠT (SƠ BỘ: kịch bản nháp chưa duyệt).") and out[1].startswith("Cần xem:")
    assert out[2] == "File: " + str(report.with_name("bao_cao_Clicky.html"))
    assert not any(l.lstrip().startswith("{") for l in out)
    report.with_name("bao_cao_Clicky.html").unlink()
    assert clicky_html.main([str(report), "--tri-nho", str(mem), "--kich-ban", str(report)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("KHÔNG ĐẠT.") and not report.with_name("bao_cao_Clicky.html").exists()


def test_mp3_without_ffmpeg(monkeypatch):
    lameenc = pytest.importorskip("lameenc")
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(22050)
        w.writeframes(b"\x10\x00\xf0\xff" * 22050)
    monkeypatch.setattr(clicky_html.shutil, "which", lambda name: None)
    mp3, secs = clicky_html.wav_to_mp3(out.getvalue())
    assert goi_html.audio_mime(mp3) == "audio/mpeg" and secs == 2.0 and len(mp3) < len(out.getvalue()) / 5
    assert lameenc


def test_excel_web_page_in_windows_1258_is_read_and_rewritten_as_utf8(report, tmp_path):
    html = ('<html><head><meta http-equiv=Content-Type content="text/html; charset=windows-1258"></head><body>'
            '<table><tr><td colspan=4>Đơn vị: tỷ đồng</td></tr><tr><td rowspan=2>Khối</td><td colspan=2>Kế hoạch so Thực hiện</td><td>Chênh lệch</td></tr>'
            '<tr><td>AOP</td><td>Thực hiện</td><td>Tỷ đồng</td></tr>'
            '<tr><td>Bán hàng</td><td>63,2</td><td>79,1</td><td>15,9</td></tr>'
            '<tr><td>Văn phòng</td><td>82,0</td><td>89,3</td><td>7,3</td></tr></table></body></html>')
    path = tmp_path / "excel.htm"
    path.write_bytes(_cp1258(html))
    rep = clicky_html.build(str(path), voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    assert rep["ma_hoa"] == "windows-1258" and "charset=utf-8" in page and "Văn phòng" in page
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    # the plan column is named by its own header cell ("AOP"), not the merged banner above it
    assert "Thứ nhất, Bán hàng cao hơn AOP 15,9 tỷ." in said


def test_nothing_to_say_is_explained_and_no_empty_script_is_left(report, tmp_path):
    js = ('<html><body><h1>Báo cáo lãi lỗ tháng 10</h1><div id="app">Đang tải…</div><script>'
          'document.getElementById("app").innerHTML="<table></table>";</script></body></html>')
    with pytest.raises(goi_html.PackError, match="JavaScript"):
        clicky_html.build(str(_page(tmp_path, js, "js.html")), voice=voice)
    assert not (tmp_path / "js_kich_ban_Clicky.yaml").exists()
    frames = '<html><frameset rows="*,39"><frame src="wb_files/sheet001.htm"></frameset></html>'
    with pytest.raises(goi_html.PackError, match="wb_files"):
        clicky_html.build(str(_page(tmp_path, frames, "wb.htm")), voice=voice)
    prose = ("<html><body><h1>Thư tháng 10</h1><p>Kính gửi Ban Giám đốc,</p><p>Doanh thu thuần đạt 1.234,5 tỷ đồng, "
             "tăng 8,2% so với cùng kỳ.</p></body></html>")
    rep = clicky_html.build(str(_page(tmp_path, prose, "thu.html")), voice=voice)
    assert "Thông điệp chính: Doanh thu thuần đạt 1.234,5 tỷ đồng, tăng 8,2% so với cùng kỳ." in \
        [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]


def test_report_text_cannot_break_the_embedded_data(report, tmp_path):
    from present import kiem_bien_ban
    html = ('<html><body><h1>Thử</h1><h2>1. A</h2><p class="note">Nhận xét: mã __BIEN_BAN__ __DATA__ và '
            '&lt;!--&lt;script&gt; trong ô, doanh thu 123,4 tỷ.</p></body></html>')
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    # the draft says what a person can say; the owner may still write the markers into his own sentence
    assert not any("__BIEN_BAN__" in s or "<" in s for sc in _data_of(rep["out"])["scenes"] for s in sc["sentences"])
    kb = Path(rep["kich_ban"])
    kb.write_text(kb.read_text(encoding="utf-8").replace("Thông điệp chính:", "Mã __BIEN_BAN__ __DATA__ <!--<script>"),
                  encoding="utf-8")
    rep = clicky_html.build(str(tmp_path / "bc.html"), voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = _data_of(rep["out"])
    assert any("__BIEN_BAN__" in s and "<!--<script>" in s for sc in data["scenes"] for s in sc["sentences"])
    blob = re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert "<" not in blob
    assert kiem_bien_ban.verify(rep["out"])["ok"]


def test_kiem_bien_ban_does_not_pass_a_talk_without_figures(report, tmp_path):
    from present import kiem_bien_ban
    rep = clicky_html.build(str(report), voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    rec = _record_of(rep["out"])
    data = _data_of(rep["out"])
    rec["cau"], data["scenes"] = [], []
    rec["ma_bam"] = clicky_html.seal(rec)
    page = re.sub(r'(<script id="clicky-bien-ban" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(rec) + m.group(2), page, flags=re.S)
    page = re.sub(r'(<script id="clicky-data" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(data) + m.group(2), page, flags=re.S)
    empty = report.with_name("rong.html")
    empty.write_text(page, encoding="utf-8")
    res = kiem_bien_ban.verify(str(empty))
    assert not res["ok"] and "không nói con số nào" in res["errors"][0]


def test_an_excel_export_without_title_or_h1_names_itself_from_its_first_caption():
    """The page shows meta.title as the source; an Excel export must not show its file name."""
    from present.clicky_html import _first_caption, parse
    page = ("<html><body><table><tr><td colspan=3>BÁO CÁO CHI PHÍ THÁNG 10/2026</td></tr>"
            "<tr><td>Lương</td><td>12,5</td></tr></table></body></html>")
    assert _first_caption(parse(page)) == "BÁO CÁO CHI PHÍ THÁNG 10/2026"
    assert _first_caption(parse("<div><div>12,5</div></div>")) == ""


# --- round 2: the lock reads tables by column, brackets by one rule, every figure by its subject ----------------

def _lock(report, chi_start, noi):
    """Why make_record stops on `noi` framed on the place that starts with `chi_start` ("" when it passes)."""
    src = Path(report).read_text(encoding="utf-8")
    root = clicky_html.parse(src)
    _, items, rows = clicky_html.tag_targets(src, root)
    page_text = root.text() + " " + clicky_html._labels(root)
    en = clicky_html.number_marks([p.text for p in clicky_html.make_places(items, rows).values()])
    declared = clicky_html.declared_unit(page_text)
    places = clicky_html.make_places(items, rows, root, en, declared)
    chi = _id(report, chi_start) if chi_start else ""
    meta = {"report": "x", "report_sha256": "x", "built_iso": "t", "owner": "anh Tuấn"}
    try:
        clicky_html.make_record({"canh": [{"muc": "A", "cau": [{"noi": noi, "chi": chi}]}]}, places, page_text, meta,
                                en, declared)
        return ""
    except goi_html.PackError as exc:
        return str(exc)


def test_table_cells_are_locked_by_column_unit_and_direction(report):
    # unsigned cells are positive; the unit comes from the table; the figure is tied to the column it names
    assert "cột «Chênh lệch» ghi tăng" in _lock(report, "Khối Bán hàng", "Khối Bán hàng giảm 14,1 tỷ, tức giảm 18,0%.")
    assert "«tỷ», câu nói lại là «triệu»" in _lock(report, "Khối Bán hàng", "Khối Bán hàng tăng 14,1 triệu.")
    assert "cột «Chênh lệch» ghi tăng" in _lock(report, "Tổng", "Tổng chi phí giảm 20,9 tỷ.")
    assert "là cột «AOPR», không phải cột «Chênh lệch»" in _lock(report, "Khối Bán hàng", "Khối Bán hàng chênh lệch 92,3 tỷ.")
    assert "là cột «AOPR», không phải cột «AOP gốc»" in _lock(report, "Khối Bán hàng",
                                                              "Khối Bán hàng: AOP gốc 92,3 tỷ, AOPR 78,2 tỷ.")
    assert "«người», câu nói lại là «tỷ»" in _lock(report, "Khối Bán hàng", "Chi phí khối Bán hàng là 712 tỷ.")
    assert "câu nói về «Khối Marketing»" in _lock(report, "Khối Bán hàng", "Khối Marketing AOPR 92,3 tỷ.")
    # what a person really says passes: the change named against the plan column, the rate after "tức"
    assert _lock(report, "Khối Bán hàng", "Khối Bán hàng tăng 14,1 tỷ, tức 18,0%.") == ""
    assert _lock(report, "Khối Bán hàng", "Thứ nhất, khối Bán hàng vượt AOP gốc 14,1 tỷ, tức 18,0%.") == ""
    assert _lock(report, "Tổng", "Tổng chi phí nhân sự AOPR là 186,4 tỷ, tăng 20,9 tỷ so với AOP gốc 165,5 tỷ.") == ""


def test_cards_and_notes_are_locked_by_sign_subject_and_wording(report):
    assert "báo cáo ghi số dương" in _lock(report, "Tổng chi phí nhân sự AOPR", "Chênh lệch là -186,4 tỷ.")
    assert "báo cáo ghi tăng" in _lock(report, "Tổng chi phí nhân sự AOPR", "Tổng chi phí 186,4 tỷ, ít đi 12,6%.")
    # the card's label gives its value a direction: "Cần giảm so với AOPR" / "8,9 tỷ"
    assert _lock(report, "Cần giảm so với AOPR", "So với AOP điều chỉnh, cần giảm 8,9 tỷ.") == ""
    assert "báo cáo ghi giảm" in _lock(report, "Cần giảm so với AOPR", "So với AOPR, cần tăng 8,9 tỷ.")
    # 7,2% is revenue's in the note, 12,6% is cost's
    assert "số 7,2 là của «doanh thu»" in _lock(report, "Thông điệp chính",
                                                "Chi phí nhân sự AOPR tăng 7,2%, doanh thu tăng 12,6%.")
    # a direction word of another clause does not reach the figure
    assert _lock(report, "Tổng chi phí nhân sự AOPR", "Doanh thu giảm nhưng chi phí nhân sự 186,4 tỷ.") == ""
    # a figure in words cannot be checked: the build asks for digits
    assert "số viết bằng chữ" in _lock(report, "Thông điệp chính", "Chi phí nhân sự tăng mười hai phẩy sáu phần trăm.")
    # a bracket the lock cannot read stops and asks
    assert "chưa rõ là số âm hay tỷ trọng" in _lock(report, "Khối Bán hàng", "Khối Bán hàng (14,1).")
    # money in brackets is negative: the cell says 14,1, so "(14,1) tỷ" is wrong, not unclear
    assert "câu nói lại là giảm/âm" in _lock(report, "Khối Bán hàng", "Khối Bán hàng (14,1) tỷ.")


def test_kiem_bien_ban_checks_table_columns_like_the_build(report):
    from present import kiem_bien_ban
    rep = clicky_html.build(str(report), voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    rec, data = _record_of(rep["out"]), _data_of(rep["out"])
    old = "Thứ nhất, khối Bán hàng vượt AOP gốc 14,1 tỷ, tức 18,0%."
    new = "Thứ nhất, khối Bán hàng chênh lệch 92,3 tỷ, tức 18,0%."
    row = next(r for r in rec["cau"] if r["noi"] == old)
    row["noi"], row["so"][0]["so"], row["so"][0]["gia_tri"] = new, "92,3", "92.3"
    for s in data["scenes"]:
        s["sentences"] = [new if x == old else x for x in s["sentences"]]
    rec["ma_bam"] = clicky_html.seal(rec)
    page = re.sub(r'(<script id="clicky-bien-ban" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(rec) + m.group(2), page, flags=re.S)
    page = re.sub(r'(<script id="clicky-data" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(data) + m.group(2), page, flags=re.S)
    bad = report.with_name("cot_sai.html")
    bad.write_text(page, encoding="utf-8")
    res = kiem_bien_ban.verify(str(bad))
    assert not res["ok"] and any("không phải cột «Chênh lệch»" in e for e in res["errors"])


def test_brackets_are_negative_only_where_a_reader_reads_them_so():
    F = clicky_html.figures
    assert [(f.sign, f.how) for f in F("(9,870)")] == [("-", "sym")]                   # a whole cell
    assert [(f.sign, f.how) for f in F("Lợi nhuận: (5,2) tỷ")] == [("-", "sym")]       # after ":"
    assert [(f.sign, f.how) for f in F("Net income was (1,234.5) million")] == [("-", "sym")]
    share = F("Chi phí 186,4 tỷ (12,6%) so với AOP")                                    # beside a figure
    assert share[1].sign is None and share[1].how == "" and share[1].unit == "%"
    assert F("Bán hàng chiếm (42%) tổng chi phí")[0].sign is None
    assert F("Khối Bán hàng (14,1)")[0].how == "paren?"                                 # unclear: asked
    assert [(f.sign, f.how) for f in F("EBIT (2,1) tỷ")] == [("-", "sym")]             # money in brackets
    assert [(f.sign, f.unit) for f in F("Lỗ khác (9,8) triệu")] == [("-", "triệu")]
    assert F("Đề xuất: (1) tăng ngân sách") == []                                       # a list marker
    # the voice reads brackets with the very same rule
    v = clicky_html.for_voice
    assert v("Lợi nhuận: (9,870) tỷ.") == "Lợi nhuận: âm 9,870 tỷ."
    assert v("Chi phí 186,4 tỷ (12,6%) so với AOP.") == "Chi phí 186,4 tỷ, 12,6%, so với AOP."


def test_a_note_in_the_other_number_marks_stops_the_build(report, tmp_path):
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "Khối Bán hàng tăng mạnh nhất, 14,1 tỷ", "Khối Bán hàng tăng mạnh nhất, 1,234.5 tỷ")
    with pytest.raises(goi_html.PackError, match="hai kiểu lẫn nhau"):
        clicky_html.build(str(_page(tmp_path, html)), voice=voice)


def test_thousands_split_by_spaces_are_one_figure():
    for t in ("Tổng 1 234 567 triệu", "Tổng 1\u00a0234\u00a0567 triệu", "Tổng 1\u202f234\u202f567 triệu"):
        f = clicky_html.figures(t)
        assert len(f) == 1 and clicky_html.value(f[0].tok) == 1234567, t
    assert clicky_html.value(clicky_html.figures("1 234 567")[0].tok, en=True) == 1234567
    assert clicky_html.number_marks(["Revenue 1 234 567 and 5.2%"]) is True       # spaces say nothing about marks


def test_card_lines_keep_the_values_sign_and_unit_and_never_repeat():
    card = clicky_html.parse('<div class="kpi"><div>Lợi nhuận gộp</div><div>−5,4</div><div>+1,2 vs AOP</div></div>')
    n = next(x for x in card.walk() if x.attrs.get("class") == "kpi")
    assert clicky_html.card_line(n, "tỷ") == "Lợi nhuận gộp là âm 5,4 tỷ, tăng 1,2 tỷ so với AOP."
    assert clicky_html.card_line(n, "tỷ", {clicky_html.value("1,2")}) == "Lợi nhuận gộp là âm 5,4 tỷ."
    card = clicky_html.parse('<div class="kpi"><div>EBITDA</div><div>(9,870)</div><div>(1,5%) vs AOP</div></div>')
    n = next(x for x in card.walk() if x.attrs.get("class") == "kpi")
    assert clicky_html.card_line(n) == "EBITDA là âm 9,870, giảm 1,5% so với AOP."
    card = clicky_html.parse('<div class="kpi"><div>Số khách hàng</div><div>1.200</div></div>')
    n = next(x for x in card.walk() if x.attrs.get("class") == "kpi")
    assert clicky_html.card_line(n, "triệu") == "Số khách hàng là 1.200."             # not money: no unit added


def test_v2_cards_say_the_declared_unit(report, tmp_path):
    html = """<html><body><h1>P&L T10</h1><div>Đơn vị: triệu VND</div>
<div class="kpi"><div>Doanh thu thuần</div><div>412.580</div><div>−5,2% vs AOP</div></div>
<p class="insight">Kết luận: doanh thu thấp hơn kế hoạch 5,2%.</p></body></html>"""
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Doanh thu thuần là 412.580 triệu, giảm 5,2% so với AOP." in said or \
        "Doanh thu thuần là 412.580 triệu." in said


def test_sections_are_named_without_the_reports_numbering():
    assert clicky_html.heading_title("2. Chi tiết theo khối") == "Chi tiết theo khối"
    assert clicky_html.heading_title("II. Rủi ro") == "Rủi ro"
    assert clicky_html.heading_title("Phần 3: Đề xuất") == "Đề xuất"
    assert clicky_html.heading_title("Key messages") == "Thông điệp chính"


def test_every_recommendation_is_read_and_extra_ones_are_named(report, tmp_path):
    items = "".join(f"<li>Ý kiến số {k} về ngân sách {k},5 tỷ.</li>" for k in range(1, 8))
    html = f"""<html><body><h1>Báo cáo tháng 10/2026</h1><h2>Tổng quan</h2>
<p class="insight">Kết luận: doanh thu đạt 412,6 tỷ.</p><h2>Kiến nghị</h2><ul>{items}</ul></body></html>"""
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Kính đề nghị Ban Giám đốc: thứ nhất, ý kiến số 1 về ngân sách 1,5 tỷ." in said
    assert "Thứ năm, ý kiến số 5 về ngân sách 5,5 tỷ." in said
    assert any("bản nháp chỉ đọc 5 ý đầu" in n and "Ý kiến số 6" in n for n in rep["canh_bao"])


def _approve(path):
    p = Path(path)
    p.write_text(p.read_text(encoding="utf-8").replace("da_duyet: 'chưa'", "da_duyet: 'có'"), encoding="utf-8")


def test_a_changed_ranking_turns_an_approved_talk_back_to_preliminary(report):
    rep = clicky_html.build(str(report), voice=voice)
    _approve(rep["kich_ban"])
    assert clicky_html.build(str(report), voice=voice)["trang_thai"].startswith("CHÍNH THỨC")
    # Khối CNTT (1,9) now ranks third instead of Chuỗi cung ứng (2,1): the approved "Thứ hai" line is wrong
    html = report.read_text(encoding="utf-8").replace(
        "<td>Khối Marketing</td><td>12,5</td><td>14,0</td><td>1,5</td>",
        "<td>Khối Marketing</td><td>12,5</td><td>14,0</td><td>1,5</td>")
    html = re.sub(r"(<td>Khối Chuỗi cung ứng</td><td>31,0</td><td>33,1</td><td>)2,1(</td>)", r"\g<1>2,1\2", html)
    rows = clicky_html.drivers(clicky_html.tag_targets(html, clicky_html.parse(html))[2], False)
    assert rows                                                                  # the fixture has drivers
    script = clicky_html.load_script(rep["kich_ban"])
    line = next(c for s in script["canh"] for c in s["cau"] if c["noi"].startswith("Thứ hai"))
    line["noi"] = line["noi"].replace("Thứ hai", "Thứ ba")
    Path(rep["kich_ban"]).write_text(clicky_html.dump_script(script, report.name, None, draft=False)
                                     .replace("da_duyet: 'chưa'", "da_duyet: 'có'"), encoding="utf-8")
    rep = clicky_html.build(str(report), voice=voice)
    assert rep["trang_thai"] == "SƠ BỘ: thứ tự chênh lệch đã đổi"
    note = next(n for n in rep["canh_bao"] if n.startswith("Sửa câu «Thứ ba"))
    assert ": thứ hạng đã đổi" in note and "Câu nháp mới: «Thứ ba, khối CNTT" in note
    # the console names the cause first; the talk is still approved, so nothing about da_duyet
    todo = clicky_html.todo_list(rep)
    assert todo[0].startswith("Sửa câu «Thứ ba") and not any("da_duyet" in t for t in todo)
    # the intro "Nguyên nhân chính nằm ở … lớn nhất" ranks nothing: it is never replaced by a "Thứ nhất" line
    assert not any(c.get("noi", "").startswith("Nguyên nhân chính") for c, _ in
                   clicky_html.ranking_issues(clicky_html.load_script(rep["kich_ban"]),
                                              clicky_html.tag_targets(html, clicky_html.parse(html))[2], False))


def test_next_months_report_keeps_the_approved_words_and_puts_in_the_new_figures(report):
    rep = clicky_html.build(str(report), voice=voice)
    _approve(rep["kich_ban"])
    html = report.read_text(encoding="utf-8")
    html = html.replace("186,4 tỷ", "190,2 tỷ").replace("+12,6% so với", "+14,9% so với").replace(
        "tăng 12,6% trong khi", "tăng 14,9% trong khi").replace(
        "<td>78,2</td><td>92,3</td><td>14,1</td><td>18,0%</td>", "<td>78,2</td><td>95,0</td><td>16,8</td><td>21,5%</td>")
    html = re.sub(r"<td>186,4</td><td>20,9</td><td>12,6%</td>", "<td>190,2</td><td>24,7</td><td>14,9%</td>", html)
    html = html.replace("mạnh nhất, 14,1 tỷ", "mạnh nhất, 16,8 tỷ").replace(
        "<td>15,8</td><td>17,7</td><td>1,9</td><td>12,0%</td>", "<td>15,8</td><td>15,0</td><td>-0,8</td><td>-5,1%</td>")
    report.write_text(html, encoding="utf-8")
    rep = clicky_html.build(str(report), voice=voice)
    assert rep["trang_thai"] == "SƠ BỘ: báo cáo kỳ mới, số mới đã thay vào lời đã duyệt"
    assert rep["canh_bao"][0].startswith("Đây là báo cáo kỳ mới")
    assert report.with_name("bao_cao_kich_ban_Clicky (kỳ trước).yaml").exists()
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Thứ nhất, khối Bán hàng vượt AOP gốc 16,8 tỷ, tức 21,5%." in said
    # CNTT now spends less than plan and drops out of the top three: its line is replaced, and named
    assert "Thứ ba, khối Marketing vượt AOP gốc 1,5 tỷ, tức 12,0%." in said
    assert not any("CNTT" in s for s in said)
    assert "«Thứ ba, khối CNTT" in rep["canh_bao"][0]


def test_an_approved_talk_whose_report_changed_says_last_times_figure_plainly(report):
    rep = clicky_html.build(str(report), voice=voice)
    _approve(rep["kich_ban"])
    report.write_text(report.read_text(encoding="utf-8").replace("+64 người", "+66 người"), encoding="utf-8")
    with pytest.raises(goi_html.PackError) as err:
        clicky_html.build(str(report), voice=voice)
    assert "là số cũ ở chỗ «Headcount bình quân»" in str(err.value)
    assert "so_rieng" not in str(err.value) and "xac_nhan" not in str(err.value)
    path = Path(rep["kich_ban"])
    path.write_text(path.read_text(encoding="utf-8").replace("tăng 64 người", "tăng 66 người"), encoding="utf-8")
    rep = clicky_html.build(str(report), voice=voice)
    assert rep["trang_thai"] == "SƠ BỘ: báo cáo đổi số sau khi duyệt"
    assert "da_duyet: 'chưa'" in path.read_text(encoding="utf-8")            # approve again after reading it
    _approve(path)
    assert clicky_html.build(str(report), voice=voice)["trang_thai"].startswith("CHÍNH THỨC")


def test_a_failed_build_keeps_last_times_page_at_its_name_and_says_how_old_it_is(report, monkeypatch, capsys):
    # a SharePoint or Teams link points at "<tên>_Clicky.html": a failed run must not rename it
    monkeypatch.setattr(clicky_html, "build", BUILD)
    monkeypatch.setattr(clicky_html, "offline_voice", lambda: voice)
    monkeypatch.setattr(clicky_html.tri_nho, "owner_info", lambda path=None: ("anh Tuấn", "tuan@congty.vn"))
    assert clicky_html.main([str(report)]) == 0
    out = report.with_name("bao_cao_Clicky.html")
    before = out.read_bytes()
    path = report.with_name("bao_cao_kich_ban_Clicky.yaml")
    path.write_text(path.read_text(encoding="utf-8").replace("186,4 tỷ", "187,4 tỷ"), encoding="utf-8")
    assert clicky_html.main([str(report)]) == 2
    assert out.read_bytes() == before and not list(report.parent.glob("*bản cũ*"))
    err = capsys.readouterr().err
    assert f"Bản trình bày cũ từ {dt.datetime.fromtimestamp(out.stat().st_mtime):%d/%m %H:%M} vẫn ở chỗ cũ, chưa cập nhật" \
        in err


def test_the_launcher_flow_prints_three_lines_and_keeps_the_rest_in_a_log(report, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(clicky_html, "build", BUILD)
    monkeypatch.setattr(clicky_html, "offline_voice", lambda: voice)
    monkeypatch.setenv("CLICKY_TRI_NHO", str(tmp_path / "nho"))
    clicky_html.tri_nho.save_owner("anh Tuấn", "tuan@congty.vn", str(tmp_path / "nho"))
    amp = report.with_name("P&L_T10.html")
    report.rename(amp)
    opened = []
    # Explorer leaves "P&L_T10.html" unquoted: %1 is cut at "&", the full command line still has it. The kit may
    # sit in a folder whose name has ".bat" in it: the files come after the launcher's own name
    cmd = f'C:\\WINDOWS\\system32\\cmd.exe /c ""C:\\bao.bat cu\\kit\\Gan_Clicky_vao_bao_cao.bat" {amp}"'
    code = clicky_html.run_drop([str(amp.with_name("P"))], cmdline=cmd, opener=opened.append, look=False)
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 0 and len(lines) == 3 and lines[0].startswith("ĐẠT") and lines[2].startswith("File: ")
    assert opened == [str(amp.with_name("P&L_T10_Clicky.html"))]
    # the log lives in the memory folder (never beside the report the Board sees), and the console names it
    logs = list((tmp_path / "nho" / "nhat_ky").glob("P&L_T10 *.txt"))
    assert len(logs) == 1 and re.fullmatch(r"P&L_T10 \d{4}-\d\d-\d\d \d{4}\.txt", logs[0].name)
    assert str(logs[0]) in lines[2] and not amp.with_name("P&L_T10_nhat_ky.txt").exists()
    log = logs[0].read_text(encoding="utf-8")
    assert "Kiểm lại biên bản: ĐẠT" in log and "Trí nhớ" in log
    # a missing file and a failed build: three lines too, never a traceback
    assert clicky_html.run_drop([str(tmp_path / "khong_co.html")], cmdline="", look=False) == 1
    assert "không tìm thấy file" in capsys.readouterr().out
    out = amp.with_name("P&L_T10_Clicky.html")
    before = out.read_bytes()
    path = amp.with_name("P&L_T10_kich_ban_Clicky.yaml")
    path.write_text(path.read_text(encoding="utf-8").replace("186,4 tỷ", "187,4 tỷ"), encoding="utf-8")
    assert clicky_html.run_drop([str(amp)], cmdline="", look=False) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3 and lines[0].startswith("KHÔNG ĐẠT") and "187,4" in lines[1]
    # last time's page stays at its name, untouched, and the owner is told its date
    assert f"Bản trình bày cũ từ {dt.datetime.fromtimestamp(out.stat().st_mtime):%d/%m %H:%M} vẫn ở chỗ cũ" in lines[0]
    assert out.read_bytes() == before and not list(amp.parent.glob("*bản cũ*")) and not list(amp.parent.glob("~*"))
    assert "vẫn ở chỗ cũ, chưa cập nhật" in logs[0].read_text(encoding="utf-8") or any(
        "vẫn ở chỗ cũ, chưa cập nhật" in f.read_text(encoding="utf-8") for f in (tmp_path / "nho" / "nhat_ky").glob("*"))


def test_script_mistakes_owners_make_in_notepad(report, tmp_path):
    rep = clicky_html.build(str(report), voice=voice)
    good = Path(rep["kich_ban"]).read_text(encoding="utf-8")
    # an apostrophe inside the sentence is fixed for him
    line = next(l for l in good.splitlines() if "Em xin cảm ơn anh chị" in l)
    apos = tmp_path / "apos.yaml"
    apos.write_text(good.replace(line, line.replace("lắng nghe.", "lắng nghe 'sếp' ạ.")), encoding="utf-8")
    rep2 = clicky_html.build(str(report), script_path=str(apos), voice=voice)
    assert "Em xin cảm ơn anh chị đã lắng nghe 'sếp' ạ." in _data_of(rep2["out"])["scenes"][-1]["sentences"]
    # a second "noi:" in one line of the talk is an error, not the last one silently
    k = good.splitlines().index(line)
    lines = good.splitlines()
    twice = tmp_path / "twice.yaml"
    twice.write_text("\n".join(lines[:k + 1] + [lines[k].replace("- noi:", "  noi:")] + lines[k + 1:]),
                     encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="hai dòng «noi:»"):
        clicky_html.build(str(report), script_path=str(twice), voice=voice)
    # saved by Notepad as "Unicode" (UTF-16)
    u16 = tmp_path / "u16.yaml"
    u16.write_bytes(good.encode("utf-16"))
    assert clicky_html.build(str(report), script_path=str(u16), voice=voice)["scenes"] >= 3


def test_a_stray_byte_csp_and_a_body_tag_in_a_script_are_handled(report, tmp_path):
    raw = FIXTURE.read_bytes().replace("Đề xuất".encode(), "Đề xuất".encode() + b"\xff", 1)
    rep = clicky_html.build(str(_page(tmp_path, raw.decode("utf-8", "replace"), "goc.html")), voice=voice)
    path = tmp_path / "byte.html"
    path.write_bytes(raw)
    rep = clicky_html.build(str(path), voice=voice)
    assert rep["ma_hoa"] == "utf-8" and any("ký tự hỏng" in n for n in rep["canh_bao"])
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "<head>", '<head><meta http-equiv="Content-Security-Policy" content="script-src \'self\'">', 1).replace(
        "</body>", '<script>var tpl = "</body>";</script></body>')
    rep = clicky_html.build(str(_page(tmp_path, html, "csp.html")), voice=voice)
    assert any("Content-Security-Policy" in n for n in rep["canh_bao"])
    page = Path(rep["out"]).read_text(encoding="utf-8")
    assert page.index('var tpl = "</body>"') < page.index('data-clicky-layer="1"')


def test_a_row_named_by_a_number_can_be_said(report, tmp_path):
    html = """<html><body><h1>Báo cáo chi nhánh</h1><table><tr><th>Chi nhánh</th><th>Thực hiện</th><th>Kế hoạch</th>
<th>Chênh lệch</th></tr><tr><td>Dòng 1</td><td>11,1</td><td>13,3</td><td>-2,2</td></tr>
<tr><td>Dòng 2</td><td>12,0</td><td>12,4</td><td>-0,4</td></tr></table></body></html>"""
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    assert rep["record"]["so"] >= 2


def test_the_launcher_without_an_owner_and_no_keyboard_says_so(report, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLICKY_TRI_NHO", str(tmp_path / "trong"))

    def closed(_prompt):
        raise EOFError
    assert clicky_html.run_drop([str(report)], cmdline="", ask=closed, look=False) == 1
    out = capsys.readouterr().out
    assert "KHÔNG ĐẠT: chưa có tên và email" in out and "Traceback" not in out


def test_a_csp_meta_tag_is_dropped_from_the_presentation_copy_only():
    """A report with a CSP meta blocks Clicky's inline script, so the built copy leaves it out."""
    from present.clicky_html import inject
    src = ('<html><head><meta http-equiv="Content-Security-Policy" content="script-src \'self\'"></head>'
           '<body><p>x</p></body></html>')
    out = inject(src, {"meta": {}}, {})
    assert "Content-Security-Policy\" content" not in out and "<p>x</p>" in out


def test_fractions_are_figures_and_dates_and_months_are_not():
    F = clicky_html.figures
    for text, tok in (("Giãn 3/10 khối", "3/10"), ("2/5 vị trí bán hàng", "2/5"), ("Tỷ lệ 1/5", "1/5")):
        f = F(text)
        assert [x.tok for x in f] == [tok], text
    assert clicky_html.value("3/10") == clicky_html.value("0,3")
    for text in ("ngày 3/10 họp", "hạn 20/11", "từ 1/4 đến 30/6", "3/10/2026", "ngày 25/12", "11/2026",
                 "tháng 11-2026", "T11-2026", "tháng 11.2026", "tháng 10"):
        assert F(text) == [], text
    assert [x.tok for x in F("Doanh thu tháng 11.2026 đạt 5,2 tỷ")] == ["5,2"]


def test_a_spoken_fraction_is_checked_by_the_lock(report, tmp_path):
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "Hai phần ba phần tăng đến từ", "2/3 phần tăng đến từ")
    page = _page(tmp_path, html)
    assert _lock(page, "Thông điệp chính", "Khoảng 2/3 phần tăng đến từ tuyển mới.") == ""
    assert "không có trong báo cáo" in _lock(page, "Thông điệp chính", "Khoảng 3/4 phần tăng đến từ tuyển mới.")


EXCEL_PL = Path("/tmp/claude-0/review/design/r2/rp/excel.html")


@pytest.mark.skipif(not EXCEL_PL.exists(), reason="the UI reviewer's Excel P&L is not on this machine")
def test_an_excel_pl_with_costs_in_brackets_passes_its_own_lock(report, tmp_path):
    rep = clicky_html.build(str(_page(tmp_path, EXCEL_PL.read_text(encoding="utf-8"), "excel.html")), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    # "(534,1)" and "6,5%" in one row: the two amounts say the cost grew, so "vượt … tức 6,5%" is right
    assert "Thứ hai, Giá vốn hàng bán vượt kế hoạch 534,1 tỷ, tức 6,5%." in said


def test_a_cost_row_whose_direction_cannot_be_read_is_left_out_of_the_draft(report, tmp_path):
    # no amounts to compare (only the variance and the percent): the bracket alone cannot say up or down
    html = """<html><body><h1>Chi phí T10</h1><div>Đơn vị: tỷ</div><table><tr><th>Chỉ tiêu</th><th>Chênh lệch</th>
<th>% chênh</th></tr><tr><td>Doanh thu</td><td>652,5</td><td>5,2%</td></tr>
<tr><td>Giá vốn</td><td>(534,1)</td><td>6,5%</td></tr><tr><td>Chi phí bán hàng</td><td>(84,8)</td><td>4,7%</td></tr>
</table></body></html>"""
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Thứ nhất, Doanh thu tăng 652,5 tỷ, tức 5,2%." in said
    assert not any("534,1" in s for s in said) and not any("Thứ hai" in s for s in said)
    assert "giảm" in _lock(_page(tmp_path, html, "lai.html"), "Giá vốn", "Giá vốn giảm 534,1 tỷ.")


REVIEW = Path("/tmp/claude-0/review")


def _all_reports():
    found = sorted(Path(__file__).parent.glob("fixtures/*.htm*"))
    if REVIEW.exists():
        for p in sorted(REVIEW.rglob("*.htm*")):
            parts = set(p.parts)
            if "cham-doc-lap" in parts or "freshvenv" in parts or "shots" in parts or "_Clicky" in p.name \
                    or "bản cũ" in p.name or p.stat().st_size > 1_500_000:
                continue
            found.append(p)
    return found


def test_every_untouched_draft_passes_its_own_lock(report, tmp_path):
    """For every report we can reach (tests and the review sets), a plain drag-and-drop never ends in
    KHÔNG ĐẠT because of a sentence Clicky wrote itself."""
    import hashlib
    seen, bad = set(), []
    for src in _all_reports():
        raw = src.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if b"data-clicky-layer" in raw or digest in seen:
            continue
        seen.add(digest)
        work = tmp_path / digest[:12]
        work.mkdir()
        dst = work / src.name
        dst.write_bytes(raw)
        try:
            clicky_html.build(str(dst), voice=voice)
        except goi_html.PackError as exc:
            if "chưa khớp" in str(exc) or "Kịch bản" in str(exc):
                bad.append(f"{src}: {str(exc)[:200]}")
    assert seen and not bad, "\n".join(bad)


# --- QA round 3 and FP&A owner round 3 ---------------------------------------------------------------------

PERIOD_TABLE = ('<!doctype html><html><head><meta charset="utf-8"><title>Dòng tiền</title></head><body>'
                '<h1>Dòng tiền tháng 10 (SỐ GIẢ LẬP)</h1><p>Đơn vị: tỷ đồng</p><h2>1. Dòng tiền</h2><table><tr>'
                '<th>Chỉ tiêu</th><th>{a}</th><th>{b}</th></tr><tr><td>Tiền đầu kỳ</td><td>120,4</td><td>131,2</td>'
                '</tr><tr><td>Thu từ khách hàng</td><td>210,5</td><td>198,7</td></tr><tr><td>Chi nhà cung cấp</td>'
                '<td>(160,2)</td><td>(171,9)</td></tr><tr><td>Tiền cuối kỳ</td><td>131,2</td><td>118,6</td></tr>'
                '</table></body></html>')


def test_numbers_that_name_a_period_are_not_figures():
    for text in ("Tháng 9", "Quý 3", "kỳ 2", "tuần 12", "T 10", "Q 4", "Kế hoạch tháng 10", "Thực hiện Quý 4/2026"):
        assert clicky_html.numbers(text) == [], text
    # the same small number with a unit is a figure ("cùng kỳ 12 tỷ"), as is any other number
    assert clicky_html.numbers("cùng kỳ 12 tỷ") == ["12"]
    assert clicky_html.numbers("T 10 tăng 3,2 tỷ") == ["3,2"]


@pytest.mark.parametrize("a,b,said", [
    ("Tháng 9", "Tháng 10", "Tiền cuối kỳ tháng 9 là 131,2 tỷ, tháng 10 là 118,6 tỷ."),                 # QA I
    ("Quý 3", "Quý 4", "Tiền cuối kỳ quý 3 là 131,2 tỷ, quý 4 là 118,6 tỷ."),                           # QA L
    ("Kế hoạch tháng 10", "Thực hiện tháng 10",                                                         # QA N
     "Tiền cuối kỳ kế hoạch tháng 10 là 131,2 tỷ, thực hiện tháng 10 là 118,6 tỷ.")])
def test_a_table_headed_by_periods_passes_its_own_lock_on_a_plain_drop(report, tmp_path, a, b, said):
    from present import kiem_bien_ban
    src = _page(tmp_path, PERIOD_TABLE.format(a=a, b=b), "dong_tien.html")
    rep = clicky_html.build(str(src), voice=voice)
    lines = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    # the row that changed most (12,6), not the first row; periods in lower case inside the sentence
    assert said in lines
    assert kiem_bien_ban.verify(rep["out"], str(src))["ok"]


def test_a_one_column_table_is_said_row_by_row_and_the_script_is_written(report, tmp_path):
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>Doanh thu</title></head><body><h1>Doanh thu '
            'tháng 10 (SỐ GIẢ LẬP)</h1><h2>1. Doanh thu</h2><table><tr><th>Mục</th><th>Giá trị (tỷ)</th></tr><tr>'
            '<td>Doanh thu</td><td>123,4</td></tr><tr><td>Giá vốn</td><td>80,1</td></tr></table></body></html>')
    rep = clicky_html.build(str(_page(tmp_path, html, "H_one_row.html")), voice=voice)
    lines = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Doanh thu là 123,4 tỷ." in lines and "Giá vốn là 80,1 tỷ." in lines
    assert Path(rep["kich_ban"]).exists()


def test_a_direction_word_before_a_level_figure_is_stopped(report):
    # 92,3 is the AOPR amount, 186,4 the card's value: neither is how much it fell
    assert "không phải mức giảm" in _lock(report, "Khối Bán hàng", "Khối Bán hàng AOPR giảm 92,3 tỷ.")
    assert "không phải cột «Chênh lệch»" in _lock(report, "Tổng", "Tổng chi phí giảm 186,4 tỷ.")
    assert "không phải mức giảm" in _lock(report, "Tổng chi phí nhân sự AOPR", "Tổng chi phí nhân sự AOPR giảm 186,4 tỷ.")
    # a level joined by "lên", "còn", "từ … xuống …" is a level; a change is still a change
    assert _lock(report, "Khối Bán hàng", "Khối Bán hàng AOPR tăng lên 92,3 tỷ.") == ""
    assert _lock(report, "Khối CNTT", "Khối CNTT tăng từ 15,8 lên 17,7 tỷ.") == ""
    assert _lock(report, "Khối Bán hàng", "Khối Bán hàng tăng 14,1 tỷ.") == ""
    assert _lock(report, "Cần giảm so với AOPR", "So với AOPR, cần giảm 8,9 tỷ.") == ""


def test_a_wrong_unit_is_stopped_before_so_rieng_or_xac_nhan(report, tmp_path):
    for cau in ({"noi": "Headcount là 1.248 tỷ.", "chi": _id(report, "Headcount bình quân"), "xac_nhan": ["1.248"]},
                {"noi": "Tổng chi phí 186,4 triệu.", "chi": _id(report, "Tổng chi phí nhân sự AOPR"),
                 "so_rieng": ["186,4"]}):
        with pytest.raises(goi_html.PackError) as exc:
            clicky_html.build(str(report), script_path=_script(tmp_path, [cau]), voice=voice)
        assert "Anh sửa đơn vị" in str(exc.value) and "xac_nhan" not in str(exc.value)


def test_a_bracketed_percent_after_tuc_follows_the_negative_amount():
    f = clicky_html.figures("Chênh lệch giá vốn (455,1) tỷ, tức (5,7%).")
    assert [(g.tok, g.sign) for g in f] == [("455,1", "-"), ("5,7", "-")]
    assert [g.sign for g in clicky_html.figures("Chi phí 455,1 tỷ, tức (5,7%).")] == [None, None]


def test_drafted_lines_are_cleaned_of_markup_and_broken_letters():
    clean = clicky_html.say_clean
    assert clean('Báo cáo "thử" <script>alert(\'h1\')</script> (SỐ GIẢ LẬP)') == 'Báo cáo "thử" (SỐ GIẢ LẬP)'
    assert clean("Nhãn </script><script>window.PWN=1</script> là 55,5.") == "Nhãn là 55,5."
    assert clean("mã __BIEN_BAN__ __DATA__ <!--<script> doanh thu 123,4 tỷ.") == "mã doanh thu 123,4 tỷ."
    assert clean("Thứ ba, Kh�i Marketing cao hơn AOP gốc 1,5 tỷ.") == "Thứ ba, Marketing cao hơn AOP gốc 1,5 tỷ."
    assert clean("P&L T10 & kế hoạch, lãi < 5 tỷ, biên 1.4 điểm %") == "P&L T10 và kế hoạch, lãi dưới 5 tỷ, biên 1.4 điểm %"


def test_a_title_of_markup_only_is_said_as_this_report(report, tmp_path):
    html = ('<html><head><title>&lt;/script&gt;</title></head><body><h1>&lt;/script&gt;</h1><h2>1. A</h2>'
            '<p class="note">Nhận xét: doanh thu 123,4 tỷ.</p></body></html>')
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    assert _draft(rep)["canh"][0]["cau"][0]["noi"].endswith("xin trình bày báo cáo này.")


def test_lock_messages_never_cut_a_word_in_half(report):
    long = "Khối Bán hàng tăng 14,1 tỷ do tuyển thêm nhân viên kinh doanh ở các vùng miền trong quý vừa qua 187,4 tỷ."
    msg = _lock(report, "Khối Bán hàng", long)
    quoted = re.search(r"câu «([^»]*)»", msg).group(1)
    assert quoted.endswith("…") and long.startswith(quoted[:-1]) and long[len(quoted) - 1] == " "
    assert clicky_html.clip("một hai ba bốn", 9) == "một hai…"


def test_the_ranking_intro_is_never_replaced(report):
    src = report.read_text(encoding="utf-8")
    rows = clicky_html.tag_targets(src, clicky_html.parse(src))[2]
    second = _id(report, "Khối Chuỗi cung ứng")
    script = {"canh": [{"cau": [{"noi": "Nguyên nhân chính nằm ở ba dòng có chênh lệch lớn nhất trong bảng.",
                                 "chi": second}]}]}
    assert clicky_html.ranking_issues(script, rows, False) == []


def test_superlatives_and_from_to_must_agree_with_the_table(report):
    # Bán hàng grew 18,0%: Marketing at 12,0% is not the fastest
    assert "dòng «Khối Bán hàng» là 18,0%" in _lock(report, "Khối Marketing", "Khối Marketing tăng nhanh nhất, 12,0%.")
    assert _lock(report, "Khối Bán hàng", "Khối Bán hàng tăng nhanh nhất, 18,0%.") == ""
    assert "Khối Tài chính - Kế toán" in _lock(report, "Khối CNTT", "Khối CNTT có chênh lệch nhỏ nhất, 1,9 tỷ.")
    assert _lock(report, "Khối Tài chính - Kế toán", "Khối Tài chính - Kế toán có chênh lệch nhỏ nhất, 0,6 tỷ.") == ""
    # "từ X lên Y" with Y below X says the opposite of its own figures
    assert "là giảm, câu nói lại là tăng" in _lock(report, "Khối CNTT", "Khối CNTT từ 17,7 lên 15,8 tỷ.")
    assert "câu nói tăng nhưng «còn»" in _lock(report, "Khối CNTT", "Khối CNTT tăng từ 17,7 còn 15,8 tỷ.")
    # "từ" is the base column (AOP gốc 15,8), the end is the current one (AOPR 17,7): swapped, it stops
    assert "ngược thứ tự" in _lock(report, "Khối CNTT", "Khối CNTT giảm từ 17,7 xuống 15,8 tỷ.")
    assert _lock(report, "Khối CNTT", "Khối CNTT tăng từ 15,8 lên 17,7 tỷ.") == ""


def test_kiem_bien_ban_reads_the_same_sentence_rules(report):
    from present import kiem_bien_ban
    rep = clicky_html.build(str(report), voice=voice)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    rec, data = _record_of(rep["out"]), _data_of(rep["out"])
    chi = _id(report, "Khối Marketing")
    bad = "Khối Marketing tăng nhanh nhất, 12,0%."
    rec["cau"].append({"canh": "A", "noi": bad, "chi": chi, "so": [{"so": "12,0", "gia_tri": "12.0", "dau": "+",
                                                                      "nguon": chi}]})
    rec["ma_bam"] = clicky_html.seal(rec)
    data["scenes"][-1]["sentences"].append(bad)
    page = re.sub(r'(<script id="clicky-bien-ban" type="application/json">)(.*?)(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(rec) + m.group(3), page, flags=re.S)
    page = re.sub(r'(<script id="clicky-data" type="application/json">)(.*?)(</script>)',
                  lambda m: m.group(1) + clicky_html._blob(data) + m.group(3), page, flags=re.S)
    Path(rep["out"]).write_text(page, encoding="utf-8")
    res = kiem_bien_ban.verify(rep["out"])
    assert any("nhanh nhất" in e for e in res["errors"])


def test_money_cards_get_the_declared_unit_and_a_share_of_plan_is_said_as_such(report, tmp_path):
    html = ('<html><body><h1>P&amp;L T10</h1><div>Đơn vị: triệu VND</div>'
            '<div class="stat"><div class="lbl">EBITDA</div><div class="val">41,250</div><div class="var">(9,870) vs AOP'
            '</div></div><div class="stat"><div class="lbl">LNTT YTD</div><div class="val">286,900</div>'
            '<div class="var">101.4% AOP YTD</div></div></body></html>')
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    lines = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert any("EBITDA là 41,250 triệu, giảm 9,870 triệu so với AOP." in x for x in lines)
    assert any("LNTT YTD là 286,900 triệu, đạt 101.4% kế hoạch lũy kế." in x for x in lines)


def test_a_cost_under_plan_is_a_saving_only_when_revenue_is_not_under_plan_too(report, tmp_path):
    html = EN_REPORT.replace("<td>412,580</td><td>435,210</td><td>(22,630)</td><td>(5.2%)</td>",
                             "<td>457,840</td><td>435,210</td><td>22,630</td><td>5.2%</td>")
    rep = clicky_html.build(str(_page(tmp_path, html)), voice=voice)
    lines = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert any(x.startswith("Thứ hai, Giá vốn tiết kiệm so với AOP 10,060 triệu") for x in lines), lines


# --- QA round 4 (real packs) and FP&A owner round 4 ----------------------------------------------------------

def _table(head, rows):
    return ("<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


B01 = ('<html><head><meta charset="utf-8"><title>BCĐKT 31/10</title></head><body><h1>Bảng cân đối kế toán tại ngày '
       '31/10/2026 (SỐ GIẢ LẬP)</h1><p>Đơn vị tính: tỷ đồng</p><h2>1. Bảng cân đối</h2>'
       + _table(["Chỉ tiêu", "Mã số", "Thuyết minh", "Số cuối kỳ", "Số đầu năm"],
                [["A. TÀI SẢN NGẮN HẠN", "100", "", "1.245,6", "1.198,3"],
                 ["I. Tiền và các khoản tương đương tiền", "110", "V.01", "231,4", "187,9"],
                 ["B. TÀI SẢN DÀI HẠN", "200", "", "2.010,2", "2.055,8"],
                 ["TỔNG CỘNG TÀI SẢN", "270", "", "3.255,8", "3.254,1"]]) + "</body></html>")
B02 = ('<html><head><meta charset="utf-8"><title>B02-DN</title></head><body><h1>Báo cáo kết quả hoạt động kinh doanh '
       '10 tháng 2026 (SỐ GIẢ LẬP)</h1><p>Đơn vị tính: tỷ đồng</p><h2>1. Kết quả</h2>'
       + _table(["Chỉ tiêu", "Mã số", "Thuyết minh", "Năm nay", "Năm trước"],
                [["1. Doanh thu bán hàng và cung cấp dịch vụ", "01", "VI.25", "4.120,5", "3.871,2"],
                 ["11. Giá vốn hàng bán", "11", "VI.27", "(2.950,2)", "(2.801,9)"],
                 ["20. Lợi nhuận gộp", "20", "", "1.150,2", "1.050,6"],
                 ["50. Tổng lợi nhuận kế toán trước thuế", "50", "", "540,8", "498,3"]]) + "</body></html>")
HEADCOUNT = ('<html><head><meta charset="utf-8"><title>NS T10</title></head><body><h1>Nhân sự tháng 10 (SỐ GIẢ LẬP)</h1>'
             '<p>Đơn vị: tỷ đồng</p><h2>1. Chi phí</h2>'
             + _table(["Khối", "Kế hoạch", "Thực hiện", "Chênh lệch"], [["Bán hàng", "40,1", "45,9", "5,8"],
                                                                        ["Quản lý", "21,0", "21,4", "0,4"]])
             + "<h2>2. Số người</h2>"
             + _table(["Khối", "Kế hoạch (người)", "Thực hiện (người)", "Chênh lệch"],
                      [["Bán hàng", "700", "718", "18"], ["Quản lý", "165", "161", "(4)"]])
             + "<h2>3. Nhân sự</h2>"
             + _table(["Khối", "T9/2026", "T10/2026"], [["Bán hàng", "712", "718"], ["Quản lý", "164", "161"]])
             + "</body></html>")


def _all_said(rep):
    return [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]


def test_code_columns_are_labels_never_figures(report, tmp_path):
    from present import kiem_bien_ban
    for name, html in (("b01.html", B01), ("b02.html", B02)):
        src = _page(tmp_path, html, name)
        rep = clicky_html.build(str(src), voice=voice)
        said = " ".join(_all_said(rep))
        assert "mã số" not in said.lower() and " 270 " not in said and " 01 tỷ" not in said, said
        assert kiem_bien_ban.verify(rep["out"], str(src))["ok"]
        cols = next(iter(_data_of(rep["out"])["cot"].values()))
        assert [c["cot_nhan"] for c in cols] == [True, True, True, False, False]
        assert [c["don_vi"] for c in cols][3:] == ["tỷ", "tỷ"]
    # spoken, "mã số 270" names the line; with a unit it is a figure the report does not have there
    assert clicky_html.numbers("Tổng cộng tài sản, mã số 270, là 3.255,8 tỷ.") == ["3.255,8"]
    assert _lock(_page(tmp_path, B01, "b01.html"), "TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản là 270 tỷ.") != ""


def test_people_columns_never_get_the_money_unit(report, tmp_path):
    src = _page(tmp_path, HEADCOUNT, "ns.html")
    rep = clicky_html.build(str(src), voice=voice)
    said = _all_said(rep)
    assert "Bán hàng kế hoạch là 700 người, thực hiện là 718 người, chênh lệch là 18 người." in said
    assert "Bán hàng tháng 9/2026 là 712 người, tháng 10/2026 là 718 người." in said
    assert not any(re.search(r"\b(712|718|700|18) tỷ", s) for s in said)
    # the page gets each column's unit, so a click answer says "người" too
    units = [[c["don_vi"] for c in cols] for cols in _data_of(rep["out"])["cot"].values()]
    assert units == [["", "tỷ", "tỷ", "tỷ"], ["", "người", "người", "người"], ["", "người", "người"]]
    # and the lock: "18 tỷ" on the people row is a wrong unit
    assert "người" in _lock(src, "Bán hàng", "Bán hàng chênh lệch 18 tỷ.")


def test_a_balance_sheet_and_a_b02_say_their_bottom_line_and_biggest_change(report, tmp_path):
    rep = clicky_html.build(str(_page(tmp_path, B02, "b02.html")), voice=voice)
    said = _all_said(rep)
    assert "Tổng lợi nhuận kế toán trước thuế năm nay là 540,8 tỷ, năm trước là 498,3 tỷ." in said
    assert not any(s.startswith("1. ") for s in said)
    rep = clicky_html.build(str(_page(tmp_path, B01, "b01.html")), voice=voice)
    assert "Tổng cộng tài sản số cuối kỳ là 3.255,8 tỷ, số đầu năm là 3.254,1 tỷ." in _all_said(rep)


def test_a_merged_header_pl_and_a_twelve_month_table_say_the_reports_month(report, tmp_path):
    merged = ('<html><head><meta charset="utf-8"><title>KQKD tháng 10</title></head><body><h1>Báo cáo kết quả kinh '
              'doanh tháng 10/2026 (SỐ GIẢ LẬP)</h1><p>Đơn vị: tỷ đồng</p><h2>2. Kết quả kinh doanh</h2><table><tr>'
              '<th rowspan="2">Chỉ tiêu</th><th colspan="3">Tháng 10</th><th colspan="3">Lũy kế 10 tháng</th></tr>'
              '<tr><th>KH</th><th>TH</th><th>% TH/KH</th><th>KH</th><th>TH</th><th>% TH/KH</th></tr>'
              '<tr><td>Doanh thu thuần</td><td>412,5</td><td>398,2</td><td>96,5%</td><td>3.950,0</td><td>3.871,4</td>'
              '<td>98,0%</td></tr><tr><td>Lợi nhuận trước thuế</td><td>60,4</td><td>49,9</td><td>82,6%</td>'
              '<td>575,0</td><td>523,8</td><td>91,1%</td></tr></table></body></html>')
    said = _all_said(clicky_html.build(str(_page(tmp_path, merged, "kqkd.html")), voice=voice))
    assert "Lợi nhuận trước thuế tháng 10: kế hoạch là 60,4 tỷ, thực hiện là 49,9 tỷ, tỷ lệ thực hiện so với kế " \
           "hoạch là 82,6%." in said
    months = ["T%d" % k for k in range(1, 13)]
    year = ('<html><head><meta charset="utf-8"><title>DT theo miền</title></head><body><h1>Doanh thu theo miền 12 '
            'tháng 2026 (SỐ GIẢ LẬP)</h1><p>Đơn vị: tỷ đồng</p><h2>1. Nhận xét</h2><p class="note">Miền Nam tháng 10 '
            'đạt 81,0 tỷ, cao nhất năm.</p><h2>2. Chi tiết</h2>'
            + _table(["Miền"] + months, [["Miền Bắc"] + ["%d,1" % (50 + k) for k in range(1, 13)],
                                         ["Miền Nam"] + ["%d,0" % (70 + k) for k in range(1, 13)]]) + "</body></html>")
    said = _all_said(clicky_html.build(str(_page(tmp_path, year, "mien.html")), voice=voice))
    assert any("tháng 9 là" in s and "tháng 10 là" in s for s in said), said
    assert not any("tháng 1 là" in s or "T1 " in s for s in said)


def test_an_english_pack_drafts_its_variances_with_the_period_in_words(report, tmp_path):
    html = ('<html><head><meta charset="utf-8"><title>Mgmt accounts Oct-26</title></head><body><h1>Management '
            'accounts Oct-26 (SYNTHETIC)</h1><p>VND bn</p><h2>2. P&amp;L</h2>'
            + _table(["Line", "Budget Oct-26", "Actual Oct-26", "Var"],
                     [["Net revenue", "412.5", "398.2", "(14.3)"], ["Gross profit", "123.7", "117.2", "(6.5)"],
                      ["PBT", "60.4", "49.9", "(10.5)"]]) + "</body></html>")
    rep = clicky_html.build(str(_page(tmp_path, html, "mgmt.html")), voice=voice)
    said = _all_said(rep)
    assert "Thứ nhất, Net revenue so với ngân sách tháng 10/2026, thấp hơn 14.3 tỷ." in said
    assert not any("chưa rõ" in n for n in rep["canh_bao"])


def test_a_drafted_line_never_puts_a_period_code_right_before_a_figure(report, tmp_path):
    html = EN_REPORT.replace("<th>AOP</th>", "<th>AOP T10</th>")
    said = _all_said(clicky_html.build(str(_page(tmp_path, html)), voice=voice))
    assert "Thứ hai, Giá vốn so với AOP tháng 10, thấp hơn 10,060 triệu, tức 3.3%." in said
    code = re.compile(r"(?<!\w)(?:T\d{1,2}|Q[1-4]|tháng \d{1,2}(?:/\d{4})?|[A-Z][a-z]{2}-\d\d|20\d\d)\s+\d")
    assert not [s for s in said if code.search(s)]


def test_an_offset_is_said_after_the_causes_never_as_one_of_them(report, tmp_path):
    html = report.read_text(encoding="utf-8").replace(
        "<td>15,8</td><td>17,7</td><td>1,9</td><td>12,0%</td>", "<td>15,8</td><td>13,9</td><td>(1,9)</td><td>(12,0%)</td>")
    said = _all_said(clicky_html.build(str(_page(tmp_path, html, "bu.html")), voice=voice))
    causes = [s for s in said if s.startswith(("Thứ nhất", "Thứ hai", "Thứ ba"))]
    assert causes and not any("CNTT" in s for s in causes)
    assert any(s.startswith("Bù lại, khối CNTT") and "1,9 tỷ" in s for s in said), said


def _next_month_card(html: str) -> str:
    """A month later: the ratio card and the totals moved (FP&A round 4, nm2)."""
    return html.replace("2,52%", "2,61%").replace("186,4 tỷ", "182,6 tỷ").replace(
        "<td>165,5</td><td>186,4</td><td>20,9</td><td>12,6%</td>", "<td>165,5</td><td>182,6</td><td>17,1</td><td>10,3%</td>")


def test_next_month_refuses_his_own_figure_from_last_period_and_keeps_so_cho(report, monkeypatch, capsys):
    rep = clicky_html.build(str(report), voice=voice)
    kb = Path(rep["kich_ban"])
    script = clicky_html.load_script(kb)
    card = _id(report, "Tỷ lệ chi phí nhân sự/DTT")
    script["canh"][-1]["cau"].insert(0, {"noi": "Tỷ lệ chi phí nhân sự trên doanh thu là 2,52%.", "chi": card,
                                         "neo": "Tỷ lệ chi phí nhân sự/DTT", "so_rieng": ["2,52"]})
    kb.write_text(clicky_html.dump_script(script, report.name, clicky_html.snapshot(
        script, clicky_html.make_places(*clicky_html.tag_targets(report.read_text(encoding="utf-8"), clicky_html.parse(
            report.read_text(encoding="utf-8")))[1:], clicky_html.parse(report.read_text(encoding="utf-8")))),
        draft=False).replace("da_duyet: 'chưa'", "da_duyet: 'có'"), encoding="utf-8")
    assert clicky_html.build(str(report), voice=voice)["trang_thai"].startswith("CHÍNH THỨC")
    before = clicky_html.load_script(kb)["so_cho"]
    report.write_text(_next_month_card(report.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(goi_html.PackError) as exc:
        clicky_html.build(str(report), voice=voice)
    msg = str(exc.value)
    assert msg.startswith("Đây là báo cáo kỳ mới; câu anh tự thêm số còn số kỳ trước: «Tỷ lệ chi phí nhân sự")
    assert "(báo cáo bây giờ ghi 2,61%" in msg
    # the stopped run left last period's figures in so_cho: nothing looks current that is not
    assert clicky_html.load_script(kb)["so_cho"] == before
    # running again gives the same advice, and the kept "(kỳ trước)" script is not renamed twice
    with pytest.raises(goi_html.PackError, match="câu anh tự thêm số còn số kỳ trước"):
        clicky_html.build(str(report), voice=voice)
    assert len(list(report.parent.glob("*kỳ trước*"))) == 1
    # he writes this month's figure: the build passes and so_cho now holds this month's figures
    kb.write_text(kb.read_text(encoding="utf-8").replace("là 2,52%.", "là 2,61%.").replace("'2,52'", "'2,61'"),
                  encoding="utf-8")
    rep = clicky_html.build(str(report), voice=voice)
    assert rep["trang_thai"].startswith("SƠ BỘ") and "2,61%" in clicky_html.load_script(kb)["so_cho"][
        "Tỷ lệ chi phí nhân sự/DTT"]
    # and last period's figure typed as his own later is refused against the "(kỳ trước)" script
    kb.write_text(kb.read_text(encoding="utf-8").replace("là 2,61%.", "là 2,52%.").replace("'2,61'", "'2,52'"),
                  encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="là số kỳ trước"):
        clicky_html.build(str(report), voice=voice)


def test_the_launcher_says_a_new_period_first_on_line_two(report, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(clicky_html, "build", BUILD)
    monkeypatch.setattr(clicky_html, "offline_voice", lambda: voice)
    monkeypatch.setenv("CLICKY_TRI_NHO", str(tmp_path / "nho"))
    clicky_html.tri_nho.save_owner("anh Tuấn", "tuan@congty.vn", str(tmp_path / "nho"))
    assert clicky_html.run_drop([str(report)], cmdline="", look=False) == 0
    kb = report.with_name("bao_cao_kich_ban_Clicky.yaml")
    text = kb.read_text(encoding="utf-8")
    card = _id(report, "Tỷ lệ chi phí nhân sự/DTT")
    text = text.replace("canh:\n", "canh:\n- muc: 'Riêng'\n  cau:\n  - noi: 'Tỷ lệ chi phí nhân sự trên doanh thu là "
                        f"2,52%.'\n    chi: {card}\n    neo: 'Tỷ lệ chi phí nhân sự/DTT'\n    so_rieng: ['2,52']\n", 1)
    assert "'Tỷ lệ chi phí nhân sự/DTT': '2,52% 2,40%'" in text           # last period's figures at that card
    kb.write_text(text, encoding="utf-8")
    report.write_text(_next_month_card(report.read_text(encoding="utf-8")), encoding="utf-8")
    capsys.readouterr()
    assert clicky_html.run_drop([str(report)], cmdline="", look=False) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[1].startswith("Đây là báo cáo kỳ mới; câu anh tự thêm số còn số kỳ trước: «")
    assert "báo cáo bây giờ ghi 2,61%" in lines[1]


def test_words_the_voice_spells_are_named_to_the_owner(report, tmp_path):
    import io
    import wave

    class Voice:                        # knows every word except the brand "Xyzco"
        missing = []

        def synthesize(self, text):
            self.missing = [w for w in re.findall(r"\w+", text) if w.lower() == "xyzco"]
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
                w.writeframes(b"\0\0" * 80)
            return out.getvalue()

    html = FIXTURE.read_text(encoding="utf-8").replace("Thông điệp chính:", "Thông điệp chính: kênh Xyzco ổn định.", 1)
    rep = clicky_html.build(str(_page(tmp_path, html, "xyz.html")), voice=Voice().synthesize)
    note = next(n for n in rep["canh_bao"] if n.startswith("Chữ em đang đánh vần:"))
    assert "Xyzco" in note and "TU_DOC.txt" in note
    assert "MT" not in note.split("—")[0] and "KA" not in note.split("—")[0]     # short codes are said as letters
    assert any(t.startswith("Chữ em đang đánh vần:") for t in clicky_html.todo_list(rep))


def test_the_lock_reads_a_slash_as_the_voice_does():
    # one shared rule (vn_format.slash_kind): a date is skipped, a count is a figure
    assert clicky_html.numbers("cập nhật 5/11, doanh thu 5 tỷ") == ["5"]
    assert clicky_html.numbers("3/10 khối đạt kế hoạch") == ["3/10"]
    assert clicky_html.numbers("4/4 khối đạt kế hoạch") == ["4/4"]
    assert clicky_html.numbers("tỷ lệ 2/5") == ["2/5"]


def test_a_dotted_date_in_a_vietnamese_report_is_a_date_not_a_mixed_mark(report, tmp_path):
    # real-report run 2026-11-13: "cập nhật 29.10" beside "672,7" stopped the build as mixed number marks
    from present import kiem_bien_ban

    report.write_text(report.read_text(encoding="utf-8").replace("cập nhật 11/11", "cập nhật 29.10"), encoding="utf-8")
    heard = []
    path = _script(tmp_path, [{"noi": "Số liệu cập nhật 29.10, tổng chi phí nhân sự 186,4 tỷ.", "chi": "c2"}])
    rep = clicky_html.build(str(report), script_path=path, voice=lambda t: heard.append(t) or t.encode())
    assert rep["dau_so"] == "vn"
    assert [x["so"] for r in _record_of(rep["out"])["cau"] for x in r["so"]] == ["186,4"]
    assert any("cập nhật ngày hai mươi chín tháng mười" in h for h in heard)
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert clicky_html.figures("cập nhật 29.10")[0].tok == "29.10"       # outside a build: no marks known yet


def test_dotted_dates_never_decide_the_number_marks():
    assert clicky_html.number_marks(["Cập nhật 29.10", "Doanh thu 672,7 tỷ", "Kỳ 01.11.2026 đến 10.2026"]) is False
    assert clicky_html.number_marks(["Revenue 12.6% as of 29.10", "EBITDA 412,580.5"]) is True
    v = clicky_html.for_voice
    assert v("Số liệu cập nhật 29.10.") == "Số liệu cập nhật 29/10."
    assert v("Cập nhật ngày 5.2, tăng 12,6%.") == "Cập nhật ngày 5/2, tăng 12,6%."
    assert v("Revenue as of 29.10", en=True) == "Revenue as of 29.10"
    assert v("Doanh thu 29.10 tỷ") == "Doanh thu 29.10 tỷ"                # a unit after it: a figure, never a date


def test_a_real_mixed_mark_stop_says_which_figures_and_where():
    with pytest.raises(clicky_html.PackError) as e:
        clicky_html.number_marks(["Doanh thu 672,7 tỷ, tăng 12,6%", "Chi phí 5,3 tỷ", "Hệ số vòng quay 1.5 lần"])
    msg = str(e.value)
    assert "3 số kiểu Việt" in msg and "1 số kiểu Anh" in msg and "«1.5» (chỗ «Hệ số vòng quay 1.5 lần»)" in msg


def test_the_launcher_shows_each_step_while_it_works_then_only_three_lines(report, tmp_path, monkeypatch):
    # real-report run 2026-11-13: a blank black window for minutes looked like a hang
    import io

    class Console(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(clicky_html, "build", BUILD)
    monkeypatch.setattr(clicky_html, "offline_voice", lambda: voice)
    monkeypatch.setenv("CLICKY_TRI_NHO", str(tmp_path / "nho"))
    clicky_html.tri_nho.save_owner("anh Tuấn", "tuan@congty.vn", str(tmp_path / "nho"))
    console = Console()
    monkeypatch.setattr(sys, "stdout", console)
    assert clicky_html.run_drop([str(report)], cmdline="", opener=lambda p: None, look=False) == 0
    shown = console.getvalue()
    assert "đừng tắt cửa sổ" in shown and "Đang đọc giọng: đoạn 1/" in shown and "Đang kiểm lại từng con số" in shown
    lines = shown.rsplit("\r", 1)[1].strip().splitlines()      # what stays on screen after the last step is wiped
    assert len(lines) == 3 and lines[0].startswith("ĐẠT")
    assert clicky_html.progress is clicky_html._quiet               # the next build in this process is silent
