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
    out = Path(clicky_html.build(str(dst), owner="anh Tuấn", contact="tuan@congty.vn", voice=lambda t: t.encode("utf-8"))["out"])
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
    assert res["checked"] >= 40 and res["sizes"] == ["1366x768", "1920x1080", "390x844 (điện thoại)"]


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
    # the unit comes from the report itself ("14,1 tỷ" is written in the paragraph above the table)
    assert "Khối Marketing, AOPR: 14,0 tỷ." in text and "đúng chỗ đang khoanh" in text and "Cả dòng Khối Marketing" in text
    assert "Chi phí nhân sự quản trị»" in text and "bao_cao.html" not in text      # the report title, not its file name
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
    assert said.startswith("Đoạn này nói: ") and said.split()[3] in para.inner_text()
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
    out = clicky_html.build(str(dst), owner="anh Tuấn", contact="tuan@congty.vn", voice=lambda t: t.encode("utf-8"), memory=d)["out"]
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
    assert res["cau"][0]["label"] == "Khối Bán hàng, Headcount AOPR: 712 người." and res["cau"][1]["label"].startswith("câu đã trả lời")
    # the usual questions are only tried in the check; they never go into the file
    assert "Doanh thu theo kênh MT" not in Path(out).read_text(encoding="utf-8")
    # the owner's ready answer is about CNTT: "Vì sao tăng?" on another place never gets it
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, Path(out))
        assert "ERP" not in _ask(pg, "Vì sao tăng?", '[data-clicky="c2"]')
        assert "ERP" not in _ask(pg, "Vì sao tăng?", 'table[data-clicky] tr:nth-child(1) td:nth-child(2)')
        assert "ERP" in _ask(pg, "Vì sao tăng?", 'table[data-clicky] tr:nth-child(6) td:nth-child(2)')      # Khối CNTT
        assert "ERP" not in _ask(pg, "Khối CNTT tăng bao nhiêu?")       # not a "why": the table answers
        browser.close()


# --- round 1 review: answers ---

STUB_AUDIO = """(() => { const P = HTMLMediaElement.prototype;
  Object.defineProperty(P, 'paused', {get() { return !this._on; }, configurable: true});
  P.play = function () { this._on = true; return Promise.resolve(); };
  P.pause = function () { this._on = false; }; })();"""


def _ask(pg, q, sel=None):
    return pg.evaluate("([q, s]) => window.__clickyHoi(q, s)", [q, sel])


def _parked(pg):
    return pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('tl').innerText")


def test_basic_questions_are_answered_with_units_and_no_false_notes(page):
    pg, errors = page
    a = _ask(pg, "Headcount bán hàng bao nhiêu?")                  # no "khối", 2 of 3 words: still the row
    assert a.startswith("Khối Bán hàng, Headcount AOPR: 712 người.")
    assert _ask(pg, "Marketing").startswith("Khối Marketing: AOP gốc 12,5 tỷ")
    assert _ask(pg, "Cần giảm bao nhiêu?").startswith("Cần giảm so với AOPR: 8,9 tỷ")
    assert _ask(pg, "AOP gốc bao nhiêu?").startswith("Tổng, AOP gốc: 165,5 tỷ")
    a = _ask(pg, "Tổng chi phí nhân sự?")                          # answered: no «phí nhân sự» note, nothing parked
    assert a.startswith("Tổng chi phí nhân sự AOPR: 186,4 tỷ") and "Em chưa thấy" not in a
    assert "Em chưa thấy" not in _ask(pg, "Trần đề xuất là gì?")    # «gì» is a question word
    assert _parked(pg) == ""
    # a question set aside says plainly that it reaches the owner only after "Gửi"
    a = _ask(pg, "Lương bình quân khối bán hàng?")
    assert "Em chưa thấy «Lương bình quân»" in a and "bấm «Gửi»" in a
    assert "Lương bình quân khối bán hàng?" in _parked(pg)
    assert pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('send').textContent") == "Gửi cho anh Tuấn"
    # after "Gửi" the list is marked sent, so the next e-mail does not repeat it
    pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('send').click()")
    pg.wait_for_timeout(200)
    assert _parked(pg) == "" and "Em đã mở thư gửi anh Tuấn với 1 câu" in pg.evaluate(
        "document.getElementById('clicky-host').shadowRoot.getElementById('sent').textContent")
    assert not errors


