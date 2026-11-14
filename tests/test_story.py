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

from present import clicky_html, goi_html, kiem_bien_ban, kiem_hinh, story

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
    return BUILD(str(report), **{"voice": voice, **OWNER, **k})


def lines(rep):
    script = yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))
    return [c for s in script["canh"] for c in s["cau"]]


def test_the_story_asks_first_then_tells_one_driver_and_never_reads_the_page_in_order(report):
    rep = build(report)
    said = lines(rep)
    # the AI says it is an AI, then the decisions come first, one short sentence each, in the owner's own words
    assert [c["noi"] for c in said[:4]] == [
        "Thưa anh chị, em là trợ lý AI của anh Tuấn.", "Anh Tuấn xin anh chị quyết hai việc.",
        "Một là giữ tỷ lệ chi phí nhân sự trên doanh thu thuần ở 2,40%, tức trần 177,5 tỷ.",
        "Hai là giãn tuyển 30 vị trí bán hàng sang quý 3 và gắn với doanh thu trên mỗi người."]
    text = " ".join(c["noi"] for c in said)
    assert "Tiếp theo là phần" not in text and "Thứ nhất" not in text
    # a question to the room, then a silence before the answer
    qs = [c for c in said if c["noi"].endswith("?")]
    assert [(c["noi"], c.get("dung")) for c in qs] == [("Vì sao cần quyết?", 0.8), ("Vậy phần tăng thêm nằm ở đâu?", 1.5)]
    # said as it is, never spun: 7,8% is a little faster than revenue's 7,2%, not "sát nhịp"; one plain name per plan
    assert "Bỏ khối Bán hàng ra, các khối còn lại vẫn tăng khoảng 8%, nhanh hơn doanh thu 7,2% một chút." in text
    assert "Theo kế hoạch điều chỉnh, tổng là 186,4 tỷ, cao hơn kế hoạch gốc 20,9 tỷ." in text
    assert not re.search(r"AOPR|AOP gốc|bản điều chỉnh|/DTT|sát nhịp|^Một,", text)
    # one driver; the rest computed by code with its formula and checked by the number lock
    rest = next(c for c in said if "khoảng 8%" in c["noi"])
    assert rest["so_tinh"] == ["8 = (20,9 - 14,1) / (165,5 - 78,2) × 100"]
    # the page's "hai phần ba" is a doubted sentence, so the driver says the figure instead of echoing its words
    driver = next(c for c in said if c.get("vai") == "dong_luc")
    assert driver["noi"] == "Khối Bán hàng chiếm khoảng 67% phần tăng: 14,1 tỷ." and "hai phần ba" not in text.lower()
    assert driver["so_tinh"] == ["67 = 14,1 / 20,9 × 100"]
    assert rep["record"]["em_tinh"] == 5
    # round 10: the talk names what is still missing after the cut, with its working, instead of leaving it to a question
    rest_line = next(c for c in said if c.get("vai") == "con_thieu")
    assert rest_line["noi"] == "Còn lại khoảng 5,0 tỷ, báo cáo chưa nêu cách bù." and rest_line["so_tinh"] == ["5,0 = 8,9 - 30 × 92,3 / 712"]
    # how much faster is said as a figure the Board can check: 12,6 / 7,2 = 1,75, said "khoảng 1,8"
    fast = next(c for c in said if "nhanh gấp khoảng 1,8 lần doanh thu" in c["noi"])
    assert fast["so_tinh"] == ["1,8 = 12,6 / 7,2"]
    # it ends on the decision and hands the room the floor
    # it closes on every ask again as a short tag with its money, never word for word, then invites the room
    assert [c["noi"] for c in said[-2:]] == [
        "Tóm lại, anh Tuấn xin anh chị quyết: một là giữ trần 177,5 tỷ; hai là giãn tuyển 30 vị trí bán hàng sang quý 3.",
        "Em kính mời anh chị cho ý kiến ạ."]
    # every line says which beat it is, so a question after the talk finds its answer
    assert all(c.get("vai") for c in said)


def test_a_computed_figure_is_rechecked_from_the_output_file(report):
    rep = build(report)
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["em_tinh"] == 5
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    chips = [e for s in data["scenes"] for ev in s["ev"] for e in ev if e[1] == "t"]
    assert chips == [["1,8", "t", "12,6 / 7,2"], ["67", "t", "14,1 / 20,9 × 100"],
                     ["8", "t", "(20,9 - 14,1) / (165,5 - 78,2) × 100"], ["3,9", "t", "30 × 92,3 / 712"],
                     ["5,0", "t", "8,9 - 30 × 92,3 / 712"]]


def test_the_owner_pre_flight_is_in_the_result_and_never_on_the_page(report):
    rep = build(report)
    flags = rep["phan_bien"]
    # "Hai phần ba … tuyển mới": headcount × rate says the new people are only about 43% of the increase
    claim = next(f for f in flags if "Hai phần ba" in f)
    assert "khoảng 43%" in claim and "đơn giá" in claim
    # the ask to delay 30 hires has no savings figure: the talk says so plainly instead of making one up
    gap = next(f for f in flags if "30 vị trí" in f)
    assert "3,9 tỷ" in gap and "8,9 tỷ" in gap
    # the gap carries no figure, so it is said by default (round-5 Chair, three rounds running); the owner can switch
    # it off; the bound is worked out by code and, by the owner's decision (11-14), said with its formula
    gapline = next(c for c in lines(rep) if c["noi"].startswith("Có một điểm báo cáo chưa có số: giãn 30 vị trí"))
    assert "bat" not in gapline
    bound = next(c for c in lines(rep) if c.get("vai") == "uoc_tiet_kiem")
    assert "khoảng 3,9 tỷ" in bound["noi"] and "4 tỷ" not in bound["noi"]   # 30 × 92,3 / 712 = 3,89, never rounded up to 4
    assert bound["noi"] == ("Em tính theo chi phí bình quân một người ở khối Bán hàng: kể cả giãn trọn năm, 30 vị trí "
                            "cũng chỉ tiết kiệm khoảng 3,9 tỷ, chưa bằng một nửa mức cần giảm.")   # 8,9 said twice at most (round 12)
    assert bound["so_tinh"] == ["3,9 = 30 × 92,3 / 712"] and bound["bat"] == "có" and bound["xac_nhan"] == ["8,9"]
    assert "anh không muốn thì ghi bat: 'chưa'" in gap
    page = Path(rep["out"]).read_text(encoding="utf-8")
    assert "Hai phần ba" not in page.split('id="clicky-data"')[1] and "43%" not in page
    assert any(t.startswith("Trước khi gửi, anh soát:") for t in clicky_html.todo_list(rep))


def test_a_kpi_that_holds_within_rounding_is_not_flagged_but_a_wrong_one_is(report):
    # 149,4 triệu, +6,7%: the headcounts are exact, so from the rounded totals it is 6,79…6,91 and 6,7 is flagged
    # (round-4 panel, CFO); 9,5 is flagged too
    flag = next(f for f in build(report)["phan_bien"] if "Chi phí bình quân" in f)
    assert "6,7%" in flag and "6,85%" in flag and "quyet" in flag
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
    # a headcount is a count: only the money moves, so the card's 6,7% is out of (6,79; 6,91) and is flagged
    lo, hi = story.spread("(186,4 / 1.248) / (165,5 / (1.248 - 64)) × 100 - 100", False, exact=("1.248", "64"))
    assert Decimal("6.78") < lo < Decimal("6.80") and Decimal("6.90") < hi < Decimal("6.92")


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
    # approved without looking at the pre-flight checks: still a draft, and the checks are named
    held = build(report)
    assert held["trang_thai"] == "SƠ BỘ: còn mục cần soát" and "da_xem: 'có'" in held["canh_bao"][0]
    path.write_text(path.read_text(encoding="utf-8").replace("da_xem: 'chưa'", "da_xem: 'có'"), encoding="utf-8")
    # looked at is not decided: an item with proposed lines or a doubted sentence needs a line on or a reason
    held = build(report)
    assert held["trang_thai"] == "SƠ BỘ: còn mục cần soát" and "ly_do_bo" in held["canh_bao"][0]
    path.write_text(path.read_text(encoding="utf-8").replace("ly_do_bo: ''", "ly_do_bo: 'Đã xem, giữ nguyên'"),
                    encoding="utf-8")
    assert build(report)["trang_thai"].startswith("CHÍNH THỨC")
    src = report.read_text(encoding="utf-8")
    for a, b in (("<td>92,3</td><td>14,1</td><td>18,0%</td>", "<td>96,7</td><td>18,5</td><td>23,7%</td>"),
                 ("<td>186,4</td><td>20,9</td><td>12,6%</td>", "<td>190,8</td><td>25,3</td><td>15,3%</td>")):
        assert a in src
        src = src.replace(a, b)
    report.write_text(src, encoding="utf-8")
    rep = build(report)
    assert rep["trang_thai"].startswith("SƠ BỘ: báo cáo kỳ mới")
    rest = next(c for c in lines(rep) if "khoảng 8%" in c["noi"])
    # (25,3 - 18,5) / (165,5 - 78,2) × 100 = 7,79: still "khoảng 8%", from this period's figures
    assert rest["so_tinh"] == ["8 = (25,3 - 18,5) / (165,5 - 78,2) × 100"]
    # a word of degree is worked out again too: 18,5 / 25,3 = 73% is "phần lớn", no longer "hai phần ba"; the line
    # that shares its place with "Bỏ khối Bán hàng ra" is matched by its beat and both survive
    said = [c["noi"] for c in lines(rep)]
    assert "Khối Bán hàng chiếm phần lớn phần tăng: 18,5 tỷ." in said and not any("hai phần ba" in x for x in said)
    assert sum(x.startswith("Bỏ khối Bán hàng ra") for x in said) == 1
    assert any("đổi mức độ" in n and "phần lớn" in n for n in rep["canh_bao"])
    # the stake line ("… tức vượt trần 8,9 tỷ") keeps its lock on the tile that still prints 8,9: month 2 builds
    stake = [c for c in lines(rep) if "vượt trần" in c["noi"]]
    # round 10: the opening says the stakes too, locked the same way
    assert [c["vai"] for c in stake] == ["mo_rui_ro"] and all(c["xac_nhan"] == ["8,9"] for c in stake)   # round 12: once up front
    assert "8,9 tỷ" in report.read_text(encoding="utf-8")
    assert kiem_bien_ban.verify(rep["out"], str(report))["ok"]


DRAWN = Path(__file__).parent / "fixtures" / "bao_cao_html_tu_ve.html"


@pytest.fixture
def drawn_report(tmp_path, monkeypatch):
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path / "tu_ve.html"
    shutil.copyfile(DRAWN, dst)
    return dst


def test_a_cap_said_to_keep_the_plans_ratio_is_checked_against_that_ratio(drawn_report):
    # round-4 panel (Chair, CFO, Owner): "trần 120,0 tỷ, bằng tỷ lệ chi phí trên doanh thu của AOP gốc", but
    # 110,0 × (1 + 8,0 ÷ 100) = 118,8; the owner is told first what to do, then why
    pytest.importorskip("playwright")
    flag = next(f for f in build(drawn_report)["phan_bien"] if "trần" in f and "118,8" in f)
    assert flag.startswith("Anh sửa trần hoặc ghi quyet") and "110,0 × (1 + 8,0 ÷ 100) = 118,8 tỷ" in flag


