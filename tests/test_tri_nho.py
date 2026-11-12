"""Company memory: a parked question goes from the reader's e-mail into CAU_TREO.md; the owner's answer is read
once at the start of the next talk, kept as a ready answer, and recorded with its figures."""
import json
import re
import shutil
from pathlib import Path

import pytest

from present import clicky_html, kiem_bien_ban, tri_nho

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"

MAIL = """Chào anh Tuấn,

Khi xem báo cáo "bao_cao.html", tôi có các câu hỏi sau:

1. Vì sao khối CNTT tăng 12%?
2. Lương bình quân khối bán hàng là bao nhiêu?

(Gửi từ Clicky, mã báo cáo 0123456789abcdef)"""


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path / "bao_cao.html"
    shutil.copyfile(FIXTURE, dst)
    return dst


def voice(text):
    return text.encode("utf-8")


def test_templates_are_created_once_and_never_rewritten(tmp_path):
    d = tri_nho.ensure(str(tmp_path / "nho"))
    assert sorted(p.name for p in d.iterdir()) == ["BAI_HOC.md", "CAU_TREO.md", "SEP_HAY_HOI.md"]
    (d / "BAI_HOC.md").write_text("- Tháng 9 thiếu cột lương bình quân.\n", encoding="utf-8")
    tri_nho.ensure(str(d))
    mem = tri_nho.load(str(d))
    # the examples inside the instructions are not read as real entries
    assert mem.lessons == ["Tháng 9 thiếu cột lương bình quân."] and mem.bosses == {} and mem.questions == []


def test_questions_from_the_readers_email_are_added_once(tmp_path):
    mail = tri_nho.parse_mail(MAIL)
    assert mail == {"report": "bao_cao.html",
                    "questions": ["Vì sao khối CNTT tăng 12%?", "Lương bình quân khối bán hàng là bao nhiêu?"]}
    d = str(tmp_path / "nho")
    first = tri_nho.add_questions(mail["questions"], mail["report"], "anh Hùng", "12/11/2026", path=d)
    again = tri_nho.add_questions(mail["questions"], mail["report"], path=d)
    assert len(first["added"]) == 2 and again["added"] == [] and len(again["already"]) == 2
    mem = tri_nho.load(d)
    assert [e.text for e in mem.open_questions()] == mail["questions"]
    assert mem.questions[0].fields["ai_hoi"] == "anh Hùng" and mem.questions[0].fields["bao_cao"] == "bao_cao.html"
    with pytest.raises(ValueError, match="Gửi từ Clicky"):
        tri_nho.parse_mail("1. một câu bất kỳ")


def test_cli_reads_an_email_file(tmp_path, capsys):
    f = tmp_path / "mail.txt"
    f.write_text(MAIL, encoding="utf-8")
    d = str(tmp_path / "nho")
    assert tri_nho.main(["--thu-muc", d, "ghi", str(f)]) == 0
    assert "Đã ghi 2 câu mới" in capsys.readouterr().out
    assert tri_nho.main(["--thu-muc", d, "xem"]) == 0
    assert "Câu chưa trả lời: 2" in capsys.readouterr().out


def _answer(d, question, text):
    f = Path(d) / tri_nho.TREO
    lines = f.read_text(encoding="utf-8").splitlines()
    i = lines.index(f"- Câu: {question}")
    j = next(k for k in range(i, len(lines)) if lines[k].strip().startswith("Trả lời:"))
    lines[j] = f"  Trả lời: {text}"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _data(page):
    return json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))


def test_an_answer_is_read_once_kept_ready_and_recorded(report, tmp_path):
    d = str(tmp_path / "nho")
    tri_nho.add_questions(["Vì sao khối CNTT tăng 12%?", "Lương bình quân khối bán hàng là bao nhiêu?"],
                          "thang_truoc.html", path=d)
    # 15,8 and 17,7 are in the report; 2,7 is the owner's own figure
    _answer(d, "Vì sao khối CNTT tăng 12%?", "Khối CNTT từ 15,8 lên 17,7 tỷ do dự án ERP thêm 2,7 tỷ thuê ngoài.")
    with open(Path(d) / tri_nho.TREO, "a", encoding="utf-8") as f:
        f.write("\n- Hứa: Gửi chi tiết lương khối bán hàng cho CFO\n  Hạn: 20/11/2026\n")
    rep = clicky_html.build(str(report), voice=voice, memory=d)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = _data(page)
    said = [s for sc in data["scenes"] for s in sc["sentences"]]
    assert data["scenes"][1]["title"] == "Trả lời câu hỏi lần trước"
    assert "Anh Tuấn trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ do dự án ERP thêm 2,7 tỷ thuê ngoài." in said
    # the open question is not read; it is reminded to the owner with the open promise
    assert not any("Lương bình quân" in s for s in said)
    m = rep["tri_nho"]
    assert m["doc_dau"] == 1 and m["tra_loi_san"] == 1
    assert m["cau_chua_tra_loi"] == ["Lương bình quân khối bán hàng là bao nhiêu?"]
    assert m["hua_chua_xong"] == ["Gửi chi tiết lương khối bán hàng cho CFO (hạn 20/11/2026)"]
    assert "Còn 1 câu treo" in clicky_html.remind(rep)
    # figures: the owner's 2,7 is his, the others are in the report; the record re-check passes
    ev = data["scenes"][1]["ev"][2]
    assert ["2,7", "o"] in ev and ["15,8", "b"] in ev
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["tra_loi_san"] == 1 and data["bank"][0]["q"] == "Vì sao khối CNTT tăng 12%?"
    # read once per report: the same report again still reads it, another report does not
    assert "Đã nói trong: bao_cao.html" in (Path(d) / tri_nho.TREO).read_text(encoding="utf-8")
    Path(rep["out"]).unlink()
    assert clicky_html.build(str(report), voice=voice, memory=d)["tri_nho"]["doc_dau"] == 1
    other = report.with_name("thang_sau.html")
    shutil.copyfile(FIXTURE, other)
    rep2 = clicky_html.build(str(other), voice=voice, memory=d)
    assert rep2["tri_nho"]["doc_dau"] == 0 and rep2["tri_nho"]["tra_loi_san"] == 1
    # a ready answer changed on the page after the build is caught
    tampered = Path(rep2["out"]).read_text(encoding="utf-8").replace("thêm 2,7 tỷ", "thêm 7,2 tỷ")
    bad = report.with_name("sua.html")
    bad.write_text(tampered, encoding="utf-8")
    assert not kiem_bien_ban.verify(str(bad))["ok"]


def test_the_page_writes_the_email_that_ghi_reads():
    layer = (Path(clicky_html.__file__).with_name("clicky_html_layer.html")).read_text(encoding="utf-8")
    assert 'Khi xem báo cáo \\"" + M.report + "\\", tôi có các câu hỏi sau' in layer
    assert '(i + 1) + ". " + x.q' in layer and "(" + tri_nho.MAIL_MARK + ", mã báo cáo" in layer
