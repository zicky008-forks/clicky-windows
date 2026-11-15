"""Company memory: a parked question goes from the reader's e-mail into CAU_TREO.md; the owner's answer is read
once at the start of the next talk, kept as a ready answer, and recorded with its figures."""
import datetime
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from present import clicky_html, kiem_bien_ban, tri_nho

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"

MAIL = """Chào anh Tuấn,

Khi xem báo cáo "bao_cao.html", tôi có các câu hỏi sau:

1. Vì sao khối CNTT tăng 12%?
2. Lương bình quân khối bán hàng là bao nhiêu?

(Gửi từ Clicky, mã báo cáo 0123456789abcdef)"""


BUILD = clicky_html.build
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


def test_templates_are_created_once_and_never_rewritten(tmp_path):
    d = tri_nho.ensure(str(tmp_path / "nho"))
    assert sorted(p.name for p in d.iterdir()) == ["BAI_HOC.md", "CAU_TREO.md", "SEP_HAY_HOI.md", "TU_DOC.txt"]
    # the example in TU_DOC.txt is a comment: it shows the format without changing how anything is read
    assert all(l.startswith("#") for l in (d / "TU_DOC.txt").read_text(encoding="utf-8").splitlines() if l.strip())
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


def test_an_answer_is_read_once_kept_ready_and_recorded(report, tmp_path, monkeypatch):
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
    # right after the greeting, worded the way a person says it, and in the script the owner can edit
    assert data["scenes"][1]["title"] == "Câu hỏi lần trước"
    assert data["scenes"][1]["sentences"][0] == "Lần trước có anh chị hỏi: Vì sao khối CNTT tăng 12%?"
    assert "Anh Tuấn xin trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ do dự án ERP thêm 2,7 tỷ thuê ngoài." in said
    script = yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))
    assert script["canh"][1]["muc"] == "Câu hỏi lần trước" and script["canh"][1]["tri_nho"] is True
    # the open question is not read; it is reminded to the owner with the open promise
    assert not any("Lương bình quân" in s for s in said)
    m = rep["tri_nho"]
    assert m["doc_dau"] == 1 and m["tra_loi_san"] == 1
    assert m["cau_chua_tra_loi"] == ["Lương bình quân khối bán hàng là bao nhiêu?"]
    assert m["hua_chua_xong"] == ["Gửi chi tiết lương khối bán hàng cho CFO (hạn 20/11/2026)"]
    assert "Còn 1 câu treo" in clicky_html.remind(rep)
    # figures: 15,8 and 17,7 sit on the CNTT row the answer is about, so that row is framed; 2,7 is his
    ev = data["scenes"][1]["ev"][1]
    assert ev == [["15,8", "k"], ["17,7", "k"], ["2,7", "o"]]
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["tra_loi_san"] == 1 and data["bank"][0]["q"] == "Vì sao khối CNTT tăng 12%?"
    # read once per report: the same report again still reads it, another report does not
    # no month in the file name or title: the month it is built in
    month = tri_nho.report_period("bao_cao.html")
    assert re.fullmatch(r"\d\d/20\d\d", month)
    assert f"Đã nói trong: {month}" in (Path(d) / tri_nho.TREO).read_text(encoding="utf-8")
    Path(rep["out"]).unlink()
    assert clicky_html.build(str(report), voice=voice, memory=d)["tri_nho"]["doc_dau"] == 1
    # next month, the same file saved under a new name (or the same one): the answer is not read again
    real = tri_nho.report_period
    monkeypatch.setattr(tri_nho, "report_period", lambda n, t="", today=None: real(n, t, datetime.date(2099, 1, 1)))
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
    assert re.search(r'Khi xem báo cáo \\"" \+ [\w.]+ \+ "\\", tôi có các câu hỏi sau', layer)
    assert '(i + 1) + ". " + x.q' in layer and "(" + tri_nho.MAIL_MARK + ", mã báo cáo" in layer


def _cp1258(text):
    """What Notepad's "ANSI" writes on a Vietnamese Windows: Windows-1258, tone marks as combining characters."""
    import unicodedata
    out = b""
    for ch in text:
        try:
            out += ch.encode("cp1258")
        except UnicodeEncodeError:
            d = unicodedata.normalize("NFD", ch)
            tone = "".join(c for c in d if c in "\u0300\u0301\u0303\u0309\u0323")
            out += unicodedata.normalize("NFC", "".join(c for c in d if c not in tone)).encode("cp1258") + \
                tone.encode("cp1258")
    return out


def test_owner_files_saved_by_notepad_in_any_encoding_are_read(tmp_path):
    text = "- Câu: Vì sao khối CNTT tăng?\n  Trả lời: Do dự án ERP.\n"
    f = tmp_path / "a.md"
    for data in (text.encode("utf-8"), b"\xef\xbb\xbf" + text.encode("utf-8"),
                 _cp1258(text)):
        f.write_bytes(data)
        entries = tri_nho.read_treo(tri_nho.read_text(f))
        assert len(entries) == 1 and entries[0].answer == "Do dự án ERP."   # the BOM never hides the first entry