def test_a_report_whose_script_draws_its_table_is_told_from_the_page_as_drawn(drawn_report):
    pytest.importorskip("playwright")
    rep = build(drawn_report)
    said = lines(rep)
    # the decisions come from the part headed "BOD cần quyết định": the bold title of each item, the first one first
    # four asks are all said up front, each keeping its object ("… trước khi duyệt phần tăng thuê kho"), and again
    # at the close
    asks = ["Một là duyệt trần chi phí vận hành 120,0 tỷ cho cả năm.",
            "Hai là Logistics phải giải trình trước khi duyệt phần tăng thuê kho.",
            "Ba là giữ quỹ lương bằng kế hoạch gốc cho cả năm.", "Bốn là hoãn dự án kho mới sang năm sau."]
    assert said[1]["noi"] == "Anh Tuấn xin anh chị quyết bốn việc." and [c["noi"] for c in said[2:6]] == asks
    close = [c["noi"] for c in said if c.get("vai") == "chot"]
    # four asks: the close says a few words each, never the opening again word for word (round-6 Coach)
    assert close == ["Tóm lại, anh Tuấn xin anh chị quyết: một là duyệt trần 120,0 tỷ; hai là Logistics giải trình; "
                     "ba là giữ quỹ lương; bốn là hoãn dự án kho mới."]
    # the table has no variance column: code works it out from the drawn rows; a group row ("Khối vận hành" adds up
    # Logistics and Mua hàng) is never the driver; the unit comes from the table's heading "(tỷ đồng)"
    driver = next(c for c in said if c.get("vai") == "dong_luc")
    assert driver["noi"] == "Logistics chiếm gần hai phần ba phần tăng: 12,0 tỷ; giải trình ghi: thuê kho." and driver["so_tinh"] == ["12,0 = 52,0 - 40,0", "63 = (52,0 - 40,0) / (129,0 - 110,0) × 100"]   # the share behind "gần hai phần ba"
    assert driver["chi"].startswith("v")
    rest = next(c for c in said if c["noi"].startswith("Bỏ Logistics ra"))
    # 10% is still faster than revenue's 8,0%: never "chỉ"
    assert rest["noi"].endswith("vẫn tăng khoảng 10%, nhanh hơn doanh thu.") and "chỉ tăng" not in rest["noi"]
    # the cap is only inside the first ask: the cut it means is worked out, proposed, and put to the owner first
    cut = next(c for c in said if c["noi"].startswith("Trần 120,0 tỷ nghĩa là"))
    assert cut["noi"] == "Trần 120,0 tỷ nghĩa là phải giảm 9,0 tỷ so với kế hoạch điều chỉnh." and cut["bat"] == "chưa"
    assert cut["so_tinh"] == ["9,0 = 129,0 - 120,0"]
    assert any("9,0" in f and "129,0 - 120,0" in f for f in rep["phan_bien"])
    # 18,0 / 8,0 = 2,25, said "khoảng 2,3 lần"; the owner's own "Nếu …" sentence says what happens if nothing changes
    # ... but 18,0% disagrees with the table (17,3%): until the owner rules, the talk says only what holds under both,
    # frames the total, and keeps the printed rate and the 2,3 built on it as his to switch on
    why = next(k for k, c in enumerate(said) if c["noi"] == "Vì sao cần quyết?")   # the question alone, a breath
    assert said[why]["vai"] == "cau_hoi" and said[why]["dung"] == 0.8
    assert any(c["noi"] == "Vì doanh thu chỉ tăng 8,0%, chưa bằng một nửa tốc độ tăng của chi phí."   # holds under 17,3 and 18,0; 19,0 said once
               and c["xac_nhan"] == ["8,0"] and c["chi"] == next(x for x in said if x.get("vai") == "quy_mo")["chi"]
               for c in said)
    ratio = next(c for c in said if c["noi"] == "Tức là chi phí tăng nhanh gấp khoảng 2,3 lần doanh thu.")
    assert ratio["bat"] == "chưa"
    assert not any("18,0" in c["noi"] for c in said if c.get("bat") != "chưa")
    item = next(k for k in clicky_html.load_script(rep["kich_ban"])["kiem_tra"] if k.get("ma_cau"))
    assert item["cau"] == ["Chi phí tăng 18,0%, trong khi doanh thu chỉ tăng 8,0%.", ratio["noi"]]
    assert any(c["noi"].startswith("Nếu doanh thu chỉ đạt 90% kế hoạch") for c in said)
    # the page finds the drawn rows again by their words; the record re-check opens the page and finds them too
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert driver["chi"] in data["ve"] and data["ve"][driver["chi"]]["tag"] == "TR"
    res = kiem_bien_ban.verify(rep["out"], str(drawn_report))
    assert res["ok"], res["errors"]
    # the size line no longer adds "tức khoảng 17%": the note's printed 18,0% is the only rate said, and the owner
    # is told the two disagree
    assert not any("khoảng 17%" in c["noi"] for c in said)
    assert any("18,0%" in f and "17,3%" in f for f in rep["phan_bien"])
    assert res["tong"]["em_tinh"] == 3   # round 12: the contrast says no worked-out 19,0 a second time


def test_the_screen_check_finds_drawn_places_and_keeps_a_tall_item_clear_of_a_sticky_header(drawn_report):
    pytest.importorskip("playwright")
    from present import kiem_hinh
    rep = build(drawn_report)
    res = kiem_hinh.check(rep["out"])
    if res.get("skipped"):
        pytest.skip("no browser")
    assert res["ok"], res["problems"]


def test_the_talk_breathes_after_the_greeting_and_the_asks_and_rests_on_the_cap(report):
    said = lines(build(report))
    assert said[0]["vai"] == "chao" and said[0]["dung"] == 0.8
    opening = [c for c in said if c.get("vai") == "hoi"]
    assert opening[-1]["dung"] == 1.2 and all("dung" not in c for c in opening[:-1])
    contrast = [c for c in said if c.get("vai") == "mau_thuan"]
    assert contrast[-1]["dung"] == 0.8
    assert said[-1]["vai"] == "moi_hoi" and said[-1]["dung"] == 2.0
    # the stakes in money are said up front (mo_rui_ro); the ratio line names the measure only (round 12: 8,9 four times)
    stakes = next(c for c in said if c.get("vai") == "neu_giu")
    assert stakes["noi"].endswith("thay vì 2,40% như kế hoạch gốc.") and not stakes.get("xac_nhan")


def test_words_of_degree_and_names_inside_a_sentence():
    assert story.share_words(Decimal("0.63")) == "gần hai phần ba" and story.share_words(Decimal("0.56")) == "hơn một nửa"
    assert story.share_words(Decimal("0.40")) is None
    assert story.times_words(Decimal("2.25")) == "nhanh hơn gấp đôi" and story.times_words(Decimal("1.75")) == "nhanh gần gấp đôi"
    assert story.times_words(Decimal("1.1")) is None
    assert story._short("Khối Bán hàng") == "khối Bán hàng" and story._short("Logistics") == "Logistics"
    assert story._lc("CNTT: phân bổ license") == "CNTT: phân bổ license" and story._lc("Giữ quỹ lương") == "giữ quỹ lương"


def _data(rep):
    page = Path(rep["out"]).read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="clicky-data" type="application/json">(.*?)</script>', page, re.S).group(1))


def test_questions_after_the_talk_are_answered_from_the_lines_the_owner_approved(report):
    rep = build(report)
    places = clicky_html.page_places(clicky_html.parse(Path(rep["out"]).read_text(encoding="utf-8")), False, None)
    book = story.facts(clicky_html.load_script(rep["kich_ban"]), places, False, "anh Tuấn")
    assert [f["a"] for f in book] == [f["a"] for f in _data(rep)["facts"]]          # what the page carries
    ask = lambda q, **k: (story.answer_fact(book, q, **k) or {}).get("k")       # noqa: E731
    # "tóm lại cần quyết gì": the decisions, exactly as said at the start
    hoi = story.answer_fact(book, "Tóm lại anh Tuấn cần BOD quyết gì?")
    assert hoi["a"].startswith("Anh Tuấn xin anh chị quyết hai việc. Một là giữ tỷ lệ")
    # a figure Clicky worked out: the line, the formula, and the exact result beside the rounded one
    t = story.answer_fact(book, "Con số 1,8 lần em lấy ở đâu ra?")
    assert t["a"].endswith("Em tính từ các số trên báo cáo: 12,6 ÷ 7,2 = 1,75, nói tròn là 1,8.")
    assert "= 7,79, nói tròn là 8." in story.answer_fact(book, "8% tính thế nào?")["a"]
    assert ask("Số 18 tính thế nào?") is None and ask("1,8 tỷ là bao nhiêu?") is None    # never inside another figure
    # the beats, when the page itself has nothing better
    assert ask("Em là ai?") == "ai" and "số nào em tính đều kèm công thức" in story.answer_fact(book, "Em là ai?")["a"]
    assert ask("Nếu không cắt thì sao?") == "neu_giu" and ask("Vì sao phải quyết việc này?") == "can_quyet"
    assert ask("Phần tăng chủ yếu do đâu?") == "dong_luc"
    # 2026-11-13 round 3: decision questions go to the beat that answers them, whatever is pointed at
    assert ask("Vì sao phải quyết ngay?", scoped=True) == "can_quyet" and ask("Vì sao cần quyết?") == "can_quyet"
    assert ask("Nếu không duyệt trần thì sao?") == "neu_giu"
    assert ask("Em đề xuất chọn phương án nào?") == "hoi" and ask("Nhắc lại hai việc là gì?") == "hoi"
    # a question that names another row goes to that row on the page, never to the talk's driver
    assert ask("Vì sao Marketing tăng?") is None and ask("Vì sao khối CNTT tăng?") is None
    # pointing at a place: only the decisions and the formulas, the rest is about that place
    assert ask("Vì sao?", scoped=True) is None and ask("Tóm lại quyết gì?", scoped=True) == "hoi"
    # a line the owner has not switched on is never an answer
    assert not any("43%" in f["a"] for f in book)


def test_the_answers_after_the_talk_are_sealed_and_rechecked(report):
    rep = build(report)
    res = kiem_bien_ban.verify(rep["out"], str(report))
    assert res["ok"], res["errors"]
    assert res["tong"]["hoi_dap"] == len(_data(rep)["facts"]) >= 6
    page = Path(rep["out"]).read_text(encoding="utf-8")
    Path(rep["out"]).write_text(page.replace("12,6 ÷ 7,2 = 1,75", "12,6 ÷ 6,3 = 2", 1), encoding="utf-8")
    assert any("Câu trả lời sau bài nói" in e for e in kiem_bien_ban.verify(rep["out"], str(report))["errors"])


def test_the_decision_card_lists_the_approved_asks_and_the_record_check_holds_it_to_them(report):
    script = {"canh": [{"muc": "Mở đầu", "cau": [
        {"noi": "Anh Tuấn xin anh chị quyết hai việc.", "vai": "hoi"},
        {"noi": "Một, giữ trần 177,5 tỷ.", "vai": "hoi"}, {"noi": "Hai, giãn tuyển 30 vị trí.", "vai": "hoi"},
        {"noi": "Ba, việc chưa bật.", "vai": "hoi", "bat": "chưa"}]}]}
    assert story.decisions(script) == ["Giữ trần 177,5 tỷ", "Giãn tuyển 30 vị trí"]
    assert story.decisions({"canh": [{"cau": [{"noi": "Đề nghị duyệt trần 120,0 tỷ.", "vai": "hoi"}]}]}) == \
        ["Đề nghị duyệt trần 120,0 tỷ"]
    rep = build(report)
    page = Path(rep["out"]).read_text(encoding="utf-8")
    assert _data(rep)["quyet"][0] == "Giữ tỷ lệ chi phí nhân sự trên doanh thu thuần ở 2,40%, tức trần 177,5 tỷ"
    Path(rep["out"]).write_text(page.replace("tức trần 177,5 tỷ\"", "tức trần 199,0 tỷ\"", 1), encoding="utf-8")
    assert any("Thẻ quyết định" in e for e in kiem_bien_ban.verify(rep["out"], str(report))["errors"])


