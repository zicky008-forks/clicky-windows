"""The talk as a story (present/story.py): ask first, one driver, figures by code, a pre-flight for the owner."""
import io
import json
import re
import shutil
import wave
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from present import clicky_html, goi_html, kiem_bien_ban, story

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"
BUILD = clicky_html.build
OWNER = {"owner": "anh Tuấn", "contact": "tuan@congty.vn"}


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path / "bao_cao.html"
    shutil.copyfile(FIXTURE, dst)
    return dst


def voice(text):
    return text.encode("utf-8")


def build(report, **k):
    return BUILD(str(report), voice=voice, **{**OWNER, **k})


def lines(rep):
    script = yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))
    return [c for s in script["canh"] for c in s["cau"]]


def test_the_story_asks_first_then_tells_one_driver_and_never_reads_the_page_in_order(report):
    rep = build(report)
    said = lines(rep)
    first = said[0]["noi"]
    # the AI says it is an AI, and the decision comes in the first sentence, in the owner's own words
    assert first.startswith("Thưa anh chị, em là trợ lý AI của anh Tuấn.")
    assert "quyết hai việc" in first and "giữ tỷ lệ chi phí nhân sự/DTT ở 2,40%" in first and "giãn tuyển 30 vị trí" in first
    text = " ".join(c["noi"] for c in said)
    assert "Tiếp theo là phần" not in text and "Thứ nhất" not in text
    # a question to the room, then a silence before the answer
    q = next(c for c in said if c["noi"].endswith("?") and c["noi"] != "Vì sao?")
    assert q.get("dung") == 1.5
    # one driver; the rest computed by code with its formula and checked by the number lock
    rest = next(c for c in said if c.get("so_tinh"))
    assert "khoảng 8%" in rest["noi"] and rest["so_tinh"] == ["8 = (20,9 - 14,1) / (165,5 - 78,2) × 100"]
    assert rep["record"]["em_tinh"] == 1
    # degree words come from computed ratios: 12,6 / 7,2 = 1,75 is "gần gấp đôi"
    assert any("nhanh gần gấp đôi doanh thu" in c["noi"] for c in said)
    # it ends on the decision and hands the room the floor
    assert "Quyết định là của anh chị." in said[-2]["noi"] and "bấm vào con số" in said[-1]["noi"]


def test_a_computed_figure_is_rechecked_from_the_output_file(report):
    rep = build(report)
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["em_tinh"] == 1
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    chips = [e for s in data["scenes"] for ev in s["ev"] for e in ev if e[1] == "t"]
    assert chips == [["8", "t", "(20,9 - 14,1) / (165,5 - 78,2) × 100"]]


def test_the_owner_pre_flight_is_in_the_result_and_never_on_the_page(report):
    rep = build(report)
    flags = rep["phan_bien"]
    # "Hai phần ba … tuyển mới": headcount × rate says the new people are only about 43% of the increase
    claim = next(f for f in flags if "Hai phần ba" in f)
    assert "khoảng 43%" in claim and "đơn giá" in claim
    # the ask to delay 30 hires has no savings figure: the talk says so plainly instead of making one up
    gap = next(f for f in flags if "30 vị trí" in f)
    assert "3,9 tỷ" in gap and "8,9 tỷ" in gap
    assert any(c["noi"].startswith("Em xin nói thẳng: báo cáo chưa ghi giãn 30 vị trí") for c in lines(rep))
    page = Path(rep["out"]).read_text(encoding="utf-8")
    assert "Hai phần ba" not in page.split('id="clicky-data"')[1] and "43%" not in page
    assert any(t.startswith("Trước khi gửi, anh soát:") for t in clicky_html.todo_list(rep))


def test_a_kpi_that_holds_within_rounding_is_not_flagged_but_a_wrong_one_is(report):
    # 149,4 triệu, +6,7%: recomputed from the rounded totals it is 6,74…6,96, so 6,7 holds; 9,5 does not
    assert not any("Chi phí bình quân" in f for f in build(report)["phan_bien"])
    report.write_text(report.read_text(encoding="utf-8").replace("+6,7% so với AOP gốc", "+9,5% so với AOP gốc"),
                      encoding="utf-8")
    for f in report.parent.glob("bao_cao_kich_ban*"):
        f.unlink()
    flag = next(f for f in build(report)["phan_bien"] if "Chi phí bình quân" in f)
    assert "9,5%" in flag and "6,85%" in flag


