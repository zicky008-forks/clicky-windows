"""Golden Q&A grader and the Vietnamese figure reader it stands on."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine import so_viet
from tools import golden_qa


@pytest.mark.parametrize("text,value,unit", [
    ("1.500.000", "1500000", ""), ("1.234,5", "1234.5", ""), ("(25,99)", "-25.99", ""), ("−0,2 tỷ", "-0.2", "tỷ"),
    ("7,03%", "7.03", "%"), ("3,2 tỷ đồng", "3.2", "tỷ"), ("1.500", "1500", ""), ("150,0 triệu", "150.0", "triệu"),
    ("1,234,567.5", "1234567.5", ""), ("1 500 000 ₫", "1500000", "đồng"), ("0,12 điểm %", "0.12", "điểm %"),
])
def test_a_vietnamese_figure_is_read_as_written_and_never_as_a_float(text, value, unit):
    s = so_viet.parse(text)
    assert s is not None and s.value == Decimal(value) and s.unit == unit, (text, s)
    assert isinstance(s.value, Decimal)


def test_a_dotted_figure_follows_the_reports_marks_and_garbage_is_not_zero():
    assert so_viet.parse("1.500", vn=False).value == Decimal("1.5")
    assert so_viet.parse("1.50.0") is None and so_viet.parse("abc") is None
    assert so_viet.parse("3,2 tỷ").dong == Decimal("3200000000")


def test_a_sentence_gives_each_figure_with_its_sign_and_a_working_minus_is_not_a_sign():
    xs = so_viet.find_all("thấp hơn kế hoạch gốc 0,2 tỷ (em tính: 11,8 − 12,0). Cột «Chênh lệch» ghi (3,2).")
    assert [(str(x.value), x.unit) for x in xs] == [("0.2", "tỷ"), ("11.8", ""), ("12.0", ""), ("-3.2", "")]
    assert so_viet.same(so_viet.parse("3.200 triệu"), 3.2, "tỷ")


def test_the_grader_fails_a_verdict_on_a_judgement_and_a_missing_or_wrong_figure():
    case = {"id": "X", "cau_hoi": "… có đáng lo không?", "loai": "phan_xet", "so": [{"gia_tri": 3.2, "don_vi": "tỷ"}],
            "khong_duoc_co_so": [{"gia_tri": 3.5, "don_vi": "tỷ"}]}
    assert golden_qa.grade(case, "EBIT thấp hơn kế hoạch 3,2 tỷ. Báo cáo chỉ ghi số, không đánh giá có đáng lo hay không.")["dat"]
    r = golden_qa.grade(case, "Có, đáng lo. EBIT thấp hơn kế hoạch 3,5 tỷ.")
    assert not r["dat"] and len(r["loi"]) == 4, r
    out = {"id": "Y", "cau_hoi": "Đối thủ năm nay lãi bao nhiêu?", "loai": "ngoai_du_lieu"}
    assert golden_qa.grade(out, "Câu này em chưa tìm thấy trong báo cáo. Em ghi lại để anh Đài trả lời anh chị.")["dat"]
    assert not golden_qa.grade(out, "Khoảng 12 tỷ.")["dat"]


def test_the_presenter_page_passes_its_synthetic_golden_file(tmp_path, monkeypatch):
    from present import clicky_html
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    from test_story import build
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_hai_ke_hoach_ky.html"
    src.write_text((fx / "bao_cao_hai_ke_hoach_ky.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    try:
        ask = golden_qa.page_asker(rep["out"])
    except RuntimeError as exc:
        pytest.skip(str(exc))
    try:
        res = golden_qa.run(json.loads((Path(__file__).parent / "golden" / "hai_ke_hoach_ky.json").read_text(encoding="utf-8")), ask)
    finally:
        ask.close()
    assert res["dat"] == res["tong"], [r for r in res["cau"] if not r["dat"]]
