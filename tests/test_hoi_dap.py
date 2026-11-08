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
    assert a.parts[0] == "Em hiểu là: lợi nhuận gộp, tháng 10/2026, so với kế hoạch AOP."
    assert a.text.startswith(a.parts[0] + " Lợi nhuận gộp tháng 10/2026: thực hiện ")
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
    assert (a.status, a.cites) == (hoi_dap.NO_FIGURE, []) and a.parts[-1] == hoi_dap.NO_FIGURE_TEXT
    assert a.parts[0].startswith("Em hiểu là: ") and not hoi_dap.NUM_RE.findall(a.text)


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
    assert played[0].text == "Em hiểu là: vì sao chi phí hoạt động tháng 10/2026 chênh so với kế hoạch AOP."
    assert played[1].text.startswith("Chi phí hoạt động tháng 10/2026") and played[1].mark == "ok"
    assert played[2].text == "Chi phí chênh nhiều nhất:" and "chứng từ" not in played[3].text
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
        "Q10: số đúng từng đồng", "Hỏi trong chat ra đúng câu trả lời như file",
        "File của case không bị sửa", "Nhật ký không ghi lời câu hỏi"}


def test_a_department_is_found_by_its_short_name_and_an_unknown_one_gets_no_total(book):
    costs = [dict(r, dept={"MKT": "Marketing", "KD": "Kinh doanh"}.get(r["dept"], r["dept"])) for r in book.costs]
    named = hoi_dap.Book(book.path, book.sha256, book.basis, book.pnl, costs, book.variances, period=book.period)
    for said, dept in (("Chi phí bộ phận MKT thế nào?", "Marketing"), ("phòng KD tháng này chi bao nhiêu?",
                                                                       "Kinh doanh")):
        a = hoi_dap.answer(named, said)
        assert (a.query.intent, a.query.target) == ("dept", dept) and a.parts[1].startswith(dept), said
    a = hoi_dap.answer(book, "Chi phí phòng Pháp chế tháng này?")
    assert (a.status, a.cites) == (hoi_dap.NO_FIGURE, [])
    assert hoi_dap.answer(book, "phòng nào chi nhiều nhất?").query.intent == "top"


DEPTS = {"KD": "Kinh doanh", "MKT": "Marketing", "HC": "Hành chính"}
ELEMENTS = {"QC": "Quảng cáo", "LUONG": "Lương", "CTP": "Công tác phí", "KH": "Khấu hao", "THUE": "Thuê văn phòng"}


@pytest.fixture(scope="module")
def named(book):
    """The report as a company with department and cost element names (not codes) prints it."""
    def label(text):
        d, _, e = text.partition(" · ")
        return f"{DEPTS.get(d, d)} · {ELEMENTS.get(e, e)}" if e else text
    costs = [dict(r, dept=DEPTS.get(r["dept"], r["dept"]), element=ELEMENTS.get(r["element"], r["element"]))
             for r in book.costs]
    variances = [dict(v, label=label(v["label"])) for v in book.variances]
    return hoi_dap.Book(book.path, book.sha256, book.basis, book.pnl, costs, variances, book.bridge, book.bridge_said,
                        book.open, period=book.period)