def test_owner_name_and_email_are_asked_once_and_kept(tmp_path, capsys):
    d = str(tmp_path / "nho")
    answers = iter(["anh Tuấn", "khong-phai-email", "anh Tuấn", "tuan@congty.vn"])
    assert tri_nho.ask_owner(d, ask=lambda _: next(answers)) == ("anh Tuấn", "tuan@congty.vn")
    assert "chưa đúng" in capsys.readouterr().out
    assert tri_nho.ask_owner(d, ask=lambda _: pytest.fail("asked twice")) == ("anh Tuấn", "tuan@congty.vn")
    assert "Email: tuan@congty.vn" in (Path(d) / tri_nho.CHU).read_text(encoding="utf-8")


def test_period_key_lets_every_version_of_the_month_read_the_answer():
    assert tri_nho.report_period("BC nhan su T10 v1.html", "Báo cáo tháng 10/2026") == "10/2026"
    assert tri_nho.report_period("BC nhan su T10 final.html", "Báo cáo tháng 10/2026") == "10/2026"
    assert tri_nho.report_period("PL T10-2026 Actual vs AOP.html") == "10/2026"
    assert tri_nho.report_period("BC Q3 2026.html") == "Q3/2026"
    assert tri_nho.report_period("Bao cao nhan su v2.html") == tri_nho.report_period("Bao cao nhan su final.html")


def test_v1_and_final_both_read_last_months_answer_and_a_coincidence_is_not_the_reports(report, tmp_path):
    d = str(tmp_path / "nho")
    tri_nho.add_questions(["Vì sao khối CNTT tăng?"], "thang 9.html", "anh Hùng", path=d)
    # 1,5 is also on the page (Marketing's variance) but this answer is about CNTT: it is the owner's figure
    _answer(d, "Vì sao khối CNTT tăng?", "Khối CNTT từ 15,8 lên 17,7 tỷ, chủ yếu do dự án ERP chuyển 1,5 tỷ thuê ngoài.")
    html = FIXTURE.read_text(encoding="utf-8").replace("<h1>AOP Revised", "<h1>Tháng 10/2026: AOP Revised")
    for name in ("BC nhan su T10 v1.html", "BC nhan su T10 final.html"):
        (tmp_path / name).write_text(html, encoding="utf-8")
        rep = clicky_html.build(str(tmp_path / name), voice=voice, memory=d)
        assert rep["tri_nho"]["doc_dau"] == 1 and rep["tri_nho"]["ky"] == "10/2026"
        data = _data(Path(rep["out"]).read_text(encoding="utf-8"))
        scene = data["scenes"][1]
        assert scene["sentences"][:2] == ["Lần trước anh Hùng hỏi: Vì sao khối CNTT tăng?",
                                          "Anh Tuấn xin trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ, chủ yếu do dự án ERP "
                                          "chuyển 1,5 tỷ thuê ngoài."]
        assert scene["ev"][1] == [["15,8", "k"], ["17,7", "k"], ["1,5", "o"]]
    assert "Đã nói trong: 10/2026" in (Path(d) / tri_nho.TREO).read_text(encoding="utf-8")
    nov = tmp_path / "BC nhan su T11.html"
    nov.write_text(html.replace("Tháng 10/2026", "Tháng 11/2026"), encoding="utf-8")
    assert clicky_html.build(str(nov), voice=voice, memory=d)["tri_nho"]["doc_dau"] == 0


def test_a_report_without_a_month_is_keyed_on_the_month_it_is_built(tmp_path):
    when = datetime.date(2026, 12, 3)
    assert tri_nho.report_period("Bao cao nhan su gia lap.html", "AOP Revised FY2026–2027", when) == "12/2026"
    assert tri_nho.report_period("Bao cao.html", "", datetime.date(2027, 1, 5)) == "01/2027"
    assert tri_nho.report_period("BC nhan su T10 v1.html", "", when) == "T10"         # a month named wins


def test_notepad_unicode_files_are_read(tmp_path):
    f = tmp_path / "CAU_TREO.md"
    f.write_bytes("- Câu: Vì sao khối CNTT tăng?\r\n".encode("utf-16"))
    assert tri_nho.read_text(f) == "- Câu: Vì sao khối CNTT tăng?\n"
    f.write_bytes("- Câu: Lương?\n".encode("utf-16-le"))
    assert tri_nho.read_text(f) == "- Câu: Lương?\n"
    assert "# AOP = kế hoạch năm" in tri_nho.TEMPLATES[tri_nho.TU_DOC]