def test_a_script_drawn_table_answers_questions_and_a_rows_own_explanation_is_the_why(drawn_report):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    rep = build(drawn_report)
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(800)
        ask = lambda q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        # the table was read by the build with its header only; on the page its drawn rows answer like any other
        assert ask("Logistics AOPR bao nhiêu?").startswith("Logistics, AOPR: 52,0 tỷ")
        assert ask("Sao tăng nhiều vậy?", "#t tbody tr:nth-child(2) td:nth-child(3)").startswith(
            "Logistics: báo cáo ghi giải trình «Thuê kho».")
        # 18,0% printed, 17,3% from the table: the owner is asked, the Board gets neither as an answer
        a = ask("Chi phí tăng 17% hay 18%?")
        assert "đang kiểm lại" in a and "17,3%" in a                      # both figures, the table's with its formula
        a = ask("Chi phí tăng bao nhiêu phần trăm?")
        assert "18,0%" in a and "(129,0 − 110,0) ÷ 110,0 × 100" in a and "đang kiểm lại" in a
        assert ask("Doanh thu tăng bao nhiêu?").startswith("Báo cáo ghi: «doanh thu chỉ tăng 8,0%».")   # undoubted half
        assert "đang kiểm lại" not in ask("Doanh thu tăng 8% có đúng không?")
        assert ask("Phải cắt bao nhiêu tỷ?").startswith(
            "Theo đề nghị trần 120,0 tỷ, phải giảm 9,0 tỷ so với AOPR (em tính: 129,0 − 120,0)")
        a = ask("Nếu không duyệt trần thì sao?")                                  # money first, then the printed risk
        assert a.startswith("Nếu không duyệt, chi phí theo AOPR là 129,0 tỷ, cao hơn trần đề nghị 9,0 tỷ (em tính: 129,0 − 120,0).")
        assert a.index("9,0") < a.index("6,4%")
        assert ask("ok cảm ơn em").startswith("Dạ.")
        # round-5 panel: cost never answered with the revenue clause; a scenario the report does not compute gets no
        # figure; "bỏ X ra" is the other rows; a level the report does not print is said so; a child foots to its parent
        assert ask("Chi phí tăng bao nhiêu tỷ?").startswith("Tổng tăng 19,0 tỷ từ AOP gốc lên AOPR (em tính: 129,0 − 110,0)")
        a = ask("Nếu doanh thu chỉ đạt 90% thì chi phí phải cắt bao nhiêu?")
        assert a.startswith("Báo cáo không tính kịch bản này, nên em không đưa số.") and "9,0" not in a
        assert ask("Bỏ Logistics ra thì sao?").startswith(
            "Bỏ Logistics ra, các dòng còn lại tăng 7,0 tỷ, khoảng 10,0% (em tính: 19,0 − 12,0 = 7,0; 7,0 ÷ (110,0 − 40,0) × 100)")
        assert ask("Doanh thu AOPR là bao nhiêu?").startswith("Báo cáo không ghi mức doanh thu, chỉ ghi: «doanh thu chỉ tăng 8,0%»")
        assert ask("9,0 tính thế nào?").startswith("Theo đề nghị trần 120,0 tỷ, phải giảm 9,0 tỷ so với AOPR (em tính: 129,0 − 120,0)")
        assert "Logistics 52,0 + Mua hàng 33,0 = 85,0 tỷ, khớp dòng Khối vận hành" in ask("Mua hàng 33,0 có đúng không?")
        assert "Em cộng các dòng con: Logistics 52,0 + Mua hàng 33,0 = 85,0 tỷ, khớp." in ask("Khối vận hành 85,0 có đúng không?")
        # every figure Clicky has said can be asked back: its own 17,3, a row's 30,0%, the ratio on a doubted rate
        a = ask("17,3% ở đâu ra?")
        assert "(129,0 − 110,0) ÷ 110,0 × 100 ≈ 17,3%" in a and "18,0%" in a
        assert ask("Logistics tăng 30% đúng không?").startswith("Đúng theo bảng: Logistics tăng 30,0% (em tính: (52,0 − 40,0) ÷ 40,0 × 100)")
        a = ask("Gấp mấy lần?")
        assert "17,3%" in a and "18,0%" in a and "chưa tìm thấy" not in a
        assert ask("6,4% là sao?").startswith("Báo cáo ghi: «Nếu doanh thu chỉ đạt 90% kế hoạch, tỷ lệ chi phí trên doanh thu lên 6,4%.»")
        assert ask("2,3 ở đâu ra?").startswith("Báo cáo không ghi 2,3 ở chỗ nào")      # the ratio line is off: never explained
        a = ask("Rủi ro lớn nhất là gì?")
        assert a.startswith("Nếu doanh thu chỉ đạt 90% kế hoạch") and "«Rủi ro»" not in a
        assert ask("110,0 có đúng không?").startswith("Tổng, AOP gốc: 110,0 tỷ. Em cộng 4 dòng cột AOP gốc (không tính dòng tổng của nhóm) ra 110,0 tỷ, khớp")
        a = ask("Chi phí tăng nhanh gấp mấy lần?")                               # built on the doubted rate: held with both figures
        assert "17,3%" in a and "đang kiểm lại" in a
        assert ask("Nói ngắn trong 1 câu").startswith("Anh Tuấn xin anh chị duyệt trần chi phí vận hành 120,0 tỷ, Logistics phải giải trình,")   # 9,0 is worked out and its line is off: never said unasked
        a = ask("Logistics bị ảnh hưởng thế nào?")
        assert a.startswith("Logistics: AOP gốc 40,0 tỷ; AOPR 52,0 tỷ. Tăng 12,0 tỷ (30,0%) từ AOP gốc lên AOPR (em tính: 52,0 − 40,0).")
        assert "«Thuê kho»" in a and "Báo cáo chưa nói ảnh hưởng" in a
        # pre-flight doubts the cap (118,8 keeps the ratio): held with both figures until the owner rules
        assert ask("Tại sao trần 120,0?").startswith("Báo cáo ghi 120,0; giữ đúng tỷ lệ chi phí trên doanh thu của kế hoạch gốc thì trần là 110,0 × (1 + 8,0 ÷ 100) = 118,8 tỷ.")
        # no variance column: the change is worked out per row, and the subtotal "Khối vận hành" is never ranked
        assert ask("Phòng nào tăng ít nhất?").startswith(
            "Hành chính thấp nhất về mức tăng từ AOP gốc lên AOPR (em tính: AOPR 16,5 − AOP gốc 15,0): 1,5 tỷ.")
        assert ask("Phòng nào tăng nhiều nhất?").startswith("Logistics cao nhất")
        a = ask("Logistics chiếm bao nhiêu % phần tăng?")
        assert a.startswith("Logistics chiếm 63,2% phần tăng từ AOP gốc lên AOPR") and "12,0 ÷ 19,0" in a
        assert ask("Logistics tăng bao nhiêu %?").startswith(
            "Logistics tăng 30,0% từ AOP gốc lên AOPR (em tính: (52,0 − 40,0) ÷ 40,0 × 100)")
        assert ask("Khối vận hành tăng bao nhiêu?").startswith("Khối vận hành tăng 15,0 tỷ từ AOP gốc lên AOPR")
        # round-5 Director: "nghĩa là gì" on a figure says its row plainly with its printed reason, never a bare subtraction
        a = ask("52,0 nghĩa là gì?")
        assert a.startswith("Logistics: AOP gốc 40,0 tỷ; AOPR 52,0 tỷ. Tăng 12,0 tỷ (30,0%)") and "«Thuê kho»" in a, a
        a = ask("85,0 nghĩa là gì?")
        assert a.startswith("Khối vận hành: AOP gốc 70,0 tỷ; AOPR 85,0 tỷ.") and "Dòng này cộng từ: Logistics 52,0 + Mua hàng 33,0 = 85,0 tỷ" in a, a
        a = ask("Các phòng cộng lại có khớp tổng không?")
        assert "(không tính dòng tổng của nhóm)" in a and "cộng ra 129,0 tỷ, khớp dòng Tổng 129,0 tỷ" in a, a
        browser.close()


def test_once_the_owner_rules_a_doubted_sentence_every_route_says_the_ruling_never_his_private_note(drawn_report):
    # round-5 panel: after a ruling "bao nhiêu phần trăm" quoted 18,0% alone, and the owner's free-text note
    # ("… em sẽ sửa báo cáo") was read out on the Board's screen
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    rep = build(drawn_report)
    path = Path(rep["kich_ban"])
    path.write_text(path.read_text(encoding="utf-8").replace("ly_do_bo: ''", "ly_do_bo: 'Kế toán xác nhận, em sẽ sửa sau'")
                    .replace("da_xem: 'chưa'", "da_xem: 'có'").replace("da_duyet: 'chưa'", "da_duyet: 'có'")
                    .replace("quyet: ''", "quyet: 'giữ câu'"), encoding="utf-8")
    rep = build(drawn_report)
    assert rep["trang_thai"].startswith("CHÍNH THỨC"), rep["canh_bao"]

    def answers(out, qs):
        with sync_playwright() as pw:
            try:
                browser = kiem_hinh._launch(pw)
            except RuntimeError as exc:
                pytest.skip(str(exc))
            pg = browser.new_page(viewport={"width": 1366, "height": 768})
            pg.goto(Path(out).as_uri())
            pg.wait_for_timeout(800)
            got = [pg.evaluate("q => window.__clickyHoi(q, null)", q) for q in qs]
            browser.close()
            return got

    qs = ("18% đúng không?", "Chi phí vận hành AOPR tăng 18,0% trong khi doanh thu chỉ tăng 8,0% có đúng không?",
          "Chi phí tăng bao nhiêu phần trăm?")
    for a in answers(rep["out"], qs):
        assert a.startswith("Báo cáo ghi: «Chi phí vận hành AOPR tăng 18,0%") and "chưa tìm thấy" not in a, a
        assert "đã soát và giữ số này" in a and "17,3%" in a and "em sẽ sửa" not in a and "tính lại: tính" not in a
    # "báo cáo sai": the table's figure leads, never 18,0 alone, and the pack stays a draft until the report is fixed
    path.write_text(path.read_text(encoding="utf-8").replace("quyet: 'giữ câu'", "quyet: 'báo cáo sai'"), encoding="utf-8")
    rep = build(drawn_report)
    assert rep["trang_thai"].startswith("SƠ BỘ") and any("báo cáo sai" in n for n in rep["canh_bao"])
    for a in answers(rep["out"], qs):
        assert a.startswith("Theo bảng: (129,0 − 110,0) ÷ 110,0 × 100 ≈ 17,3%. Báo cáo đang ghi 18,0%"), a


def test_a_report_is_wrong_ruling_without_a_formula_is_never_told_as_kept(report):
    # round-6 blocker (all five roles): A's "Hai phần ba …" has no table formula; ruled "báo cáo sai", the Board was
    # told "anh Tuấn đã soát và giữ câu này", the opposite of the ruling
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    rep = build(report)
    path = Path(rep["kich_ban"])
    base = path.read_text(encoding="utf-8")
    q = "Hai phần ba phần tăng đến từ tuyển mới, đúng không?"

    def answer_for(text):
        path.write_text(text, encoding="utf-8")
        out = build(report)
        with sync_playwright() as pw:
            try:
                browser = kiem_hinh._launch(pw)
            except RuntimeError as exc:
                pytest.skip(str(exc))
            pg = browser.new_page(viewport={"width": 1366, "height": 768})
            pg.goto(Path(out["out"]).as_uri())
            pg.wait_for_timeout(800)
            a = pg.evaluate("q => window.__clickyHoi(q, null)", q)
            browser.close()
            return out, a

    out, a = answer_for(base.replace("quyet: ''", "quyet: 'báo cáo sai'"))
    assert out["trang_thai"].startswith("SƠ BỘ")
    assert "đã xác nhận câu này cần sửa" in a and "SƠ BỘ" in a and "giữ" not in a, a
    # kept, with the owner's words for the Board: they replace "em ghi lại", and his private note never shows
    out, a = answer_for(base.replace("quyet: ''", "quyet: 'giữ câu'").replace("noi_voi_BOD: ''", "noi_voi_BOD: 'Phần tuyển mới tính cả người thay thế'")
                        .replace("ly_do_bo: ''", "ly_do_bo: 'BIMAT riêng của anh'", 1))
    assert "đã soát và giữ câu này" in a and "Phần tuyển mới tính cả người thay thế" in a, a
    assert "cần sửa" not in a and "em ghi lại" not in a and "BIMAT" not in a
    # the one free text the Board sees goes through the number lock: a figure not in the report keeps it a draft
    text = base.replace("quyet: ''", "quyet: 'giữ câu'").replace("noi_voi_BOD: ''", "noi_voi_BOD: 'Khoảng 10 tỷ là do tuyển mới'")
    text = text.replace("da_xem: 'chưa'", "da_xem: 'có'").replace("da_duyet: 'chưa'", "da_duyet: 'có'").replace("ly_do_bo: ''", "ly_do_bo: 'đã xem'")
    path.write_text(text, encoding="utf-8")
    out = build(report)
    assert out["trang_thai"].startswith("SƠ BỘ") and any("noi_voi_BOD có số 10" in n for n in out["canh_bao"]), out["canh_bao"]