# (question as a BOD member would say it, the reading it must get); traps: 'quản lý', 'lý do', 'tỷ lệ', 'ra sao',
# 'chi tiết', 'năm nay' against 'cả năm', 'tháng trước' against 'cùng kỳ'
BATTERY = [
    ("Doanh thu tháng 10 đạt bao nhiêu?", dict(intent="line", line="revenue", time=None)),
    ("Lãi gộp tháng này so với AOP?", dict(intent="line", line="gp")),
    ("Giá vốn hàng bán tháng này thế nào?", dict(intent="line", line="cogs")),
    ("EBIT tháng này bao nhiêu?", dict(intent="line", line="op")),
    ("Lợi nhuận hoạt động lũy kế đến nay?", dict(intent="line", line="op", time="ytd")),
    ("Doanh thu năm nay đến giờ được bao nhiêu?", dict(intent="line", line="revenue", time="ytd")),
    ("Chi phí quản lý tháng này?", dict(intent="line", line="opex", sub="admin")),
    ("Chi phí bán hàng tháng 10 so kế hoạch?", dict(intent="line", line="opex", sub="selling")),
    ("Tỷ lệ lãi gộp tháng này?", dict(intent="line", line="gp", time=None)),
    ("Lãi gộp tháng 10 ra sao?", dict(intent="line", line="gp")),
    ("Cần chi tiết doanh thu tháng này", dict(intent="line", line="revenue")),
    ("Vì sao chi phí hoạt động vượt kế hoạch?", dict(intent="why", line="opex")),
    ("sao chi phí bán hàng vượt?", dict(intent="why", line="opex", sub="selling")),
    ("Tại sao lợi nhuận hoạt động thấp hơn AOP?", dict(intent="why", line="op")),
    ("Lý do lãi gộp giảm là gì?", dict(intent="why", line="gp")),
    ("Nguyên nhân giá vốn tăng?", dict(intent="why", line="cogs")),
    ("Giải thích giúp chênh lệch lợi nhuận lũy kế", dict(intent="why", line="op", time="ytd")),
    ("Chi phí quản lý vì sao cao?", dict(intent="why", line="opex", sub="admin")),
    ("Top 3 khoản chi phí chênh lớn nhất?", dict(intent="top", n=3)),
    ("Những khoản nào chênh nhiều nhất?", dict(intent="top")),
    ("5 khoản chi phí bất thường nhất tháng này", dict(intent="top", n=5)),
    ("Khoản nào đáng chú ý?", dict(intent="top")),
    ("Phòng nào chi nhiều nhất?", dict(intent="top")),
    ("Hai khoản chênh lớn nhất là gì", dict(intent="top", n=2)),
    ("Chi phí bộ phận MKT thế nào?", dict(intent="dept", target="Marketing")),
    ("Marketing tháng này chi bao nhiêu?", dict(intent="dept", target="Marketing")),
    ("phòng KD lũy kế chi bao nhiêu?", dict(intent="dept", target="Kinh doanh", time="ytd")),
    ("Khối hành chính tháng 10?", dict(intent="dept", target="Hành chính")),
    ("Bộ phận Kinh doanh vượt ở đâu?", dict(intent="dept", target="Kinh doanh")),
    ("Cho chị chi tiết chi phí MKT", dict(intent="dept", target="Marketing")),
    ("Chi phí quảng cáo tháng này?", dict(intent="element", target="Quảng cáo")),
    ("Lương tháng 10 so kế hoạch?", dict(intent="element", target="Lương")),
    ("Công tác phí lũy kế?", dict(intent="element", target="Công tác phí", time="ytd")),
    ("Khấu hao tháng này bao nhiêu", dict(intent="element", target="Khấu hao")),
    ("Tiền thuê văn phòng có vượt không?", dict(intent="element", target="Thuê văn phòng")),
    ("Chi phí lương các phòng thế nào?", dict(intent="element", target="Lương")),
    ("Cầu nối lợi nhuận gộp?", dict(intent="bridge", part=None)),
    ("Ảnh hưởng của giá bán bao nhiêu?", dict(intent="bridge", part="price")),
    ("Giá vốn tăng do giá hay do lượng?", dict(intent="bridge")),
    ("Sản lượng tác động thế nào đến lãi gộp?", dict(intent="bridge", part="volume")),
    ("Cơ cấu sản phẩm ảnh hưởng ra sao?", dict(intent="bridge", part="mix")),
    ("Lãi gộp thay đổi do giá hay do lượng", dict(intent="bridge")),
    ("Có khoản một lần nào không?", dict(intent="one_off")),
    ("Chi phí đột xuất tháng này?", dict(intent="one_off")),
    ("Khoản phát sinh một lần là gì?", dict(intent="one_off")),
    ("Có one-off nào không", dict(intent="one_off")),
    ("Những khoản chi đột xuất", dict(intent="one_off")),
    ("Khoản nào chỉ xảy ra một lần?", dict(intent="one_off")),
    ("Có khoản nào lệch thời điểm không?", dict(intent="timing")),
    ("Chi phí nào chi trước?", dict(intent="timing")),
    ("Khoản nào chi bù tháng sau?", dict(intent="timing")),
    ("Có timing difference nào?", dict(intent="timing")),
    ("Có khoản nào lệch tháng không", dict(intent="timing")),
    ("Có khoản nào ghi sớm hơn không", dict(intent="timing")),
    ("Ước cả năm lợi nhuận?", dict(intent="forecast", line="op", time="full")),
    ("EBIT cả năm ước bao nhiêu", dict(intent="forecast", line="op", time="full")),
    ("Dự báo doanh thu cả năm?", dict(intent="forecast", line="revenue")),
    ("Full year chi phí hoạt động?", dict(intent="forecast", line="opex")),
    ("LE lãi gộp thế nào?", dict(intent="forecast", line="gp")),
    ("Doanh thu dự báo năm nay?", dict(intent="forecast", line="revenue", time="full")),
    ("Cuối năm lợi nhuận hoạt động ước bao nhiêu", dict(intent="forecast", line="op")),
    ("Còn điểm nào chưa rõ?", dict(intent="open")),
    ("Còn gì cần chị xác nhận không?", dict(intent="open")),
    ("Có vấn đề mở nào không?", dict(intent="open")),
    ("Điểm nào còn tồn đọng?", dict(intent="open")),
    ("Còn gì chưa chốt?", dict(intent="open")),
    ("Những gì đang chờ chị trả lời?", dict(intent="open")),
    ("So với bản kế hoạch nào?", dict(intent="basis")),
    ("Kế hoạch nào được dùng?", dict(intent="basis")),
    ("AOP nào?", dict(intent="basis")),
    ("Phiên bản kế hoạch?", dict(intent="basis")),
    ("So với gì vậy?", dict(intent="basis")),
    ("Căn cứ nào để so?", dict(intent="basis")),
    ("Doanh thu cùng kỳ năm trước?", dict(intent="ly", line="revenue")),
    ("Lãi gộp so với năm ngoái?", dict(intent="ly", line="gp")),
    ("Chi phí tăng bao nhiêu so với LY?", dict(intent="ly", line="opex")),
    ("Doanh thu YoY?", dict(intent="ly", line="revenue")),
    ("Lợi nhuận cùng kỳ", dict(intent="ly", line="op")),
    ("So với năm trước thì sao", dict(intent="ly")),
    ("Doanh thu so với tháng trước?", dict(intent="prev", line="revenue")),
    ("Chi phí tháng trước bao nhiêu?", dict(intent="prev", line="opex")),
    ("Lãi gộp tăng so với tháng rồi?", dict(intent="prev", line="gp")),
    ("MoM doanh thu?", dict(intent="prev", line="revenue")),
    ("So với kỳ trước thì sao?", dict(intent="prev")),
    ("Chi phí hoạt động so với tháng 9?", dict(intent="prev", line="opex")),
    ("Mảng nào kéo lãi gộp xuống?", dict(intent="sku", line="gp")),
    ("Doanh thu theo kênh?", dict(intent="sku", line="revenue")),
    ("Sản phẩm nào bán chạy?", dict(intent="sku")),
    ("Khu vực nào tăng trưởng?", dict(intent="sku")),
    ("Mã hàng nào lỗ?", dict(intent="sku")),
    ("Ngành hàng nào giảm?", dict(intent="sku")),
    ("Lợi nhuận trước thuế tháng này?", dict(intent="absent", absent="lợi nhuận trước thuế")),
    ("LNST lũy kế?", dict(intent="absent", absent="lợi nhuận sau thuế")),
    ("EBITDA cả năm?", dict(intent="absent", absent="EBITDA")),
    ("Dòng tiền tháng này thế nào?", dict(intent="absent", absent="dòng tiền")),
    ("Công nợ phải thu bao nhiêu?", dict(intent="absent", absent="công nợ")),
    ("Tồn kho cuối tháng?", dict(intent="absent", absent="tồn kho")),
    ("Thời tiết hôm nay?", dict(intent=None)),
    ("Cảm ơn em", dict(intent=None)),
    ("Mấy giờ họp?", dict(intent=None)),
]


