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
        "Thưa anh chị, em là Vera, trợ lý AI của anh Tuấn.", "Anh Tuấn xin anh chị quyết hai việc.",
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
        "Anh chị muốn quyết điều gì hôm nay ạ? Anh chị bấm chọn trên thẻ, em ghi lại gửi anh Tuấn."]   # gate 1, item 19
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
    gapline = next(c for c in lines(rep) if c["noi"].startswith("Vậy giãn 30 vị trí tiết kiệm được bao nhiêu? Báo cáo chưa ghi số này."))
    assert "bat" not in gapline
    bound = next(c for c in lines(rep) if c.get("vai") == "uoc_tiet_kiem")
    assert "khoảng 3,9 tỷ" in bound["noi"] and "4 tỷ" not in bound["noi"]   # 30 × 92,3 / 712 = 3,89, never rounded up to 4
    assert bound["noi"] == ("Em tính theo chi phí bình quân một người ở khối Bán hàng: kể cả giãn trọn năm, 30 vị trí "
                            "cũng chỉ tiết kiệm khoảng 3,9 tỷ, chưa bằng một nửa mức cần giảm.")   # 8,9 said twice at most (round 12)
    assert bound["so_tinh"] == ["3,9 = 30 × 92,3 / 712"] and bound["bat"] == "có" and bound["cho_khac"] == ["8,9"]
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
    assert [c["vai"] for c in stake] == ["mo_rui_ro"] and all(c["cho_khac"] == ["8,9"] for c in stake)   # round 12: once up front
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
    assert said[1]["noi"] == "Anh Tuấn xin anh chị quyết bốn việc." and [c["noi"] for c in said[2:7] if c.get("vai") == "hoi"] == asks
    # round 18: the doubted cap's spoken note and the bridge before the close are proposed, off until the owner switches them on;
    # round 21: the note comes after «Bốn là», so the list is heard whole
    nghi = said[6]
    assert nghi["noi"] == "Mức 120,0 anh Tuấn đang kiểm lại; giữ đúng tỷ lệ chi phí trên doanh thu của kế hoạch gốc thì trần là khoảng 118,8 tỷ." and nghi["bat"] == "chưa"
    assert nghi["so_tinh"] and nghi["so_tinh"][0].startswith("118,8 = ")
    bridge = next(c for c in said if c["noi"].startswith("Để kéo phần tăng này xuống"))
    assert bridge["noi"] == "Để kéo phần tăng này xuống, anh Tuấn đề nghị giữ quỹ lương bằng kế hoạch gốc và hoãn dự án kho mới." and bridge["bat"] == "chưa"
    close = [c["noi"] for c in said if c.get("vai") == "chot" and not c.get("thay")]
    # four asks: the close says a few words each, never the opening again word for word (round-6 Coach)
    assert close == ["Tóm lại, anh Tuấn xin anh chị quyết: một là duyệt trần 120,0 tỷ; hai là Logistics giải trình; "
                     "ba là giữ quỹ lương; bốn là hoãn dự án kho mới."]
    # round 21: while 120,0 is doubted, a close that never says it as firm is proposed; switched on, it replaces the close
    soft = next(c for c in said if c.get("thay") == "chot")
    assert soft["noi"] == ("Tóm lại, anh Tuấn xin anh chị quyết: một là duyệt trần ở mức anh Tuấn chốt sau khi kiểm lại; "
                           "hai là Logistics giải trình; ba là giữ quỹ lương; bốn là hoãn dự án kho mới.") and soft["bat"] == "chưa"
    # the table has no variance column: code works it out from the drawn rows; a group row ("Khối vận hành" adds up
    # Logistics and Mua hàng) is never the driver; the unit comes from the table's heading "(tỷ đồng)"
    driver = next(c for c in said if c.get("vai") == "dong_luc")
    assert driver["noi"] == "Logistics chiếm gần hai phần ba phần tăng: 12,0 tỷ, theo giải trình là do thuê kho." and driver["so_tinh"] == ["12,0 = 52,0 - 40,0", "63 = (52,0 - 40,0) / (129,0 - 110,0) × 100"]   # the share behind "gần hai phần ba"
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
               and c["cho_khac"] == ["8,0"] and c["chi"] == next(x for x in said if x.get("vai") == "quy_mo")["chi"]
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
    assert stakes["noi"].endswith("thay vì 2,40% như kế hoạch gốc.") and not stakes.get("cho_khac")


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
        assert ask("Đồng ý hết trừ việc một và việc bốn").startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý». Việc một, bốn anh chị trừ ra, em chưa ghi: anh chị không đồng ý hay cần bàn thêm ạ?")
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
    assert len(script["kiem_tra"]) == len(rep["phan_bien"]) == 5   # 4 findings, and the figures found elsewhere (TB11)
    assert "em lấy ở chỗ khác" in script["kiem_tra"][-1]["muc"]
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
    assert bound["cho_khac"] == ["8,9"]
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
    assert "3,9 tỷ" in said and "Vậy giãn 30 vị trí tiết kiệm được bao nhiêu?" in said


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
        assert x.startswith("Em đã ghi «Đồng ý» cho việc: «Giãn tuyển") and "Việc một anh chị trừ ra, em chưa ghi: anh chị không đồng ý hay cần bàn thêm ạ?" in x, x
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
        assert ask("Đồng ý tất cả trừ việc 1 và 4").startswith("Em đã ghi việc hai «Đồng ý»; việc ba «Đồng ý». Việc một, bốn anh chị trừ ra, em chưa ghi: anh chị không đồng ý hay cần bàn thêm ạ?")
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
    assert "Báo cáo ghi lý do là kế hoạch tuyển thêm 48 nhân viên bán hàng kênh MT và HRC+KA." in talk
    assert "Giữ 2,40% như kế hoạch gốc thì trần là 177,5 tỷ. Tức là phải giảm 8,9 tỷ" in talk
    assert "Để giảm, anh Tuấn đề nghị giãn tuyển 30 vị trí bán hàng sang quý 3." in talk
    gap = talk.index("Còn lại khoảng 5,0 tỷ")
    assert talk[gap:].split(". ", 1)[1].startswith("Tóm lại")   # round 18: «Phần này anh chị có thể hỏi…» is proposed only (the talk ran long)
    assert next(c for c in lines(a) if c["noi"].startswith("Phần này anh chị có thể hỏi"))["bat"] == "chưa"
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
    assert why["chi"] == next(c for c in lines(repB) if c.get("vai") == "quy_mo")["chi"] and why["cho_khac"] == ["8,0"]
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