def test_superlative_is_ordered_from_the_table_and_follows_the_lasso(page):
    pg, errors = page
    a = _ask(pg, "Khối nào tăng nhiều nhất?")
    assert a.startswith("Khối Bán hàng cao nhất về Chênh lệch: 14,1 tỷ.") and "không tính dòng Tổng" in a
    assert _ask(pg, "Khối nào có % tăng thấp nhất?").startswith("Khối Tài chính - Kế toán thấp nhất về % tăng: 4,1%")
    # a lasso over Marketing and Chuỗi cung ứng: the question is about those rows only
    pg.keyboard.press("Escape")
    rows = pg.locator("table[data-clicky] tr")
    rows.nth(3).scroll_into_view_if_needed()
    a, b = rows.nth(2).locator("td").nth(1).bounding_box(), rows.nth(3).locator("td").nth(4).bounding_box()
    pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('lassob').click()")
    pg.mouse.move(a["x"] - 4, a["y"] - 4)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] + 4, b["y"] + b["height"] + 4, steps=6)
    pg.mouse.up()
    pg.wait_for_timeout(300)
    pg.evaluate("""() => { const s = document.getElementById('clicky-host').shadowRoot;
      s.getElementById('bq').value = 'Khối nào tăng nhiều nhất?'; s.getElementById('bf').requestSubmit(); }""")
    pg.wait_for_timeout(300)
    a = _answer(pg)
    assert a.startswith("Khối Chuỗi cung ứng cao nhất về Chênh lệch: 2,1 tỷ.") and "trong vùng anh chị khoanh" in a
    assert not errors


def test_the_clicked_place_narrows_the_answer(page):
    pg, errors = page
    sale = 'table[data-clicky] tr:nth-child(1) td:nth-child(6)'          # Khối Bán hàng, Headcount AOPR
    a = _ask(pg, "Vì sao tăng?", sale)                                   # the report's own sentence about this row
    assert a.startswith("Báo cáo viết: «Khối Bán hàng tăng mạnh nhất, 14,1 tỷ, do kế hoạch")
    a = _ask(pg, "Chi phí bình quân mỗi người?", sale)                   # company-wide figure: said so, never passed off
    assert a.startswith("Chi phí bình quân mỗi người: 149,4 triệu/năm")
    assert "Đây là số chung của công ty, báo cáo chưa tách theo Khối Bán hàng." in a
    mk = 'table[data-clicky] tr:nth-child(2) td:nth-child(3)'            # Khối Marketing, AOPR
    assert _ask(pg, "Headcount?", mk).startswith("Khối Marketing, Headcount AOPR: 64 người.")   # named column wins
    a = _ask(pg, "Vì sao chi phí nhân sự tăng?")
    assert a.startswith("Báo cáo viết: «Thông điệp chính: Chi phí nhân sự AOPR tăng 12,6%") and "đến từ tuyển mới" in a
    assert not errors


def test_bubble_never_covers_the_row_label_or_the_header(page):
    pg, errors = page
    rows = pg.locator("table[data-clicky] tbody tr")
    head = pg.locator("table[data-clicky] thead th")
    for i in range(rows.count()):
        for j in range(1, 6):
            cell = rows.nth(i).locator("td").nth(j)
            cell.scroll_into_view_if_needed()
            cell.click()
            pg.wait_for_timeout(120)
            bub = _box(pg, "bub")
            assert _apart(bub, cell.bounding_box()), (i, j)
            assert _apart(bub, rows.nth(i).locator("td").nth(0).bounding_box()), ("row label", i, j)
            h = head.nth(j).bounding_box()
            if h["y"] >= 0:
                assert _apart(bub, h), ("header", i, j)
            pg.keyboard.press("Escape")
    assert not errors


