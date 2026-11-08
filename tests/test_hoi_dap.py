"""BOD questions answered from the saved FP&A report (and forecast pack) of the synthetic month folder: every number
an answer says is a cell of the saved file, follow-ups keep the question above, a question with no figure in the
report and a question not understood each get their own status, and the report itself is never changed."""
import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import fpa_samples as fs
import sales_samples as ss
from engine import forecast, fpa_report, hoi_dap, saved_check
from test_fpa_report import RULES, _with_ytd, case
from test_forecast import case as forecast_case

D = Decimal


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    d = case(tmp_path_factory.mktemp("book"), tb=False)
    fs.write_csv(d / "TB T10 2026.csv", _with_ytd(fs.tb_rows()[:-1] + ss.tb_rows()[1:]))
    out = fpa_report.write(fpa_report.compute(d))
    assert out["verified"]
    return hoi_dap.read(out["xlsx"])


@pytest.fixture(scope="module")
def with_pack(tmp_path_factory):
    d = forecast_case(tmp_path_factory.mktemp("pack"), ytd=True)
    report = fpa_report.write(fpa_report.compute(d))["xlsx"]
    pack = forecast.write(forecast.compute(d))
    assert pack["verified"]
    return hoi_dap.read(report, pack["xlsx"])


def _cells_hold(book, a):
    """Every cited cell of the answer, reopened in its own file, holds the cited value or text."""
    files = {}
    for c in a.cites:
        path = book.forecast.path if c.get("file") else book.path
        wb = files.setdefault(path, load_workbook(path, data_only=True))
        sheet, cell = saved_check.split(c["o"])
        got = wb[sheet][cell].value
        if "gia_tri" in c:
            assert D(str(got)) == D(c["gia_tri"]), c
        else:
            assert got == c["van_ban"], c


QUESTIONS = ["Lợi nhuận gộp tháng này so kế hoạch thế nào?", "Doanh thu lũy kế từ đầu năm?",
             "Vì sao chi phí hoạt động vượt kế hoạch?", "Top 3 khoản chi phí chênh lớn nhất?",
             "Cầu nối lợi nhuận gộp?", "Ảnh hưởng của giá bán bao nhiêu?", "Chi phí bộ phận MKT thế nào?",
             "Có khoản một lần nào không?", "Còn điểm nào chưa rõ?", "Vì sao lợi nhuận hoạt động thấp hơn kế hoạch?",
             "Giá vốn tháng này?", "Chi phí của KD lũy kế?"]


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_number_said_is_a_cell_of_the_saved_report(book, question):
    a = hoi_dap.answer(book, question)
    assert a.status == hoi_dap.ANSWERED, (question, a.query)
    rec = hoi_dap.record("Q1", question, a, book)
    assert hoi_dap.numbers_without_source(rec) == []
    assert any("gia_tri" in c for c in a.cites) or a.query.intent == "open"
    _cells_hold(book, a)
    assert rec["sha256"] == hoi_dap.sha256(book.path) and rec["ban_ke_hoach"] == "AOP"


def test_the_month_line_says_the_report_figures_and_their_source(book):
    a = hoi_dap.answer(book, "Lợi nhuận gộp tháng này so kế hoạch thế nào?")
    gp = book.pnl["gp"]
    assert a.text.startswith("Lợi nhuận gộp tháng 10/2026: thực hiện ")
    assert [c["o"] for c in a.cites] == [gp[f].ref for f in ("month", "plan", "var", "pct")]
    assert a.text.endswith("Nguồn: doanh thu thuần trừ giá vốn.")


@pytest.mark.parametrize("question", ["Lãi gộp tháng 10 ra sao?", "GP so với AOP?", "Gross profit tháng này?",
                                      "lợi nhuận gộp đạt bao nhiêu"])
def test_other_wordings_ask_the_same_thing(book, question):
    assert hoi_dap.answer(book, question).cites == hoi_dap.answer(book, "Lợi nhuận gộp tháng này?").cites