def test_a_figure_said_as_exact_must_survive_the_rounding_of_its_inputs(report):
    on_page = {Decimal(x) for x in ("20.9", "14.1", "165.5", "78.2")}
    expr = "(20,9 - 14,1) / (165,5 - 78,2) × 100"           # 7,789…: 7,67 to 7,91 within the inputs' rounding
    assert story.check_computed("8", expr, "chỉ tăng khoảng ", False, on_page) is None
    assert "khoảng" in story.check_computed("7,8", expr, "chỉ tăng ", False, on_page)
    assert story.check_computed("9", expr, "chỉ tăng ", False, on_page) == "công thức ra 8"
    assert story.check_computed("10", expr, "khoảng ", False, on_page) == "công thức ra 8"
    assert "không có trong báo cáo" in story.check_computed("8", "(20,9 - 13,1) / 87,3 × 100", "khoảng ", False,
                                                             on_page | {Decimal("87.3")})


def test_the_formula_reader_keeps_precedence_and_refuses_what_it_cannot_read():
    assert story.evaluate("2 + 3 × 4") == 14 and story.evaluate("(2 + 3) × 4") == 20
    assert story.evaluate("1.248 - 64") == 1184 and story.evaluate("1,5 / 3") == Decimal("0.5")
    assert story.evaluate("1,248 - 64", en=True) == 1184
    for bad in ("1 / (2 - 2)", "(1 + 2", "1 + ", "1 + abc"):
        with pytest.raises(ValueError):
            story.evaluate(bad)
    # one figure used twice is one quantity: it moves the same way in both places
    lo, hi = story.spread("(186,4 / 1.248) / (165,5 / (1.248 - 64)) × 100 - 100", False)
    assert Decimal("6.74") < lo < Decimal("6.75") and Decimal("6.96") < hi < Decimal("6.97")


def test_an_owner_who_changes_a_computed_figure_or_its_formula_is_stopped(report):
    rep = build(report)
    path = Path(rep["kich_ban"])
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("khoảng 8%", "khoảng 12%").replace("'8 = (20,9", "'12 = (20,9"), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="là số em tính, nhưng công thức ra 8"):
        build(report)
    path.write_text(text.replace("(20,9 - 14,1)", "(20,9 - 13,1)"), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="13,1 trong công thức không có trong báo cáo"):
        build(report)
    path.write_text(text.replace("'8 = (20,9", "'(20,9"), encoding="utf-8")
    with pytest.raises(goi_html.PackError, match="số = công thức"):
        build(report)


def test_a_question_to_the_room_gets_a_silence_before_the_answer():
    def wav_voice(text):                   # 0.05 s per character
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
            w.writeframes(b"\0\0" * int(400 * len(text)))
        return out.getvalue()

    _wav, plain = clicky_html.voice_lines(["Vì sao?", "Vì chi phí."], wav_voice)
    _wav, paused = clicky_html.voice_lines(["Vì sao?", "Vì chi phí."], wav_voice, pauses=[1.5, 0])
    assert paused[1] - plain[1] == pytest.approx(1.5, abs=0.01)
    assert clicky_html._pause("1,5") == 1.5 and clicky_html._pause(9) == 4.0 and clicky_html._pause("x") == 0.0


def test_a_report_without_a_plan_column_keeps_the_page_order_draft(report):
    # two periods side by side (a balance sheet) are not a plan and an actual: no story about a plan
    report.write_text(report.read_text(encoding="utf-8").replace("<th>AOP gốc</th>", "<th>Năm trước</th>"),
                      encoding="utf-8")
    said = " ".join(c["noi"] for c in lines(build(report)))
    assert "kế hoạch gốc" not in said and "khoảng 8%" not in said


def test_a_draft_dropped_again_keeps_its_formula_and_pauses(report):
    rep = build(report)
    first = Path(rep["kich_ban"]).read_text(encoding="utf-8")
    again = build(report)
    assert Path(again["kich_ban"]).read_text(encoding="utf-8") == first
    script = clicky_html.load_script(again["kich_ban"])
    assert any(c.get("so_tinh") for s in script["canh"] for c in s["cau"])
    assert clicky_html.unedited(script)