def test_keyboard_space_arrows_esc_and_resume(built):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = kiem_hinh._launch(pw)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        pg = browser.new_page(viewport={"width": 1366, "height": 768})
        pg.add_init_script(STUB_AUDIO)
        pg.goto(built.as_uri())
        label = lambda: pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('play').getAttribute('aria-label')")
        cap = lambda: pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('cap').innerText")
        pg.keyboard.press("Space")
        assert label() == "Tạm dừng"
        pg.keyboard.press("ArrowRight")
        assert "Phần 2/" in cap()
        pg.keyboard.press("Escape")                                       # Esc pauses
        assert label() == "Nghe tiếp"
        pg.keyboard.press("Space")
        assert label() == "Tạm dừng"
        pg.locator('[data-clicky="c3"]').click()                          # a question during the talk
        pg.wait_for_timeout(200)
        assert label() == "Tiếp tục trình bày"
        pg.keyboard.press("Escape")
        pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('play').click()")
        assert label() == "Tạm dừng" and "Phần 2/" in cap()               # back to the same part, not the start
        # the end: no dim left over the report, and the button offers to listen again
        for _ in range(8):
            pg.keyboard.press("ArrowRight")
        pg.evaluate("() => { const a = window.__clickyAudio; a.dispatchEvent(new Event('ended')); }")
        assert label() == "Nghe lại từ đầu"
        assert pg.evaluate("getComputedStyle(document.querySelector('[data-clicky-layer] svg')).display") == "none"
        browser.close()


def test_figures_take_focus_and_enter_asks(page):
    pg, errors = page
    cell = pg.locator("table[data-clicky] tr").nth(2).locator("td").nth(2)
    assert cell.get_attribute("tabindex") == "0"
    cell.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    assert _answer(pg).startswith("Khối Marketing, AOPR: 14,0 tỷ.")
    assert pg.evaluate("document.getElementById('clicky-host').shadowRoot.activeElement.id") == "bq"
    pg.keyboard.press("Escape")
    assert pg.evaluate("document.activeElement === document.querySelector('table[data-clicky] tr:nth-child(2) td:nth-child(3)')")
    assert not errors


# --- round 1 review: other report shapes ---

def _build_html(tmp_path, html, name="bc"):
    mp = pytest.MonkeyPatch()
    mp.setattr(clicky_html, "wav_to_mp3", lambda wav: (wav, 3.0))
    src = tmp_path / f"{name}.html"
    src.write_text(html, encoding="utf-8")
    try:
        return Path(clicky_html.build(str(src), owner="anh Tuấn", contact="tuan@congty.vn",
                                      voice=lambda t: t.encode("utf-8"))["out"])
    finally:
        mp.undo()


def _open(pw, out, w=1366, h=768, **kw):
    try:
        browser = kiem_hinh._launch(pw)
    except RuntimeError as exc:
        pytest.skip(str(exc))
    ctx = browser.new_context(viewport={"width": w, "height": h}, **kw)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(out.as_uri())
    return browser, pg, errors


DARK = """<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Doanh thu theo vùng (SỐ GIẢ LẬP)</title><style>body{margin:0;background:#0b1220;color:#e2e8f0;padding:32px}
table{width:100%;border-collapse:collapse}td,th{padding:10px;text-align:right}td:first-child,th:first-child{text-align:left}</style></head><body>
<h1>Doanh thu thuần Q3 theo vùng</h1><p>Báo cáo cho Ban Giám đốc · SỐ GIẢ LẬP · đơn vị tỷ đồng</p>
<h2>1. Doanh thu theo vùng</h2><p>Miền Nam đóng góp 64,2 tỷ trong phần tăng 121,8 tỷ.</p>
<table><thead><tr><th>Vùng</th><th>Kế hoạch</th><th>Thực hiện</th><th>Chênh lệch</th></tr></thead><tbody>
<tr><td>Miền Bắc</td><td>412,5</td><td>455,2</td><td>42,7</td></tr><tr><td>Miền Trung</td><td>188,0</td><td>176,4</td><td>-11,6</td></tr>
<tr><td>Miền Nam</td><td>603,9</td><td>668,1</td><td>64,2</td></tr><tr><td>Tổng</td><td>1.204,4</td><td>1.299,7</td><td>95,3</td></tr></tbody></table>
<h2>2. Theo tháng</h2><figure><svg viewBox="0 0 300 120" width="300" height="120" aria-label="Doanh thu thuần theo tháng, tỷ đồng">
<text x="40" y="20">98,2</text><text x="40" y="110">T1</text><text x="140" y="20">104,5</text><text x="140" y="110">T2</text>
<text x="240" y="20">111,9</text><text x="240" y="110">T3</text></svg></figure></body></html>"""