def _plain(text: str) -> str:
    """The question typed without diacritics, as people often do in chat."""
    import unicodedata
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


@pytest.mark.parametrize("plain", [False, True], ids=["co-dau", "khong-dau"])
@pytest.mark.parametrize("question,want", BATTERY, ids=[q for q, _w in BATTERY])
def test_the_paraphrase_battery_reads_each_wording_as_meant(named, question, want, plain):
    q = hoi_dap.parse(_plain(question) if plain else question, named)
    assert {k: getattr(q, k) for k in want} == want, q


def test_each_intent_has_at_least_six_wordings():
    from collections import Counter
    counts = Counter(w["intent"] for _q, w in BATTERY)
    assert all(n >= 6 for i, n in counts.items() if i is not None), counts


def test_every_answer_opens_with_how_it_read_the_question(named):
    for question, want in BATTERY:
        a = hoi_dap.answer(named, question)
        if want["intent"] is None:
            assert a.status == hoi_dap.NOT_UNDERSTOOD
            continue
        assert a.parts[0].startswith("Em hiểu là: ") and a.text.startswith(a.parts[0]), question
        if a.status == hoi_dap.NO_FIGURE:
            assert not hoi_dap.NUM_RE.findall(a.text), question
    a = hoi_dap.answer(named, "Chi phí bán hàng tháng 10 so kế hoạch?")
    assert a.parts[0] == "Em hiểu là: chi phí bán hàng, tháng 10/2026, so với kế hoạch AOP."
    assert a.parts[1].startswith("Báo cáo chưa tách riêng chi phí bán hàng")
    a = hoi_dap.answer(named, "Lợi nhuận trước thuế tháng này?")
    assert (a.status, a.parts[0]) == (hoi_dap.NO_FIGURE, "Em hiểu là: lợi nhuận trước thuế.")


def test_a_follow_up_with_a_new_department_keeps_the_line_and_time(named):
    first = hoi_dap.answer(named, "Chi phí hoạt động lũy kế?")
    a = hoi_dap.answer(named, "còn Marketing?", first.query)
    assert (a.query.intent, a.query.target, a.query.time) == ("dept", "Marketing", "ytd")
    assert a.cites[0]["o"].startswith("'Chi phi theo bo phan'!I")                 # the year-to-date column
    b = hoi_dap.answer(named, "còn Kinh doanh?", hoi_dap.answer(named, "Chi phí bộ phận MKT tháng này?").query)
    assert (b.query.intent, b.query.target, b.query.time) == ("dept", "Kinh doanh", None)
    c = hoi_dap.answer(named, "còn doanh thu?", first.query)
    assert (c.query.intent, c.query.line, c.query.time) == ("line", "revenue", "ytd")