def test_next_months_report_works_the_computed_figure_out_again(report):
    rep = build(report)
    path = Path(rep["kich_ban"])
    path.write_text(path.read_text(encoding="utf-8").replace("da_duyet: 'chưa'", "da_duyet: 'có'"), encoding="utf-8")
    assert build(report)["trang_thai"].startswith("CHÍNH THỨC")
    src = report.read_text(encoding="utf-8")
    for a, b in (("<td>92,3</td><td>14,1</td><td>18,0%</td>", "<td>96,7</td><td>18,5</td><td>23,7%</td>"),
                 ("<td>186,4</td><td>20,9</td><td>12,6%</td>", "<td>190,8</td><td>25,3</td><td>15,3%</td>")):
        assert a in src
        src = src.replace(a, b)
    report.write_text(src, encoding="utf-8")
    rep = build(report)
    assert rep["trang_thai"].startswith("SƠ BỘ: báo cáo kỳ mới")
    rest = next(c for c in lines(rep) if c.get("so_tinh"))
    # (25,3 - 18,5) / (165,5 - 78,2) × 100 = 7,79: still "khoảng 8%", from this period's figures
    assert rest["so_tinh"] == ["8 = (25,3 - 18,5) / (165,5 - 78,2) × 100"]
    assert kiem_bien_ban.verify(rep["out"], str(report))["ok"]


DRAWN = Path(__file__).parent / "fixtures" / "bao_cao_html_tu_ve.html"


@pytest.fixture
def drawn_report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path / "tu_ve.html"
    shutil.copyfile(DRAWN, dst)
    return dst


def test_a_report_whose_script_draws_its_table_is_told_from_the_page_as_drawn(drawn_report):
    pytest.importorskip("playwright")
    rep = build(drawn_report)
    said = lines(rep)
    # the decisions come from the part headed "BOD cần quyết định": the bold title of each item, the first one first
    assert said[0]["noi"].endswith("hôm nay có bốn việc cần anh chị quyết, việc đầu tiên là: duyệt trần chi phí vận "
                                   "hành 120,0 tỷ.")
    assert [c["noi"] for c in said if c["noi"].startswith(("Một là", "Hai là", "Ba là", "Bốn là"))] == [
        "Một là, duyệt trần chi phí vận hành 120,0 tỷ.", "Hai là, logistics phải giải trình trước khi duyệt.",
        "Ba là, giữ quỹ lương bằng AOP gốc.", "Bốn là, hoãn dự án kho mới."]
    # the table has no variance column: code works it out from the drawn rows; a group row ("Khối vận hành" adds up
    # Logistics and Mua hàng) is never the driver; the unit comes from the table's heading "(tỷ đồng)"
    driver = next(c for c in said if "nằm ở một chỗ" in c["noi"])
    assert driver["noi"] == "Gần hai phần ba nằm ở một chỗ: Logistics, tăng 12,0 tỷ." and driver["so_tinh"] == ["12,0 = 52,0 - 40,0"]
    assert driver["chi"].startswith("v")
    rest = next(c for c in said if c["noi"].startswith("Bỏ Logistics ra"))
    assert "chỉ tăng khoảng 10%" in rest["noi"]
    # 18,0 / 8,0 = 2,25: "nhanh hơn gấp đôi", and the owner's own "Nếu …" sentence says what happens if nothing changes
    assert any(c["noi"] == "Tức là chi phí đang chạy nhanh hơn gấp đôi doanh thu." for c in said)
    assert any(c["noi"].startswith("Nếu doanh thu chỉ đạt 90% kế hoạch") for c in said)
    # the page finds the drawn rows again by their words; the record re-check opens the page and finds them too
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert driver["chi"] in data["ve"] and data["ve"][driver["chi"]]["tag"] == "TR"
    res = kiem_bien_ban.verify(rep["out"], str(drawn_report))
    assert res["ok"], res["errors"]
    assert res["tong"]["em_tinh"] == 2


def test_the_screen_check_finds_drawn_places_and_keeps_a_tall_item_clear_of_a_sticky_header(drawn_report):
    pytest.importorskip("playwright")
    from present import kiem_hinh
    rep = build(drawn_report)
    res = kiem_hinh.check(rep["out"])
    if res.get("skipped"):
        pytest.skip("no browser")
    assert res["ok"], res["problems"]


def test_words_of_degree_and_names_inside_a_sentence():
    assert story.share_words(Decimal("0.63")) == "gần hai phần ba" and story.share_words(Decimal("0.56")) == "hơn một nửa"
    assert story.share_words(Decimal("0.40")) is None
    assert story.times_words(Decimal("2.25")) == "nhanh hơn gấp đôi" and story.times_words(Decimal("1.75")) == "nhanh gần gấp đôi"
    assert story.times_words(Decimal("1.1")) is None
    assert story._short("Khối Bán hàng") == "khối Bán hàng" and story._short("Logistics") == "Logistics"
    assert story._lc("CNTT: phân bổ license") == "CNTT: phân bổ license" and story._lc("Giữ quỹ lương") == "giữ quỹ lương"
