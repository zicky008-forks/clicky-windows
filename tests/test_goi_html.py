"""The e-mail pack: one HTML file built only from the locked workbook and the approved bank."""
import json
import re

import openpyxl
import pytest
from pptx import Presentation
from pptx.util import Inches

from present import goi_html


def _deck(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for title, boxes in [("Tổng quan chi phí", ["Đề xuất trần 518,0 tỷ", "AOPR 520,0 tỷ"]),
                         ("Năm quyết định", ["1 Duyệt trần", "2 Marketing theo quý"])]:
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = title
        for i, text in enumerate(boxes):
            s.shapes.add_textbox(Inches(1), Inches(2 + i), Inches(4), Inches(0.6)).text_frame.text = text
    prs.save(path)


def _book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tong_quan"
    for r, (k, v, kind) in enumerate([("cost_aopr", 520, "bn"), ("cost_cap_aop", 518, "bn"),
                                      ("ratio_aop", 0.07, "pct2")], start=4):
        ws.cell(r, 1, k), ws.cell(r, 2, v), ws.cell(r, 3, kind)
    d = wb.create_sheet("De_xuat")
    for r, (name, aop, aopr) in enumerate([("P. Brand MKT", 80, 110), ("P. Nhân sự", 20, 22),
                                           ("Tổng cộng", 100, 132)], start=4):
        d.cell(r, 4, name), d.cell(r, 5, aop), d.cell(r, 7, aopr)
        d.cell(r, 5).number_format = d.cell(r, 7).number_format = "#,##0"
    wb.save(path)


SCRIPT = {
    "meta": {"mo_dau": "Kính chào anh chị.", "ket_thuc": "Em xin hết."},
    "slides": [
        {"so": 1, "loi_noi": "Phòng ban xin {tq:cost_aopr}. Đề xuất trần {tq:cost_cap_aop}.",
         "chi_vao": [{"shape": "tiêu đề"}, {"shape_chua": "Đề xuất trần"}]},
        {"so": 2, "loi_noi": "{slide:title}: một, duyệt trần {tq:cost_cap_aop}.", "chi_vao": [{"shape_chua": "1"}]},
    ],
}
BANK = {
    "cau_hoi": [
        {"id": "Q01", "hoi_mau": "Tổng chi phí bao nhiêu?", "tu_khoa": {"va": [["tong", "bao nhieu"]]},
         "tra_loi": "AOPR là {tq:cost_aopr}.", "drill": {"sheet": "Tong_quan"}},
        {"id": "Q04", "hoi_mau": "Phòng X xin bao nhiêu?", "tu_khoa": {"va": [["phong"], ["xin"]]},
         "tham_so": {"ten_phong": {"sheet": "De_xuat", "cot": 4, "tu_dong": 4}},
         "tra_loi": "{ten_phong}: AOP gốc {xl:De_xuat!E<dong>}, AOPR {xl:De_xuat!G<dong>}."},
        {"id": "Q05", "hoi_mau": "Nếu doanh thu giảm?", "tu_khoa": {"va": [["neu"]]}, "what_if": {"ghi": []},
         "tra_loi": "{xl:Kich_ban!B23}"},
    ],
    "khong_khop": {"tra_loi": "Câu này em ghi lại, anh Tuấn sẽ trả lời sau."},
}


@pytest.fixture
def files(tmp_path):
    import yaml
    deck, book = tmp_path / "deck.pptx", tmp_path / "so_lieu.xlsx"
    _deck(deck)
    _book(book)
    script, bank = tmp_path / "kb.yaml", tmp_path / "hd.yaml"
    script.write_text(yaml.safe_dump(SCRIPT, allow_unicode=True), encoding="utf-8")
    bank.write_text(yaml.safe_dump(BANK, allow_unicode=True), encoding="utf-8")
    return tmp_path, deck, book, script, bank


def _fake(monkeypatch):
    spoken = []
    monkeypatch.setattr(goi_html, "wav_to_mp3", lambda wav: (wav, 2.0))
    voice = lambda text: (spoken.append(text) or text.encode("utf-8"))   # noqa: E731
    renderer = lambda deck, work: [b"jpg1", b"jpg2"]                     # noqa: E731
    return spoken, voice, renderer


def _data(path):
    page = path.read_text(encoding="utf-8")
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    return page, json.loads(blob.replace("<\\/", "</"))


def test_pack_speaks_only_checked_figures(files, monkeypatch):
    tmp, deck, book, script, bank = files
    spoken, voice, renderer = _fake(monkeypatch)
    out = tmp / "goi.html"
    rep = goi_html.build(str(deck), str(script), str(bank), str(book), str(out), voice=voice, renderer=renderer,
                         label="SỐ GIẢ LẬP", title="AOPR thử")
    assert rep["slides"] == 2 and rep["not_found"] == {}
    page, data = _data(out)
    s1, s2 = data["scenes"]
    assert s1["line"].startswith("Kính chào anh chị.") and "520,0 tỷ" in s1["line"] and "518,0 tỷ" in s1["line"]
    assert s2["line"].startswith("Năm quyết định") and s2["line"].endswith("Em xin hết.")
    assert len(s1["points"]) == 2 and all(0 <= v <= 1 for p in s1["points"] for v in p["box"][:2])
    # The second frame lands where its words are said, not at the start of the line.
    assert s1["points"][1]["at"] > s1["points"][0]["at"]
    assert not any("{" in t for t in spoken)
    assert data["meta"]["label"] == "SỐ GIẢ LẬP" and len(data["meta"]["workbook_sha256"]) == 64
    assert "Trợ lý AI của" in page


def test_bank_answers_are_filled_and_what_if_is_parked(files, monkeypatch):
    tmp, deck, book, script, bank = files
    _, voice, renderer = _fake(monkeypatch)
    out = tmp / "goi.html"
    goi_html.build(str(deck), str(script), str(bank), str(book), str(out), voice=voice, renderer=renderer)
    _, data = _data(out)
    q1, q4, q5 = data["qa"]["entries"]
    assert q1["kind"] == "answer" and "520,0 tỷ" in q1["text"] and q1["source"] == "sheet Tong_quan"
    assert q4["kind"] == "dept" and [d["name"] for d in q4["depts"]] == ["P. Brand MKT", "P. Nhân sự"]
    assert q4["depts"][0]["key"] == "brand mkt" and "dòng 4" in q4["depts"][0]["source"]
    assert q5["kind"] == "park" and "Kich_ban" not in q5["text"]
    assert data["qa"]["fallback_clip"]["audio"]


def test_missing_figure_stops_the_build(files, monkeypatch):
    tmp, deck, book, script, bank = files
    _, voice, renderer = _fake(monkeypatch)
    import yaml
    bad = dict(SCRIPT, slides=[dict(SCRIPT["slides"][0], loi_noi="Chi phí {tq:khong_co}.")])
    script.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="khong_co"):
        goi_html.build(str(deck), str(script), str(bank), str(book), str(tmp / "x.html"), voice=voice,
                       renderer=renderer)
    assert not (tmp / "x.html").exists()


def test_data_cannot_close_the_script_tag(tmp_path):
    out = tmp_path / "p.html"
    goi_html.write_html(str(out), {"meta": {"title": "a</script><b>"}, "x": "</script>"})
    page = out.read_text(encoding="utf-8")
    assert page.count("</script>") == 2          # the two real closing tags only
