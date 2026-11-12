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


def _answer(pg):
    return pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('a').innerText")


def test_click_a_figure_to_ask_about_it(page):
    pg, errors = page
    cell = pg.locator("table[data-clicky] tr").nth(2).locator("td").nth(2)      # Khối Marketing, AOPR
    assert cell.evaluate("e => getComputedStyle(e).cursor") == "help"
    cell.click()
    pg.wait_for_timeout(700)
    text = _answer(pg)
    assert "Khối Marketing, AOPR: 14,0." in text and "đúng ô đang khoanh" in text and "Cả dòng Khối Marketing" in text
    # the framed cell stays visible: the panel moves to the side that hides no figure
    panel = pg.evaluate("(() => { const r = document.getElementById('clicky-host').shadowRoot"
                        ".getElementById('panel').getBoundingClientRect(); return [r.left, r.right]; })()")
    box = cell.bounding_box()
    assert panel[1] <= box["x"] or panel[0] >= box["x"] + box["width"]
    pg.locator('[data-clicky="c3"]').click()                                    # a number card
    pg.wait_for_timeout(700)
    assert _answer(pg).startswith("Headcount bình quân: 1.248 người") and "Nghe lại đoạn" in _answer(pg)
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
        "Headcount khối bán hàng?", "khối CNTT vì sao tăng", "Doanh thu theo kênh MT?", "Lương bình quân khối bán hàng?"])
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert res["ok"], res["problems"]
    assert [r["ok"] for r in res["cau"]] == [True, True, False, False]
    # only the row matched: not counted as answered, and the missing words are named
    assert res["cau"][3]["partial"] and "thiếu «Lương bình quân»" in res["cau"][3]["label"]
    assert res["cau"][0]["label"] == "Khối Bán hàng" and res["cau"][1]["label"].startswith("câu đã trả lời")
    # the usual questions are only tried in the check; they never go into the file
    assert "Doanh thu theo kênh MT" not in Path(out).read_text(encoding="utf-8")