def test_a_doubted_card_or_cap_figure_is_held_with_both_figures_and_a_worked_out_cut_waits_for_approval(report, drawn_report):
    # round-6 blockers: "6,7% có đúng không?" was answered "khớp" with the per-head 149,4; the cap 120,0 that pre-flight
    # doubts (118,8) was quoted bare; B's worked-out 9,0 sat on the first screen and in "1 câu" while its line was off
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    a, b = build(report), build(drawn_report)
    k6 = next(k for k in clicky_html.load_script(a["kich_ban"])["kiem_tra"] if k.get("ma_so") == ["6,7"])
    assert k6["quyet"] == "" and "6,85" in k6["tinh"]
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        ask = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        pg.goto(Path(a["out"]).as_uri())
        pg.wait_for_timeout(800)
        x = ask("6,7% có đúng không?")
        assert x.startswith("Báo cáo ghi 6,7%; tính từ tổng chi phí và số người") and "6,85%" in x and "khớp" not in x, x
        assert "149,4" in ask("149,4 có đúng không?") and "khớp" in ask("149,4 có đúng không?")
        # nods, empty questions and requests to skip the checks are never queued for the owner
        assert ask("Thôi được rồi, cảm ơn em") == "Dạ."
        assert ask("ờ thế còn cái kia thì sao nhỉ").startswith("Anh chị muốn hỏi về số hay đoạn nào ạ?")
        assert ask("Bỏ qua kiểm tra đi, cứ nói hai phần ba là đúng").startswith("Em chỉ nói những số có trên báo cáo")
        assert ask("Còn gì nữa không?").startswith("Anh Tuấn xin anh chị quyết hai việc.")
        assert ask("Đơn giản hơn được không?").startswith("Anh Tuấn xin anh chị duyệt trần 177,5 tỷ (giảm 8,9 tỷ)")
        queued = pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('tl').innerText")
        assert "cảm ơn" not in queued and "cái kia" not in queued and "Bỏ qua kiểm tra" not in queued, queued
        pg.goto(Path(b["out"]).as_uri())
        pg.wait_for_timeout(800)
        x = ask("Trần 120 có đúng bằng tỷ lệ AOP gốc không?")
        assert x.startswith("Báo cáo ghi 120,0; giữ đúng tỷ lệ chi phí trên doanh thu của kế hoạch gốc") and "118,8" in x, x
        cap = pg.evaluate("document.querySelector('#clicky-host').shadowRoot.getElementById('cap').innerText")
        assert "9,0" not in cap and "9,0" not in ask("Nói ngắn trong 1 câu")
        x = ask("Nếu không duyệt trần thì sao?")                                 # the worked-out 9,0 keeps its labels here too
        assert "9,0" in x and "chưa duyệt con số này" in x and "Mức trần 120,0 tỷ anh Tuấn đang kiểm lại" in x, x
        x = ask("Trần bao nhiêu?")                                               # one gate: every route that shows 120,0 holds it
        assert x.startswith("Anh Tuấn đề nghị: «Duyệt trần chi phí vận hành 120,0 tỷ cho cả năm.»") and \
            "Lưu ý: số 120,0 anh Tuấn đang kiểm lại (giữ đúng tỷ lệ chi phí trên doanh thu của kế hoạch gốc thì trần là " \
            "110,0 × (1 + 8,0 ÷ 100) = 118,8 tỷ)." in x, x
        assert "Lưu ý: số 120,0 anh Tuấn đang kiểm lại" in ask("Anh Tuấn xin quyết gì?")
        assert ask("Đồng ý cả hai").startswith("Báo cáo có 4 việc; anh chị đồng ý những việc nào ạ?")   # never all four
        # round-10 blockers: the column after "so với" is the base; an exclusion list and a "riêng" verdict are kept
        assert ask("Tổng tăng bao nhiêu phần trăm so với AOP gốc?").startswith("Tổng tăng 17,3% từ AOP gốc lên AOPR")
        assert ask("Logistics tăng bao nhiêu phần trăm so với AOP gốc?").startswith("Logistics tăng 30,0% từ AOP gốc lên AOPR")
        assert ask("Đồng ý hết trừ việc một và việc bốn").startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý». Việc một, bốn em chưa ghi.")
        assert ask("Đồng ý hết, riêng việc một để bàn thêm").startswith("Em đã ghi việc một «Cần bàn thêm»; việc hai «Đồng ý»; việc ba «Đồng ý»; việc bốn «Đồng ý».")   # ask order (round 11)
        assert pg.evaluate("q => window.__clickyHoi(q, \"[data-clicky='c9']\")", "Đồng ý").startswith("Em đã ghi «Đồng ý» cho việc: «Logistics phải giải trình")   # a tapped ask
        assert ask("Đồng ý việc hai và việc bốn").startswith("Em đã ghi việc hai «Đồng ý»; việc bốn «Đồng ý».")
        x = ask("Đồng ý việc một, việc ba cần bàn thêm")
        assert x.startswith("Em đã ghi «Cần bàn thêm» cho việc: «Giữ quỹ lương") and "Việc một: mức 120,0 anh Tuấn đang kiểm lại; anh chị đồng ý thế nào ạ?" in x, x   # round 14
        assert ask("Tôi chỉ duyệt trần 118,8 thôi").startswith("Đề nghị là «Duyệt trần chi phí vận hành 120,0 tỷ cho cả năm»; anh chị nói 118,8.")
        # round-9 panel: "hai phần ba à?" was parked; the doubted 18,0 and its table working 17 were confirmed bare
        x = ask("Hai phần ba à?")
        assert x.startswith("Logistics chiếm gần hai phần ba") and "(52,0 − 40,0) ÷ (129,0 − 110,0) × 100" in x, x
        assert "Lưu ý: số 18,0 anh Tuấn đang kiểm lại (tính từ bảng" in ask("18,0% à?")
        assert "Lưu ý: số 18,0 anh Tuấn đang kiểm lại (tính từ bảng" in ask("17% à?")
        x = ask("Có số nào sai không?")
        assert x.startswith("Có 2 chỗ anh Tuấn đang kiểm lại: số 18,0% (tính từ bảng") and "số 120,0 (giữ đúng tỷ lệ" in x, x
        assert ask("Lo nhất là gì?").startswith("Nếu doanh thu chỉ đạt 90% kế hoạch")   # the report redraws: the place is found again
        assert pg.evaluate("q => window.__clickyHoi(q, \"[data-clicky='c6']\")", "nghĩa là gì?").startswith("Đoạn này ghi: «Nếu doanh thu chỉ đạt 90%")
        x = ask("Đồng ý trần 120")                                                # a typed decision on a doubted cap says so
        assert x.startswith("Em chưa ghi; anh chị chọn giúp em một bước nữa. Việc một: mức 120,0 anh Tuấn đang kiểm lại") and \
            "Đồng ý về nguyên tắc, chờ mức chốt" in x, x   # round 14: one more press on a doubted cap
        x = ask("Phải cắt bao nhiêu tỷ?")
        assert "9,0" in x and "chưa duyệt con số này" in x and "Mức trần 120,0 tỷ anh Tuấn đang kiểm lại" in x, x
        browser.close()


def test_board_questions_in_the_talks_own_words_get_the_asks_the_columns_and_honest_gaps(report, drawn_report):
    # round-6 panel (all five): "kế hoạch điều chỉnh" heard in the talk found nothing in Q&A; plain questions about
    # the asks fell to "chưa tìm thấy" or echoed a fragment; a revenue level was answered with a growth sentence
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    a, b = build(report), build(drawn_report)
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        ask = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        pg.goto(Path(a["out"]).as_uri())
        pg.wait_for_timeout(800)
        assert ask("Kế hoạch điều chỉnh tổng là bao nhiêu?").startswith("Tổng, AOPR: 186,4 tỷ.")
        assert ask("Khối Bán hàng kế hoạch gốc bao nhiêu?").startswith("Khối Bán hàng, AOP gốc: 78,2 tỷ.")
        assert "Trong bài nói em gọi cột này là «kế hoạch điều chỉnh»" in ask("Kế hoạch điều chỉnh là gì?")
        assert ask("Doanh thu năm nay bao nhiêu?").startswith("Báo cáo không ghi mức doanh thu, chỉ ghi: «doanh thu chỉ tăng 7,2%»")
        assert ask("Cắt bao nhiêu người?").startswith("Báo cáo không ghi số người phải cắt.")
        assert ask("9,0 tính thế nào?").startswith("Báo cáo này không có số 9,0")
        x = ask("Em khuyên anh nên chọn gì?")
        assert x.startswith("Em không khuyên thay anh chị. Đề xuất của anh Tuấn là: «Giữ tỷ lệ") and "2,52%" in x
        assert ask("Cắt 8,9 tỷ ở đâu?").startswith("Báo cáo chỉ nêu cách: «Giãn tuyển 30 vị trí")
        assert ask("Có gì đáng lo nhất?").startswith("Nếu giữ nguyên, chi phí vượt trần đề xuất 8,9 tỷ.")   # the talk's own risk line (round 16)
        pg.goto(Path(b["out"]).as_uri())
        pg.wait_for_timeout(800)
        assert ask("Ai phải giải trình?").startswith("Anh Tuấn đề nghị: «Logistics phải giải trình")
        assert ask("Câu thứ hai là gì?").startswith("Việc thứ hai anh Tuấn xin: «Logistics phải giải trình")
        x = ask("Quỹ lương có ảnh hưởng nhân viên không?")
        assert x.startswith("Anh Tuấn đề nghị: «Giữ quỹ lương bằng kế hoạch gốc cho cả năm.»") and "chưa nói ảnh hưởng tới nhân viên" in x
        browser.close()


def test_the_pre_flight_checks_head_the_script_and_hold_the_approval(report):
    rep = build(report)
    script = clicky_html.load_script(rep["kich_ban"])
    assert len(script["kiem_tra"]) == len(rep["phan_bien"]) == 4
    assert all(k["da_xem"] == "chưa" for k in script["kiem_tra"])
    path = Path(rep["kich_ban"])
    path.write_text(re.sub(r"^(\s+da_xem:) 'chưa'", r"\1 'có'", path.read_text(encoding="utf-8"), count=1, flags=re.M),
                    encoding="utf-8")
    again = build(report)                       # a ticked check is the owner's edit: the draft is not written over
    assert clicky_html.load_script(again["kich_ban"])["kiem_tra"][0]["da_xem"] == "có"


def test_switching_on_a_proposed_line_settles_its_check_and_never_breaks_the_build(report):
    rep = build(report)
    path = Path(rep["kich_ban"])
    script = clicky_html.load_script(path)
    gap = next(k for k in script["kiem_tra"] if k.get("cau"))
    assert gap["ly_do_bo"] == "" and any("3,9" in c for c in gap["cau"])
    # the bound names 8,9, printed on another card: confirmed at draft time, so switching it on just works
    bound = next(c for s in script["canh"] for c in s["cau"] if c.get("vai") == "uoc_tiet_kiem")
    assert bound["xac_nhan"] == ["8,9"]
    text = path.read_text(encoding="utf-8").replace("da_xem: 'chưa'", "da_xem: 'có'").replace("bat: 'chưa'", "bat: 'có'")
    text = text.replace("da_duyet: 'chưa'", "da_duyet: 'có'")
    path.write_text(text, encoding="utf-8")
    held = build(report)                        # every line on: the gap item is settled by its lines
    assert not any("3,9" in n and "ly_do_bo" in n for n in held["canh_bao"])
    path.write_text(path.read_text(encoding="utf-8").replace("ly_do_bo: ''", "ly_do_bo: 'Đã sửa câu trong báo cáo'"),
                    encoding="utf-8")
    done = build(report)
    assert done["trang_thai"].startswith("CHÍNH THỨC"), done["canh_bao"]
    said = " ".join(c["noi"] for c in lines(done))
    assert "3,9 tỷ" in said and "Có một điểm báo cáo chưa có số" in said


def _silent_wav(text):   # playable silence at a natural pace (~15 letters a second), so the talk really plays
    import io, wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * max(0.6, len(text) / 15)))
    return out.getvalue()