def test_dark_report_gets_a_dark_dim_and_dark_controls(tmp_path):
    out = _build_html(tmp_path, DARK, "toi")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, out)
        assert pg.evaluate("document.getElementById('clicky-host').getAttribute('data-theme')") == "dark"
        pg.locator("tbody tr").nth(0).locator("td").nth(2).click()
        pg.wait_for_timeout(300)
        fill = pg.evaluate("document.querySelector('[data-clicky-layer] svg path').getAttribute('fill')")
        assert fill.startswith("rgba(11,18,32,")                          # the page's own dark background, not cream
        bg = pg.evaluate("getComputedStyle(document.getElementById('clicky-host').shadowRoot.getElementById('bar')).backgroundColor")
        assert bg != "rgb(255, 255, 255)"
        assert _answer(pg).startswith("Miền Bắc, Thực hiện: 455,2 tỷ đồng.")   # unit from "đơn vị tỷ đồng"
        # an SVG chart is read as label/value pairs, never glued together
        pg.keyboard.press("Escape")
        pg.locator("svg text").first.click()
        pg.wait_for_timeout(300)
        a = _answer(pg)
        assert "Doanh thu thuần theo tháng" in a and "T1: 98,2" in a and "T3: 111,9" in a and "98,2T1" not in a
        assert not errors
        browser.close()


def test_phone_bar_stays_on_screen_with_a_wide_table(tmp_path):
    wide = FIXTURE.read_text(encoding="utf-8").replace("<table>", '<table style="min-width:440px">')
    out = _build_html(tmp_path, wide, "rong")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, out, 390, 844, is_mobile=True, has_touch=True)
        res = pg.evaluate("window.__clickyKiemDienThoai()")
        assert res["problems"] == [], res
        bar = _box(pg, "bar")
        assert bar["x"] >= 0 and bar["x"] + bar["width"] <= 390 and bar["y"] + bar["height"] <= 844
        cap, play = _box(pg, "cap"), _box(pg, "play")
        assert cap["y"] < play["y"]                                       # caption above the one row of buttons
        assert not errors
        browser.close()


EXCEL = """<html><head><meta charset="utf-8"></head><body><table border=0>
<tr><td colspan=6>BÁO CÁO CHI PHÍ NHÂN SỰ THÁNG 10 (SỐ GIẢ LẬP)</td></tr><tr><td colspan=6>Đơn vị: tỷ đồng</td></tr>
<tr><td rowspan=2>Khối</td><td rowspan=2>Khoản mục</td><td colspan=2>Kế hoạch so Thực hiện</td><td colspan=2>Chênh lệch</td></tr>
<tr><td>AOP</td><td>Thực hiện</td><td>Tỷ đồng</td><td>%</td></tr>
<tr><td rowspan=2>Khối Bán hàng</td><td>Lương</td><td>63,2</td><td>79,1</td><td>15,9</td><td>25,2%</td></tr>
<tr><td>Thưởng</td><td>83,4</td><td>81,2</td><td>-2,2</td><td>-2,6%</td></tr>
<tr><td rowspan=2>Khối Văn phòng</td><td>Lương</td><td>57,1</td><td>80,1</td><td>23,0</td><td>40,3%</td></tr>
<tr><td>Thưởng</td><td>82,0</td><td>89,3</td><td>7,3</td><td>8,9%</td></tr>
<tr><td colspan=2>Tổng cộng</td><td>285,7</td><td>329,7</td><td>44,0</td><td>15,4%</td></tr></table>
<h2>Doanh thu theo tháng</h2><table><tr><th>Chỉ tiêu</th><th>Giá trị</th></tr>
<tr><td>Doanh thu tháng 3</td><td>31,0</td></tr><tr><td>Doanh thu tháng 10</td><td>45,0</td></tr></table>
<div role="grid"><div role="row"><span role="columnheader">Vùng</span><span role="columnheader">Thực hiện</span></div>
<div role="row"><span>Miền Bắc</span><span>456,7</span></div><div role="row"><span>Miền Nam</span><span>598,2</span></div></div>
</body></html>"""


