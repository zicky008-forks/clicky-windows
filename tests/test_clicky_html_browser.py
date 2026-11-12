"""Clicky inside an HTML report, seen in a real (headless) browser: nothing of Clicky covers a figure, and a figure
can be clicked to ask about it. Skipped where no browser can run (the Windows CI has none)."""
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from present import clicky_html, kiem_hinh  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "bao_cao_html_gia_lap.html"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    dst = tmp_path_factory.mktemp("bc") / "bao_cao.html"
    shutil.copyfile(FIXTURE, dst)
    out = Path(clicky_html.build(str(dst), voice=lambda t: t.encode("utf-8"))["out"])
    mp.undo()
    return out


@pytest.fixture
def page(built):   # per test: a running sync browser blocks kiem_hinh.check() in later tests
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(built.as_uri())
        yield pg, errors
        browser.close()


def test_nothing_of_clicky_covers_a_figure(built):
    res = kiem_hinh.check(str(built))
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert res["ok"], res["problems"]
    assert res["checked"] >= 40 and res["sizes"] == ["1366x768", "1920x1080"]


def test_a_bar_that_covers_the_report_is_caught(built, tmp_path):
    bad = tmp_path / "che_so.html"
    bad.write_text(built.read_text(encoding="utf-8").replace("bottom:12px;z-index", "bottom:12px;top:0;z-index", 1),
                   encoding="utf-8")
    res = kiem_hinh.check(str(bad), sizes=((1366, 768),))
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert not res["ok"] and any("che số" in p for p in res["problems"])
    assert kiem_hinh.main([str(bad)]) == 1


def _answer(pg, where="ba"):
    return pg.evaluate(f"document.getElementById('clicky-host').shadowRoot.getElementById('{where}').innerText")


def _box(pg, id_):
    return pg.evaluate(f"(() => {{ const r = document.getElementById('clicky-host').shadowRoot.getElementById('{id_}')"
                       ".getBoundingClientRect(); return {x: r.left, y: r.top, width: r.width, height: r.height}; })()")


def _apart(a, b):
    return (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
            or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


def test_click_a_figure_to_ask_about_it(page):
    pg, errors = page
    cell = pg.locator("table[data-clicky] tr").nth(2).locator("td").nth(2)      # Khối Marketing, AOPR
    assert cell.evaluate("e => getComputedStyle(e).cursor") == "help"
    cell.click()
    pg.wait_for_timeout(700)
    text = _answer(pg)
    assert "Khối Marketing, AOPR: 14,0." in text and "đúng ô đang khoanh" in text and "Cả dòng Khối Marketing" in text
    # the answer opens right beside the cell, never on it, and the side panel stays closed
    bub, box = _box(pg, "bub"), cell.bounding_box()
    assert bub["width"] > 0 and _apart(bub, box)
    assert abs(bub["y"] - (box["y"] + box["height"])) < 60 or abs(bub["y"] + bub["height"] - box["y"]) < 60
    assert _box(pg, "panel")["width"] == 0
    # nor on the figures the reader compares it with: its own row and its own column
    rows = pg.locator("table[data-clicky] tr")
    same = [rows.nth(2).locator("td").nth(i).bounding_box() for i in range(1, 6)]
    same += [rows.nth(i).locator("td").nth(2).bounding_box() for i in range(1, 8)]
    assert all(_apart(bub, x) for x in same)
    # a follow-up typed in the bubble keeps the place: "so với AOP gốc" is read on this row
    pg.evaluate("(() => { const s = document.getElementById('clicky-host').shadowRoot;"
                " s.getElementById('bq').value = 'AOP gốc thì sao?'; s.getElementById('bf').requestSubmit(); })()")
    pg.wait_for_timeout(500)
    assert _answer(pg).startswith("Khối Marketing, AOP")
    pg.locator('[data-clicky="c3"]').click()                                    # a number card
    pg.wait_for_timeout(700)
    assert _answer(pg).startswith("Headcount bình quân: 1.248 người") and "Nghe lại đoạn" in _answer(pg)
    pg.keyboard.press("Escape")
    assert _box(pg, "bub")["width"] == 0
    assert not errors


def test_click_a_paragraph_or_heading(page):
    pg, errors = page
    para = pg.locator("p").first
    para.click()
    pg.wait_for_timeout(600)
    said = _answer(pg)
    assert said and said.split()[0] in para.inner_text()
    assert _apart(_box(pg, "bub"), para.bounding_box())
    pg.locator("h2").first.click()
    pg.wait_for_timeout(600)
    assert _answer(pg).startswith("Phần «")
    assert not errors


def test_draw_a_box_around_part_of_the_report(page):
    pg, errors = page
    rows = pg.locator("table[data-clicky] tr")
    rows.nth(2).scroll_into_view_if_needed()
    a, b = rows.nth(1).locator("td").nth(1).bounding_box(), rows.nth(2).locator("td").nth(3).bounding_box()
    pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('lassob').click()")
    pg.mouse.move(a["x"] - 4, a["y"] - 4)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] + 4, b["y"] + b["height"] + 4, steps=6)
    pg.mouse.up()
    pg.wait_for_timeout(600)
    text = _answer(pg)
    assert text.startswith("Vùng anh chị khoanh có 2 mục") and "Khối Marketing" in text
    bub = _box(pg, "bub")
    region = {"x": a["x"], "y": a["y"], "width": b["x"] + b["width"] - a["x"], "height": b["y"] + b["height"] - a["y"]}
    assert bub["width"] > 0 and _apart(bub, region)
    assert not errors


def test_skipped_without_playwright(built, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert kiem_hinh.main([str(built)]) == kiem_hinh.SKIPPED
    assert "BỎ QUA" in capsys.readouterr().out


def test_ready_answers_and_the_bosses_usual_questions(tmp_path, monkeypatch):
    from present import tri_nho
    monkeypatch.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    d = str(tmp_path / "nho")
    tri_nho.add_questions(["Vì sao khối CNTT tăng?"], path=d)
    f = Path(d) / tri_nho.TREO
    f.write_text(f.read_text(encoding="utf-8").replace("  Trả lời: ", "  Trả lời: Do dự án ERP."), encoding="utf-8")
    dst = tmp_path / "bao_cao.html"
    shutil.copyfile(FIXTURE, dst)
    out = clicky_html.build(str(dst), voice=lambda t: t.encode("utf-8"), memory=d)["out"]
    res = kiem_hinh.check(out, sizes=((1366, 768),), questions=[
        "Headcount khối bán hàng?", "khối CNTT vì sao tăng", "Doanh thu theo kênh MT?", "Lương bình quân khối bán hàng?",
        "Chi phí nhân sự trên doanh thu?"])
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert res["ok"], res["problems"]
    assert [r["ok"] for r in res["cau"]] == [True, True, False, False, True], res["cau"]
    # said in words, found by its abbreviation: "trên doanh thu" is the ".../DTT" card
    assert res["cau"][4]["label"].startswith("Tỷ lệ chi phí nhân sự/DTT")
    # only the row matched: not counted as answered, and the missing words are named
    assert res["cau"][3]["partial"] and "thiếu «Lương bình quân»" in res["cau"][3]["label"]
    assert res["cau"][0]["label"] == "Khối Bán hàng" and res["cau"][1]["label"].startswith("câu đã trả lời")
    # the usual questions are only tried in the check; they never go into the file
    assert "Doanh thu theo kênh MT" not in Path(out).read_text(encoding="utf-8")
