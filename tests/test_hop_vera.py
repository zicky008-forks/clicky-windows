"""Hộp Vera: the seven pieces, the rule book's first rule and the six checks between pieces."""
from pathlib import Path

import yaml

from present import hop_vera
from present.placeholders import Resolver


def write(d: Path, name: str, data) -> None:
    text = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    (d / name).write_text(text, encoding="utf-8")


def small_box(d: Path) -> None:
    write(d, "tu_dien_so.yaml", {"khoa_chinh": {"cost": "Chi phí", "ratio": "Tỷ lệ"}})
    write(d, "kich_ban.yaml", {"slides": [{"so": 1, "loi_noi": "Kênh SME chi {tq:cost}, bằng {tq:ratio} doanh thu."}]})
    write(d, "hoi_dap.yaml", {"cau_hoi": [
        {"id": "Q1", "hoi_mau": "Tổng chi phí bao nhiêu?", "tra_loi": "Chi phí {tq:cost}.", "drill": {"sheet": "Tong_quan"}},
        {"id": "Q2", "hoi_mau": "Tỷ lệ này có đáng lo không?", "tra_loi": "Có, đáng lo. Tỷ lệ {tq:missing}."}]})


def test_the_box_says_which_pieces_are_there_part_there_or_missing(tmp_path):
    small_box(tmp_path)
    write(tmp_path, "CAU_TREO.md", "# Câu treo\n")
    box = hop_vera.open_box(tmp_path)
    st = {p.key: box.status(p) for p in hop_vera.PIECES}
    assert st == {"tu_dien_so": "đủ", "boi_canh": "thiếu", "quy_tac": "thiếu", "kich_ban": "đủ", "hoi_dap": "đủ",
                  "nguon": "đủ", "cau_treo": "đủ"}
    assert [p.so for p in hop_vera.PIECES] == [1, 2, 3, 4, 5, 6, 7]
    assert "Hộp Vera" in hop_vera.summary(box, hop_vera.check(box))


def test_a_broken_piece_is_named_never_dropped_in_silence(tmp_path):
    write(tmp_path, "hoi_dap.yaml", "cau_hoi: [\n  - id: Q1\n tra_loi: x")
    box = hop_vera.open_box(tmp_path)
    assert "hoi_dap.yaml" in box.broken
    assert "hoi_dap.yaml không đọc được" in hop_vera.summary(box, hop_vera.check(box))


def test_rule_one_takes_the_verdict_off_a_judgement_answer_and_hands_the_judgement_to_the_owner():
    for q, a in (("EBIT tháng 10 thấp hơn kế hoạch có đáng lo không?", "Có. EBIT thấp hơn kế hoạch 3,2 tỷ."),
                 ("Chi phí vượt kế hoạch có ổn không?", "Không, theo số trên bảng. Chi phí cao hơn kế hoạch 0,8 tỷ."),
                 ("Hai kế hoạch có nghiêm trọng không", "Không so với kế hoạch gốc; có so với kế hoạch linh hoạt. Chi phí …")):
        out, hit = hop_vera.guard(q, a)
        assert hit and not out.startswith(("Có", "Không", "Chưa")), out
        assert out.endswith(hop_vera.JUDGE_NOTE), out
    # a fact question keeps its yes/no lead
    assert hop_vera.guard("EBIT có đạt kế hoạch không?", "Có. EBIT 236,0 tỷ.") == ("Có. EBIT 236,0 tỷ.", False)


def test_the_owner_can_add_his_own_judgement_words_but_a_short_file_never_drops_a_rule(tmp_path):
    write(tmp_path, "quy_tac.yaml", {"khong_phan_xet": {"bat": "có", "them_tu": ["Có căng không"]}})
    book = hop_vera.rules(hop_vera.open_box(tmp_path))
    assert hop_vera.judgement("Dòng tiền quý 4 có căng không?", book) == "co cang khong"
    assert hop_vera.judgement("Chi phí có đáng lo không?", book) == "dang lo"
    assert set(book) == set(hop_vera.DEFAULT_RULES)
    write(tmp_path, "quy_tac.yaml", {"khong_phan_xet": {"bat": "không"}})
    book = hop_vera.rules(hop_vera.open_box(tmp_path))
    assert hop_vera.judgement("Chi phí có đáng lo không?", book) == ""
    light = hop_vera.check(hop_vera.open_box(tmp_path))[-1]
    assert light.den == "vàng" and "đang tắt" in light.chi_tiet