def _wav_secs(wav):
    import io, wave
    with wave.open(io.BytesIO(wav)) as w:
        return wav, w.getnframes() / w.getframerate()


def test_round_ten_desktop_frames_bar_chips_charts_and_answer_wording(report, drawn_report, monkeypatch):
    # round-10 panel: the close framed ask 1 only; long captions grew the bar to 125-149 px; a doubted cap's chip said
    # «đúng chỗ đang khoanh»; an undrawn chart left a hole; the panel heading was printed twice; the script's source
    # closed every answer; "hụt 10%" was answered with the 9,0 cut; "Doanh thu 90%" denied the printed 90% sentence
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    monkeypatch.setattr(clicky_html, "wav_to_mp3", _wav_secs)
    a, b = build(report, voice=_silent_wav), build(drawn_report, voice=_silent_wav)
    SR = "document.getElementById('clicky-host').shadowRoot"
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        ask = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        pg.goto(Path(a["out"]).as_uri())
        pg.wait_for_timeout(1800)
        # the chart its library never drew is one line, not a 150 px hole
        holes = pg.evaluate("""[...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
            return r.height > 120 && !e.textContent.trim() && !e.closest('[data-clicky-layer]') && !e.querySelector('img,svg,table,canvas:not([data-clicky-trong])'); }).length""")
        assert holes == 0 and pg.evaluate("[...document.querySelectorAll('canvas[data-clicky-trong]')].map(c => c.getBoundingClientRect().height)") == [24]
        # the script's source is said on the first answer it backs, not on every one
        assert "Theo kịch bản nháp của anh Tuấn (chưa duyệt)" in ask("Tóm lại anh Tuấn cần quyết gì?")
        assert "Theo kịch bản nháp" not in ask("Còn gì nữa không?")
        x = ask("Giãn 30 vị trí tiết kiệm được bao nhiêu?")
        assert x.count("Em tính từ") <= 1, x
        assert ask("9,0 tính thế nào?").startswith("Báo cáo này không có số 9,0") and "9,0 tính thế nào?" in pg.evaluate(f"{SR}.getElementById('tl').innerText")
        pg.goto(Path(b["out"]).as_uri())
        pg.wait_for_timeout(900)
        x = ask("Doanh thu hụt 10% thì chi phí phải cắt bao nhiêu?")
        assert x.startswith("Báo cáo không tính kịch bản này") and "9,0" not in x, x
        assert ask("Doanh thu 90% thì sao?").startswith("Báo cáo ghi: «Nếu doanh thu chỉ đạt 90% kế hoạch, tỷ lệ chi phí trên doanh thu lên 6,4%.»")
        ask("Không đồng ý việc một")   # a yes on the doubted cap waits for a press (round 14)
        heads = pg.evaluate(f"[...{SR}.querySelectorAll('#tbh,#tl li.sec')].filter(x => x.getClientRects().length).map(x => x.textContent)")
        assert heads == ["Quyết định anh chị đã ghi", "Câu chờ anh Tuấn trả lời"], heads
        # play the talk fast: the bar stays two lines, a doubted figure's chip says so, the close frames every ask
        pg.goto(Path(b["out"]).as_uri())
        pg.wait_for_timeout(900)
        pg.evaluate(f"{SR}.getElementById('play').click(); window.__clickyAudio.playbackRate = 4")
        tallest, chips, close, caps = 0, set(), None, []
        for _ in range(400):
            pg.wait_for_timeout(120)
            s = pg.evaluate(f"""(() => {{ const r = {SR}; window.__clickyAudio.playbackRate = 4;
                const f = [...r.querySelectorAll('[data-clicky-frame=main]'), ...document.querySelectorAll('[data-clicky-frame=main]')].map(x => x.getBoundingClientRect())[0];
                const li = [...document.querySelectorAll('ol.dec li')].map(x => x.getBoundingClientRect());
                return {{cap: r.getElementById('cap').textContent, bar: r.getElementById('bar').getBoundingClientRect().height,
                        chips: [...r.querySelectorAll('#cap .ev span')].map(x => x.textContent),
                        all: !!f && li.every(q => q.top >= f.top - 2 && q.bottom <= f.bottom + 2)}}; }})()""")
            tallest = max(tallest, s["bar"]); caps.append(s["cap"][:40])
            chips.update(s["chips"])
            if "Tóm lại" in s["cap"] and close is None:
                pg.wait_for_timeout(400)
                close = pg.evaluate(f"""(() => {{ const f = [...{SR}.querySelectorAll('[data-clicky-frame=main]'), ...document.querySelectorAll('[data-clicky-frame=main]')].map(x => x.getBoundingClientRect())[0];
                    return !!f && [...document.querySelectorAll('ol.dec li')].every(q => {{ const r = q.getBoundingClientRect(); return r.top >= f.top - 2 && r.bottom <= f.bottom + 2; }}); }})()""")
            if "mời anh chị cho ý kiến" in s["cap"]:
                break
        assert close is True, caps
        assert tallest <= 110, tallest
        assert "120,0 · đang kiểm lại" in chips and not any("đúng chỗ đang khoanh" in c for c in chips), chips
        browser.close()


def test_round_eleven_decisions_by_clause_a_tapped_row_and_who_or_when(report, drawn_report):
    # round-11 panel: clauses recorded the opposite or were queued as questions; a tapped row's "%" gave the total's
    # doubt; "18% à?" and a tapped doubted paragraph carried no note; "ai / bao giờ" found nothing
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    a, b = build(report), build(drawn_report)
    SR = "document.getElementById('clicky-host').shadowRoot"
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        ask = lambda q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        listed = lambda: pg.evaluate(f"{SR}.getElementById('tl').innerText")   # noqa: E731
        pg.goto(Path(a["out"]).as_uri())
        pg.wait_for_timeout(800)
        assert ask("Việc một không, còn lại đồng ý").startswith("Em đã ghi việc một «Không đồng ý»; việc hai «Đồng ý».")
        x = ask("Đồng ý hết trừ trần")
        assert x.startswith("Em đã ghi «Đồng ý» cho việc: «Giãn tuyển") and "Việc một em chưa ghi." in x, x
        assert ask("Không duyệt trần, giãn tuyển thì triển khai").startswith("Em đã ghi việc một «Không đồng ý»; việc hai «Đồng ý».")
        assert ask("Việc hai làm đi. Việc một để tuần sau họp lại").startswith("Em đã ghi việc một «Cần bàn thêm»; việc hai «Đồng ý».")
        x = ask("Ok hết")   # round 13: a «hết» asks before it changes an ask chosen on its own
        assert "Việc một đang ghi «Cần bàn thêm»; anh chị đổi thành «Đồng ý» ạ?" in x and "Giữ như cũ" in x, x
        ask("Tôi không đồng ý giãn tuyển, đội tôi cần người")
        assert "Không đồng ý — anh chị ghi: «Tôi không đồng ý giãn tuyển, đội tôi cần người»" in listed()
        assert "(đã đổi từ Đồng ý)" in listed() and "đã đổi từ Không đồng ý)" not in listed().split("Giãn tuyển")[-1]
        assert not ask("Vì sao không duyệt trần").startswith("Em đã ghi")       # a question, never a decision
        assert ask("Khi nào giãn tuyển?").startswith("Báo cáo ghi: «giãn tuyển 30 vị trí bán hàng sang quý 3")
        pg.goto(Path(b["out"]).as_uri())
        pg.wait_for_timeout(900)
        assert ask("Đồng ý tất cả trừ việc 1 và 4").startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý». Việc một, bốn em chưa ghi.")
        assert ask("Ok hết, riêng việc một để bàn thêm").startswith(
            "Em đã ghi việc một «Cần bàn thêm»; việc hai «Đồng ý»; việc ba «Đồng ý»; việc bốn «Đồng ý».")
        assert ask("Không, việc bốn chưa được").startswith("Em đã ghi «Không đồng ý» cho việc: «Hoãn dự án kho mới")
        x = ask("Cứ làm như anh Tuấn đề xuất đi")   # round 13: asks 1 and 4, chosen on their own, are asked before they change
        assert x.startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý». Việc bốn đang ghi «Không đồng ý»; anh chị đổi thành «Đồng ý» ạ?"), x
        # round 15: the doubted cap is never one [Đổi] from a plain yes; it gets the two-button step
        assert "Việc một: mức 120,0 anh Tuấn đang kiểm lại; anh chị đồng ý thế nào ạ?" in x and "Đồng ý mức 120,0" not in x, x
        assert ask("Đồng ý ba việc đầu; không đồng ý hoãn kho mới, đội bán hàng cần kho").startswith(
            "Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý»; việc bốn «Không đồng ý». Việc một: mức 120,0 anh Tuấn đang kiểm lại")   # round 14
        assert "chờ mức chốt" in ask("Không phản đối việc một")
        assert ask("Nhất trí việc hai và ba, việc bốn tạm treo").startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý»; việc bốn «Cần bàn thêm».")
        # a tapped row: "%" is that row's own rate, never the total's doubt
        assert ask("Tăng bao nhiêu phần trăm?", "#t tbody tr:nth-child(1) td:nth-child(2)").startswith("Khối vận hành tăng 21,4%")
        assert ask("Tăng bao nhiêu phần trăm?", "#t tbody tr:nth-child(2) td:nth-child(2)").startswith("Logistics tăng 30,0%")
        # one doubt gate: the rounded figure and a tapped doubted paragraph say it is being checked
        assert "Lưu ý: số 18,0 anh Tuấn đang kiểm lại" in ask("18% à?")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        pg.evaluate("document.querySelector('[data-clicky=c3]').scrollIntoView({block: 'center'})")
        box = pg.locator("[data-clicky=c3]").bounding_box()
        pg.mouse.click(box["x"] + 20, box["y"] + box["height"] / 2)
        pg.wait_for_timeout(500)
        assert "Lưu ý: câu «Chi phí vận hành AOPR tăng…» anh Tuấn đang kiểm lại" in pg.evaluate(f"{SR}.getElementById('ba').textContent")
        # who and when: the clause that names them
        assert ask("Ai theo dõi nhịp chi hằng tháng?").startswith("Báo cáo ghi: «FP&A theo dõi nhịp chi hằng tháng")
        assert "mười lăm" in ask("Bao giờ báo lại?")
        assert ask("Ai phải giải trình?").startswith("Anh Tuấn đề nghị: «Logistics phải giải trình")
        browser.close()