def test_excel_tables_merged_cells_periods_and_div_grids(tmp_path):
    out = _build_html(tmp_path, EXCEL, "excel")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, out)
        pg.locator("td", has_text="63,2").click()
        pg.wait_for_timeout(200)
        a = _answer(pg)
        assert a.startswith("Lương (Khối Bán hàng), Kế hoạch so Thực hiện AOP: 63,2 tỷ đồng.")
        pg.keyboard.press("Escape")
        pg.locator("td", has_text="285,7").click()
        pg.wait_for_timeout(200)
        a = _answer(pg)
        assert a.startswith("Tổng cộng, Kế hoạch so Thực hiện AOP: 285,7") and "undefined" not in a
        assert _ask(pg, "Lương khối văn phòng thực hiện?").startswith("Lương (Khối Văn phòng), Kế hoạch so Thực hiện Thực hiện: 80,1")
        a = _ask(pg, "Doanh thu tháng 3?")
        assert a.startswith("Doanh thu tháng 3: Giá trị 31,0") and "Em chưa thấy" not in a     # never "tháng 10"
        assert _ask(pg, "Doanh thu miền Bắc?").startswith("Miền Bắc: Thực hiện 456,7")
        chips = pg.evaluate("[...document.getElementById('clicky-host').shadowRoot.querySelectorAll('#a .chips button')].map(b => b.textContent)")
        assert chips == [] or len(set(chips)) == len(chips)
        a = _ask(pg, "Lương?")                                             # same label twice: chips name the group
        chips = pg.evaluate("[...document.getElementById('clicky-host').shadowRoot.querySelectorAll('#a .chips button')].map(b => b.textContent)")
        assert a.startswith("Anh chị muốn hỏi chỗ nào") and len(set(chips)) == len(chips) == 2 and "Khối Bán hàng" in chips[0]
        assert not errors
        browser.close()


CENTERED = """<!doctype html><html><head><meta charset="utf-8"><title>Căn giữa</title>
<style>body{position:relative;max-width:700px;margin:30px auto;padding:0 20px}
header{position:fixed;top:0;left:0;right:0;height:80px;background:#036;z-index:10}</style></head><body><header></header>
<h1 style="margin-top:100px">Báo cáo căn giữa (SỐ GIẢ LẬP)</h1><h2>1. Chi tiết</h2><p>Nhận xét: khoản 3 đạt 21,3 tỷ.</p>
<table><tr><th>Khoản</th><th>Thực hiện</th><th>AOP</th></tr>""" + "".join(
    f"<tr><td>Khoản {i}</td><td>{7.1 * i:.1f}</td><td>{9.1 * i:.1f}</td></tr>".replace(".", ",") for i in range(1, 41)) + """</table>
<h2>2. Kết luận</h2><p>Kết luận: Khoản 25 đạt 177,5 tỷ.</p></body></html>"""