def test_the_rule_book_template_is_written_once_and_never_over_the_owners_file(tmp_path):
    p = hop_vera.ensure_rules(tmp_path)
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["khong_phan_xet"]["bat"] is not None
    p.write_text("khong_phan_xet: {bat: có, them_tu: [x]}\n", encoding="utf-8")
    hop_vera.ensure_rules(tmp_path)
    assert "them_tu: [x]" in p.read_text(encoding="utf-8")


class Cells:
    def __init__(self, table):
        self.table = table

    def rows(self, sheet, first_row, cols):
        return [[k, v, "bn" if v > 1 else "pct2"] for k, v in self.table.items()]

    def cell(self, sheet, address):
        return None

    def number_format(self, sheet, address):
        return ""


def test_six_checks_light_yellow_for_what_does_not_fit_and_never_green_for_what_was_not_looked_at(tmp_path):
    small_box(tmp_path)
    lights = {x.ten: x for x in hop_vera.check(hop_vera.open_box(tmp_path))}
    assert len(lights) == 6
    assert lights["Kịch bản ↔ Từ điển số"].den == "vàng" and "missing" in lights["Kịch bản ↔ Từ điển số"].chi_tiet
    assert lights["Từ điển số ↔ Excel"].den == "vàng" and "chưa mở Excel" in lights["Từ điển số ↔ Excel"].chi_tiet
    assert lights["Slide ↔ Excel"].den == "vàng"
    assert lights["Câu hỏi BOD ↔ Bằng chứng"].den == "vàng" and "Q2" in lights["Câu hỏi BOD ↔ Bằng chứng"].chi_tiet
    assert lights["Bối cảnh ↔ Slide"].den == "vàng" and "SME" in lights["Bối cảnh ↔ Slide"].chi_tiet
    assert lights["Sổ quy tắc ↔ Câu trả lời"].den == "vàng" and "Q2" in lights["Sổ quy tắc ↔ Câu trả lời"].chi_tiet

    # fixed pieces: all green except the deck check, which has no deck to look at
    write(tmp_path, "hoi_dap.yaml", {"cau_hoi": [
        {"id": "Q1", "hoi_mau": "Tổng chi phí bao nhiêu?", "tra_loi": "Chi phí {tq:cost}.", "drill": {"sheet": "Tong_quan"}},
        {"id": "Q2", "hoi_mau": "Tỷ lệ này có đáng lo không?", "tra_loi": "Tỷ lệ {tq:ratio}. Báo cáo không đánh giá.",
         "drill": {"sheet": "Ky"}}]})
    write(tmp_path, "TU_DOC.txt", "SME = khách hàng doanh nghiệp vừa và nhỏ\n")
    resolver = Resolver(Cells({"cost": 12.5, "ratio": 0.07}))
    lights = {x.ten: x for x in hop_vera.check(hop_vera.open_box(tmp_path), resolver=resolver)}
    assert [n for n, x in lights.items() if not x.ok] == ["Slide ↔ Excel"], lights


def test_the_deck_talk_answers_a_judgement_question_without_a_verdict():
    from present.talk import Talk
    bank = {"cau_hoi": [{"id": "Q9", "hoi_mau": "Tỷ lệ chi phí có đáng lo không?",
                         "tu_khoa": {"va": [["ty le"], ["dang lo"]]}, "tra_loi": "Có. Tỷ lệ là {tq:ratio}."}]}
    said = []
    talk = Talk({"slides": []}, bank, Resolver(Cells({"ratio": 0.0703})), deck=None, say=lambda a, b: said.append(a),
                point=lambda *a: None, wait=lambda s: None)
    r = talk.answer("Tỷ lệ chi phí có đáng lo không?")
    assert r["id"] == "Q9" and not r["text"].startswith("Có"), r
    assert "không đánh giá" in r["text"]