def test_a_follow_up_keeps_the_question_above(book, with_pack):
    first = hoi_dap.answer(book, "Lợi nhuận gộp tháng này?")
    ytd = hoi_dap.answer(book, "còn lũy kế?", first.query)
    assert ytd.status == hoi_dap.ANSWERED and ytd.cites[0]["o"] == book.pnl["gp"]["ytd"].ref
    why = hoi_dap.answer(book, "vì sao?", hoi_dap.answer(book, "Chi phí hoạt động tháng này?").query)
    assert why.query.intent == "why" and "Chi phí chênh nhiều nhất" in why.text
    full = hoi_dap.answer(with_pack, "Ước cả năm lợi nhuận?")
    assert full.query.intent == "forecast" and all(c["file"] == with_pack.forecast.path.name for c in full.cites)
    back = hoi_dap.answer(with_pack, "còn lũy kế?", full.query)
    assert back.query.intent == "line" and back.cites[0]["o"] == with_pack.pnl["op"]["ytd"].ref
    assert hoi_dap.answer(book, "vì sao?").status == hoi_dap.NOT_UNDERSTOOD     # no question above to take it from


def test_the_full_year_comes_from_the_forecast_pack_cells(with_pack):
    a = hoi_dap.answer(with_pack, "Dự báo doanh thu cả năm so kế hoạch năm?")
    assert a.status == hoi_dap.ANSWERED and "cả năm ước" in a.text and "LE T09" in a.text
    _cells_hold(with_pack, a)
    assert hoi_dap.numbers_without_source(hoi_dap.record("Q1", "x", a, with_pack)) == []


@pytest.mark.parametrize("question", ["Doanh thu cùng kỳ năm trước?", "Ước cả năm lợi nhuận?",
                                      "Doanh thu theo kênh nào tăng?"])
def test_a_question_the_report_has_no_figure_for_is_written_down_not_guessed(book, question):
    a = hoi_dap.answer(book, question)
    assert (a.status, a.text, a.cites) == (hoi_dap.NO_FIGURE, hoi_dap.NO_FIGURE_TEXT, [])


def test_a_question_not_understood_says_so(book):
    a = hoi_dap.answer(book, "Thời tiết hôm nay thế nào?")
    assert (a.status, a.text, a.cites) == (hoi_dap.NOT_UNDERSTOOD, hoi_dap.NOT_UNDERSTOOD_TEXT, [])


def test_without_a_sales_ledger_there_is_no_bridge_to_quote(tmp_path):
    d = tmp_path / "F01"
    fs.folder(d)
    ss.write_xlsx(d / "DanhMuc.xlsx", {"Ma hang": ss.catalogue_rows(), "Tham so": RULES})
    fs.write_csv(d / "TB T10 2026.csv", fs.tb_rows()[:-1] + ss.tb_rows()[1:])
    b = hoi_dap.read(fpa_report.write(fpa_report.compute(d))["xlsx"])
    assert not b.bridge and b.bridge_said.startswith("Chưa tính được")
    assert hoi_dap.answer(b, "Ảnh hưởng của giá bán?").status == hoi_dap.NO_FIGURE
    assert hoi_dap.answer(b, "Doanh thu lũy kế?").status == hoi_dap.NO_FIGURE   # the folder has no year to date


def test_open_points_quote_the_receipt_cells(book):
    a = hoi_dap.answer(book, "Còn điểm nào chưa rõ?")
    assert a.cites and all(c["o"].startswith("'Phieu nhan file'!A") and "van_ban" in c for c in a.cites)
    _cells_hold(book, a)


def test_a_number_not_in_any_cell_is_caught():
    rec = {"tra_loi": "Doanh thu 1.000.000 đ, tăng 5,0%.", "so_lieu": [{"o": "'P&L'!B4", "gia_tri": "1000000"}]}
    assert hoi_dap.numbers_without_source(rec) == ["5,0"]


def test_the_question_file_numbers_questions_and_follow_ups(tmp_path):
    p = tmp_path / "cau_hoi.txt"
    p.write_bytes("﻿Q1. Lợi nhuận gộp?\r\n+ còn lũy kế?\r\n\r\n+ vì sao?\r\nCâu 5: Chi phí MKT?\nTop 3 chi phí?\n"
                  .encode("utf-8"))
    assert hoi_dap.read_questions(p) == [("Q1", "Lợi nhuận gộp?", False), ("Q1.1", "còn lũy kế?", True),
                                         ("Q1.2", "vì sao?", True), ("Q5", "Chi phí MKT?", False),
                                         ("Q6", "Top 3 chi phí?", False)]