def test_round_seventeen_an_asks_own_verb_said_the_other_way_is_never_a_yes_and_the_mail_quotes_only_typed_words(report, drawn_report):
    # round-17 panel: «Kho mới cứ làm tiếp», «khỏi hoãn», «làm luôn năm nay» were recorded as a yes on «Hoãn dự án kho mới»;
    # «Đừng giãn tuyển» was a yes; «rồi mới tính kho mới» inherited the yes; «mấy việc kia chưa» took the sibling's yes; the mail
    # quoted «Đồng ý hết» (a rewrite) as the reader's words; «Trần 180 thôi» reached the owner with no words at all
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        ghi = lambda pg, q: pg.evaluate("q => window.__clickyGhi(q, null)", q)   # noqa: E731
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        thu = lambda pg: pg.evaluate("() => window.__clickyThu()")   # noqa: E731
        fresh = lambda pg: (pg.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"), pg.reload(), pg.wait_for_timeout(700))   # noqa: E731
        # metamorphic: the stop ask «Hoãn dự án kho mới» said the other way is never a yes, alone or beside a blanket yes
        for neg in ("Kho mới cứ làm tiếp", "Kho mới khỏi hoãn", "Kho mới thì khỏi hoãn", "Kho mới thì làm luôn năm nay",
                    "Kho mới cứ triển khai ngay", "Đừng hoãn kho mới", "Kho mới thì khó lắm", "Riêng kho mới thì đội tôi không chịu được"):
            for q in (neg, neg + ", mấy việc kia duyệt", neg + ", còn lại ok", "Đồng ý hết, " + neg[0].lower() + neg[1:]):
                r = ghi(B, q)
                assert r["rec"].get("3", ["?"])[0] != "Đồng ý", (q, r["rec"])
        for q in ("Đừng giãn tuyển", "Giãn tuyển thì khó"):
            assert ghi(A, q)["rec"]["1"][0] == "Không đồng ý", q
        # the ask's own verb, or naming it by number, is still a yes
        for q in ("Hoãn kho mới thì được", "Việc bốn thì cứ làm"):
            assert ghi(B, q)["rec"]["3"][0] == "Đồng ý", q
        assert ghi(A, "Giãn tuyển cứ làm")["rec"]["1"][0] == "Đồng ý"
        # «rồi mới tính», «để anh Tuấn tính thêm», «cần thêm số liệu», «xem sau»: put off, never the sibling's yes
        assert {k: v[0] for k, v in ghi(B, "Đồng ý Logistics rồi mới tính kho mới")["rec"].items()} == {"1": "Đồng ý", "3": "Cần bàn thêm"}
        for q in ("Trần đồng ý, giãn tuyển để anh Tuấn tính thêm đã", "Giãn tuyển cần thêm số liệu, trần thì được", "Giãn tuyển xem sau, trần ok"):
            assert {k: v[0] for k, v in ghi(A, q)["rec"].items()} == {"0": "Đồng ý", "1": "Cần bàn thêm"}, q
        # «S được, C chưa»: the rest are asked back in one line, never the sibling's yes and never a no
        r = ghi(B, "Logistics được, mấy việc kia chưa")
        assert {k: v[0] for k, v in r["rec"].items()} == {"1": "Đồng ý"} and "Việc một, ba, bốn anh chị nói «mấy việc kia chưa»" in r["a"], r
        r = ghi(B, "Kho mới chưa được")
        assert r["rec"] == {} and "ý anh chị là để bàn thêm, hay không đồng ý ạ?" in r["a"], r
        # the mail quotes only what the reader typed: never the rewritten «Đồng ý hết»
        fresh(B)
        hoi(B, "Nếu không ai có ý kiến gì thì coi như thông qua")
        mail = thu(B)
        assert "anh chị nói: «Đồng ý hết»" not in mail and "anh chị đồng ý chung (câu: «Nếu không ai có ý kiến gì thì coi như thông qua»)" in mail, mail
        # an ask-back keeps the reader's words until a button replaces them
        fresh(B)
        hoi(B, "Logistics được, mấy việc kia chưa")
        mail = thu(B)
        assert mail.count("chưa cho ý kiến — anh chị nói: «mấy việc kia chưa» (chưa xác nhận)") == 3, mail
        fresh(A)
        r = hoi(A, "Trần 180 thôi")
        assert r.startswith("Việc một anh chị nói «Trần 180 thôi»"), r
        assert "chưa cho ý kiến — anh chị nói: «Trần 180 thôi» (chưa xác nhận)" in thu(A)
    finally:
        browser.close()
        pw.stop()


def test_round_seventeen_consent_words_row_claims_and_board_questions(report, drawn_report):
    # round-17 panel: «Dạ, cứ thế», «Tôi ủng hộ anh Tuấn», «Mọi người đồng ý chứ? Ok vậy duyệt» were mailed as questions;
    # «Logistics tăng 15 tỷ đúng không?» was answered with Hành chính's 15,0; «Nói lại đoạn 7 tỷ» replayed the greeting;
    # «Nếu chúng tôi không duyệt thì sao?» and «Có phải sa thải không?» were queued; B's «Quan trọng nhất» gave only the 90% line
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        thu = lambda pg: pg.evaluate("() => window.__clickyThu()")   # noqa: E731
        fresh = lambda pg: (pg.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"), pg.reload(), pg.wait_for_timeout(700))   # noqa: E731
        for q in ("Dạ, cứ thế", "Cứ theo anh Tuấn", "Theo đề xuất của anh Tuấn", "Tôi ủng hộ anh Tuấn", "Tôi không phản đối việc nào",
                  "Không có lý do gì để không duyệt", "Mọi người đồng ý chứ? Ok vậy duyệt"):
            fresh(B)
            hoi(B, q)
            mail = thu(B)
            assert "CÂU HỎI" not in mail and "4. «Hoãn dự án kho mới sang năm sau»: Đồng ý" in mail, (q, mail)
            assert f"anh chị đồng ý chung (câu: «{q}»)" in mail, (q, mail)
        fresh(A)
        assert hoi(A, "Cứ tuyển đi").startswith("Em đã ghi «Không đồng ý» cho việc: «Giãn tuyển")
        fresh(A)
        assert hoi(A, "Cứ giãn tuyển đi").startswith("Em đã ghi «Đồng ý» cho việc: «Giãn tuyển")
        # a claim about a named row is checked on that row only
        x = hoi(B, "Logistics tăng 15 tỷ đúng không?")
        assert x.startswith("Không ạ, dòng Logistics không có số 15 tỷ. Theo bảng, Logistics tăng 12,0 tỷ (em tính: 52,0 − 40,0), tức 30,0%") and "Hành chính" not in x, x
        assert hoi(B, "Logistics tăng 30% đúng không?").startswith("Đúng theo bảng: Logistics tăng 30,0%")
        x = hoi(A, "Bán hàng tăng 20 tỷ đúng không?")
        assert x.startswith("Không ạ") and "14,1" in x, x
        assert hoi(A, "Khối CNTT 12,0% đúng không?").startswith("Khối CNTT, % tăng: 12,0%.")
        # a replay of a figure the talk never says is said so, never the greeting
        x = hoi(B, "Nói lại đoạn 7 tỷ")
        assert x.startswith("Trong bài em không nói số 7 tỷ ạ.") and "trợ lý AI" not in x, x
        # everyday forms reach the answers the talk has
        for q in ("Nếu chúng tôi không duyệt thì sao?", "Không duyệt thì có sao không?"):
            assert hoi(B, q).startswith("Nếu không duyệt, chi phí theo AOPR là 129,0 tỷ"), q
        assert hoi(A, "Có phải sa thải không?").startswith("Báo cáo không ghi số người phải cắt.")
        x = hoi(B, "Quan trọng nhất là gì?")
        assert x.startswith("Theo kế hoạch điều chỉnh, tổng là 129,0 tỷ, cao hơn kế hoạch gốc 19,0 tỷ.") and "6,4%" not in x, x
        x = hoi(B, "Rủi ro lớn nhất là gì?")
        assert x.startswith("Nếu doanh thu chỉ đạt 90% kế hoạch") and "129,0" in x, x
        assert hoi(A, "Quan trọng nhất là gì?").startswith("Nếu giữ nguyên, chi phí vượt trần đề xuất 8,9 tỷ.")
    finally:
        browser.close()
        pw.stop()


def test_round_eighteen_row_claims_match_at_the_readers_precision_and_the_queue_keeps_typed_words(report, drawn_report):
    # round-18 panel: «Khối Bán hàng tăng 14 tỷ đúng không?» said «Không ạ» to 14,1; «Tổng tăng 19 tỷ» said «Không ạ … 19,0»;
    # «Logistics tăng 12 tỷ đúng không?» answered with the doubted 18,0 (12 was found inside 129,0) and was queued; A's
    # «Nếu chúng tôi không duyệt thì sao?» replayed the 2,52% line; «Có phải sa thải người không?» was mailed as «Cắt bao nhiêu người?»
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        thu = lambda pg: pg.evaluate("() => window.__clickyThu()")   # noqa: E731
        fresh = lambda pg: (pg.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"), pg.reload(), pg.wait_for_timeout(700))   # noqa: E731
        for pg, q, start, has in (
                (A, "Khối Bán hàng tăng 14 tỷ đúng không?", "Đúng ạ, khoảng 14 tỷ: Khối Bán hàng 14,1 tỷ", "Chênh lệch"),
                (A, "Tổng tăng 21 tỷ đúng không?", "Đúng ạ, khoảng 21 tỷ: Tổng 20,9 tỷ", "Chênh lệch"),
                (B, "Tổng tăng 19 tỷ đúng không?", "Đúng ạ: Tổng tăng 19,0 tỷ", "129,0 − 110,0"),
                (B, "Logistics tăng 12 tỷ đúng không?", "Đúng ạ: Logistics tăng 12,0 tỷ", "52,0 − 40,0"),
                (B, "Logistics tăng 12 tỷ à?", "Đúng ạ: Logistics tăng 12,0 tỷ", "52,0 − 40,0"),
                (B, "Mua hàng tăng 3 tỷ phải không?", "Đúng ạ: Mua hàng tăng 3,0 tỷ", "33,0 − 30,0"),
                (B, "Logistics tăng 10 tỷ phải không?", "Không ạ, dòng Logistics không có số 10 tỷ.", "12,0"),
                (A, "Bán hàng tăng 20 tỷ đúng không?", "Không ạ, dòng Khối Bán hàng không có số 20 tỷ.", "14,1")):
            fresh(pg)
            x = hoi(pg, q)
            assert x.startswith(start) and has in x and (pg is A or ("18,0" not in x and "17,3" not in x)), (q, x)   # B's 18,0 is doubted
            assert "CÂU HỎI" not in thu(pg) and "tôi có các câu hỏi" not in thu(pg), q
        # a doubted figure keeps its own answer
        assert "Anh Tuấn đang kiểm lại số 18,0%" in hoi(B, "Tổng tăng 18% đúng không?")
        # A's «không duyệt» says what a no means from the talk's own lines
        for q in ("Nếu chúng tôi không duyệt thì sao?", "Nếu không duyệt trần thì sao?"):
            x = hoi(A, q)
            assert x.startswith("Nếu không duyệt trần: theo kế hoạch điều chỉnh, tổng là 186,4 tỷ") and "8,9" in x and "177,5" in x, x
        # the queue keeps what the reader typed
        fresh(A)
        hoi(A, "Có phải sa thải người không?")
        mail = thu(A)
        assert "Có phải sa thải người không?" in mail and "Cắt bao nhiêu người?" not in mail, mail
    finally:
        browser.close()
        pw.stop()


def test_round_nineteen_a_disclosure_line_never_rules_a_doubted_figure():
    # round-19 panel: switching on «Mức 120,0 anh Tuấn đang kiểm lại …» marked the doubt done, so the page answered
    # as if the cap were checked; a disclosure tells the Board the figure is open, it never settles it
    tell = "Mức 120,0 anh Tuấn đang kiểm lại; giữ đúng tỷ lệ thì trần là khoảng 118,8 tỷ."
    plain = "Trần 120,0 tỷ giữ nguyên như báo cáo."
    item = {"ma_cau": "abc123", "ma_so": ["120,0"], "cau": [tell, plain]}
    script = {"canh": [{"cau": [{"noi": tell, "bat": "có"}, {"noi": plain, "bat": "chưa"}]}], "kiem_tra": [item]}
    assert clicky_html.doubts(script)[0]["xong"] is False
    assert clicky_html.doubted_open(script) == ["abc123"]
    script["canh"][0]["cau"][1]["bat"] = "có"       # a line that states the figure as the talk's own: the owner ruled
    assert clicky_html.doubts(script)[0]["xong"] is True
    assert clicky_html.doubted_open(script) == []


def test_round_nineteen_row_claims_judge_the_named_or_tapped_row_and_every_figure(report, drawn_report):
    # round-19 panel: after a tap, «Khối Bán hàng tăng 14 tỷ đúng không?» said «Không ạ» (the claim check was skipped) or
    # answered another row; «Tổng tăng 19 tỷ, tức 17%» was refused; «giảm», «triệu», «hơn» and «sai» swapped into a true
    # claim could still be confirmed; 2,5 was «Đúng ạ, khoảng 3»
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        sweep = {B: ("#t tbody tr:nth-of-type(%d) td:nth-of-type(3)", [("Khối vận hành", "15"), ("Logistics", "12"), ("Mua hàng", "3"),
                                                                       ("Khối hỗ trợ", "4"), ("CNTT", "2,5"), ("Hành chính", "1,5"), ("Tổng", "19")]),
                 A: ("table tbody tr:nth-of-type(%d) td:nth-of-type(4)", [("Khối Bán hàng", "14"), ("Khối Marketing", "1,5"),
                                                                          ("Khối Chuỗi cung ứng", "2"), ("Khối Tài chính - Kế toán", "0,6"),
                                                                          ("Khối Nhân sự - Hành chính", "0,7"), ("Khối CNTT", "2"), ("Tổng", "21")])}
        for pg, (sel, rows) in sweep.items():
            for i, (row, v) in enumerate(rows):
                for scope in (None, sel % (i + 1), sel % ((i + 3) % len(rows) + 1)):   # the panel, the same row tapped, another row tapped
                    for tail in ("đúng không?", "phải không?"):
                        x = hoi(pg, f"{row} tăng {v} tỷ {tail}", scope)
                        assert x.startswith(("Đúng", "Khối")) and "Không ạ" not in x and row.split(" - ")[0] in x, (row, scope, tail, x)
                        assert "khớp" in x or x.startswith("Đúng"), x
        for pg, q, scope, start in (
                (B, "Tổng tăng 19 tỷ, tức 17% đúng không?", None, "Đúng ạ: Tổng tăng 19,0 tỷ (em tính: 129,0 − 110,0), tức 17,3%"),
                (B, "Logistics tăng 12 tỷ và CNTT tăng 2,5 tỷ đúng không?", None, "Đúng ạ: Logistics tăng 12,0 tỷ"),
                (B, "Tăng 12 tỷ đúng không?", "#t tbody tr:nth-of-type(2) td:nth-of-type(3)", "Đúng ạ: Logistics tăng 12,0 tỷ"),
                (B, "CNTT tăng 2,5 tỷ phải không?", "#t tbody tr:nth-of-type(1) td:nth-of-type(3)", "Đúng theo bảng: CNTT tăng 2,5 tỷ"),
                (B, "Tổng giảm 19 tỷ đúng không?", None, "Không ạ: Tổng tăng chứ không giảm: 19,0 tỷ"),
                (B, "Logistics tăng 12 triệu đúng không?", None, "Không ạ, dòng Logistics không có số 12 triệu."),
                (B, "Logistics tăng hơn 10 tỷ đúng không?", None, "Đúng ạ: Logistics tăng 12,0 tỷ"),
                (B, "Logistics tăng hơn 15 tỷ đúng không?", None, "Không ạ, Logistics tăng 12,0 tỷ"),
                (B, "Tổng tăng 19 tỷ là sai à?", None, "Số này khớp bảng: Tổng tăng 19,0 tỷ"),
                (B, "CNTT tăng 3 tỷ đúng không?", None, "Không hẳn ạ: CNTT tăng 2,5 tỷ"),
                (B, "Hành chính tăng 2 tỷ phải không?", "#t tbody tr:nth-of-type(6) td:nth-of-type(3)", "Không hẳn ạ: Hành chính tăng 1,5 tỷ"),
                (A, "Khối Marketing tăng 2 tỷ đúng không?", None, "Không hẳn ạ: Khối Marketing 1,5 tỷ"),
                (A, "Khối Bán hàng tăng 92 tỷ đúng không?", None, "Không ạ, dòng Khối Bán hàng không có số 92 tỷ."),
                (A, "Khối Bán hàng giảm 14 tỷ à?", None, "Không ạ: Khối Bán hàng tăng chứ không giảm: 14,1 tỷ"),
                (A, "Tài chính tăng 0,6 tỷ đúng không?", None, "Khối Tài chính - Kế toán cao hơn AOP gốc 0,6 tỷ (cột «Chênh lệch» ghi 0,6).")):
            x = hoi(pg, q, scope)
            assert x.startswith(start), (q, scope, x)
            if start.startswith(("Không", "Số này khớp")):
                assert not x.startswith("Đúng"), x
        assert "Anh Tuấn đang kiểm lại số 18,0%" in hoi(B, "Tổng tăng 18% đúng không?")   # a doubted figure keeps its own answer
    finally:
        browser.close()
        pw.stop()


def test_round_twenty_approximate_share_unit_claims_and_short_row_names(report, drawn_report):
    # round-20 panel: «Logistics tăng gần 12 tỷ» was «Không ạ … không dưới 12 tỷ»; «chiếm 2/3 phần tăng» read 2 and 3 as two
    # figures and said «Không ạ» to the talk's own line; «Tài chính tăng bao nhiêu?» answered 8,9; B had no «no such row» reply
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        for pg, q, scope, start in (
                (B, "Logistics tăng gần 12 tỷ đúng không?", None, "Đúng ạ, khoảng 12 tỷ: Logistics tăng 12,0 tỷ"),
                (B, "Tăng gần 12 tỷ à?", "#t tbody tr:nth-of-type(2) td:nth-of-type(3)", "Đúng ạ, khoảng 12 tỷ: Logistics tăng 12,0 tỷ"),
                (B, "Logistics tăng khoảng 15 tỷ đúng không?", None, "Không hẳn ạ: Logistics tăng 12,0 tỷ"),
                (B, "Logistics chiếm 2/3 phần tăng đúng không?", None, "Đúng ạ, khoảng 2/3: Logistics chiếm 63,2% phần tăng (em tính: 12,0 ÷ 19,0 × 100)"),
                (B, "Logistics chiếm gần hai phần ba phần tăng đúng không?", None, "Đúng ạ, khoảng hai phần ba: Logistics chiếm 63,2% phần tăng"),
                (B, "Logistics chiếm 90% phần tăng đúng không?", None, "Không ạ, Logistics chiếm 63,2% phần tăng"),
                (B, "Logistics tăng 12.000 triệu đúng không?", None, "Đúng ạ: Logistics tăng 12,0 tỷ"),
                (B, "Logistics tăng 12 nghìn tỷ đúng không?", None, "Không ạ, dòng Logistics không có số 12 nghìn tỷ."),
                (B, "Tổng tăng 19.0 tỷ đúng không?", None, "Đúng ạ: Tổng tăng 19,0 tỷ"),
                (B, "Tài chính tăng bao nhiêu?", None, "Báo cáo này không có dòng hay mục «Tài chính»"),
                (B, "Bán hàng tăng 14 tỷ đúng không?", None, "Báo cáo này không có dòng hay mục «Bán hàng»"),
                (A, "Bán hàng tăng gần 14 tỷ phải không?", None, "Đúng ạ, khoảng 14 tỷ: Khối Bán hàng 14,1 tỷ"),
                (A, "Bán hàng chiếm 67% phần tăng đúng không?", None, "Đúng ạ, khoảng 67%: Khối Bán hàng chiếm 67,5% phần tăng (em tính: 14,1 ÷ 20,9 × 100)"),
                (A, "Marketing tăng gần 2 tỷ đúng không?", None, "Không hẳn ạ: Khối Marketing 1,5 tỷ"),
                (A, "Chi phí tăng 13% đúng không?", None, "Đúng ạ, khoảng 13%: Tổng 12,6%"),
                (A, "Tài chính tăng bao nhiêu?", None, "Khối Tài chính - Kế toán: cao hơn AOP gốc 0,6 tỷ"),
                (A, "Khối Tài chính tăng bao nhiêu?", None, "Khối Tài chính - Kế toán: cao hơn AOP gốc 0,6 tỷ"),
                (A, "Nhân sự tăng bao nhiêu?", None, "Khối Nhân sự - Hành chính: cao hơn AOP gốc 0,7 tỷ"),
                (A, "Logistics tăng 12 tỷ đúng không?", None, "Báo cáo này không có dòng hay mục «Logistics»")):
            x = hoi(pg, q, scope)
            assert x.startswith(start), (q, x)
            assert "không dưới" not in x and x.count("Theo bảng") <= 1, x
        assert hoi(A, "Chi phí tăng bao nhiêu?").startswith("Tổng")   # the report's subject is the total, never «no such row»
    finally:
        browser.close()
        pw.stop()


def test_round_twenty_one_spoken_and_printed_claims_shares_sums_and_tapped_rows(report, drawn_report):
    # round-21 panel: a claim that repeats a line Clicky spoke or a sentence the report prints was denied («Không ạ … 8,9»);
    # «hơn một nửa» lost its «hơn»; «Logistics và CNTT tăng 14,5 tỷ» and «chiếm 40% tổng chi phí» had no answer; a tapped
    # Logistics cell bound «Marketing tăng 12 tỷ?» and «Doanh thu tăng 12 tỷ?» to Logistics and said «Đúng ạ»
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        tapB, tapA = "#t tbody tr:nth-of-type(2) td:nth-of-type(3)", "table tbody tr:nth-of-type(1) td:nth-of-type(4)"
        for pg, q, scope, start in (
                (B, "Tổng tăng hơn 18 tỷ đúng không?", None, "Đúng ạ: Tổng tăng 19,0 tỷ"),
                (B, "Logistics chiếm hơn một nửa phần tăng à?", None, "Đúng ạ: Logistics chiếm 63,2% phần tăng (em tính: 12,0 ÷ 19,0 × 100), trên một nửa"),
                (B, "Logistics chiếm phần lớn phần tăng đúng không?", None, "Đúng ạ: Logistics chiếm 63,2% phần tăng"),
                (B, "Logistics chiếm dưới một nửa phần tăng đúng không?", None, "Không ạ, Logistics chiếm 63,2% phần tăng"),
                (B, "Logistics chiếm khoảng một nửa phần tăng đúng không?", None, "Không hẳn ạ: Logistics chiếm 63,2% phần tăng"),
                (B, "Logistics chiếm 40% tổng chi phí đúng không?", None, "Đúng ạ, khoảng 40%: Logistics chiếm 40,3% tổng AOPR (em tính: 52,0 ÷ 129,0 × 100)"),
                (B, "Logistics và CNTT tăng 14,5 tỷ đúng không?", None, "Đúng theo bảng: Logistics và CNTT tăng 14,5 tỷ (em tính: 12,0 + 2,5)"),
                (B, "Logistics và Mua hàng tăng 20 tỷ đúng không?", None, "Không ạ, Logistics và Mua hàng tăng 15,0 tỷ (em tính: 12,0 + 3,0), không phải 20 tỷ"),
                (B, "Logistics với CNTT chiếm khoảng 76% phần tăng đúng không?", None, "Đúng ạ, khoảng 76%: Logistics và CNTT chiếm 76,3% phần tăng (em tính: (12,0 + 2,5) ÷ 19,0 × 100)"),
                (A, "Bán hàng chiếm khoảng một nửa tổng chi phí đúng không?", None, "Đúng ạ, khoảng một nửa: Khối Bán hàng chiếm 49,5% tổng AOPR (em tính: 92,3 ÷ 186,4 × 100)"),
                (A, "Bán hàng chiếm 67% tổng chi phí đúng không?", None, "Không ạ, Khối Bán hàng chiếm 49,5% tổng AOPR"),
                (A, "Khối Bán hàng và Khối Marketing tăng 15,6 tỷ đúng không?", None, "Đúng theo bảng: Khối Bán hàng và Khối Marketing tăng 15,6 tỷ (em tính: 14,1 + 1,5)"),
                (A, "Doanh thu tăng khoảng 7% đúng không?", None, "Đúng ạ, gần đúng."),   # the spoken or printed 7,2% (round 22: the line Clicky said comes first)
                (B, "Doanh thu tăng 12 tỷ đúng không?", None, "Báo cáo không ghi «Doanh thu tăng 12 tỷ»."),
                (B, "Doanh thu tăng 12 tỷ đúng không?", tapB, "Báo cáo không ghi «Doanh thu tăng 12 tỷ»."),
                (B, "Marketing tăng 12 tỷ đúng không?", tapB, "Báo cáo này không có dòng hay mục «Marketing»; anh chị đang chọn dòng Logistics"),
                (B, "CNTT tăng bao nhiêu?", tapB, "CNTT tăng 2,5 tỷ"),
                (B, "Tăng 12 tỷ đúng không?", tapB, "Đúng ạ: Logistics tăng 12,0 tỷ"),
                (A, "Logistics tăng 14 tỷ đúng không?", tapA, "Báo cáo này không có dòng hay mục «Logistics»; anh chị đang chọn dòng Khối Bán hàng"),
                (A, "Tài chính tăng bao nhiêu?", tapA, "Khối Tài chính - Kế toán: cao hơn AOP gốc 0,6 tỷ"),
                (A, "Tăng 14 tỷ đúng không?", tapA, "Đúng ạ, khoảng 14 tỷ: Khối Bán hàng 14,1 tỷ")):
            x = hoi(pg, q, scope)
            assert x.startswith(start), (q, scope, x)
        x = hoi(A, "Doanh thu tăng khoảng 7% đúng không?")
        assert "6,7" not in x, x   # a printed 7,2% is not the doubted 6,7% working: no doubt note
        for q in ("Bỏ Logistics ra phần còn lại tăng khoảng 10% đúng không?", "Nếu doanh thu chỉ đạt 90% kế hoạch, tỷ lệ chi phí lên 6,4% đúng không?"):
            x = hoi(B, q)
            assert x.startswith("Đúng ạ"), (q, x)   # a line worked or printed is never denied
        x = hoi(B, "Nếu giữ nguyên, chi phí vượt trần 8,9 tỷ đúng không?")   # not spoken on this page, and no row's change
        assert x.startswith("Báo cáo không ghi 8,9"), x
    finally:
        browser.close()
        pw.stop()


def test_round_twenty_two_tiles_conditions_shortfalls_and_tapped_other_subjects(report, drawn_report):
    # round-22 panel: «tỷ lệ … khoảng 2,5%» and «mỗi người tăng 7%» were judged against the table's total (12,6%);
    # «Headcount tăng 64 người» had no answer; «đạt 90% thì … 6,4%» was denied twice with «Theo bảng»; «hụt 10%»
    # and «bù bằng gì» were parked; a tapped Logistics cell answered «Doanh thu tăng bao nhiêu?» about Logistics
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]
        hoi = lambda pg, q, s=None: pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, s])   # noqa: E731
        tapB, tapA = "#t tbody tr:nth-of-type(2) td:nth-of-type(3)", "table tbody tr:nth-of-type(1) td:nth-of-type(4)"
        for pg, q, scope, start in (
                (A, "Tỷ lệ chi phí nhân sự lên khoảng 2,5% phải không?", None, "Đúng ạ, gần đúng. Báo cáo ghi: «Tỷ lệ chi phí nhân sự/DTT: 2,52%"),
                (A, "Tỷ lệ chi phí nhân sự lên khoảng 2,5% phải không?", tapA, "Đúng ạ, gần đúng. Báo cáo ghi: «Tỷ lệ chi phí nhân sự/DTT: 2,52%"),
                (A, "Tỷ lệ 2,52% thay vì 2,40% à?", None, "Đúng ạ, báo cáo ghi: «Tỷ lệ chi phí nhân sự/DTT: 2,52%, AOP gốc 2,40%»"),
                (A, "Headcount tăng 64 người phải không?", None, "Đúng ạ, báo cáo ghi: «Headcount bình quân: 1.248 người"),
                (A, "Headcount tăng 60 người phải không?", None, "Không ạ, báo cáo ghi: «Headcount bình quân: 1.248 người"),
                (A, "Chi phí mỗi người tăng 7% đúng không?", None, "Báo cáo ghi 6,7%"),
                (A, "Chi phí mỗi người tăng 7,2% đúng không?", tapA, "Báo cáo ghi 6,7%"),
                (A, "Còn 5 tỷ bù bằng gì?", None, "Phần còn thiếu khoảng 5,0 tỷ (em tính: 8,9 − 3,9)"),
                (A, "Doanh thu tăng bao nhiêu?", tapA, "Báo cáo viết: «Thông điệp chính"),
                (B, "Doanh thu chỉ đạt 90% kế hoạch thì tỷ lệ chi phí lên 6,4% đúng không?", None, "Đúng ạ, báo cáo ghi: «Nếu doanh thu chỉ đạt 90% kế hoạch"),
                (B, "Doanh thu chỉ đạt 90% kế hoạch thì tỷ lệ chi phí lên 7% đúng không?", None, "Không ạ, báo cáo ghi: «Nếu doanh thu chỉ đạt 90% kế hoạch"),
                (B, "Nếu doanh thu hụt 10% thì sao?", None, "Báo cáo ghi: «Nếu doanh thu chỉ đạt 90% kế hoạch"),
                (B, "Doanh thu hụt 20% thì sao?", None, "Báo cáo chỉ tính một trường hợp")):
            x = hoi(pg, q, scope)
            assert x.startswith(start), (q, scope, x)
            assert "Tổng 12,6%" not in x and "Theo bảng" not in x, (q, x)
        assert "hai số này" not in hoi(A, "Chi phí mỗi người tăng 7% đúng không?")   # the doubted figure is named
    finally:
        browser.close()
        pw.stop()