def _next_month(html: str) -> str:
    """The fixture a month later: the CNTT row and the totals moved (FP&A round 3, nm/B)."""
    html = html.replace("<td>15,8</td><td>17,7</td><td>1,9</td><td>12,0%</td>",
                        "<td>15,8</td><td>13,9</td><td>-1,9</td><td>-12,0%</td>")
    html = html.replace("186,4 tỷ", "182,6 tỷ").replace("<td>165,5</td><td>186,4</td><td>20,9</td><td>12,6%</td>",
                                                       "<td>165,5</td><td>182,6</td><td>17,1</td><td>10,3%</td>")
    return html


def test_next_month_never_rewrites_the_owners_quoted_answer(report, tmp_path, monkeypatch):
    d = str(tmp_path / "nho")
    tri_nho.add_questions(["Vì sao khối CNTT tăng?"], "thang_truoc.html", path=d)
    _answer(d, "Vì sao khối CNTT tăng?", "Khối CNTT từ 15,8 lên 17,7 tỷ, chủ yếu do dự án ERP thêm 1,5 tỷ thuê ngoài.")
    rep = clicky_html.build(str(report), voice=voice, memory=d)
    kb = Path(rep["kich_ban"])
    kb.write_text(kb.read_text(encoding="utf-8").replace("da_duyet: 'chưa'", "da_duyet: 'có'"), encoding="utf-8")
    assert "lên 17,7 tỷ" in kb.read_text(encoding="utf-8")
    # next month: same file name, new figures. The quoted answer belongs to last month and is not substituted
    real = tri_nho.report_period
    monkeypatch.setattr(tri_nho, "report_period", lambda n, t="", today=None: real(n, t, datetime.date(2099, 1, 1)))
    report.write_text(_next_month(report.read_text(encoding="utf-8")), encoding="utf-8")
    rep2 = clicky_html.build(str(report), voice=voice, memory=d)
    assert rep2["trang_thai"] == "SƠ BỘ: báo cáo kỳ mới, số mới đã thay vào lời đã duyệt"
    text = Path(rep2["kich_ban"]).read_text(encoding="utf-8")
    assert "từ 15,8 lên 13,9" not in text
    # it was read last month, so this month's talk has no "Câu hỏi lần trước" part
    assert not any(s.get("tri_nho") for s in clicky_html.load_script(rep2["kich_ban"])["canh"])
    # the ready answer stays on the page, stamped with the month it answered and marked as old
    bank = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>',
                                Path(rep2["out"]).read_text(encoding="utf-8"), re.S).group(1))["bank"]
    month = real("bao_cao.html")
    assert bank[0]["ky"] == month and bank[0]["cu"] is True
    assert kiem_bien_ban.verify(rep2["out"], str(report))["ok"]


def test_next_period_leaves_quoted_answers_and_own_figures_alone():
    script = {"canh": [
        {"muc": "Câu hỏi lần trước", "tri_nho": True,
         "cau": [{"noi": "Anh Tuấn xin trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ.", "chi": "c9r6", "neo": "Khối CNTT"}]},
        {"muc": "A", "cau": [{"noi": "Khối CNTT là 17,7 tỷ, thêm 1,5 tỷ thuê ngoài.", "chi": "c9r6", "neo": "Khối CNTT",
                              "so_rieng": ["1,5"]},
                             {"noi": "Khối CNTT là 17,7 tỷ.", "chi": "c9r6", "neo": "Khối CNTT"}]}]}
    place = clicky_html.Place("c9r6", "row", "Khối CNTT 15,8 13,9", "Khối CNTT", None, "Khối CNTT",
                              cols=[clicky_html.Col("AOP", "15,8", clicky_html.figures("15,8"), "tỷ", "value"),
                                    clicky_html.Col("AOPR", "13,9", clicky_html.figures("13,9"), "tỷ", "value")])
    out, _ = clicky_html.next_period(script, {"Khối CNTT": "15,8 17,7"}, {"c9r6": place}, False)
    said = [c["noi"] for s in out["canh"] for c in s["cau"]]
    assert said == ["Anh Tuấn xin trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ.",
                    "Khối CNTT là 17,7 tỷ, thêm 1,5 tỷ thuê ngoài.", "Khối CNTT là 13,9 tỷ."]


def test_a_ready_answer_of_this_period_is_not_old(report, tmp_path):
    d = str(tmp_path / "nho")
    tri_nho.add_questions(["Vì sao khối CNTT tăng?"], "thang_truoc.html", path=d)
    _answer(d, "Vì sao khối CNTT tăng?", "Khối CNTT từ 15,8 lên 17,7 tỷ.")
    rep = clicky_html.build(str(report), voice=voice, memory=d)
    bank = _data(Path(rep["out"]).read_text(encoding="utf-8"))["bank"]
    assert bank[0]["cu"] is False and bank[0]["ky"] == tri_nho.report_period("bao_cao.html")