def test_answers_go_to_a_copy_and_the_report_is_never_changed(book, tmp_path, monkeypatch):
    before = book.path.read_bytes()
    q = tmp_path / "q.txt"
    q.write_text("Lợi nhuận gộp tháng này?\n+ còn lũy kế?\nThời tiết?\nCòn điểm nào chưa rõ?\n", encoding="utf-8")
    records = hoi_dap.answer_file(book, hoi_dap.read_questions(q))
    assert [r["trang_thai"] for r in records] == [hoi_dap.ANSWERED, hoi_dap.ANSWERED, hoi_dap.NOT_UNDERSTOOD,
                                                  hoi_dap.ANSWERED]
    out = hoi_dap.write(book, records)
    assert out["verified"] and book.path.read_bytes() == before
    assert out["xlsx"].name == "Clicky_FPA_T10-2026_HoiDap.xlsx"
    rows = list(load_workbook(out["xlsx"])[hoi_dap.SHEET].iter_rows(values_only=True))
    assert rows[0][0].startswith("SƠ BỘ") and rows[2] == tuple(hoi_dap.HEADER)
    assert [r[0] for r in rows[3:]] == ["Q1", "Q1.1", "Q2", "Q3"]
    lines = out["jsonl"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert hoi_dap.latest(book.path.parent.parent, fpa_report.OUT_PREFIX) == book.path          # never the copy
    monkeypatch.setenv(saved_check.TAMPER_ENV, f"{records[0]['so_lieu'][0]['o']}=1000")
    assert not hoi_dap.write(book, records)["verified"]


def test_the_chat_and_the_file_give_the_same_answer_and_the_log_holds_no_words(book, tmp_path, monkeypatch):
    import asyncio
    import re
    from office import hoi_dap_skill, month_close
    from skills import load_all, match
    assert hoi_dap_skill.args(["Clicky.exe", "--hoi-dap", "D:/case", "--cau-hoi", "D:/q.txt"]) == ("D:/case",
                                                                                                    "D:/q.txt")
    assert hoi_dap_skill.args(["Clicky.exe", "--hoi-dap", "D:/case"]) is None
    rx = re.compile(hoi_dap_skill.TRIGGER, re.I)
    for said in ("hỏi: lợi nhuận gộp?", "Hỏi tiếp: vì sao?", "BOD hỏi: chi phí MKT", "hoi: + còn lũy kế?"):
        assert rx.search(said), said
    for other in ("hôm nay làm gì", "câu hỏi này khó", "làm báo cáo FP&A tháng"):
        assert not rx.search(other), other
    load_all()
    assert match("hỏi: làm forecast gửi HO chưa, dự báo cả năm bao nhiêu?")["name"] == "Hỏi đáp BOD"
    played, logged = [], []

    async def play(manager, beats, **k):
        played.extend(beats)
    monkeypatch.setattr(month_close, "play", play)
    monkeypatch.setattr(hoi_dap_skill.scene_log, "record", lambda event, **data: logged.append((event, data)))
    folder = book.path.parent.parent

    class Manager:
        _excel = None
    m = Manager()
    monkeypatch.setattr(month_close, "_SOURCE", str(folder), raising=False)
    for said in ("hỏi: Vì sao chi phí hoạt động vượt kế hoạch?", "hỏi tiếp: còn lũy kế?", "hỏi: thời tiết?"):
        asyncio.run(hoi_dap_skill.handle(m, said))
    chat = [json.loads(x) for x in (book.path.parent / hoi_dap.JSONL).read_text(encoding="utf-8").splitlines()][-3:]
    assert [r["so"] for r in chat] == ["Q1", "Q1.1", "Q2"]
    assert chat[1]["so_lieu"][0]["o"] == book.pnl["opex"]["ytd"].ref
    assert played[0].text.startswith("Chi phí hoạt động tháng 10/2026") and played[0].mark == "ok"
    assert played[1].text == "Chi phí chênh nhiều nhất:" and "chứng từ" not in played[2].text
    assert played[-1].text == hoi_dap.NOT_UNDERSTOOD_TEXT and played[-1].mark == "warn"
    q = tmp_path / "q.txt"
    q.write_text("Vì sao chi phí hoạt động vượt kế hoạch?\n+ còn lũy kế?\nthời tiết?\n", encoding="utf-8")
    result = hoi_dap_skill.run_file(folder, q)
    assert [(r["tra_loi"], r["so_lieu"]) for r in result["records"]] == [(r["tra_loi"], r["so_lieu"]) for r in chat]
    words = repr(logged)
    assert "chi phí" not in words.lower() and str(folder) not in words and "Vì sao" not in words
    assert ("hoi_dap", {"so": "Q1", "trang_thai": hoi_dap.ANSWERED, "so_lieu": len(chat[0]["so_lieu"])}) in logged
    played.clear()
    asyncio.run(hoi_dap_skill.run(m, folder, q))
    assert played[0].text == "Em đã trả lời 2/3 câu bằng số trong báo cáo, mở lại kiểm tra thấy khớp."
    assert played[1].mark == "warn" and played[-1].text.endswith("Báo cáo gốc em không sửa gì.")


def test_without_a_report_the_chat_says_what_to_do_first(tmp_path, monkeypatch):
    import asyncio
    from office import hoi_dap_skill, month_close
    played = []

    async def play(manager, beats, **k):
        played.extend(beats)
    monkeypatch.setattr(month_close, "play", play)
    monkeypatch.setattr(month_close, "_SOURCE", str(tmp_path), raising=False)
    monkeypatch.setattr(hoi_dap_skill.scene_log, "record", lambda event, **data: None)

    class Manager:
        _excel = None
    asyncio.run(hoi_dap_skill.handle(Manager(), "hỏi: doanh thu?"))
    assert played[0].mark == "warn" and "báo cáo FP&A tháng" in played[0].text


def test_the_robot_grades_the_answers_with_its_own_code(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import hoi_dap_robot as robot
    import tao_case_chi_phi
    from office import hoi_dap_skill
    d = tao_case_chi_phi.make(tmp_path / "case", forecast=True)
    env = robot.robot_env(tmp_path / "x.jsonl")
    assert env["CLICKY_LOCAL_ONLY"] == "1" and env["CLICKY_OUTLOOK"] == "0"
    sums = robot.expected(d)
    m = fpa_report.compute(d)
    assert (m.row("opex").actual, m.row("opex").ytd_actual) == (sums["opex_month"], sums["opex_ytd"])
    report = fpa_report.write(m)["xlsx"]
    forecast.write(forecast.compute(d))
    sha = robot.sha256(report)
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    result = hoi_dap_skill.run_file(d, robot.question_file(tmp_path / "q.txt"))
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    records = result["records"]
    pack = sorted(d.glob("Clicky_Forecast_T*/Clicky_Forecast_T*.xlsx"))[-1]
    chat = robot.chat_answers(d)
    res = robot.grade(records, report, pack, events, sums, chat, sha, True)
    assert res.ok, res.markdown("x")
    wrong = [dict(r) for r in records]
    wrong[0] = dict(wrong[0], so_lieu=[dict(wrong[0]["so_lieu"][0], gia_tri="1")] + wrong[0]["so_lieu"][1:])
    wrong[6] = dict(wrong[6], tra_loi=wrong[5]["tra_loi"] + " Thêm 7,5%.")
    wrong[8] = dict(wrong[8], trang_thai=robot.ANSWERED)
    bad = robot.grade(wrong[:-1], report, pack, events + [{"event": "hoi_dap", "q": "chi phí"}], sums, [], sha, False)
    assert {l.name for l in bad.lines if not l.ok} == {
        "Mỗi câu hỏi có một câu trả lời và trạng thái", "Mọi số trích đúng ô trong báo cáo đã lưu",
        "Không có số nào thiếu nguồn", "Q1: số đúng từng đồng", "Q7: nói rõ “chưa có số”, không đoán số",
        "Q9: nói rõ “không hiểu câu hỏi”, không đoán số", "Hỏi trong chat ra đúng câu trả lời như file",
        "File của case không bị sửa", "Nhật ký không ghi lời câu hỏi"}


def test_a_department_is_found_by_its_short_name_and_an_unknown_one_gets_no_total(book):
    costs = [dict(r, dept={"MKT": "Marketing", "KD": "Kinh doanh"}.get(r["dept"], r["dept"])) for r in book.costs]
    named = hoi_dap.Book(book.path, book.sha256, book.basis, book.pnl, costs, book.variances, period=book.period)
    for said, dept in (("Chi phí bộ phận MKT thế nào?", "Marketing"), ("phòng KD tháng này chi bao nhiêu?",
                                                                       "Kinh doanh")):
        a = hoi_dap.answer(named, said)
        assert (a.query.intent, a.query.target) == ("dept", dept) and a.text.startswith(dept), said
    a = hoi_dap.answer(book, "Chi phí phòng Pháp chế tháng này?")
    assert (a.status, a.cites) == (hoi_dap.NO_FIGURE, [])
    assert hoi_dap.answer(book, "phòng nào chi nhiều nhất?").query.intent == "top"