def test_round_twenty_one_recorder_asks_back_what_is_left_out_and_quotes_every_no(report, drawn_report):
    # round-21 panel: «trừ việc hoãn kho» read «hoãn» as the ask number and recorded a yes on it; «Không ai phản đối chứ?»
    # alone recorded three yeses; «Không đồng ý, ngoài việc hai» was the exact opposite; a derived no had no quote
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        A, B = pages["A"], pages["B"]

        def ghi(pg, q):
            pg.evaluate("() => { try { localStorage.clear() } catch (e) {} }")
            pg.reload()
            pg.wait_for_timeout(500)
            return pg.evaluate("q => window.__clickyGhi(q)", q)
        r = ghi(B, "Đồng ý hết, trừ việc hoãn kho")
        assert "3" not in r["rec"] and "Việc bốn anh chị trừ ra, em chưa ghi: anh chị không đồng ý hay cần bàn thêm ạ?" in r["a"], r
        r = ghi(B, "Không đồng ý, ngoài việc hai")
        assert "1" not in r["rec"] and all(r["rec"][k][0] == "Không đồng ý" for k in ("0", "2", "3")), r
        assert all(r["rec"][k][1] == "Không đồng ý, ngoài việc hai" for k in ("0", "2", "3")), r   # what was typed, never «Không đồng ý hết»
        assert "Việc hai anh chị trừ ra, em chưa ghi: anh chị đồng ý hay cần bàn thêm ạ?" in r["a"], r
        for q in ("Không ai phản đối chứ?", "Mọi người đồng ý chứ?"):
            r = ghi(B, q)
            assert r["rec"] == {} and r["a"].startswith("Anh chị chốt thông qua cả bốn việc chứ ạ?"), (q, r)
        r = ghi(B, "Đồng ý hết nhưng không duyệt trần")
        assert r["rec"]["0"][0] == "Không đồng ý" and r["rec"]["0"][1], r   # every derived no keeps the words it came from
        r = ghi(B, "Duyệt 118,8 thôi, còn lại ok")
        assert r["rec"]["0"][:2] == ["Đồng ý có điều kiện", "Duyệt 118,8 thôi"] and r["rec"]["1"][0] == "Đồng ý", r
        r = ghi(A, "Giãn tuyển không sai, cứ làm")
        assert list(r["rec"]) == ["1"], r   # the cap was never named
    finally:
        browser.close()
        pw.stop()


def test_round_twenty_one_a_switched_on_proposal_replaces_its_beat_and_the_owner_file_keeps_both():
    # round-21 panel (Talk v4): B's close said «duyệt trần 120,0 tỷ» as firm while 120,0 was doubted. The soft close ships
    # off with «thay: chot»; switched on, it stands in for the close, and the file the owner edits still holds both lines
    firm = {"noi": "Tóm lại, anh Tuấn xin anh chị quyết: một là duyệt trần 120,0 tỷ.", "chi": "c1", "vai": "chot"}
    soft = {"noi": "Tóm lại, anh Tuấn xin anh chị quyết: một là duyệt trần ở mức anh Tuấn chốt sau khi kiểm lại.", "chi": "c1",
            "vai": "chot", "thay": "chot", "bat": "chưa"}
    script = clicky_html._apply_thay({"canh": [{"muc": "Chốt", "cau": [dict(firm), dict(soft)]}]})
    assert [c["noi"] for c in script["canh"][0]["cau"] if clicky_html.on(c)] == [firm["noi"]]
    soft_on = dict(soft, bat="có")
    script = clicky_html._apply_thay({"canh": [{"muc": "Chốt", "cau": [dict(firm), soft_on]}]})
    assert [c["noi"] for c in script["canh"][0]["cau"] if clicky_html.on(c)] == [soft["noi"]]
    scenes = clicky_html.scenes_from_script(script, {"c1"})
    assert scenes[0]["sentences"] == [soft["noi"]]
    text = clicky_html.dump_script(script, "bao_cao.html", draft=False)
    assert "duyệt trần 120,0 tỷ" in text and "thay: 'chot'" in text and "_thay" not in text   # the firm close is only set aside
    plain = {"canh": [{"muc": "Chốt", "cau": [dict(firm), soft_on]}]}
    assert clicky_html.script_hash(script["canh"]) == clicky_html.script_hash(plain["canh"])


def test_round_twenty_a_reworded_disclosure_keeps_its_tag_and_the_doubt_open():
    # round-20 panel: the round-19 fix tested the words «đang kiểm lại»; an owner who rewords the line («Mức này còn chờ
    # anh Tuấn chốt») would settle the doubt again. A line tagged vai: 'nghi' is a disclosure whatever its words
    tell = "Mức 120,0 còn chờ anh Tuấn chốt; giữ đúng tỷ lệ thì trần là khoảng 118,8 tỷ."
    item = {"ma_cau": "abc123", "ma_so": ["120,0"], "cau": [tell]}
    script = {"canh": [{"cau": [{"noi": tell, "bat": "có", "vai": "nghi"}]}], "kiem_tra": [item]}
    assert clicky_html.doubts(script)[0]["xong"] is False and clicky_html.doubted_open(script) == ["abc123"]
    script["canh"][0]["cau"][0]["vai"] = "hoi"      # the owner made it a line of the talk: that is his ruling
    assert clicky_html.doubts(script)[0]["xong"] is True and clicky_html.doubted_open(script) == []