def test_round_twelve_decision_clauses_table_grid_and_tapped_asks(report, drawn_report):
    # round-12 panel: a reason clause («vì … thuê kho») named another ask, «trần thì không» and «việc hai chưa» were queued,
    # clauses with no verdict were recorded blank, and a tapped ask line took no «Ok» and found no «Ai theo dõi?».
    # Every sentence in tests/fixtures/ghi_quyet_dinh.json, then a generated grid of two-clause sentences in every order,
    # joiner and reason tail (with trap words), must record exactly the asks and verdicts it names
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    a, b = build(report), build(drawn_report)
    cases = json.loads((Path(__file__).parent / "fixtures" / "ghi_quyet_dinh.json").read_text(encoding="utf-8"))
    num, alias = ["một", "hai", "ba", "bốn"], {"A": ["trần", "giãn tuyển"], "B": ["trần", "logistics", "quỹ lương", "kho mới"]}
    forms = [("Y", "đồng ý {}"), ("Y", "{} thì được"), ("N", "không đồng ý {}"), ("N", "{} thì không"), ("D", "tạm treo {}"), ("D", "{} để bàn thêm")]
    joins = [", ", "; ", ". ", ", còn ", ", riêng ", " nhưng "]
    traps = ["kho cũ hết chỗ", "hợp đồng thuê kho hết hạn", "phải giữ người", "không muốn hoãn", "anh đồng ý với chị Lan", "trần cũ quá thấp"]
    tails = ["", ", vì {}", " vì {}", " do {}"]
    n = 0
    for pk in ("A", "B"):
        m = len(alias[pk])
        for i in range(m):
            for j in range(m):
                if i == j:
                    continue
                for v1, f1 in forms:
                    for v2, f2 in forms:
                        ref = lambda k, t: ["việc " + num[k], "việc %d" % (k + 1), alias[pk][k]][t % 3]   # noqa: E731
                        c1 = f1.format(ref(i, n)) + tails[n % 4].format(traps[n % 6])
                        c2 = f2.format(ref(j, n + 1))
                        jn = joins[n % 6]
                        q = c1[:1].upper() + c1[1:] + jn + (c2[:1].upper() + c2[1:] if jn == ". " else c2)
                        cases.append([pk, q, {str(i): v1, str(j): v2}])
                        n += 1
    assert n == 504
    bad = []
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pages = {}
        for pk, out in (("A", a["out"]), ("B", b["out"])):
            pg = browser.new_page(viewport={"width": 1366, "height": 768})
            pg.goto(Path(out).as_uri())
            pg.wait_for_timeout(700)
            pages[pk] = pg
        for pk, q, want in cases:
            r = pages[pk].evaluate("q => window.__clickyGhi(q)", q)
            if not _ghi_ok(pk, r, want):   # "?" (round 13): asked back, never recorded
                bad.append((pk, q, r["rec"], want, r["a"][:120]))
        assert not bad, "\n".join(map(str, bad[:20]))
        pg = pages["B"]
        ghi = lambda q, s: pg.evaluate("([q, s]) => window.__clickyGhi(q, s)", [q, s])   # noqa: E731
        # the report's own ask line, tapped: a bare verdict is that ask's, a question is asked of the whole report
        for cid, k in (("c8", "0"), ("c9", "1"), ("c11", "3")):
            for q, v in (("Ok", "Đồng ý"), ("Không", "Không đồng ý"), ("Để bàn thêm", "Cần bàn thêm")):
                r = ghi(q, f"[data-clicky={cid}]")
                if cid == "c8" and v == "Đồng ý":   # round 14: the cap 120,0 is still being checked, one more press
                    assert r["rec"] == {} and "chờ mức chốt" in r["a"], r
                else:
                    assert r["rec"] == {k: [v, "", False]}, (cid, q)
        r = ghi("Không, đội bán hàng cần kho", "[data-clicky=c11]")
        assert r["rec"] == {"3": ["Không đồng ý", "Không, đội bán hàng cần kho", False]}
        assert ghi("Ai theo dõi?", "[data-clicky=c8]")["a"].startswith("Báo cáo ghi: «FP&A theo dõi nhịp chi hằng tháng")
        assert ghi("Trần bao nhiêu?", "[data-clicky=c8]")["a"].startswith("Anh Tuấn đề nghị: «Duyệt trần chi phí vận hành 120,0 tỷ")
        assert ghi("Vì sao?", "[data-clicky=c10]")["a"].startswith("Báo cáo chưa ghi lý do riêng cho đề nghị «Giữ quỹ lương")
        assert ghi("Không có ý kiến", None)["a"].startswith("Dạ, em ghi anh chị chưa cho ý kiến")
        # a clause with asks but no verdict is asked back, never recorded blank
        r = ghi("Đồng ý trần, việc hai thì anh Tuấn gặp anh sau", None)
        assert r["rec"] == {} and "chờ mức chốt" in r["a"] and "Việc hai anh chị nói «việc hai thì anh Tuấn gặp anh sau»" in r["a"], r   # B's cap: one more press
        browser.close()


def test_round_twelve_talk_arc_says_each_figure_at_most_twice_with_cause_and_bridge(report, drawn_report):
    # round-12 panel: 8,9 said four times, «Nếu giữ nguyên» twice, «Vì vậy … đề xuất mức trần» circular, the printed cause
    # (48 hires) never voiced, no bridge to the second ask; on B 19,0 twice (tỷ against %) and the 6,4% stakes at 62 s of 88 s
    a = build(report)
    said_a = [c for c in lines(a) if c.get("bat", "có") == "có"]
    talk = " ".join(c["noi"] for c in said_a)
    assert talk.count("8,9") == 2 and talk.count("Nếu giữ nguyên") == 1 and "Vì vậy" not in talk
    assert "Báo cáo ghi lý do: kế hoạch tuyển thêm 48 nhân viên bán hàng kênh MT và HRC+KA." in talk
    assert "Giữ 2,40% như kế hoạch gốc thì trần là 177,5 tỷ. Tức là phải giảm 8,9 tỷ" in talk
    assert "Để giảm, anh Tuấn đề nghị giãn tuyển 30 vị trí bán hàng sang quý 3." in talk
    gap = talk.index("Còn lại khoảng 5,0 tỷ")
    assert talk[gap:].split(". ", 1)[1].startswith("Phần này anh chị có thể hỏi anh Tuấn ngay sau phần em trình bày.")
    pytest.importorskip("playwright")              # B draws its table with a script: told from the page only once rendered
    from present import ve_trang
    b = build(drawn_report)
    said_b = [c for c in lines(b) if c.get("bat", "có") == "có"]
    assert any(c.get("vai") == "hoi" for c in said_b), "B was not drawn: " + ve_trang.WHY_NOT
    tb = " ".join(c["noi"] for c in said_b)
    assert tb.count("19,0") == 1 and "chưa bằng một nửa tốc độ tăng của chi phí" in tb
    vai = [c.get("vai") for c in said_b]
    assert vai.index("neu_hut") == max(k for k, v in enumerate(vai) if v == "hoi") + 1   # the stakes right after the asks


def _ghi_ok(pk, r, want):
    # one comparison for every recorder test: "?" = asked back, never recorded; on B the cap 120,0 is still being checked,
    # so a plain yes on ask 1 waits for one more press (round 14) and the reply offers «… chờ mức chốt»
    letter = {"Đồng ý": "Y", "Không đồng ý": "N", "Cần bàn thêm": "D", "Đồng ý có điều kiện": "C"}
    got = {k: letter.get(v[0], v[0]) for k, v in r["rec"].items()}
    want = {str(k): v for k, v in (want or {}).items()}
    hold = pk == "B" and want.get("0") == "Y"
    exp = {k: v for k, v in want.items() if v != "?" and not (hold and k == "0")}
    return got == exp and ("?" not in want.values() or "ạ?" in r["a"]) and (not hold or "chờ mức chốt" in r["a"])


def _ghi_pages(report, drawn_report, **viewport):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from present import kiem_hinh
    a, b = build(report), build(drawn_report)
    pw = sync_playwright().start()
    try:
        browser = kiem_hinh._launch(pw)
    except RuntimeError as exc:
        pw.stop()
        pytest.skip(str(exc))
    pages = {}
    for pk, out in (("A", a["out"]), ("B", b["out"])):
        pg = browser.new_page(viewport=viewport or {"width": 1366, "height": 768})
        pg.goto(Path(out).as_uri())
        pg.wait_for_timeout(700)
        pages[pk] = pg
    return pw, browser, pages


def test_round_thirteen_put_off_tails_no_comma_clauses_and_money_words_record_what_was_said(report, drawn_report):
    # round-13 panel: «Đồng ý hết, trừ kho mới để quý sau» inverted, «Trần ok giãn tuyển chưa» (no comma) was one clause,
    # «còn lại 5 tỷ» was read as the rest of the asks; each axis is generated in both orders for both packs
    alias, num = {"A": ["trần", "giãn tuyển"], "B": ["trần", "logistics", "quỹ lương", "kho mới"]}, ["một", "hai", "ba", "bốn"]
    forms = [("Y", "đồng ý {}"), ("Y", "{} thì được"), ("N", "không đồng ý {}"), ("N", "{} thì không"), ("D", "tạm treo {}"), ("D", "{} để bàn thêm")]
    defer = ["để quý sau", "để tuần sau họp lại", "chờ anh Tuấn kiểm lại", "để bàn thêm", "để sang tháng sau"]
    ex = ["Đồng ý hết, trừ {x} {d}", "Duyệt tất cả, riêng {x} {d}", "Trừ {x} {d}, còn lại đồng ý", "Đồng ý cả {n} việc, riêng {x} thì {d}"]
    cases = []
    for pk, names in alias.items():
        m = len(names)
        for k in range(m):
            for d, dv in enumerate(defer):
                for e, form in enumerate(ex):
                    ref = [names[k], "việc " + num[k]][(k + d + e) % 2]
                    q = form.format(x=ref, d=dv, n=["", "", "hai", "ba", "bốn"][m])
                    cases.append((pk, q, {str(j): "D" if j == k else "Y" for j in range(m)}))
        for i in range(m):
            for j in range(m):
                for v1, f1 in forms:
                    for v2, f2 in forms:
                        if i == j or (f1.endswith("không") and f2.startswith("đồng ý")):   # «trần thì không đồng ý logistics» reads both ways
                            continue
                        c1 = f1.format(names[i])
                        cases.append((pk, c1[:1].upper() + c1[1:] + " " + f2.format(names[j]), {str(i): v1, str(j): v2}))
        cases += [(pk, "Ok trần, còn lại 5 tỷ thì để anh Tuấn bù", {"0": "C"}), (pk, "Duyệt trần, phần còn lại anh Tuấn tự tính", {"0": "Y"}),
                  (pk, "Đồng ý trần, số còn lại 5 tỷ chờ phương án", {"0": "C"})]
    assert len(cases) == 616
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        bad = []
        for pk, q, want in cases:
            r = pages[pk].evaluate("q => window.__clickyGhi(q)", q)
            if not _ghi_ok(pk, r, want):
                bad.append((pk, q, r["rec"], want, r["a"][:100]))
        assert not bad, "\n".join(map(str, bad[:20]))
    finally:
        browser.close()
        pw.stop()


def test_round_thirteen_record_guards_tap_scope_places_with_two_asks_and_no_silent_overwrite(report, drawn_report):
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        ghi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyGhi(q, s)", [q, s])   # noqa: E731
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        # A's «Đề xuất: … trần 177,5 tỷ; giãn tuyển …» states both asks: a bare verdict asks which one; the cap tile is the cap
        for q in ("Đồng ý", "Ok"):
            r = ghi(A, q, "[data-clicky=c13]")
            assert r["rec"] == {} and r["a"].startswith("Chỗ này nói về 2 việc anh Tuấn xin quyết; anh chị đồng ý việc nào ạ?"), r
        assert ghi(A, "Đồng ý cả hai", "[data-clicky=c13]")["rec"] == {"0": ["Đồng ý", "", False], "1": ["Đồng ý", "", False]}
        assert ghi(A, "Đồng ý", "[data-clicky=c14]")["rec"] == {"0": ["Đồng ý", "", False]}
        assert ghi(A, "Không", "[data-clicky=c14]")["rec"] == {"0": ["Không đồng ý", "", False]}
        # a bare «chưa» on a tapped ask is asked back, never recorded as «Không»
        for q in ("Chưa", "Cái này thì chưa"):
            r = ghi(B, q, "[data-clicky=c10]")
            assert r["rec"] == {} and "ý anh chị là để bàn thêm, hay không đồng ý ạ?" in r["a"], r
        # Owner: ask 4 «Không», then ask 1 tapped + «Đồng ý nhưng chỉ 118,8» touches ask 1 only, and never offers the refused figure
        ghi(B, "Không", "[data-clicky=c11]")
        a = hoi(B, "Đồng ý nhưng chỉ 118,8", "[data-clicky=c8]")
        assert a.startswith("Đề nghị là «Duyệt trần chi phí vận hành 120,0 tỷ cho cả năm»; anh chị nói 118,8") and "Đồng ý như đề nghị" not in a, a
        got = B.evaluate("() => window.__clickyDaGhi()")
        assert got["3"] == ["Không đồng ý", "", False] and got["0"][0] == "Đồng ý có điều kiện" and set(got) == {"0", "3"}, got
        # a condition on a tapped ask is that ask's, with its words
        r = ghi(B, "Ok nếu Logistics giải trình trước ngày 15", "[data-clicky=c11]")
        assert r["rec"] == {"3": ["Đồng ý có điều kiện", "Ok nếu Logistics giải trình trước ngày 15", True]}, r
        # CFO: chosen ask by ask, then «Đồng ý hết» asks before changing them; a changed mind is said
        ghi(B, "Để bàn thêm", "[data-clicky=c8]")
        hoi(B, "Không", "[data-clicky=c9]")
        a = hoi(B, "Đồng ý hết")
        assert "Việc hai đang ghi «Không đồng ý»; anh chị đổi thành «Đồng ý» ạ?" in a and "Giữ như cũ" in a, a
        assert "Việc một: mức 120,0 anh Tuấn đang kiểm lại; anh chị đồng ý thế nào ạ?" in a, a   # the doubted cap: the two-button step (round 15)
        a = hoi(B, "Việc hai để bàn thêm")
        assert "Việc hai em đổi từ «Không đồng ý»" in a, a
        # «Không có ý kiến gì, cứ làm» records; «…, kho mới để anh Tuấn quyết» says kho mới is not recorded
        assert len(ghi(A, "Không có ý kiến gì, cứ làm")["rec"]) == 2
        r = ghi(B, "Trần thì không, kho mới để anh Tuấn quyết")
        assert r["rec"] == {"0": ["Không đồng ý", "", False]} and "Việc bốn anh chị chưa cho ý kiến" in r["a"], r
    finally:
        browser.close()
        pw.stop()


