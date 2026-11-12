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
    # a talk to the Board: greeting, key message, the largest variances, one request, thanks
    assert [s["muc"] for s in draft["canh"]] == ["Mở đầu", "Thông điệp chính", "Nguyên nhân chính",
                                                  "2. Chi tiết theo khối", "Đề nghị Ban Giám đốc"]
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
    assert "Thứ nhất, Doanh thu thuần chênh lệch âm 22,630 triệu, tức âm 5.2%." in said
    assert not any("EBITDA chênh lệch" in s for s in said)
    # 412,580 is four hundred twelve thousand, not 412.58
    assert any("bốn trăm mười hai nghìn năm trăm tám mươi" in h for h in heard)
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
    assert "Kính đề nghị Ban Giám đốc phê duyệt tăng 2,0 tỷ trade kênh GT cho T11–T12." in said
    assert any("«3. Phụ lục»" in n for n in rep["canh_bao"])           # a part that says no figure is named


def test_cards_are_said_as_sentences_and_never_repeat_a_figure(report):
    rep = clicky_html.build(str(report), voice=voice)
    said = [c["noi"] for s in _draft(rep)["canh"] for c in s["cau"]]
    assert "Tổng chi phí nhân sự AOPR là 186,4 tỷ, tăng 12,6% so với AOP gốc." in said
    assert not any(s.startswith("Trần đề xuất") for s in said)           # 177,5 was just said in the request
    assert "Cần giảm so với AOPR là 8,9 tỷ." in said
    # the drivers frame the row they talk about, not the whole table
    row = _id(report, "Khối Bán hàng")
    assert {"noi": "Thứ nhất, Khối Bán hàng chênh lệch 14,1, tức 18,0%.", "chi": row, "neo": "Khối Bán hàng"} in \
        [c for s in _draft(rep)["canh"] for c in s["cau"]]
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
    bad.write_text(good.replace("- noi: 'Phần 2. Chi tiết theo khối.'", "- noi: Phần 2: Chi tiết: xem bảng."),
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
    assert "Thứ nhất, Bán hàng chênh lệch 15,9 tỷ." in said


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
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = _data_of(rep["out"])
    assert any("__BIEN_BAN__" in s for sc in data["scenes"] for s in sc["sentences"])
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