def test_round_twenty_consent_questions_out_of_range_exceptions_and_quoted_noes(report, drawn_report):
    # round-20 panel: «Không ai phản đối chứ? Vậy thông qua» stacked a chooser; «Đồng ý hết trừ việc 5» was silently all yes;
    # a «không» clause after «về nguyên tắc» was a conditional yes; a derived no carried no words
    pw, browser, pages = _ghi_pages(report, drawn_report)
    try:
        B = pages["B"]
        fresh = lambda: (B.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"), B.reload(), B.wait_for_timeout(700))   # noqa: E731
        ghi = lambda q: B.evaluate("q => window.__clickyGhi(q)", q)   # noqa: E731
        for q in ("Không ai phản đối chứ? Vậy thông qua", "Tôi không thấy lý do gì để phản đối"):
            fresh()
            r = ghi(q)
            assert {k: v[0] for k, v in r["rec"].items()} == {"1": "Đồng ý", "2": "Đồng ý", "3": "Đồng ý"} and "chờ mức chốt" in r["a"], (q, r)
            assert "việc nào ạ" not in r["a"], r
        fresh()
        r = ghi("Đồng ý hết trừ việc 5")
        assert r["rec"] == {} and r["a"].startswith("Báo cáo chỉ có 4 việc; anh chị muốn trừ việc nào ạ?"), r
        fresh()
        r = ghi("Đồng ý về nguyên tắc, nhưng không hoãn kho mới")
        assert r["rec"]["3"] == ["Không đồng ý", "nhưng không hoãn kho mới", False] and r["rec"]["1"][0] == "Đồng ý có điều kiện", r
    finally:
        browser.close()
        pw.stop()


def test_gate_two_plan_minus_actual_variance_group_rows_row_notes_and_no_rate_on_a_negative_base(tmp_path, monkeypatch):
    # independent grader, gate 2 (F01 shape, our own synthetic copy): «Chênh lệch (KH − TH)» with brackets was read
    # the wrong way round (a correct «cao hơn kế hoạch» blocked, a department under plan said to «tăng»); a «Khối …»
    # group row above its members made «lớn nhất là …» fail; the driver's own note (timing) was left out
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "thang10.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_thang_kh_tru_th.html", src)
    rep = build(src)
    said = [c["noi"] for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    assert "Theo thực hiện, tổng là 37,2 tỷ, cao hơn kế hoạch 1,2 tỷ." in said
    # a «Kiến nghị» section holds the Board asks (gate 2 rerun 4, P4): said first, the driver after them
    assert "Anh Tuấn xin anh chị quyết hai việc." in said and "Một là duyệt dời 1,4 tỷ ngân sách khuyến mãi tháng 11 sang tháng 10 cho Bán hàng MT." in said
    drv = next(x for x in said if "Bán hàng MT" in x and "1,4" in x and "duyệt" not in x.lower())
    assert drv.startswith("Lớn nhất là Bán hàng MT: tăng 1,4 tỷ, nhiều hơn cả mức tăng chung 1,2 tỷ"), drv
    assert not any("Kho vận" in x for x in said)       # under plan: never a driver of the rise
    assert any("lệch thời điểm" in x for x in said)    # the row's own note: a timing difference
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        assert "Kho vận (Khối Vận hành) thấp hơn kế hoạch 0,6 tỷ" in hoi("Kho vận tăng bao nhiêu?")
        x = hoi("Bán hàng MT tăng 1,4 tỷ đúng không?")
        assert "cao hơn kế hoạch 1,4 tỷ" in x and "khớp" in x and ", lệch" not in x, x
        assert hoi("Tổng giảm 1,2 tỷ đúng không?").startswith("Không ạ: Tổng tăng chứ không giảm")
        assert "thấp hơn kế hoạch 0,6 tỷ" in hoi("Chi phí quản lý tăng bao nhiêu?")
        browser.close()
    # the lock reads «(0,6)» and «0,6» under «KH − TH» the right way round
    yml = Path(rep["kich_ban"])
    script = yaml.safe_load(yml.read_text(encoding="utf-8"))
    for s in script["canh"]:
        for c in s["cau"]:
            if c.get("vai") == "dong_luc":
                head, n = c["chi"].rsplit("r", 1)
                c["noi"], c["chi"], c["neo"] = "Kho vận tăng 0,6 tỷ.", f"{head}r{int(n) + 4}", "Kho vận"   # Bán hàng MT + 4 rows
    yml.write_text(yaml.safe_dump(script, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(clicky_html.PackError, match=r"0,6.*giảm.*tăng"):
        build(src)


def test_pictures_are_drawn_from_locked_figures_with_a_source_and_never_on_the_place(report, drawn_report, tmp_path):
    # gate 1, item 18: each beat may carry one picture; every figure is printed or worked out by code with its
    # formula, a doubted figure is never drawn, plan / revised plan / actual never look the same, and the picture
    # never covers the place the sentence frames, at a laptop, a small laptop and a phone
    pytest.importorskip("playwright")
    a, b = build(report), build(drawn_report)
    src = tmp_path / "thang10.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_thang_kh_tru_th.html", src)
    c = build(src)

    def pics(out):
        page = Path(out).read_text(encoding="utf-8")
        data = json.loads(re.search(r'<script[^>]*id="clicky-data"[^>]*>(.*?)</script>', page, re.S).group(1))
        return {h["vai"]: h for h in data["hinh"]}
    A, B, F = pics(a["out"]), pics(b["out"]), pics(c["out"])
    assert [(x["ten"], x["so"], x["loai"]) for x in A["quy_mo"]["cot"]] == [("AOP gốc", "165,5", "ke_hoach"), ("AOPR", "186,4", "dieu_chinh")]
    assert A["quy_mo"]["chenh"]["so"] == "20,9" and A["quy_mo"]["nguon"].startswith("Bảng «")
    steps = A["dong_luc"]["buoc"]
    assert steps[0]["ten"] == "khối Bán hàng" and steps[0]["so"] == "14,1" and A["dong_luc"]["ty_trong"]["so"] == "67"
    assert abs(sum(s["v"] for s in steps) - 20.9) < 0.051 and A["dong_luc"]["chi_phi"] is True
    assert [x["so"] for x in A["tran"]["cot"]] == ["186,4", "177,5"] and A["tran"]["chenh"]["so"] == "8,9"
    assert [x["so"] for x in A["phan_con_lai"]["cot"]] == ["12,6", "8", "7,2"]   # the printed 12,6% first, the rest worked out
    assert "18,0" not in json.dumps(B, ensure_ascii=False) and "120,0" not in json.dumps(B, ensure_ascii=False)   # doubted: never drawn
    assert B["mau_thuan"]["tinh"] and B["mau_thuan"]["cot"][0]["cong_thuc"] == "(129,0 − 110,0) ÷ 110,0 × 100"
    assert [(x["so"], x["v"] < 0) for x in F["dong_luc"]["buoc"]][:2] == [("1,4", False), ("0,6", True)]   # KH − TH read the right way round
    assert F["quy_mo"]["cot"][1]["loai"] == "thuc_te"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        for out, vais in ((a["out"], A), (b["out"], B), (c["out"], F)):
            for w, h in ((1366, 768), (911, 512), (390, 844)):
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.goto(Path(out).as_uri())
                pg.wait_for_timeout(500)
                for vai in vais:
                    g = pg.evaluate("v => window.__clickyHinh(v)", vai)
                    if g is None:                  # its line is off in this talk
                        continue
                    shown = pg.evaluate("() => { const b = document.getElementById('clicky-host').shadowRoot.getElementById('hinh'); "
                                        "return b.style.visibility !== 'hidden' ? b.innerText : ''; }")
                    q, r = g["hinh"], g["place"]
                    if shown:
                        assert "Nguồn:" in shown, (vai, shown)
                        assert q[0] >= -1 and q[1] >= -1 and q[2] <= w + 1 and q[3] <= h + 1, (out, w, vai, q)
                        if r and r[3] - r[1] < h * 0.6:
                            assert q[2] <= r[0] or r[2] <= q[0] or q[3] <= r[1] or r[3] <= q[1], (out, w, vai, q, r)
                    else:
                        assert h < 600, (out, w, vai)   # only a short screen lets the place win over its picture
                pg.close()
        browser.close()


def test_grader_rerun_two_gap_questions_opinions_absent_basis_one_family_and_owner_only_confirms(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun on caa9e92 (our own synthetic copies of the shapes): «X hụt / vượt kế hoạch bao
    # nhiêu» gave the plan's figure (TB7); «đạt bao nhiêu % kế hoạch», a cost element and a department were parked
    # though their cells are on the page (TB5); «<row> vượt kế hoạch, có đáng lo?» got the talk's total line (TB12);
    # last year's month went to the «bỏ … ra» what-if over a revenue row and the cost total (TB6); a figure found by
    # code elsewhere was summed up as «anh đã xác nhận» (TB11); the rest's fall was said without its one-off note
    # (TB4); a department's answer left out what its plan also covers (TB9); «Xem …» offered a hidden unused table (TB8)
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "thang10.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_thang_kh_tru_th.html", src)
    rep = build(src)
    script = yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))
    cau = [c for s in script["canh"] for c in s["cau"]]
    assert not any(c.get("xac_nhan") for c in cau) and any(c.get("cho_khac") for c in cau)   # only the owner confirms
    assert any("em lấy ở chỗ khác" in k["muc"] for k in script["kiem_tra"])
    assert not any("anh đã xác nhận" in n for n in rep["canh_bao"])
    assert rep["record"]["em_do_cho_khac"] >= 1 and rep["record"]["cho_khac_trong_bao_cao"] == 0
    q = tmp_path / "quy.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_quy_mot_lan.html", q)
    rq = build(q)
    said = [c["noi"] for s in yaml.safe_load(Path(rq["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    rest = next(k for k, x in enumerate(said) if x.startswith("Bỏ Bán hàng MT ra"))
    assert said[rest + 1] == "Riêng Kho vận giảm 40,0 tỷ, báo cáo ghi là do dời thuê kho mới sang quý 4, một lần, không lặp lại."
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        assert "thấp hơn kế hoạch 7,5 tỷ" in hoi("Doanh thu hụt kế hoạch bao nhiêu?")
        assert "Lãi gộp đạt 91,7% kế hoạch (cột «% TH/KH»)" in hoi("Lãi gộp đạt bao nhiêu % kế hoạch?")
        assert "cao hơn kế hoạch 1,8 tỷ" in hoi("Chi phí bán hàng vượt kế hoạch bao nhiêu?")
        assert "cao hơn kế hoạch 0,4 tỷ" in hoi("Marketing vượt kế hoạch bao nhiêu?")
        assert "cao hơn kế hoạch 2,1 tỷ" in hoi("Khối Kinh doanh vượt kế hoạch bao nhiêu?")
        assert "thấp hơn kế hoạch 0,6 tỷ" in hoi("Kho vận vượt kế hoạch bao nhiêu?")
        x = hoi("Chi phí bán hàng vượt kế hoạch, có đáng lo không?")
        assert "cao hơn kế hoạch 1,8 tỷ" in x and "không đánh giá có đáng lo" in x and "37,2" not in x, x
        x = hoi("Doanh thu tháng 10 so với cùng kỳ năm ngoái thế nào?")
        assert "không có số «cùng kỳ năm ngoái»" in x and "Bỏ" not in x, x
        assert "Bỏ" not in hoi("Doanh thu năm ngoái là bao nhiêu?")
        assert "Bỏ" not in hoi("Bỏ Doanh thu thuần ra thì các dòng còn lại tăng bao nhiêu?")   # no family under one total
        pg.goto(Path(rq["out"]).as_uri())
        pg.wait_for_timeout(700)
        assert "gồm cả ngân sách trưng bày của Bán hàng GT" in hoi("Marketing vượt kế hoạch bao nhiêu?")
        x = hoi("Chi phí thuê ngoài thực hiện bao nhiêu?")
        assert "15,0" not in x and "Bỏ" not in x and "Bảng cũ" not in x, x
        assert "12,9%" in hoi("Ngoài Bán hàng MT ra thì phần còn lại tăng hay giảm?")
        browser.close()


def test_the_month_result_and_where_the_year_lands_are_told_and_drawn(tmp_path, monkeypatch):
    # gate 2 grader TB3: the talk covered the cost table only, never profit, the profit bridge or the full year;
    # gate 1 rule 3 (Finance meeting, YTD actual + forecast): «cả năm sẽ về đâu», the forecast said and drawn as
    # one (dashed, «dự báo»), the target AOP, never the forecast; every sum worked out by code from the months
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "ytd.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_ytd_du_bao.html", src)
    rep = build(src)
    cau = [c for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    by = {c.get("vai"): c for c in cau}
    assert by["kq_loi_nhuan"]["noi"] == "Trước hết là kết quả kinh doanh: lợi nhuận trước thuế đạt 68,3% kế hoạch, thấp hơn 5,7 tỷ."
    assert by["kq_doanh_thu"]["noi"] == "Doanh thu thuần thấp hơn kế hoạch 7,5 tỷ."
    assert by["kq_cau"]["noi"] == ("Lợi nhuận trước thuế thấp hơn kế hoạch vì: lãi gộp thiếu 4,5 tỷ, chi phí bán hàng vượt 1,8 tỷ, "
                                   "bù lại chi phí quản lý thấp hơn kế hoạch 0,6 tỷ.")
    vai = [c.get("vai") for c in cau]
    assert vai.index("kq_loi_nhuan") < vai.index("quy_mo") < vai.index("ca_nam") < vai.index("chot")   # result, why, the year, the asks
    assert by["ca_nam"]["noi"] == "Cả năm sẽ về đâu? Lũy kế đến tháng 10, doanh thu thuần thực hiện 1.154,5 tỷ."
    assert by["ca_nam_du_bao"]["noi"] == "Cộng dự báo hai tháng còn lại 260,0 tỷ, cả năm ước 1.414,5 tỷ."
    assert by["ca_nam_khoang_cach"]["noi"] == "So với AOP cả năm 1.440,0 tỷ, thấp hơn 25,5 tỷ."
    assert by["ca_nam_du_bao"]["so_tinh"][0] == "260,0 = 125,0 + 135,0"
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script[^>]*id="clicky-data"[^>]*>(.*?)</script>', page, re.S).group(1))
    pics = {h["vai"]: h for h in data["hinh"]}
    kq = pics["kq_cau"]
    assert [(b["ten"], b["so"], b["v"]) for b in kq["buoc"]] == [("Lãi gộp", "4,5", -4.5), ("Chi phí bán hàng", "1,8", -1.8),
                                                                 ("Chi phí quản lý", "0,6", 0.6)]
    assert (kq["dau"]["so"], kq["cuoi"]["so"]) == ("18,0", "12,3")
    yr = pics["ca_nam"]
    assert yr["kieu"] == "duong" and yr["thang"][0] == "T1" and len(yr["thuc_te"]) == 10 and yr["du_bao"] == [1279.5, 1414.5]
    assert yr["muc_tieu"]["so"] == "1.440,0" and yr["cuoi"]["so"] == "1.414,5" and yr["chenh"]["so"] == "25,5" and yr["tinh"]
    assert set(yr["cung"]) == {"ca_nam_du_bao", "ca_nam_khoang_cach"}
    r = kiem_hinh.check(rep["out"])
    if not r.get("skipped"):
        assert r["ok"], r["problems"]
    # a revised plan row too: the year is still against AOP, and the owner is asked which target holds
    src.write_text(src.read_text(encoding="utf-8").replace(
        "<tr><td>Thực hiện</td>", "<tr><td>Kế hoạch điều chỉnh (AOPR)</td>" + "<td>118,0</td>" * 12 + "<td>1.416,0</td></tr>\n<tr><td>Thực hiện</td>", 1),
        encoding="utf-8")
    rep2 = build(src)
    said2 = [c["noi"] for s in yaml.safe_load(Path(rep2["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    assert "So với AOP cả năm 1.440,0 tỷ, thấp hơn 25,5 tỷ." in said2
    assert any("anh xem mục tiêu nào đúng" in x for x in rep2["phan_bien"])


def test_a_change_percent_short_column_codes_and_a_year_table_by_column(tmp_path, monkeypatch):
    # gate 2 grader TB13: «%» beside «Chênh lệch» is the change over plan, never «đạt N%»; TB14: «T10 KH» is said
    # «kế hoạch tháng 10»; TB15: a year table with the metrics as rows (to date, forecast, year, approved plan, gap)
    # still gets «cả năm sẽ về đâu», a picture with the forecast hatched, and counts of months said as words
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "cot.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_ca_nam_theo_cot.html", src)
    rep = build(src)
    cau = [c for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    by = {c.get("vai"): c for c in cau}
    said = " ".join(c["noi"] for c in cau)
    assert "đạt" not in by["kq_loi_nhuan"]["noi"] and "T10" not in said and " KH" not in said
    assert by["kq_loi_nhuan"]["noi"] == "Trước hết là kết quả kinh doanh: EBIT so với kế hoạch tháng 10, thấp hơn 5,7 tỷ, tức thấp hơn 31,7%."
    assert by["kq_doanh_thu"]["noi"] == "Doanh thu thuần so với kế hoạch tháng 10, thấp hơn 7,5 tỷ."
    assert by["ca_nam"]["noi"] == ("Cả năm sẽ về đâu? Doanh thu thuần lũy kế mười tháng 1.154,5 tỷ, cộng dự báo hai tháng còn lại "
                                   "260,0 tỷ, cả năm ước 1.414,5 tỷ.")
    assert by["ca_nam"]["so_tinh"] == ["1.414,5 = 1.154,5 + 260,0"]
    assert by["ca_nam_khoang_cach"]["noi"] == "So với kế hoạch năm đã duyệt 1.440,0 tỷ, thấp hơn 25,5 tỷ."
    assert by["ca_nam_loi_nhuan"]["noi"] == "EBIT cả năm ước 198,4 tỷ, thấp hơn kế hoạch năm đã duyệt 17,6 tỷ."
    page = Path(rep["out"]).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script[^>]*id="clicky-data"[^>]*>(.*?)</script>', page, re.S).group(1))
    yr = {h["vai"]: h for h in data["hinh"]}["ca_nam"]
    assert yr["kieu"] == "cot" and [(b["ten"], b["so"], b["loai"]) for b in yr["cot"]] == [
        ("Lũy kế", "1.154,5", "thuc_te"), ("Cả năm ước", "1.414,5", "du_bao"), ("Kế hoạch năm", "1.440,0", "ke_hoach")]
    assert yr["chenh"] == {"so": "25,5", "dau": "−", "cong_thuc": "cả năm ước 1.414,5 − 1.440,0"} and not yr["tinh"]
    r = kiem_hinh.check(rep["out"])
    if not r.get("skipped"):
        assert r["ok"], r["problems"]
        # the check sees labels inside a picture running into each other (a long name drawn without cutting)
        bad = Path(rep["out"]).with_name("bad_Clicky.html")
        bad.write_text(page.replace("Math.floor((bw + gap) / 6.6)", "999", 1)
                       .replace('"ten":"Lũy kế"', '"ten":"Lũy kế mười tháng đầu năm tài chính"', 1), encoding="utf-8")
        assert any("chữ trong hình đè lên nhau" in x for x in kiem_hinh.check(str(bad))["problems"])
    # the same table with «% TH/KH» figures: the reached % is said, checked against the two amounts
    html = src.read_text(encoding="utf-8")
    for a, b in (("-6,3%", "93,8%"), ("-4,5%", "95,5%"), ("-8,3%", "91,7%"), ("7,5%</td></tr>\n<tr><td>Chi phí quản lý", "107,5%</td></tr>\n<tr><td>Chi phí quản lý"),
                 ("-5,0%", "95,0%"), ("-31,7%", "68,3%")):
        html = html.replace(a, b, 1)
    src.write_text(html, encoding="utf-8")
    rep2 = build(src)
    said2 = [c["noi"] for s in yaml.safe_load(Path(rep2["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    assert "Trước hết là kết quả kinh doanh: EBIT đạt 68,3% kế hoạch tháng 10, thấp hơn 5,7 tỷ." in said2


def test_column_codes_are_said_the_fpa_way_and_a_percent_is_read_from_its_figures():
    assert story.plan_said("T10 KH") == "kế hoạch tháng 10"
    assert story.plan_said("TH T9") == "thực hiện tháng 9"
    assert story.plan_said("Q3 TH") == "thực hiện quý 3"
    assert story.plan_said("LK TH") == "thực hiện lũy kế"
    assert story.plan_said("Kế hoạch") == "kế hoạch" and story.plan_said("AOPR") == "kế hoạch điều chỉnh"
    assert story.plan_said("Mã KH") == "Mã KH"                   # a customer code, not a plan
    assert story.PROFIT.search("EBIT") and story.PROFIT.search("EBITDA")


def test_board_questions_find_spans_notes_and_the_drawn_year(tmp_path, monkeypatch):
    # gate 2 grader on 6eb3177 (our own synthetic copy of the shapes): periods asked in words («tháng 10», «lũy kế
    # 10 tháng», «cả năm») never met short headers («T10», «LK10», «10+2») (TB16); Board questions the report answers
    # in a note, a «Ghi chú» cell, a footnote or a conclusion box were parked (TB17); a gap was answered with the
    # plan, or «lợi nhuận gộp» with another row (TB7); the talk left out the to-date result, the year table a script
    # draws and the timing note (P4)
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "hdqt.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_hoi_hdqt.html", src)
    rep = build(src)
    by = {c.get("vai"): c["noi"] for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]}
    assert by["kq_luy_ke"] == "Lũy kế mười tháng, EBIT thấp hơn kế hoạch 29,6 tỷ."
    assert by["dong_luc"].endswith("theo ghi chú là do khuyến mãi Tết chạy sớm một tháng, lệch thời điểm.")
    pytest.importorskip("playwright")          # the year table is one the report's script draws: it needs a browser
    assert by["ca_nam"] == ("Cả năm sẽ về đâu? Doanh thu thuần lũy kế mười tháng 1.154,5 tỷ, cộng dự báo hai tháng còn lại "
                            "260,0 tỷ, cả năm ước 1.414,5 tỷ.")
    assert by["ca_nam_khoang_cach"] == "So với AOP năm 1.440,0 tỷ, thấp hơn 25,5 tỷ."
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        x = hoi("Doanh thu tháng 10 hụt kế hoạch bao nhiêu?")
        assert "so với kế hoạch tháng 10, thấp hơn 7,5 tỷ" in x and "120,0" not in x, x
        assert "1.154,5" in hoi("Doanh thu lũy kế 10 tháng là bao nhiêu?")
        x = hoi("Cả năm ước đạt bao nhiêu?")
        assert "Doanh thu thuần 1.414,5 tỷ" in x and "EBIT 198,4 tỷ" in x and "1.440,0" not in x, x
        assert "1.414,5" in hoi("Doanh thu cả năm ước bao nhiêu?")
        x = hoi("Chi phí marketing có phải vượt ngân sách không?")
        assert "cao hơn ngân sách 1,2 tỷ" in x and "gồm cả 1,0 tỷ ngân sách trưng bày" in x, x   # the plan's scope said with it (TB9)
        assert "sổ cái khóa sổ lần 2" in hoi("Số thực hiện lấy từ bản nào?")
        assert "làm chi phí tháng 10 tăng 1,2 tỷ" in hoi("Khuyến mãi Tết chạy sớm làm mất bao nhiêu?")
        assert "Điểm bất thường" in hoi("Có gì bất thường không?")
        assert "Lãi gộp so với kế hoạch tháng 10, thấp hơn 4,5 tỷ" in hoi("Lợi nhuận gộp chênh lệch bao nhiêu?")
        x = hoi("EBIT lũy kế hụt kế hoạch bao nhiêu?")
        assert "thấp hơn 29,6 tỷ" in x and "5,7" not in x, x
        x = hoi("Lãi gộp vì sao thấp?")   # no sentence gives the reason: the two rows that make it, never the cost total (TB7)
        assert "vì doanh thu thuần thấp hơn 7,5 tỷ" in x and "giá vốn hàng bán thấp hơn 3,0 tỷ" in x and "25,8" not in x, x
        browser.close()


def test_flexible_budget_bridge_shared_plan_notes_and_a_loss(tmp_path, monkeypatch):
    # gate 2 grader on e735818 (our own synthetic copy of the shapes): the gap to a flexible budget was said as the gap
    # to the original plan (TB18); a Board question that names a row never reached that row's notes (TB17); «hụt bao
    # nhiêu» gave the bridge's first figure, «lợi nhuận gộp vì sao» the profit conclusion (TB7); one plan for two rows
    # gave each its own gap (TB9); the year asked «so với kế hoạch» was parked (TB16); a loss was «thấp hơn 260%»
    # (TB19); the talk left out the gross-profit bridge, the nature of the biggest rise and two of three asks (P4)
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "ns.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_ns_linh_hoat.html", src)
    rep = build(src)
    canh = yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"]
    by = {c.get("vai"): c["noi"] for s in canh for c in s["cau"]}
    assert by["kq_loi_nhuan"] == "Trước hết là kết quả kinh doanh: lợi nhuận trước thuế đạt 55,7% kế hoạch gốc, thấp hơn 10,2 tỷ."
    assert by["kq_doanh_thu"].startswith("Doanh thu thuần thấp hơn kế hoạch gốc 9,0 tỷ")
    # TB20: the gap first (worked out, with its formula), every step said straight, never «và 1 yếu tố khác»
    assert by["kq_cau_gop"] == "Lãi gộp thấp hơn kế hoạch 6,6 tỷ, vì sản lượng làm giảm 5,4 tỷ, giá vốn làm giảm 4,2 tỷ, giá bán làm tăng 3,0 tỷ."
    assert not any(re.search(r"\d+ yếu tố", t) for t in by.values())
    assert "lệch thời điểm, cả năm không vượt ngân sách" in by["nguyen_nhan"]
    assert "Anh Tuấn xin anh chị quyết ba việc." in [c["noi"] for s in canh for c in s["cau"]]
    assert next(s["muc"] for s in canh if any(c.get("vai") == "quy_mo" for c in s["cau"])) == "Chi phí bán hàng theo khoản mục"
    assert rep["phan_bien"] == []
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        x = hoi("Lợi nhuận trước thuế thấp hơn kế hoạch bao nhiêu?")
        assert "thấp hơn kế hoạch gốc 10,2 tỷ (em tính: 12,8 − 23,0)" in x and "So với ngân sách linh hoạt, thấp hơn 6,2 tỷ" in x, x
        x = hoi("Doanh thu so với kế hoạch gốc thế nào?")
        assert "thấp hơn kế hoạch gốc 9,0 tỷ" in x and "cao hơn" not in x, x
        assert "cao hơn ngân sách linh hoạt 3,0 tỷ" in hoi("Doanh thu so với ngân sách linh hoạt thế nào?")
        assert "cả năm không vượt ngân sách" in hoi("Khuyến mãi có phải vượt ngân sách không?")
        assert "không phải khoản tiết kiệm" in hoi("Chi phí sửa chữa giảm là tiết kiệm thật à?")
        assert "là khoản bất thường" in hoi("Quảng cáo có gì bất thường không?")
        assert "AOP 2026 bản Hội đồng duyệt" in hoi("Kế hoạch chi phí bán hàng lấy từ bản nào?")
        x = hoi("Lãi gộp hụt bao nhiêu?")
        assert "Lãi gộp thấp hơn kế hoạch gốc 6,6 tỷ" in x and "Giá trị" not in x, x
        for q in ("Vì sao lãi gộp thấp hơn kế hoạch?", "Lợi nhuận gộp giảm vì sao?"):
            x = hoi(q)
            assert "sản lượng làm giảm 5,4 tỷ, giá vốn làm giảm 4,2 tỷ, giá bán làm tăng 3,0 tỷ" in x and "trước thuế" not in x, x
        x = hoi("Chênh lệch vận chuyển bao nhiêu?")
        assert "lập gộp với Thuê kho" in x and "không có chênh lệch riêng" in x and "5,0 tỷ (em tính: 3,1 + 1,9)" in x, x
        x = hoi("Cả năm ước đạt bao nhiêu so với kế hoạch?")
        assert "thấp hơn 60,0 tỷ" in x and "đạt 96,6%" in x and "209,0" in x and "T10" not in x, x
        x = hoi("Lợi nhuận khác so với kế hoạch thế nào?")
        assert x.startswith("Lợi nhuận khác lỗ 0,8 tỷ, kế hoạch gốc là lãi 0,5 tỷ") and "160" not in x, x
        assert "Thực hiện lỗ 0,8 tỷ" in hoi("Lợi nhuận khác tháng này bao nhiêu?")
        x = hoi("Khoản nào vượt kế hoạch nhiều nhất?")   # ranked by the gap, never by the plan column
        assert x.startswith("Khuyến mãi và chiết khấu") and "Chênh lệch" in x, x
        x = hoi("Chi phí bán hàng vì sao vượt?")   # no reason of its own: the table that breaks it down, with its notes
        assert "tăng nhiều nhất là Khuyến mãi và chiết khấu 2,6 tỷ" in x and "lệch thời điểm" in x and "rồi Quảng cáo 0,9 tỷ" in x, x
        assert "Lợi nhuận trước thuế ước cả năm 209,0 tỷ" in hoi("Cả năm lợi nhuận có đạt kế hoạch không?")
        browser.close()


def test_the_year_from_a_table_by_month(tmp_path, monkeypatch):
    # gate 2 grader on e735818 (TB16, our own synthetic copy): «cả năm ước đạt bao nhiêu so với kế hoạch» on a table
    # by month (T1 … T12, «Thực hiện», «Dự báo», «Kế hoạch (AOP)» with «Cả năm») answered «chưa thấy số T10»
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "ytd.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_ytd_du_bao.html", src)
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        for q in ("Cả năm ước đạt bao nhiêu so với kế hoạch?", "Doanh thu cả năm ước bao nhiêu?"):
            x = hoi(q)
            assert ("mười tháng đã làm 1.154,5 tỷ" in x and "260,0 tỷ (em tính: 125,0 + 135,0)" in x and "cả năm ước 1.414,5 tỷ" in x
                    and "thấp hơn kế hoạch (AOP) cả năm 1.440,0 tỷ là 25,5 tỷ" in x and "đạt 98,2% (em tính: 1.414,5 ÷ 1.440,0 × 100)" in x), x
        x = hoi("Lũy kế T10 và cả năm ước đạt bao nhiêu so với kế hoạch?")
        assert "T10" not in x.split("Theo bảng")[0] and "Lũy kế T1–T10 thấp hơn kế hoạch (AOP) cùng kỳ 45,5 tỷ (em tính: 1.154,5 − 1.200,0)" in x, x
        browser.close()


def test_a_variance_column_belongs_to_the_plan_its_header_names():
    cols = [clicky_html.Col(h, "", [], "tỷ", k) for h, k in (("Chỉ tiêu", "label"), ("KH gốc", "value"), ("NS linh hoạt", "value"),
            ("Thực hiện", "value"), ("Chênh lệch (TH − NS linh hoạt)", "var"), ("Chênh lệch (TH − KH gốc)", "var"))]
    assert story.var_base(cols, 4) == 2 and story.var_base(cols, 5) == 1
    plain = [clicky_html.Col(h, "", [], "tỷ", k) for h, k in (("Bộ phận", "label"), ("Kế hoạch", "value"), ("Thực hiện", "value"), ("Chênh lệch", "var"))]
    assert story.var_base(plain, 3) is None


def test_new_proposals_are_merged_into_the_owners_script_after_the_line_they_replace(drawn_report):
    # round 22 (Owner): a rebuild over a script he already edited never showed the new soft close; it is written into his
    # file right after the firm close, still off, every line he wrote kept, and the note says what was added
    pytest.importorskip("playwright")
    rep = build(drawn_report)
    path = Path(rep["kich_ban"])
    script = clicky_html.load_script(path)
    for sc in script["canh"]:
        sc["cau"] = [c for c in sc["cau"] if c.get("thay") != "chot"]
    first = script["canh"][0]["cau"][0]
    first["noi"] = first["noi"].replace("Thưa anh chị", "Kính thưa anh chị")
    path.write_text(clicky_html.dump_script(script, drawn_report.name, draft=False), encoding="utf-8")
    soft = lambda: len(re.findall(r"^ +thay: 'chot'", path.read_text(encoding="utf-8"), re.M))   # noqa: E731
    assert soft() == 0
    rep = build(drawn_report)
    text = path.read_text(encoding="utf-8")
    assert "Kính thưa anh chị" in text and soft() == 1
    said = [c for s in clicky_html.load_script(path)["canh"] for c in s["cau"]]
    k = next(i for i, c in enumerate(said) if c.get("thay") == "chot")
    assert said[k - 1].get("vai") == "chot" and not said[k - 1].get("thay") and said[k]["bat"] == "chưa"
    assert any(n.startswith("Đề xuất mới (1 câu)") for n in rep["canh_bao"]), rep["canh_bao"]
    rep = build(drawn_report)                  # once in: never added twice
    assert soft() == 1
    assert not any(n.startswith("Đề xuất mới") for n in rep["canh_bao"])
    assert "thay:" in clicky_html.SCRIPT_HEAD


def test_rerun_four_loss_talk_nature_notes_sources_and_yes_no_nature(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 4 (our own synthetic copy of the shapes): a profit that is a loss was said as
    # «thấp hơn 101,5%» (TB19); a note that names the biggest rise without «do …» was never said (P4); «… có phải
    # vượt ngân sách / tiết kiệm không» about an item with no row of its own was searched word for word (TB17); the
    # source of a figure in a table's foot note was not found (TB21); «Kế hoạch lấy từ …» was read as a row (TB21)
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = (Path(__file__).parent / "fixtures" / "bao_cao_ns_linh_hoat.html").read_text(encoding="utf-8")
    loss = tmp_path / "lo.html"
    loss.write_text(fx.replace("<td>Lợi nhuận trước thuế</td><td>23,0</td><td>19,0</td><td>12,8</td><td>(6,2)</td><td>55,7%</td>",
                               "<td>Lợi nhuận trước thuế</td><td>23,0</td><td>19,0</td><td>(0,3)</td><td>(19,3)</td><td>-1,3%</td>")
                    .replace("Khuyến mãi và chiết khấu tăng do chương trình tri ân nhà phân phối chạy sớm từ tháng 11 sang tháng 10.",
                             "Chương trình khuyến mãi tri ân nhà phân phối của tháng 11 chạy sớm trong tháng 10.")
                    .replace("<tr><td>Doanh thu thuần</td><td>1.380,0</td>",
                             "<tr><td>Giá vốn hàng bán</td><td>760,0</td><td>170,0</td><td>930,0</td><td>960,0</td></tr>\n"
                             "<tr><td>Doanh thu thuần</td><td>1.380,0</td>")
                    .replace('<tr><td>Thuê kho</td><td rowspan="2">4,5</td><td>3,1</td><td rowspan="2">0,5</td></tr>\n<tr><td>Vận chuyển</td><td>1,9</td></tr>',
                             "<tr><td>Thuê kho</td><td>4,5</td><td>3,1</td><td>(1,4)</td></tr>\n<tr><td>Vận chuyển</td><td>–</td><td>1,9</td><td>–</td></tr>")
                    .replace("<td>209,0</td><td>230,0</td></tr>",
                             "<td>209,0</td><td>230,0</td></tr>\n<tr><td>Lợi nhuận sau thuế</td><td>134,4</td><td>32,8</td><td>167,2</td><td>184,0</td></tr>"),
                    encoding="utf-8")
    rep = build(loss)
    cau = [c for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    by = {c.get("vai"): c for c in cau}
    assert by["kq_loi_nhuan"]["noi"] == ("Trước hết là kết quả kinh doanh: lợi nhuận trước thuế thực hiện lỗ ở mức 0,3 tỷ, "
                                         "kế hoạch gốc là lãi 23,0 tỷ; thấp hơn kế hoạch gốc 23,3 tỷ.")
    assert by["kq_loi_nhuan"]["so_tinh"] == ["23,3 = 23,0 + 0,3"] and "%" not in by["kq_loi_nhuan"]["noi"]
    assert by["nguyen_nhan"]["noi"].startswith("Về khuyến mãi và chiết khấu, báo cáo ghi rằng chương trình khuyến mãi tri ân")
    assert "đây là lệch thời điểm, cả năm không vượt ngân sách" in by["nguyen_nhan"]["noi"]
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        x = hoi("Chương trình tri ân nhà phân phối có phải vượt ngân sách không?")
        assert x.startswith("Không. Báo cáo ghi: «Chương trình khuyến mãi tri ân") and "cả năm không vượt ngân sách" in x, x   # «Không» first (TB17b)
        x = hoi("Thưởng doanh số quý 3 có trong ngân sách không?")
        assert "đã có trong ngân sách năm" in x, x
        x = hoi("Lương và thưởng đội bán có phải vượt ngân sách không?")
        assert "đã có trong ngân sách năm" in x and "cao hơn kế hoạch gốc 0,4 tỷ" in x, x
        x = hoi("Bảo dưỡng dây chuyền có phải tiết kiệm không?")
        assert "không phải khoản tiết kiệm" in x and "Duyệt hoãn" not in x, x
        x = hoi("Kế hoạch lấy từ phiên bản nào?")
        assert "AOP 2026 bản Hội đồng duyệt" in x, x
        x = hoi("Số thực hiện lấy từ đâu?")
        assert "«Nguồn: thực hiện từ sổ cái khóa ngày 05/11/2026; ước T11–T12 theo bản dự báo tháng 11.»" in x, x
        assert "sổ cái khóa ngày 05/11/2026" in hoi("Số ước cả năm lấy từ bản nào?")
        x = hoi("Thuê kho thấp hơn kế hoạch bao nhiêu?")   # TB9: one plan on one row, a note says two rows share it
        assert "Kế hoạch của Thuê kho được lập gộp với Vận chuyển" in x and "Gộp lại, thực hiện 5,0 tỷ (em tính: 3,1 + 1,9), cao hơn kế hoạch gốc 0,5 tỷ" in x, x
        x = hoi("Vận chuyển vượt kế hoạch bao nhiêu?")
        assert "Kế hoạch của Vận chuyển được lập gộp với Thuê kho" in x and "4,5 tỷ cho cả 2 dòng" in x, x
        x = hoi("Cả năm ước đạt bao nhiêu so với kế hoạch?")   # TB23: no metric named, the headline first
        assert x.startswith("Doanh thu thuần ước cả năm 1.690,0 tỷ") and "Lợi nhuận sau thuế ước cả năm 167,2 tỷ" in x and "Giá vốn" not in x, x
        browser.close()


def test_a_product_named_by_its_description_answers_from_its_own_effect_column(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 4 (TB22, our own synthetic shape): «<kind + size> làm mất bao nhiêu» never matched
    # the row whose first cell is the item code, and the answer was a page-wide note; the row's own effect column answers
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / "ma_hang.html"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "bao_cao_ma_hang.html", src)
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(700)
        hoi = lambda q: pg.evaluate("q => window.__clickyHoi(q, null)", q)   # noqa: E731
        x = hoi("Giá của kem dưỡng ẩm hũ 50ml làm mất bao nhiêu?")
        assert x.startswith("Kem dưỡng ẩm hũ 50ml (LN-KD50): Ảnh hưởng giá làm giảm 1,4 tỷ") and "Ảnh hưởng sản lượng" not in x and "1,5%" not in x, x
        x = hoi("Kem dưỡng ẩm 50ml làm mất bao nhiêu?")
        assert "Ảnh hưởng sản lượng làm giảm 2,1 tỷ" in x and "Ảnh hưởng giá làm giảm 1,4 tỷ" in x, x
        assert "Ảnh hưởng giá làm tăng 0,6 tỷ" in hoi("Sữa rửa mặt 150ml ảnh hưởng giá bao nhiêu?")
        assert "Ảnh hưởng sản lượng làm giảm 1,8 tỷ" in hoi("Toner 100ml mất bao nhiêu do sản lượng?")
        assert "LN-KD50):" not in hoi("Kem dưỡng 150ml làm mất bao nhiêu?")   # no row of that size
        browser.close()


def test_rerun_five_numbered_asks_sources_are_not_asks_the_voice_and_typed_nature_questions(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 5 (our own synthetic shapes): one paragraph «(1) …; (2) …; (3) …» gave only the
    # first ask (TB25); the plan's source note under the asks was said as an ask (TB24); the voice read «-496,8»,
    # «T10 TH» and «Đề xuất Hội đồng: (1)» as written (TB26); a nature question that carries the row's gap
    # («… thấp hơn 1,2 tỷ, có phải tiết kiệm không?») and a note tied to the row by its amount, or a source note
    # with no «lấy từ», were never found (TB17, TB21). Asked as a reader does: typed into the box, Enter.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = (Path(__file__).parent / "fixtures" / "bao_cao_ns_linh_hoat.html").read_text(encoding="utf-8")
    a = fx.index("<h2>5. Đề nghị Hội đồng quyết</h2>")
    b = fx.index("</ol>", a) + 5
    fx = (fx[:a] + "<h2>5. Đề xuất</h2>\n<p>Đề xuất Hội đồng: (1) duyệt chuyển 2,0 tỷ ngân sách khuyến mãi tháng 11 sang tháng 10; "
          "(2) duyệt hoãn bảo dưỡng dây chuyền sang tháng 12; (3) giao Khối Kinh doanh nộp kế hoạch bù sản lượng trước ngày 05/11.</p>\n"
          "<p>Kế hoạch: AOP 2026 theo Nghị quyết HĐQT số 12/2025.</p>" + fx[b:])
    fx = fx.replace("<li>Số kế hoạch lấy từ AOP 2026 bản Hội đồng duyệt ngày 18/12/2025, không dùng bản điều chỉnh tháng 6.</li>", "")
    fx = fx.replace("<li>Quảng cáo có một hóa đơn của tháng 9 ghi nhận trễ vào tháng 10, là khoản bất thường.</li>",
                    "<li>Khoản 0,9 tỷ là hóa đơn truyền hình của tháng 9 ghi nhận trễ vào tháng 10.</li>")
    fx = fx.replace("<li>Khuyến mãi và chiết khấu tăng do chương trình tri ân nhà phân phối chạy sớm từ tháng 11 sang tháng 10. Đây là lệch thời điểm, cả năm không vượt ngân sách.</li>",
                    "<li>Khoản 2,6 tỷ là chương trình tri ân nhà phân phối của tháng 11 chạy sớm, lệch thời điểm.</li>")
    src = tmp_path / "de_xuat.html"
    src.write_text(fx, encoding="utf-8")
    rep = build(src)
    said = [c["noi"] for s in yaml.safe_load(Path(rep["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    assert "Anh Tuấn xin anh chị quyết ba việc." in said
    assert "Ba là giao Khối Kinh doanh nộp kế hoạch bù sản lượng trước ngày 05/11." in said
    assert not any("Nghị quyết" in x or "(1)" in x or "Đề xuất Hội đồng" in x for x in said)
    # the biggest rise's note names its amount, not its row: still its nature
    assert any(x.startswith("Về khuyến mãi và chiết khấu, báo cáo ghi rằng khoản 2,6 tỷ là chương trình tri ân") for x in said), said
    v = clicky_html.for_voice
    assert v("Chi phí giảm -496,8 triệu.") == "Chi phí giảm 496,8 triệu." and v("Biến động -496,8 triệu") == "Biến động âm 496,8 triệu"
    assert v("Cột T10 TH ghi 37,2 tỷ.") == "Cột thực hiện tháng 10 ghi 37,2 tỷ."
    assert v("Trần 120,0 tỷ: doanh thu tăng 8,0%.") == "Trần 120 tỷ, doanh thu tăng 8%."
    assert v("Giai đoạn 2025-2026, T11–T12 ước 12,05 tỷ, lúc 9:30.") == "Giai đoạn 2025-2026, T11–T12 ước 12,05 tỷ, lúc 9:30."
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        x = typed("Sửa chữa và bảo trì thấp hơn 1,2 tỷ, có phải tiết kiệm không?")
        assert "không phải khoản tiết kiệm" in x, x
        x = typed("Quảng cáo cao hơn kế hoạch 0,9 tỷ, có phải vượt ngân sách không?")
        assert "hóa đơn truyền hình của tháng 9 ghi nhận trễ" in x, x
        x = typed("Số kế hoạch lấy từ bản nào?")
        assert "AOP 2026 theo Nghị quyết HĐQT số 12/2025" in x, x
        browser.close()


def test_rerun_six_yes_no_nature_one_off_shared_budget_footer_sources_and_codes(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 6 (our own synthetic shapes): a yes/no nature question got only a quote, never
    # «Không/Có» first, nor the row without its one-off amount (TB17b); an item only in the notes, asked «tháng 10 có gì
    # bất thường», was parked (TB17c); a footer line «<Cột>: <bản>, duyệt theo …» was not found as the plan's source
    # (TB21); a product row «MÃ – mô tả» asked by its description did not read its price-effect cell (TB22); a note
    # «kế hoạch của B nằm trong ngân sách A» left A's to-date gap said as A's own (TB9); «sự kiện ra mắt» was read
    # as a loss («mất»); the talk named a budget-keeping ask as the way to pull a timing rise down (TB27).
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_ban_chat", "bao_cao_nguon_chan_trang", "bao_cao_ma_hang_mo_ta"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    said = [c for s in yaml.safe_load(Path(reps["bao_cao_ban_chat"]["kich_ban"]).read_text(encoding="utf-8"))["canh"] for c in s["cau"]]
    assert any(c.get("vai") == "nguyen_nhan" and "lệch thời điểm" in c["noi"] for c in said)
    assert not any(c["noi"].startswith("Để kéo phần tăng này xuống") for c in said), [c["noi"] for c in said]
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_ban_chat"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Sửa chữa thấp hơn 1,2 tỷ, có phải tiết kiệm không?")
        assert x.startswith("Không. Báo cáo ghi: «Sửa chữa và bảo trì thấp hơn kế hoạch vì hoãn"), x
        x = typed("Khuyến mãi vượt 2,6 tỷ có phải vượt ngân sách không?")
        assert x.startswith("Không. ") and "cả năm không vượt ngân sách" in x, x
        x = typed("Chi phí quản lý có khoản nào bất thường không?")
        assert x.startswith("Có. ") and "Bỏ khoản một lần 0,8 tỷ ra thì Chi phí quản lý là 13,3 tỷ (em tính: 14,1 − 0,8), thấp hơn kế hoạch gốc 1,7 tỷ (em tính: 13,3 − 15,0)." in x, x
        x = typed("Chi phí quản lý thấp hơn 0,9 tỷ có phải nhờ tiết kiệm thật không?")
        assert x.startswith("Báo cáo không ghi rõ có phải tiết kiệm hay không.") and "13,3 tỷ" in x and "Đúng ạ" not in x, x
        x = typed("Phí tư vấn tháng 10 có gì bất thường không?")
        assert x.startswith("Có. Báo cáo ghi: «Chi phí quản lý tháng 10 có phí tư vấn tái cấu trúc 0,8 tỷ"), x
        x = typed("Truyền thông số lũy kế thấp hơn kế hoạch bao nhiêu?")
        assert "Gộp lại, thực hiện 66,0 tỷ (em tính: 62,0 + 4,0), cao hơn kế hoạch lũy kế 10 tháng 1,0 tỷ" in x, x
        x = typed("Sự kiện ra mắt vượt kế hoạch bao nhiêu?")
        assert "Lãi gộp" not in x and "4,0" in x, x
        pg.goto(Path(reps["bao_cao_nguon_chan_trang"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Số kế hoạch lấy từ bản nào?")
        assert "KH gốc: AOP 2026 bản v3, HĐQT duyệt theo NQ 12/2025/NQ-HĐQT" in x, x
        x = typed("Ngân sách linh hoạt lấy từ đâu?")
        assert "NS linh hoạt: KH gốc điều chỉnh theo sản lượng thực tế" in x, x
        pg.goto(Path(reps["bao_cao_ma_hang_mo_ta"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Kem dưỡng ẩm hũ 50ml giảm giá làm mất bao nhiêu?")
        assert x.startswith("LN-KD50 – Kem dưỡng ẩm hũ 50ml: Ảnh hưởng giá làm giảm 1,4 tỷ (cột «Ảnh hưởng giá» ghi (1,4))."), x
        browser.close()


def test_rerun_seven_one_off_settled_events_shared_budget_elsewhere_new_items_and_product_wording(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 7 (our own synthetic shapes): a note that pins the gap on a one-off reversal
    # still gave «không ghi rõ có phải tiết kiệm» instead of «Không» and the row without it (TB17b); «…; sau điều chỉnh,
    # ròng bằng kế hoạch» lost its first clause, and an offset event was not «Không» for «có gì bất thường» (TB17d);
    # a row whose plan sits in another row's budget, in another table, was said on its own (TB9); «<khoản> là gì, sao
    # không có trong kế hoạch» got an ask, not the note and its actual; «giảm giá <hàng> làm mất bao nhiêu doanh thu»
    # went to the revenue gap (TB22); «tiết kiệm được bao nhiêu» is an amount, not a yes/no.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_ban_chat_2", "bao_cao_ma_hang_mo_ta"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_ban_chat_2"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Sửa chữa thấp hơn 1,2 tỷ, có phải tiết kiệm không?")
        assert x.startswith("Không. Báo cáo ghi: «Khoản chênh 1,2 tỷ của sửa chữa và bảo trì là do hoàn nhập dự phòng"), x
        assert "Bỏ khoản một lần 1,2 tỷ ra thì Sửa chữa và bảo trì là 2,5 tỷ (em tính: 1,3 + 1,2), bằng kế hoạch gốc" in x, x
        x = typed("Lợi nhuận khác có gì bất thường không?")
        assert x.startswith("Không. Báo cáo ghi: «Lợi nhuận khác có khoản phạt hợp đồng 0,5 tỷ của nhà cung cấp; sau điều chỉnh bù trừ"), x
        x = typed("Khuyến mãi tăng 2,6 tỷ có phải lệch thời điểm không?")
        assert x.startswith("Có. "), x
        for q in ("Truyền thông số lũy kế thấp hơn kế hoạch bao nhiêu?", "Chênh lệch TH LK10 của truyền thông số?",
                  "Truyền thông số lũy kế tiết kiệm được bao nhiêu so với kế hoạch?"):
            x = typed(q)
            assert x.startswith("Kế hoạch của Truyền thông số được lập gộp với Sự kiện ra mắt") and "Gộp lại, thực hiện 66,0 tỷ (em tính: 62,0 + 4,0)" in x, (q, x)
        for q in ("Sự kiện ra mắt là gì, sao không có trong kế hoạch?", "Vì sao sự kiện ra mắt không có kế hoạch?"):
            x = typed(q)
            assert x.startswith("Báo cáo ghi: «Sự kiện ra mắt là chương trình giới thiệu dòng serum mới") and "nằm trong ngân sách truyền thông số" in x, (q, x)
            assert "thực hiện lũy kế 10 tháng 4,0 tỷ" in x and "Đề xuất duyệt" not in x, (q, x)
        pg.goto(Path(reps["bao_cao_ma_hang_mo_ta"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Giảm giá kem dưỡng ẩm hũ 50ml làm mất bao nhiêu doanh thu?", "Việc giảm giá kem dưỡng 50ml làm mất bao nhiêu tiền?"):
            x = typed(q)
            assert x.startswith("LN-KD50 – Kem dưỡng ẩm hũ 50ml: Ảnh hưởng giá làm giảm 1,4 tỷ"), (q, x)
        browser.close()


def test_rerun_eight_split_rows_hyphen_plans_new_items_asks_and_product_effects(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 8 (our own synthetic shapes): a row split from another, its plan written «-»
    # and «kế hoạch của dòng này» in the next sentence, left the mother's to-date gap said alone (TB9); «Chi phí KOL là
    # gì, sao không có trong kế hoạch» was read as an unknown abbreviation (TB29); the numbered asks were quoted as the
    # plan's source (TB28); a product table headed «Mã hàng» with «Hiệu ứng giá», asked with «khuyến mãi» or one word
    # and the size, must read its price-effect cell, never the talk's or a note's «% giảm giá» (TB22).
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_ban_chat_3", "bao_cao_ma_hang_hieu_ung"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_ban_chat_3"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Truyền thông số lũy kế chênh lệch bao nhiêu so với kế hoạch?")
        assert x.startswith("Kế hoạch của Truyền thông số (digital) được lập gộp với Chi phí KOL/KOC") and "Gộp lại, thực hiện 66,0 tỷ (em tính: 62,0 + 4,0)" in x, x
        x = typed("Chi phí KOL là gì, sao không có trong kế hoạch?")
        assert x.startswith("Báo cáo ghi: «Từ tháng 7, chi phí KOL được tách khỏi truyền thông số thành dòng riêng. Kế hoạch của dòng này vẫn nằm trong ngân sách truyền thông số.»"), x
        assert "thực hiện lũy kế 10 tháng 4,0 tỷ" in x and "phê duyệt" not in x, x
        x = typed("Số kế hoạch lấy từ bản nào?")
        assert "AOP 2026 bản Hội đồng duyệt ngày 18/12/2025" in x and "(1)" not in x and "phê duyệt" not in x, x
        pg.goto(Path(reps["bao_cao_ma_hang_hieu_ung"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Giảm giá kem dưỡng ẩm hũ 50ml làm mất bao nhiêu?", "Khuyến mãi kem dưỡng ẩm 50ml làm mất bao nhiêu doanh thu?",
                  "Giảm giá dòng kem 50ml làm mất bao nhiêu?"):
            x = typed(q)
            assert x.startswith("LN-KD50 – Kem dưỡng ẩm hũ 50ml: Hiệu ứng giá làm giảm 1,4 tỷ (cột «Hiệu ứng giá» ghi (1,4))."), (q, x)
        browser.close()


def test_rerun_nine_split_department_shared_plan_abbreviations_and_how_much_needs_a_figure(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 9 (our own synthetic shapes): a department split off another, its plan «-»,
    # the note naming it in one sentence and the shared budget in the next, left the parent's to-date gap said alone and
    # the wrong way round (TB9); «Bộ phận TMĐT là gì?» (an abbreviation the note spells out, no «khoản») fell to «chưa
    # thấy» or to «bỏ … ra» (TB29); «… làm thiệt / hụt / mất bao nhiêu» by product got words, not the effect cell (TB22).
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_bo_phan_tach", "bao_cao_ma_hang_hieu_ung"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_bo_phan_tach"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Khối Kinh doanh lũy kế chênh lệch bao nhiêu so với kế hoạch?", "Chênh lệch lũy kế của Khối Kinh doanh?"):
            x = typed(q)
            assert x.startswith("Kế hoạch của Khối Kinh doanh được lập gộp với Thương mại điện tử (từ T7)"), (q, x)
            assert "Gộp lại, thực hiện 41.729,0 triệu (em tính: 38.489,0 + 3.240,0), cao hơn kế hoạch lũy kế 10 tháng 729,0 triệu" in x, (q, x)
        for q in ("Bộ phận TMĐT là gì?", "TMĐT là gì, kế hoạch nằm ở đâu?"):
            x = typed(q)
            assert x.startswith("Báo cáo ghi: «Bộ phận thương mại điện tử (TMĐT) bán hàng qua sàn trực tuyến, tách khỏi Khối Kinh doanh từ tháng 7/2026.» «Kế hoạch năm vẫn tính trong ngân sách Khối Kinh doanh"), (q, x)
            assert "thực hiện tháng 10 820,0 triệu" in x and "thực hiện lũy kế 10 tháng 3.240,0 triệu" in x and "duyệt" not in x, (q, x)
        pg.goto(Path(reps["bao_cao_ma_hang_hieu_ung"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Giảm giá kem dưỡng ẩm hũ 50ml làm thiệt bao nhiêu?", "Kem dưỡng ẩm 50ml bị cắt giá thì hụt bao nhiêu?", "Sale kem dưỡng ẩm 50ml mất bao nhiêu?"):
            x = typed(q)
            assert x.startswith("LN-KD50 – Kem dưỡng ẩm hũ 50ml: Hiệu ứng giá làm giảm 1,4 tỷ"), (q, x)
        browser.close()


def test_rerun_ten_blank_child_plans_abbreviated_labels_and_never_against_the_reports_own_sentence(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 10 (our own synthetic shapes): a child row with blank plan cells, its name a label
    # «Thương mại điện tử (TMĐT)» and the notes saying only «TMĐT», still left the parent's to-date gap said alone (TB9);
    # «Kênh hiện đại (gồm TMĐT) lũy kế …» found nothing; the parent's own figure ran against the report's own sentence on
    # the same row and span; «TMĐT» was cut to «TM» as an unexplained abbreviation, and «là gì» quoted an ask (TB29).
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_kenh_tach", "bao_cao_kenh_loi_binh"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_kenh_tach"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Kênh hiện đại lũy kế chênh lệch bao nhiêu so với kế hoạch?", "Kênh hiện đại (gồm TMĐT) lũy kế vượt bao nhiêu?"):
            x = typed(q)
            assert x.startswith("Kế hoạch của Kênh hiện đại được lập gộp với Thương mại điện tử (TMĐT)"), (q, x)
            assert "cao hơn kế hoạch lũy kế 10 tháng 729,0 triệu" in x, (q, x)
        for q in ("Bộ phận TMĐT là gì?", "TMĐT là bộ phận gì?", "TMĐT là gì, sao không có trong kế hoạch?"):
            x = typed(q)
            assert x.startswith("Báo cáo ghi: «TMĐT bán hàng qua sàn trực tuyến, tách khỏi kênh hiện đại từ tháng 7/2026.» «Kế hoạch năm vẫn tính trong kế hoạch kênh hiện đại.»"), (q, x)
            assert "thực hiện tháng 10 820,0 triệu" in x and "thực hiện lũy kế 10 tháng 3.240,0 triệu" in x and "duyệt" not in x and "«TM»" not in x, (q, x)
        pg.goto(Path(reps["bao_cao_kenh_loi_binh"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Kênh hiện đại lũy kế chênh lệch bao nhiêu so với kế hoạch?")
        assert x.startswith("Báo cáo ghi: «Lũy kế 10 tháng, kênh hiện đại vượt kế hoạch 729 triệu.» Số trên bảng của riêng dòng Kênh hiện đại thì ngược chiều"), x
        assert "Hai chỗ này lệch nhau" in x, x
        x = typed("Kênh hiện đại tháng 10 thấp hơn kế hoạch bao nhiêu?")
        assert x.startswith("Kênh hiện đại so với kế hoạch tháng 10, thấp hơn 550,0 triệu"), x
        browser.close()


def test_rerun_eleven_a_row_with_its_own_plan_is_never_merged_and_the_reports_other_figure_leads(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 11 (our own synthetic shapes): the shared-plan merge took in a row whose note never
    # named it a child (a cost row with its own plan) and mixed the month's and the to-date rows; a report sentence giving
    # another figure for the same row and span did not lead; «là gì» gave no to-date actual; a yes/no answer quoted the asks.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_kenh_thang_luy_ke.html"
    src.write_text((fx / "bao_cao_kenh_thang_luy_ke.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        lead = "Kế hoạch của Kênh hiện đại được lập gộp với Thương mại điện tử (TMĐT): "
        x = typed("Kênh hiện đại lũy kế chênh lệch bao nhiêu so với kế hoạch?")   # to date: the to-date rows only
        assert x.startswith(lead + "kế hoạch 41.000,0 triệu cho cả 2 dòng"), x
        assert "(em tính: 38.489,0 + 3.240,0)" in x and "cao hơn kế hoạch 729,0 triệu" in x and "280,0" not in x, x
        x = typed("Kênh hiện đại tháng 10 chênh lệch bao nhiêu so với kế hoạch?")   # the month: the month's rows only
        assert x.startswith(lead + "kế hoạch 4.200,0 triệu cho cả 2 dòng"), x
        assert "(em tính: 3.650,0 + 820,0)" in x and "cao hơn kế hoạch 270,0 triệu" in x and "3.240" not in x, x
        x = typed("Chi phí vận chuyển theo sản lượng chênh lệch bao nhiêu so với kế hoạch?")   # its own plan: never merged
        assert x.startswith("Chi phí vận chuyển theo sản lượng thấp hơn kế hoạch 20,0 triệu"), x
        assert "gộp" not in x and "em tính" not in x, x
        x = typed("Bộ phận TMĐT là gì?")
        assert x.startswith("Báo cáo ghi: «TMĐT bán hàng qua sàn trực tuyến, tách khỏi kênh hiện đại từ tháng 7/2026.» «Kế hoạch năm vẫn tính trong kế hoạch kênh hiện đại.»"), x
        assert "thực hiện tháng 10 820,0 triệu" in x and "thực hiện lũy kế 10 tháng 3.240,0 triệu" in x and "duyệt" not in x, x
        x = typed("Kênh hiện đại có vượt ngân sách không?")   # never the asks; the table's period said when the note's differs
        assert "Đề xuất" not in x and "duyệt" not in x and "Số trên bảng (tháng 10):" in x, x
        x = typed("Kênh truyền thống lũy kế chênh lệch bao nhiêu so với kế hoạch?")   # the report's own other figure leads
        assert x.startswith("Báo cáo ghi: «Lũy kế 10 tháng, kênh truyền thống vượt kế hoạch 1.200 triệu.» Số trên bảng của riêng dòng Kênh truyền thống thì khác:"), x
        assert "ngược chiều" not in x and "900,0" in x, x
        x = typed("Kênh truyền thống tháng 10 chênh lệch bao nhiêu so với kế hoạch?")
        assert x.startswith("Kênh truyền thống cao hơn kế hoạch 150,0 triệu"), x
        browser.close()


def test_rerun_twelve_two_plan_columns_keep_their_own_names_and_year_answers_never_take_a_month_sentence(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 12 (our own synthetic shapes): a one-row volume-cost table with «KH gốc» and «KH linh hoạt»
    # under a to-date heading, its label with «(lũy kế)», lost its own-plan comparison and could name one plan's gap by the other;
    # a yes/no question on the year led with the month's conclusion and a false «lệch nhau»; «EBIT» was written «eBIT».
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_hai_ke_hoach_ky.html"
    src.write_text((fx / "bao_cao_hai_ke_hoach_ky.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Chi phí bao bì theo sản lượng chênh lệch bao nhiêu so với kế hoạch?", "Chi phí bao bì theo sản lượng vượt kế hoạch bao nhiêu?",
                  "Chi phí bao bì theo sản lượng lũy kế chênh lệch bao nhiêu?"):
            x = typed(q)
            assert "thấp hơn kế hoạch gốc 0,2 tỷ (em tính: 11,8 − 12,0)" in x, (q, x)   # both comparisons, each with its own plan
            assert "So với kế hoạch linh hoạt, cao hơn 0,8 tỷ (cột «Chênh so KH linh hoạt» ghi 0,8)" in x, (q, x)
            for s in x.split(". "):                # never one plan's name with the other plan's column or figure
                assert not ("gốc" in s and ("linh hoạt" in s or "0,8" in s)), (q, s)
        x = typed("Chi phí bao bì theo sản lượng có vượt ngân sách không?")
        assert x.startswith("Không so với kế hoạch gốc; có so với kế hoạch linh hoạt"), x
        x = typed("EBIT cả năm có đạt kế hoạch không?")
        assert x.startswith("Có. EBIT ước cả năm 236,0 tỷ"), x
        assert "tháng 10 thấp hơn" not in x and "lệch nhau" not in x and "eBIT" not in x and "Gửi anh" not in x, x
        x = typed("EBIT cả năm chênh lệch bao nhiêu so với kế hoạch?")
        assert x.startswith("EBIT ước cả năm 236,0 tỷ, cao hơn kế hoạch năm 6,0 tỷ") and "Báo cáo ghi" not in x, x
        x = typed("EBIT tháng 10 có đạt kế hoạch không?")
        assert x.startswith("Không. EBIT") and "thấp hơn 3,2 tỷ" in x, x
        browser.close()


def test_rule_book_a_judgement_question_never_gets_a_yes_or_no_verdict(tmp_path, monkeypatch):
    # Sổ quy tắc, rule 1 (gate 2's last blocker): «… có đáng lo không?» is the owner's call. The page may say the gap and its
    # direction, but never opens with «Có.» / «Không.» on the judgement, and says who judges it.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_hai_ke_hoach_ky.html"
    src.write_text((fx / "bao_cao_hai_ke_hoach_ky.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("EBIT tháng 10 thấp hơn kế hoạch có đáng lo không?", "EBIT tháng 10 hụt kế hoạch như vậy có đáng lo ngại không?",
                  "Chi phí bao bì theo sản lượng vượt kế hoạch có đáng lo không?", "EBIT cả năm vượt kế hoạch có ổn không?",
                  "EBIT tháng 10 thấp hơn kế hoạch có nghiêm trọng không?"):
            x = typed(q)
            assert not re.match(r"^(Có|Không|Chưa)\b", x), (q, x)                 # no verdict, on either part
            assert not re.search(r"\b(đáng lo|không đáng lo|nghiêm trọng|ổn)\s*[.,]", x.split("không đánh giá")[0]), (q, x)
            assert "không đánh giá" in x, (q, x)                                  # the judgement is handed to the owner, in words
        browser.close()


def test_rerun_thirteen_a_judgement_is_never_answered_yes_or_no_and_a_one_off_note_keeps_its_period(tmp_path, monkeypatch):
    # independent grader, gate 2 rerun 13 (our own synthetic shapes): «… vượt …, có đáng lo không?» was led by «Có.», a guessed
    # judgement; two plan columns did not say their difference is the volume; a to-date question quoted a month note on a one-off
    # with no period and called it «chưa tách riêng», the shared-plan words.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_hai_ke_hoach_ky", "bao_cao_bo_phan_mot_lan"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):                          # the reader's way: Escape, skip the talk, type in «Hỏi Vera», Enter
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "return ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).innerText; }")
        pg.goto(Path(reps["bao_cao_hai_ke_hoach_ky"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        vol = "Hai cột kế hoạch lệch nhau 1,0 tỷ (em tính: 12,0 − 11,0); phần này do sản lượng thực tế thấp hơn kế hoạch"
        for q in ("Chi phí bao bì theo sản lượng vượt kế hoạch, có đáng lo không?",
                  "Chi phí bao bì theo sản lượng vượt kế hoạch linh hoạt 0,8 tỷ, có đáng lo không?"):
            x = typed(q)
            assert x.startswith("Chi phí bao bì theo sản lượng (lũy kế)"), (q, x)   # the figures, never «Có.» / «Không.»
            assert vol in x and "không đánh giá có đáng lo hay không" in x and "cao hơn kế hoạch linh hoạt 0,8 tỷ" in x.replace("So với kế hoạch linh hoạt, cao hơn", "cao hơn kế hoạch linh hoạt"), (q, x)
        x = typed("EBIT tháng 10 thấp hơn kế hoạch, có sao không?")
        assert x.startswith("EBIT thấp hơn kế hoạch 3,2 tỷ"), x
        x = typed("EBIT tháng 10 có đạt kế hoạch không?")          # a yes/no on the figures keeps its «Không.»
        assert x.startswith("Không. EBIT"), x
        pg.goto(Path(reps["bao_cao_bo_phan_mot_lan"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        x = typed("Marketing lũy kế chênh lệch bao nhiêu so với kế hoạch?")
        assert x.startswith("Marketing so với kế hoạch lũy kế 10 tháng, cao hơn 1,1 tỷ"), x
        assert "0,6" not in x and "chưa tách riêng" not in x and "Báo cáo ghi" not in x, x
        x = typed("Marketing tháng 10 chênh lệch bao nhiêu so với kế hoạch?")   # the month: the note on its own gap, as a cause
        assert x.startswith("Marketing so với kế hoạch tháng 10, cao hơn 0,6 tỷ"), x
        assert "Báo cáo ghi: «Marketing vượt 0,6 tỷ do phí sự kiện ra mắt một lần 0,5 tỷ" in x and "chưa tách riêng" not in x, x
        browser.close()


def test_an_english_question_gets_an_english_answer_with_every_figure_locked(tmp_path, monkeypatch):
    # owner's choice for the 25/11 meeting: a board member may ask in English. The page asks the Vietnamese question, then words
    # the answer in English only if every figure and unit matches (the number lock); the report's own «quoted» words and row
    # names stay as printed. A Vietnamese question is answered exactly as before.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_hai_ke_hoach_ky", "bao_cao_bo_phan_mot_lan"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(reps["bao_cao_hai_ke_hoach_ky"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("What was the revenue variance vs plan in October?")
        assert "came in VND 9.0 bn below plan" in a and "(«Chênh lệch» column: (9.0))" in a and "Exact: Figure printed in the report" in a, a
        a = typed("Why was EBIT below plan in October?")
        assert "VND 3.2 bn below plan" in a and "The report says: «EBIT tháng 10 thấp hơn kế hoạch 3,2 tỷ" in a, a
        a = typed("Did EBIT meet the full-year plan?")
        assert "236.0 bn" in a and "102.6%" in a and "(worked out: 236.0 ÷ 230.0 × 100)" in a and "Estimate:" in a, a
        a = typed("What's the full-year EBIT forecast?")   # the column says forecast; «dự báo» is not a row word
        assert "«EBIT», full-year estimate («Ước cả năm»): VND 236.0 bn" in a, a
        a = typed("What is the full-year estimate versus plan?")   # no row named: never the last question's row (grader rerun 17)
        assert "EBIT" not in a.split("I read")[0] or "Doanh thu" in a, a
        assert "I read your question as: «Cả năm" in a or a.startswith("I could not match"), a
        a = typed("EBIT in October is below plan, is it worrying?")
        assert "does not judge whether this is a concern" in a and not a.startswith(("Yes", "No")), a
        a = typed("What was our competitor's profit this year?")   # never a «not in the report» claim on an English reading
        assert a.startswith("I could not match your English question") and "not in the report" not in a and "bn" not in a, a
        a = typed("Doanh thu thuần tháng 10 chênh lệch bao nhiêu so với kế hoạch?")
        assert "thấp hơn kế hoạch 9,0 tỷ" in a and "below plan" not in a, a
        pg.goto(Path(reps["bao_cao_bo_phan_mot_lan"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("Why is Marketing over plan in October?")
        assert "VND 0.6 bn above the October plan" in a and "«Marketing vượt 0,6 tỷ" in a and "could not word" not in a, a
        a = typed("What is the Marketing variance vs plan year to date?")
        assert "VND 1.1 bn above the 10-month YTD plan" in a, a
        assert "I read your question as: «Marketing lũy kế chênh lệch bao nhiêu so với kế hoạch?»" in a, a
        # grader rerun 15 classes: words not understood never reach the row matcher, a reading on the wrong row is not
        # shown, a label glued to digits (T10) is not a figure
        for q in ("How many months does the packaging compare cover?", "How much did Marketing spend on bonuses?", "What is the EBITDA margin?"):
            a = typed(q)
            assert a.startswith("I could not match your English question") and not re.search(r"\d", a.split("I read")[0]), (q, a)
        # an English department name is used only because the report prints its Vietnamese name; a follow-up keeps the subject
        a = typed("What is admin year to date versus budget?")
        assert "Admin («Hành chính») was VND 0.5 bn below the 10-month YTD plan" in a, a
        a = typed("Is that worrying?")
        assert "does not judge whether this is a concern" in a and "Hành chính" in a and not a.startswith(("Yes", "No")), a
        a = typed("Why is Sales over plan?")   # «Bán hàng» is not printed in this report: never answered from another row
        assert a.startswith("I could not match your English question") and "What is Marketing versus plan?" in a, a
        for q, want in (("How much did Marketing overspend in October?", "VND 0.6 bn"), ("What caused the Marketing overrun in October?", "«Marketing vượt 0,6 tỷ"),
                        ("Is Marketing over budget year to date?", "1.1 bn")):
            a = typed(q)
            assert want in a and "could not" not in a and "T 10" not in a, (q, a)
        # follow-ups: «And year to date?» keeps the last subject and measure; «this month» never takes the last period;
        # a question that was not answered is never the «that» of the next one
        a = typed("How much did Marketing overspend in October?")
        a = typed("And year to date?")
        assert "VND 1.1 bn above the 10-month YTD plan" in a and "«Marketing lũy kế vượt kế hoạch bao nhiêu?»" in a, a
        a = typed("And year to date for admin?")
        assert "Admin («Hành chính»)" in a and "10-month YTD plan" in a and "(0.5)" in a, a
        a = typed("What's the year-to-date marketing budget?")
        a = typed("How about admin costs this month?")
        assert "Oct actual («TH T10») VND 1.9 bn" in a and "I read your question as: «Hành chính chi phí bao nhiêu?»" in a, a
        # grader rerun 19 misses: «this year so far», «first ten months», a savings framing, «they»
        a = typed("How much did we spend on Marketing this year so far?")
        assert "YTD Oct actual («TH LK10»): VND 31.1 bn" in a, a
        a = typed("How much over plan were they?")
        assert "VND 1.1 bn above the 10-month YTD plan" in a, a
        a = typed("What did admin cost for the first ten months?")
        assert "VND 19.5 bn" in a and "TH LK10" in a, a
        a = typed("How much did admin save year to date?")
        assert "VND 0.5 bn below the 10-month YTD plan" in a, a
        a = typed("Which division overspent?")
        a = typed("What's driving that?")
        assert a.startswith("I could not match your English question"), a
        browser.close()


def test_the_english_number_lock_refuses_a_lost_or_changed_figure(tmp_path):
    # the page's own English module, run in node: a wording that drops, changes or adds a figure or unit is refused
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    layer = (Path(__file__).parent.parent / "present" / "clicky_html_layer.html").read_text(encoding="utf-8")
    mod = layer[layer.index("// --- English questions and answers (Hộp Vera"):layer.index("function answer(text, target, o) {")]
    js = tmp_path / "lock.js"
    js.write_text(mod + """
const vi = "Doanh thu thuần thấp hơn kế hoạch 9,0 tỷ (cột «Chênh lệch» ghi (9,0)). Marketing so với kế hoạch tháng 10, cao hơn 0,6 tỷ.";
const out = [enLock(vi, enText(vi)),
  enLock(vi, "Revenue is below plan by 9.0 bn (column «Chênh lệch» shows (9.0)). Marketing against the October plan: higher by 0.6 bn."),
  enLock(vi, "Revenue is below plan by 9.0 bn. Marketing against the October plan: higher by 0.6 bn."),
  enLock(vi, "Revenue is below plan by 9.0 m (column «Chênh lệch» shows (9.0)). Marketing against the October plan: higher by 0.6 bn."),
  enLock(vi, "Revenue is below plan by 9.0 bn (column «Chênh lệch» shows (9.0)). Marketing against the October plan: higher by 0.6 bn, 10 more."),
  enLock(vi, "Revenue is below plan by 90 bn (column «Chênh lệch» shows (9.0)). Marketing against the October plan: higher by 0.6 bn."),
  !!enLeft("Marketing cao hơn kế hoạch của tháng"), !!enLeft("Marketing against plan (column «Chênh lệch của T10» shows 0.6)")];
console.log(JSON.stringify(out));
""", encoding="utf-8")
    got = subprocess.run([node, str(js)], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout) == [True, True, False, False, False, False, True, False]


def test_an_abbreviation_the_report_spells_out_is_read_whole_in_both_languages(tmp_path, monkeypatch):
    # grader rerun 17: «CSKH là gì?» for a row label «Chăm sóc khách hàng (CSKH)» was said letter by letter («C», «h», …);
    # in English «What does CSKH mean?» was taken for Vietnamese and parked as «not in the report».
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_cskh.html"
    src.write_text((fx / "bao_cao_bo_phan_mot_lan.html").read_text(encoding="utf-8").replace("Hành chính", "Chăm sóc khách hàng (CSKH)"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("CSKH là gì?")
        assert a.startswith("CSKH là viết tắt của «Chăm sóc khách hàng».") and "«C»" not in a, a
        a = typed("What does CSKH mean?")
        assert a.startswith("In this report, CSKH is written next to «Chăm sóc khách hàng».") and "chưa tìm thấy" not in a, a
        a = typed("What is customer service in October versus plan?")   # the English name, because the report prints it
        assert "«Chăm sóc khách hàng (CSKH)» was VND 0.1 bn below the October plan" in a, a
        a = typed("How much did CSKH spend this year so far?")   # the abbreviation asks the row it is printed next to
        assert "«Chăm sóc khách hàng (CSKH)», YTD Oct actual («TH LK10»): VND 19.5 bn" in a, a
        browser.close()


def test_a_scope_note_is_said_only_for_its_own_row_and_period(tmp_path, monkeypatch):
    # grader rerun 23: «Ngân sách Marketing gồm cả … trưng bày» was quoted beside Trưng bày; a month's one-off note
    # («… một lần …, khoản này chưa tính trong kế hoạch») was quoted beside a to-date figure.
    # English: «end up this year» is the full-year estimate, never the remaining-months column; that column only when asked.
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_hoi_hdqt", "bao_cao_bo_phan_mot_lan"):
        src = tmp_path / (name + ".html")
        src.write_text((fx / (name + ".html")).read_text(encoding="utf-8"), encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(reps["bao_cao_hoi_hdqt"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("Trưng bày bao nhiêu?")
        assert "Thực hiện 4,2 tỷ" in a and "Ngân sách Marketing gồm cả" not in a, a
        a = typed("Marketing bao nhiêu?")
        assert "Ngân sách Marketing gồm cả 1,0 tỷ" in a, a
        a = typed("Where will EBIT end up this year?")
        assert "VND 198.4 bn" in a and "38.0" not in a, a
        a = typed("What is the EBIT forecast for the remaining two months?")
        assert "«EBIT», «Ước T11–T12»: VND 38.0 bn" in a, a
        # grader rerun 24: «AOP», «annual plan» and «this year» on a variance or yes/no question mean the full-year AOP,
        # never the October variance; a month named beside AOP stays monthly
        a = typed("Where will EBIT end up versus AOP?")
        assert "17.6" in a and "5.7" not in a and "AOP năm" in a, a
        a = typed("Will we beat AOP on revenue this year?")
        assert "25.5" in a and "7.5" not in a, a
        a = typed("How is net revenue tracking against the annual plan?")
        assert "25.5" in a and "7.5" not in a, a
        a = typed("What is October revenue versus AOP?")
        assert "7.5" in a and "25.5" not in a, a
        pg.goto(Path(reps["bao_cao_bo_phan_mot_lan"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("Marketing TH LK10 bao nhiêu?")
        assert "31,1 tỷ" in a and "một lần" not in a, a
        a = typed("Marketing TH T10 bao nhiêu?")
        assert "3,6 tỷ" in a and "một lần 0,5 tỷ" in a, a
        browser.close()


def test_a_full_year_english_question_is_answered_in_english(tmp_path, monkeypatch):
    # grader rerun 20: a full-year figure fell back to the Vietnamese wording («T1–T10» read as two labels, «dòng» left);
    # a «10+2» column is the full-year estimate
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    reps = {}
    for name in ("bao_cao_ytd_du_bao", "bao_cao_hoi_hdqt", "bao_cao_ca_nam_theo_cot", "bao_cao_hai_ke_hoach_ky", "bao_cao_ns_linh_hoat", "bao_cao_kenh_thang_luy_ke", "bao_cao_kenh_tach"):
        src = tmp_path / (name + ".html")
        html = (fx / (name + ".html")).read_text(encoding="utf-8")
        if name == "bao_cao_hai_ke_hoach_ky":   # grader rerun 21: column headers written as a first row of td cells
            html = html.replace("<th>Ước cả năm</th>", "<th>Cả năm ước (10+2)</th>")   # rerun 22: a label carrying figures
            html = html.replace("<th>", "<td>").replace("</th>", "</td>")
        src.write_text(html, encoding="utf-8")
        reps[name] = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(reps["bao_cao_ytd_du_bao"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("What is the full-year revenue forecast?")
        assert "a full-year estimate of VND 1,414.5 bn" in a and "Sum of Jan–Oct" in a and "could not word" not in a, a
        pg.goto(Path(reps["bao_cao_hoi_hdqt"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("Where will EBIT land for the full year?")
        assert "VND 198.4 bn" in a and "«EBIT 10+2 bao nhiêu?»" in a, a
        pg.goto(Path(reps["bao_cao_hai_ke_hoach_ky"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("What's the full-year EBIT forecast?")   # a one-cell answer whose header is a td cell is still worded in English
        assert "«EBIT», «Cả năm ước (10+2)»: VND 236.0 bn" in a, a
        pg.goto(Path(reps["bao_cao_ca_nam_theo_cot"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("How much did Marketing spend in October?")   # the month report's plain «Thực hiện» column, from Marketing's own table
        assert "actual («Thực hiện»): VND 5.4 bn" in a, a
        a = typed("How much did Marketing spend this year so far?")   # that table has no year-to-date column: never another table's figure
        assert a.startswith("I could not match your English question"), a
        pg.goto(Path(reps["bao_cao_ns_linh_hoat"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        typed("How did net revenue do against the flexible budget in October?")
        a = typed("And as a percentage of the original plan?")   # a percentage is never answered with one amount column
        assert "VND 150.0 bn" not in a, a
        # rerun 22: a pair of tables with the same columns, one for the month and one to date, told apart by their headings
        pg.goto(Path(reps["bao_cao_kenh_thang_luy_ke"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("What did general trade sell this year so far?")
        assert "actual («TH»): VND 20,900.0 m" in a and "Year to date: these figures are from the table «2. Doanh thu theo kênh lũy kế 10 tháng»" in a, a
        a = typed("How much did general trade sell in October?")   # never the to-date table's figure
        assert "20,900" not in a and "20.900" not in a, a
        # a percentage asked on a line whose plan is shared: never answered with amounts only
        pg.goto(Path(reps["bao_cao_kenh_tach"]["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("What percentage of plan did modern trade reach in October?")
        assert "%" in a.split("I read")[0] or a.startswith("I could not match your English question"), a
        browser.close()


def test_english_can_be_turned_off_for_a_report(tmp_path, monkeypatch):
    # fallback for a meeting: CLICKY_EN=0 builds a report where an English question gets no figure, Vietnamese works as before
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    monkeypatch.setenv("CLICKY_EN", "0")
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_hoi_hdqt.html"
    src.write_text((fx / "bao_cao_hoi_hdqt.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        a = typed("Will gross profit beat AOP this year?")
        assert "chỉ trả lời bằng tiếng Việt" in a and not any(ch.isdigit() for ch in a), a
        a = typed("What is October revenue versus AOP?")
        assert "chỉ trả lời bằng tiếng Việt" in a and "7" not in a, a
        a = typed("Doanh thu thuần tháng 10 chênh lệch bao nhiêu so với kế hoạch?")
        assert "7,5 tỷ" in a and "tiếng Việt" not in a, a
        browser.close()


def test_a_block_spend_question_never_gets_the_total_row_or_a_word_matched_note(tmp_path, monkeypatch):
    # grader rerun 25: «Khối quản lý tháng 10 chi bao nhiêu?» was answered with «Tổng chi phí hoạt động» (the block is not a row;
    # «quản» sat in the title's «quản trị»); «Kế toán … chi bao nhiêu?» quoted a note that says «hạch toán … kế hoạch»
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    fx = Path(__file__).parent / "fixtures"
    src = tmp_path / "bao_cao_khoi_khong_dong.html"
    src.write_text((fx / "bao_cao_khoi_khong_dong.html").read_text(encoding="utf-8"), encoding="utf-8")
    rep = build(src)
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 900})

        def typed(q):
            pg.keyboard.press("Escape")
            pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
            pg.wait_for_timeout(300)
            box = pg.locator("#clicky-host #q")
            box.fill(q)
            box.press("Enter")
            pg.wait_for_timeout(900)
            return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                               "const a = ((b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a')).cloneNode(true); "
                               "a.querySelectorAll('.receipt').forEach(x => x.remove()); return a.innerText; }")
        pg.goto(Path(rep["out"]).as_uri())
        pg.wait_for_timeout(1500)
        for q in ("Khối quản lý tháng 10 chi bao nhiêu?", "Khối quản lý lũy kế chi bao nhiêu?", "Khối văn phòng tháng 10 chi bao nhiêu?"):
            a = typed(q)
            assert not any(x in a for x in ("36,0", "37,2", "348,0", "351,2")), (q, a)
        a = typed("Kế toán tháng 10 chi bao nhiêu?")
        assert "Báo cáo viết" not in a and "thuê kho" not in a, a
        a = typed("Kho tháng 10 tăng bao nhiêu?")   # the note that names it is still quoted
        assert "thuê kho tăng 0,3 tỷ" in a, a
        a = typed("Tổng chi phí hoạt động tháng 10 bao nhiêu?")
        assert "37,2 tỷ" in a, a
        a = typed("Chi phí QLDN tháng 10 bao nhiêu?")
        assert "11,4 tỷ" in a, a
        browser.close()