def test_round_thirteen_phone_sheet_keeps_every_ask_tappable_above_send(report, drawn_report):
    # round-13 Chair: with asks 1-3 chosen, ask 4 sat under the sticky «Gửi anh Tuấn» and a tap reached the report
    pw, browser, pages = _ghi_pages(report, drawn_report, width=390, height=664)
    try:
        for pk in ("A", "B"):
            res = pages[pk].evaluate("window.__clickyKiemDienThoai()")
            assert res["problems"] == [], (pk, res)
    finally:
        browser.close()
        pw.stop()


def test_round_fourteen_delegation_negated_blankets_free_reasons_counts_and_sequencing(report, drawn_report):
    # round-14 panel: «Trần để anh Tuấn quyết, còn lại đồng ý» approved the cap; «Không đồng ý hết» approved everything;
    # «riêng quỹ lương thì đội bán hàng cần giữ thưởng» agreed with «Giữ quỹ lương»; «hai việc kia» took three asks;
    # «Logistics giải trình trước rồi mới duyệt trần» was a firm yes on the cap
    alias, num = {"A": ["trần", "giãn tuyển"], "B": ["trần", "logistics", "quỹ lương", "kho mới"]}, ["", "một", "hai", "ba", "bốn"]
    deleg = ["{} để anh Tuấn quyết", "không có ý kiến về {}", "{} tùy anh Tuấn"]
    rest, rv = ["còn lại", "các việc khác", "mấy việc kia"], [("Y", "đồng ý"), ("N", "không đồng ý"), ("D", "để bàn thêm")]
    free = ["thì đội bán hàng cần giữ thưởng", "thì anh muốn hỏi thêm cách tính", "thì kho cũ còn chỗ", "thì Logistics nói chưa rõ", "thì nên hoãn tuyển thêm"]
    cap = lambda t: t[:1].upper() + t[1:]   # noqa: E731
    cases = []
    for pk, names in alias.items():
        m = len(names)
        for k in range(m):
            for d in deleg:
                for rw in rest:
                    for lv, vw in rv:
                        want = {j: lv for j in range(m) if j != k}
                        cases += [(pk, cap(d.format(names[k])) + ", " + rw + " " + vw, want), (pk, cap(rw) + " " + vw + ", " + d.format(names[k]), want)]
        for ng in ("không", "chưa"):
            for vb in ("đồng ý", "duyệt"):
                for aw in ("hết", "tất cả", "cả " + num[m], "việc nào"):
                    cases.append((pk, cap(ng + " " + vb + " " + aw), {j: "N" for j in range(m)}))
        for k in range(m):
            for fr in free:
                for lead in ("Đồng ý hết, riêng ", "Duyệt tất cả, riêng "):
                    cases.append((pk, lead + names[k] + " " + fr, {j: "?" if j == k else "Y" for j in range(m)}))
            for nn in range(1, m + 1):
                right = nn == m - 1
                cases.append((pk, cap(names[k]) + " thì không, " + num[nn] + " việc kia đồng ý", {j: "N" if j == k else "Y" if right else "?" for j in range(m)}))
        for i in range(m):
            for j in range(m):
                if i != j:
                    cases += [(pk, cap(names[i]) + " xong rồi mới duyệt " + names[j], {i: "Y", j: "C"}),
                              (pk, "Duyệt " + names[j] + " sau khi " + names[i] + " xong", {i: "Y", j: "C"})]
    assert len(cases) == 464
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        bad = [(pk, q, r["rec"], want, r["a"][:100]) for pk, q, want in cases
               for r in [pages[pk].evaluate("q => window.__clickyGhi(q)", q)] if not _ghi_ok(pk, r, want)]
        assert not bad, "\n".join(map(str, bad[:20]))
    finally:
        browser.close()
        pw.stop()


def test_round_fourteen_doubted_cap_bare_words_on_the_card_and_next_turn_corrections(report, drawn_report):
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        SR = "document.getElementById('clicky-host').shadowRoot"
        ghi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyGhi(q, s)", [q, s])   # noqa: E731
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        rec = lambda pg: pg.evaluate("() => window.__clickyDaGhi()")   # noqa: E731
        press = lambda pg, t: pg.evaluate("([SR, t]) => { const b = [...eval(SR).querySelectorAll('#a button, #ba button')].filter(b => b.textContent === t && !b.disabled).pop(); if (b) b.click(); return !!b; }", [SR, t])   # noqa: E731
        # B's cap 120,0 is still being checked: no path records a plain yes on it in one step
        for q, s in (("Ok", "[data-clicky=c8]"), ("Đồng ý việc một", None), ("Duyệt 120", None), ("Ok cả 4", None)):
            r = ghi(B, q, s)
            assert "0" not in r["rec"] and "Việc một: mức 120,0 anh Tuấn đang kiểm lại; anh chị đồng ý thế nào ạ?" in r["a"], (q, r)
            assert "Đồng ý mức 120,0" not in r["a"]   # the owner has not ruled on a firm yes while the figure is checked
        assert press(B, "Đồng ý về nguyên tắc, chờ mức chốt")
        assert rec(B)["0"] == ["Đồng ý có điều kiện", "Ok cả 4 — đồng ý về nguyên tắc, chờ mức anh Tuấn chốt sau kiểm lại", False] or \
            rec(B)["0"][1].endswith("đồng ý về nguyên tắc, chờ mức anh Tuấn chốt sau kiểm lại"), rec(B)
        # the card's own «Đồng ý» on ask 1 asks the same step
        ghi(B, "Cảm ơn")
        B.evaluate("() => window.__clickyHetBai()")
        row = B.evaluate(f"() => {{ const r = {SR}.querySelector('[data-quyet=\"0\"]'); [...r.querySelectorAll('button')].find(x => x.textContent === 'Đồng ý').click(); return r.innerText; }}")
        assert "chờ mức chốt" in row and "0" not in rec(B), row
        # with the card open a bare «Ok» or «Chưa» is a decision, never «Dạ.» and never mailed
        B.evaluate("() => window.__clickyHetBai()")
        a = hoi(B, "Ok")
        assert a.startswith("Anh chị đồng ý việc nào ạ?") and "Cả 4 việc" in a, a
        B.evaluate("() => window.__clickyHetBai()")
        a = hoi(B, "Chưa")
        assert a.startswith("Anh chị nói «Chưa»: ý anh chị là để bàn thêm, hay không đồng ý ạ?"), a
        # «À không, để bàn thêm» right after «Đồng ý hết» is about the asks just recorded
        ghi(B, "Đồng ý hết")
        a = hoi(B, "À không, để bàn thêm")
        assert a.startswith("Em đổi việc hai, việc ba, việc bốn thành «Cần bàn thêm» ạ?"), a
        assert press(B, "Đổi") and {k: v[0] for k, v in rec(B).items()} == {"1": "Cần bàn thêm", "2": "Cần bàn thêm", "3": "Cần bàn thêm"}
        # a pending «Ok nếu …» is a choice: «Đồng ý hết» asks before dropping its condition
        ghi(B, "Ok nếu Logistics giải trình trước ngày 15", "[data-clicky=c9]")
        a = hoi(B, "Đồng ý hết")
        assert "Việc hai đang ghi «Đồng ý có điều kiện»; anh chị đổi thành «Đồng ý» ạ?" in a and rec(B)["1"][1] == "Ok nếu Logistics giải trình trước ngày 15", a
        # A: «177,5 hợp lý» names the cap; on c13 «Đồng ý nhưng chỉ 6 tháng» keeps its words in the chosen ask
        assert ghi(A, "177,5 hợp lý")["rec"] == {"0": ["Đồng ý", "", False]}
        ghi(A, "Đồng ý nhưng chỉ 6 tháng", "[data-clicky=c13]")
        assert press(A, "Giãn tuyển 30 vị trí bán hàng sang quý 3 và…")
        assert rec(A) == {"1": ["Đồng ý có điều kiện", "Đồng ý nhưng chỉ 6 tháng", True]}, rec(A)
    finally:
        browser.close()
        pw.stop()



def test_round_fourteen_frames_hold_the_figure_said_and_the_end_card_shows_every_ask(report, drawn_report):
    pytest.importorskip("playwright")              # B's table is drawn by its script: read from the page as drawn
    # "vượt trần 8,9 tỷ" on a tile "Cần giảm so với AOPR 8,9 tỷ" is one fact; "tăng 8,9 tỷ" on it is still refused
    tile = clicky_html.figures("Cần giảm so với AOPR 8,9 tỷ")
    over = next(f for f in clicky_html.figures("Nếu giữ nguyên, chi phí vượt trần đề xuất 8,9 tỷ.") if f.tok == "8,9")
    up = next(f for f in clicky_html.figures("Chi phí tăng 8,9 tỷ.") if f.tok == "8,9")
    assert clicky_html.check_figure(over, tile, False, "tỷ") is None
    assert clicky_html.check_figure(up, tile, False, "tỷ")
    repA, repB = build(report), build(drawn_report)
    outA, outB = Path(repA["out"]), Path(repB["out"])
    # the stakes line frames the tile that prints 8,9 (it sat on the 2,52% tile for four rounds)
    stake = next(c for c in lines(repA) if c.get("vai") == "mo_rui_ro")
    assert stake["chi"] == next(c for c in lines(repA) if c["noi"].startswith("Tức là phải giảm 8,9"))["chi"]
    # B's why line ("Vì doanh thu chỉ tăng 8,0%") stays on the total: the note printing 8,0% holds the doubted 18,0%
    why = next(c for c in lines(repB) if c["noi"].startswith("Vì doanh thu chỉ tăng 8,0%"))
    assert why["chi"] == next(c for c in lines(repB) if c.get("vai") == "quy_mo")["chi"] and why["xac_nhan"] == ["8,0"]
    # every line of the talk, played at 1366x768 and 911x512: the frame holds the figure said, the hat sits on no
    # words, a framed row shows its table header
    for out in (outA, outB):
        res = kiem_hinh.check(str(out), sizes=((1366, 768), (911, 512)), phone=None)
        if res["skipped"]:
            pytest.skip(res["reason"])
        assert res["ok"], res["problems"]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = kiem_hinh._launch(pw)
        try:
            for out, ex in ((outA, "Ví dụ: Khối Bán hàng tăng vì sao?"), (outB, "Ví dụ: Logistics tăng vì sao?")):
                pg = browser.new_page(viewport={"width": 1366, "height": 768})
                pg.goto(out.as_uri())
                pg.wait_for_function("typeof window.__clickyHetBai === 'function'")
                assert pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('q').placeholder") == ex
                pg.evaluate("window.__clickyHetBai()")
                card = pg.evaluate("""() => { const R = document.getElementById('clicky-host').shadowRoot, p = R.getElementById('panel'), q = p.getBoundingClientRect();
                    return {rows: [...R.querySelectorAll('.decrow')].map(r => { const z = r.getBoundingClientRect(); return z.top >= q.top - 1 && z.bottom <= q.bottom + 1; }),
                            scroll: p.scrollHeight - p.clientHeight, help: [...R.querySelectorAll('#panel .hint, .dec>.help')].some(h => h.getClientRects().length)}; }""")
                assert all(card["rows"]) and card["scroll"] <= 2 and not card["help"], card   # every ask at once, no help text
                pg.close()
        finally:
            browser.close()