def test_frames_land_on_the_place_in_centred_pages_with_a_fixed_header(tmp_path):
    out = _build_html(tmp_path, CENTERED, "giua")
    res = kiem_hinh.check(str(out), sizes=((1366, 768),))
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert res["ok"], res["problems"]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, out)
        cell = pg.locator("td", has_text="177,5")
        pg.evaluate("window.scrollTo(0, 0)")
        assert _ask(pg, "Khoản 25 thực hiện?").startswith("Khoản 25, Thực hiện: 177,5")   # never «Khoản 2»
        pg.wait_for_timeout(200)
        box = cell.bounding_box()
        fr = pg.evaluate("(() => { const b = [...document.querySelectorAll('[data-clicky-layer] div')].find(d => d.style.border.startsWith('3px'));"
                         " const r = b.getBoundingClientRect(); return {x: r.left, y: r.top, width: r.width, height: r.height}; })()")
        assert fr["x"] <= box["x"] and fr["x"] + fr["width"] >= box["x"] + box["width"]
        assert fr["y"] <= box["y"] and fr["y"] + fr["height"] >= box["y"] + box["height"]
        assert box["y"] > 80                                                 # not under the page's own fixed header
        assert not errors
        browser.close()


def test_a_fixed_header_over_the_figure_is_caught(tmp_path):
    out = _build_html(tmp_path, CENTERED, "giua")
    bad = out.with_name("che.html")
    # the report's header grows to cover most of the screen: the check must say so
    bad.write_text(out.read_text(encoding="utf-8").replace("height:80px;background:#036", "height:700px;background:#036"), encoding="utf-8")
    res = kiem_hinh.check(str(bad), sizes=((1366, 768),))
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert not res["ok"] and any("phần cố định của trang" in p for p in res["problems"])


def test_check_fails_cleanly_when_the_layer_never_starts(built, tmp_path, capsys):
    bad = tmp_path / "hong.html"
    bad.write_text(built.read_text(encoding="utf-8").replace('"use strict";', '"use strict"; const ;', 1), encoding="utf-8")
    res = kiem_hinh.check(str(bad), sizes=((1366, 768),), timeout_ms=8000)
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert not res["ok"] and "không chạy" in res["problems"][0]
    assert kiem_hinh.main([str(bad)]) == 1
    assert "Traceback" not in capsys.readouterr().out


def test_draft_badge_on_the_bar_only_for_a_draft(built, tmp_path):
    from playwright.sync_api import sync_playwright
    import json as _json
    import re as _re
    src = built.read_text(encoding="utf-8")
    m = _re.search(r'(<script id="clicky-data" type="application/json">)(.*?)(</script>)', src, _re.S)

    def variant(name, status):
        data = _json.loads(m.group(2))
        data["meta"]["trang_thai"] = status
        data["meta"]["canh_bao"] = ["Ghi chú riêng cho anh Tuấn"]
        blob = _json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
        f = tmp_path / name
        f.write_text(src[:m.start(2)] + blob + src[m.end(2):], encoding="utf-8")
        return f

    draft = variant("so_bo.html", "SƠ BỘ: kịch bản nháp chưa duyệt")
    final = variant("chinh_thuc.html", "CHÍNH THỨC")
    res = kiem_hinh.check(str(draft))                                  # the badge covers no figure, laptop and phone
    if res["skipped"]:
        pytest.skip(res["reason"])
    assert res["ok"], res["problems"]
    with sync_playwright() as pw:
        for w, h, mobile in ((1366, 768, False), (390, 844, True)):
            browser, pg, errors = _open(pw, draft, w, h, **({"is_mobile": True, "has_touch": True} if mobile else {}))
            badge = pg.evaluate("""(() => { const b = document.getElementById('clicky-host').shadowRoot.getElementById('draft');
              if (!b) return null; const r = b.getBoundingClientRect(), bar = b.parentElement.getBoundingClientRect();
              return {text: b.textContent, title: b.title, inBar: r.top >= bar.top && r.bottom <= bar.bottom && r.left >= bar.left && r.right <= bar.right,
                      w: r.width}; })()""")
            assert badge == {"text": "SƠ BỘ", "title": "SƠ BỘ: kịch bản nháp chưa duyệt", "inBar": True, "w": badge["w"]} and badge["w"] > 0
            text = pg.evaluate("document.getElementById('clicky-host').shadowRoot.innerHTML")
            assert "Ghi chú riêng" not in text                          # the owner's warnings never reach viewers
            assert not errors
            browser.close()
        browser, pg, errors = _open(pw, final)
        assert pg.evaluate("document.getElementById('clicky-host').shadowRoot.getElementById('draft')") is None
        browser.close()