def test_round_fifteen_typed_answers_press_the_prompt_undo_asks_back_and_a_question_is_split_from_its_decision(report, drawn_report):
    # round-15 panel: «Ghi nguyên văn» typed in the box was read as a new decision; «À không» after a record was lost;
    # «Ừ» with the card open said only «Dạ»; a sentence with a question and a decision dropped one of them;
    # «Bỏ Logistics ra» framed the driver's row, and a sentence that points at nothing kept the frame before it
    for k, src in enumerate((report, drawn_report)):
        if k:
            pytest.importorskip("playwright")   # the drawn report is told only from the page as drawn
        said = lines(build(src))
        rest = next(c for c in said if c.get("vai") == "phan_con_lai")
        assert rest["chi"] == next(c for c in said if c.get("vai") == "quy_mo")["chi"], rest   # the rest is the total, not the row taken out
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        ghi = lambda pg, q: pg.evaluate("q => window.__clickyGhi(q, null)", q)   # noqa: E731
        rec = lambda pg: pg.evaluate("() => window.__clickyDaGhi()")   # noqa: E731
        fresh = lambda pg: (pg.reload(), pg.wait_for_timeout(700))   # noqa: E731
        COND = "Đồng ý có điều kiện"
        # a figure of their own waits; typing the button's words (or a plain «Ừ») presses it
        for typed in ("Ghi nguyên văn", "Ừ"):
            fresh(A)
            assert rec(A) == {} and "Ghi nguyên văn" in ghi(A, "Đồng ý 170 cho trần")["a"]
            assert rec(A)["0"][2] is True   # pending until the reader chooses
            assert hoi(A, typed).startswith("Em đã ghi «" + COND + "» kèm nguyên văn")
            assert rec(A)["0"] == [COND, "Đồng ý 170 cho trần", False]
        # «À không» after a record asks it back; the answer typed then is the button pressed
        fresh(A)
        ghi(A, "Đồng ý trần")
        assert rec(A)["0"] == ["Đồng ý", "", False]
        assert hoi(A, "À không").startswith("Em bỏ «Đồng ý» việc một ạ?")
        assert rec(A)["0"][0] == "Đồng ý"   # nothing changes until the reader picks
        hoi(A, "Không đồng ý")
        assert rec(A)["0"][0] == "Không đồng ý"
        # «À không» while a prompt waits drops the prompt and records nothing more
        fresh(A)
        ghi(A, "Đồng ý 170 cho trần")
        hoi(A, "Ghi nguyên văn")
        assert hoi(A, "À không").startswith("Em bỏ «" + COND + "» việc một ạ?")
        # with the card open, «Ừ» and «Không» on two asks ask which one; never a bare «Dạ», never a guess
        for word, verb in (("Ừ", "đồng ý"), ("Không", "không đồng ý")):
            fresh(A)
            A.evaluate("() => window.__clickyHetBai(false)")
            assert hoi(A, word).startswith("Anh chị " + verb + " việc nào ạ?") and rec(A) == {}
        # a question and a decision in one sentence: the decision first, then the question, quoted
        r = ghi(B, "Hoãn kho mới thì đội bán hàng giao hàng có chậm không? Mấy việc kia đồng ý")
        assert r["a"].startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý».") and "3" not in r["rec"] and "0" not in r["rec"]
        assert "Còn câu «Hoãn kho mới thì đội bán hàng giao hàng có chậm không?»: " in r["a"]
        # «Nếu X thì Y»: X is a plain yes, Y waits on it
        r = ghi(B, "Nếu Logistics giải trình xong thì duyệt trần")
        assert r["rec"]["1"][0] == "Đồng ý" and r["rec"]["0"][0] == COND, r["rec"]
        # a condition on the doubted cap is never one tap from «Đồng ý như đề nghị»
        r = ghi(B, "Duyệt trần 120 nhưng phải có kế hoạch cắt giảm chi tiết")
        assert r["rec"]["0"][0] == COND and "như đề nghị" not in r["a"], r
        # a sentence that points at nothing lets the page go: the frame before it is gone 0,15 s before it starts
        fresh(A)
        k = next(i for i in range(40) if A.evaluate("i => window.__clickyLuc(i, 0)", i)["bare"])
        s = A.evaluate("i => window.__clickyLuc(i, 0)", k)
        b0 = s["starts"][s["bare"][0]]
        last = max(t for j, t in s["points"] if t < b0)
        assert A.evaluate("([i, t]) => window.__clickyLuc(i, t)", [k, last + 0.1])["frames"] >= 1
        assert A.evaluate("([i, t]) => window.__clickyLuc(i, t)", [k, b0 - 0.1])["frames"] == 0
    finally:
        browser.close()
        pw.stop()


def test_round_sixteen_scope_wait_words_kept_words_and_the_end_card_bar(report, drawn_report):
    # round-16 panel: «Ngoài việc hai ra thì không» recorded ask 2 Không; «đồng ý cho anh Tuấn» read «cho» as «chờ» (and
    # «đội tôi» as «đợi tôi»); «những việc khác thì được» was read as «nhưng …»; a held yes on the doubted cap and a
    # counter-figure lost the reader's words; «Duyệt trần thì chưa» opened the yes step; the bar kept its buttons and the
    # talk's last half-sentence while the end card was open
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        SR = "document.getElementById('clicky-host').shadowRoot"
        ghi = lambda pg, q: pg.evaluate("q => window.__clickyGhi(q, null)", q)   # noqa: E731
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        thu = lambda pg: pg.evaluate("() => window.__clickyThu()")   # noqa: E731
        fresh = lambda pg: (pg.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"), pg.reload(), pg.wait_for_timeout(700))   # noqa: E731
        # metamorphic: the excepted ask never takes the verdict said about the rest, in any word order
        for k in range(4):
            n = ["một", "hai", "ba", "bốn"][k]
            for q, v in ((f"Ngoài việc {n} ra thì không đồng ý", "Không đồng ý"), (f"Trừ việc {n} ra thì không", "Không đồng ý"),
                         (f"Ngoài việc {n} ra thì đồng ý", "Đồng ý"), (f"Không đồng ý việc nào ngoài việc {n}", "Không đồng ý")):
                r = ghi(B, q)
                assert r["rec"].get(str(k), ["?"])[0] != v and all(r["rec"][str(j)][0] == v for j in range(4) if j != k and (j or v != "Đồng ý")), (q, r["rec"])
        # «cho» (for), «đội» (team) are never a wait; «chờ / đợi» still are
        for q in ("Đồng ý cho anh Tuấn làm cả bốn việc", "Còn lại em cứ ghi đồng ý cho anh"):
            assert all(x[0] == "Đồng ý" for x in ghi(B, q)["rec"].values()), q
        assert ghi(B, "Hoãn kho mới thì đội tôi chết, còn lại ok")["rec"]["3"][0] == "Không đồng ý"
        assert ghi(A, "Trần ok, giãn tuyển thì đợi tôi họp với đội bán hàng đã")["rec"] == {"0": ["Đồng ý", "", False], "1": ["Cần bàn thêm", "giãn tuyển thì đợi tôi họp với đội bán hàng đã", False]}
        assert ghi(B, "Kho mới để chờ anh Tuấn kiểm lại")["rec"]["3"][0] == "Cần bàn thêm"
        # «chưa» on the doubted cap asks «bàn thêm hay không đồng ý», never the yes step
        r = ghi(B, "Duyệt trần thì chưa")
        assert r["rec"] == {} and "ý anh chị là để bàn thêm, hay không đồng ý ạ?" in r["a"] and "chờ mức chốt" not in r["a"], r
        # the reader's own words reach the mail: a held yes (unconfirmed), a counter-figure, a condition said before the step
        fresh(B)
        hoi(B, "Duyệt trần 120 tỷ luôn, tôi chịu trách nhiệm")
        assert "1. «Duyệt trần chi phí vận hành 120,0 tỷ cho cả năm»: chưa cho ý kiến — anh chị nói: «Duyệt trần 120 tỷ luôn, tôi chịu trách nhiệm» (chưa xác nhận)" in thu(B)
        fresh(B)
        hoi(B, "Không đồng ý trần, đề nghị 118,8")
        assert "Không đồng ý — anh chị ghi: «Không đồng ý trần, đề nghị 118,8»" in thu(B)
        fresh(B)
        hoi(B, "Đồng ý trần kèm điều kiện báo cáo hàng quý")
        hoi(B, "Đồng ý hết")
        assert B.evaluate("([SR, t]) => { const b = [...eval(SR).querySelectorAll('#a button')].filter(b => b.textContent === t && !b.disabled).pop(); if (b) b.click(); return !!b; }",
                          [SR, "Đồng ý về nguyên tắc, chờ mức chốt"])
        assert "kèm điều kiện báo cáo hàng quý; đồng ý về nguyên tắc" in thu(B)
        # a nod after a partial record asks about the asks left, never «Dạ.»
        fresh(B)
        hoi(B, "Đồng ý việc hai")
        assert hoi(B, "Ok").startswith("Còn 3 việc; anh chị đồng ý những việc nào ạ?")
        # the end card: the bar keeps only ▶ and «Hỏi em», and its caption says what is asked
        fresh(B)
        B.evaluate("() => window.__clickyHetBai(false)")
        bar = B.evaluate(f"""(() => {{ const r = {SR}; return {{shown: [...r.querySelectorAll('#bar button')].filter(b => b.getClientRects().length).map(b => b.id),
            cap: r.getElementById('cap').textContent}}; }})()""")
        assert bar["shown"] == ["play", "askb"] and bar["cap"] == "Anh Tuấn xin anh chị quyết bốn việc.", bar
    finally:
        browser.close()
        pw.stop()


def test_round_sixteen_board_questions_are_answered_from_the_talk_and_worked_caps(report, drawn_report):
    # round-16 panel: «Rủi ro lớn nhất là gì?», «Vậy tôi duyệt hay không?», «sơ bộ hay chính thức?» were queued or quoted the
    # wrong line; «Nói lại đoạn 8,9 tỷ» replayed the greeting; «Duyệt trần 118,8 thì phải giảm bao nhiêu?» answered 9,0;
    # a worked 6,4 drew the note of the doubted 6,85%
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        for q in ("Rủi ro lớn nhất là gì?", "Điều quan trọng nhất là gì?"):
            assert hoi(A, q).startswith("Nếu giữ nguyên, chi phí vượt trần đề xuất 8,9 tỷ. Còn lại khoảng 5,0 tỷ, báo cáo chưa nêu cách bù."), q
        assert hoi(B, "Rủi ro lớn nhất là gì?").startswith("Nếu doanh thu chỉ đạt 90% kế hoạch, tỷ lệ chi phí trên doanh thu lên 6,4%.")
        x = hoi(A, "Vậy tôi duyệt hay không?")
        assert x.startswith("Anh Tuấn đề nghị: ") and "Em không quyết thay anh chị" in x
        assert hoi(A, "Bản này sơ bộ hay chính thức?").startswith("Bản này SƠ BỘ: kịch bản anh Tuấn chưa duyệt.")
        x = hoi(A, "Em nói lại đoạn 8,9 tỷ được không?")
        assert "8,9 tỷ" in x and "trợ lý AI" not in x, x
        assert hoi(A, "Nói lại đoạn 5 tỷ").startswith("Còn lại khoảng 5,0 tỷ, báo cáo chưa nêu cách bù.")
        x = hoi(B, "Em nói lại đoạn Logistics được không?")
        assert "12,0 tỷ" in x and "trợ lý AI" not in x, x
        x = hoi(B, "Duyệt trần 118,8 thì phải giảm bao nhiêu?")
        assert x.startswith("Em tính: với trần 118,8 tỷ, phải giảm 10,2 tỷ so với kế hoạch điều chỉnh (129,0 − 118,8 = 10,2).") and "giảm 9,0" not in x, x
        x = hoi(A, "Trần 180 thì phải giảm bao nhiêu?")
        assert x.startswith("Em tính: với trần 180,0 tỷ, phải giảm 6,4 tỷ") and "6,7" not in x, x
        assert "Có 2 chỗ anh Tuấn đang kiểm lại" in hoi(B, "Có số nào đang nghi ngờ không?")
    finally:
        browser.close()
        pw.stop()
