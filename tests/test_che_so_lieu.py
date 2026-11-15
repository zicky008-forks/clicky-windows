"""The masked copy keeps the report's shape and periods but none of its figures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import che_so_lieu  # noqa: E402

PAGE = """<html><head><meta charset="windows-1258"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AOPR FY2627 Công ty Thật</title></head><body><h2>2. Chi tiết</h2>
<p>Khối Bán hàng đạt 92,3 tỷ, tăng 18,0%; tuyển 48 người trong tháng 10/2026, Q3, T10, ngày 20/11.</p>
<div aria-label="Tổng 186,4 tỷ">186,4 tỷ</div><td>1.248</td><td>(9,870)</td><td>0,5</td>
<script>new Chart(x,{data:{labels:['FY2425'],datasets:[{data:[148,162,186.4]}]}})</script></body></html>"""


def test_figures_change_shape_and_periods_stay():
    out, n, _ = che_so_lieu.mask(PAGE, {"Công ty Thật": "Công ty A"}, seed=7)
    for real in ("92,3", "18,0", "48 người", "186,4", "1.248", "9,870", "148", "162"):
        assert real not in out, real
    for kept in ("FY2627", "tháng 10/2026", "Q3", "T10", "20/11", "2. Chi tiết", "initial-scale=1", "Công ty A"):
        assert kept in out, kept
    assert "Công ty Thật" not in out and 'charset="utf-8"' in out
    import re                                          # the same figure is masked the same way everywhere
    card = re.search(r'aria-label="Tổng ([\d,]+) tỷ">([\d,]+) tỷ', out)
    chart = re.search(r"data:\[\d+,\d+,([\d.]+)\]", out).group(1)
    assert card.group(1) == card.group(2) == chart.replace(".", ",")
    assert re.search(r"<td>\(\d,\d{3}\)</td><td>0,\d</td>", out)   # sign brackets and "below one" stay
    assert n >= 10


def test_masked_copy_is_written_next_to_the_source(tmp_path):
    src = tmp_path / "bc.html"
    raw = b"\xef\xbb\xbf" + PAGE.encode("utf-8")                    # saved with a BOM, meta says windows-1258
    src.write_bytes(raw)
    assert che_so_lieu.main([str(src)]) == 0
    assert src.read_bytes() == raw
    assert "Khối Bán hàng" in (tmp_path / "bc_CHE.html").read_text(encoding="utf-8")


def test_chart_data_as_objects_and_json_is_masked_but_libraries_are_not():
    # real report 2026-11-13: 6 script blocks kept their numbers (data written as objects, not arrays)
    page = ("<p>x</p><script>var rows=[{name:'Khối A', \"doanh_thu\": 412580.5, ty_le:12.6}];</script>"
            "<script type=\"application/json\">{\"a\": [1, 2], \"b\": {\"c\": 98765}}</script>"
            "<script>/*! Chart.js v4.4.1 */ var t={duration:1000};</script>")
    out, _, untouched = che_so_lieu.mask(page, {}, seed=3)
    for real in ("412580.5", "12.6", "98765"):
        assert real not in out, real
    assert "duration:1000" in out and "Khối A" in out and untouched == 0   # the library is code: kept, not a doubt


def test_data_kept_as_csv_text_in_a_script_is_masked_and_the_copy_is_audited():
    # real report 2026-11-13: ~0.9 MB of data as CSV strings inside a JSON object in a script
    rows = "\\n".join(f"Khối {i},2026-10,{41258 + i}.5,{(i * 37) % 900}.25" for i in range(30))
    page = f'<p>Doanh thu 41.258,5</p><script>window.__data={{"t1": "khoi,ky,doanh_thu,ty_le\\n{rows}"}};</script>'
    out, _, _ = che_so_lieu.mask(page, {}, seed=5)
    assert "41258.5" not in out and "2026-10" in out and "Khối 7" in out
    assert che_so_lieu.leftovers(page, out) <= 2                     # made-up digits can match by chance
    assert che_so_lieu.leftovers(page, page) > 30


def test_figures_typed_into_chart_code_are_masked_but_sizes_stay():
    page = ("<script>(() => { const m = {t: 20, r: 30}; const w = 960, h = 320;"
            " g.append('line').attr('y', y(672.7)); total(41258); color('#1f77b4'); ky(2026); })();</script>")
    out, _, _ = che_so_lieu.mask(page, {}, seed=2)
    assert "672.7" not in out and "41258" not in out
    for kept in ("w = 960", "h = 320", "#1f77b4", "ky(2026)"):
        assert kept in out, kept


def test_ratios_and_ranges_are_figures_and_data_attributes_are_masked():
    out, _, _ = che_so_lieu.mask('<p>Thực hiện/AOP: 672,7/700,0 tỷ; ngày 20/11/2026</p><td data-v="412580">x</td>', {}, seed=4)
    assert "672,7" not in out and "700,0" not in out and "412580" not in out and "20/11/2026" in out


def test_a_big_data_block_is_masked_whole():
    rows = ",".join(f'"r{i}": "{1234 + i}.5"' for i in range(2000))
    page = f"<script>window.__x={{{rows}, \"ky\": \"2026-10\", \"n\": 77}};</script>"
    out, _, _ = che_so_lieu.mask(page, {}, seed=6)
    assert "1234.5" not in out and "2026-10" in out and '"r17"' in out and '"n": 77' not in out


def test_a_figure_after_a_non_breaking_space_entity_is_masked():
    out, _, _ = che_so_lieu.mask("<td>Doanh thu:&nbsp;672,7&nbsp;tỷ</td><td>&#160;</td>", {}, seed=8)
    assert "672,7" not in out and "&#160;" in out


def test_a_figure_glued_to_its_unit_is_masked_but_codes_stay():
    out, _, _ = che_so_lieu.mask("<td>5,2tr</td><td>120M</td><td>FY2627 T10 v1111</td>", {}, seed=9)
    assert "5,2tr" not in out and "120M" not in out and "FY2627 T10 v1111" in out


def test_a_library_with_its_banner_after_a_small_shim_is_left_whole():
    lib = "<script>var g=window;" + "x" * 500 + "/*! d3js.org v7.9.0 Copyright 2010-2023 */" + \
        "function f(t){return t*0.5+1e3}" * 260 + "var s='1f77b4ff7f0e2ca02c';var k={duration:250,max:86400000};</script>"
    out, _, untouched = che_so_lieu.mask(lib + "<p>672,7</p>", {}, seed=1)
    assert out.startswith(lib) and "672,7" not in out and untouched == 0


def test_chart_geometry_is_moved_but_the_frame_and_small_offsets_stay():
    page = ('<svg viewBox="0 0 960 320"><rect x="12" y="123.4" width="40" height="196.6"/>'
            '<polyline points="10,250.5 120,180.25"/><text x="345" y="40">672,7</text></svg>')
    out, _, _ = che_so_lieu.mask(page, {}, seed=11)
    for real in ("123.4", "196.6", "250.5", "180.25", 'x="345"', "672,7"):
        assert real not in out, real
    for kept in ('viewBox="0 0 960 320"', 'x="12"', 'width="40"', 'y="40"'):
        assert kept in out, kept


def test_the_audit_says_where_each_leftover_sits_without_showing_it():
    page = '<p>Doanh thu 672,7</p><script>const w = 960; plot(41258);</script><div data-k="FY2627">x</div>'
    out, _, _ = che_so_lieu.mask(page, {}, seed=12)
    found = che_so_lieu.audit(page, out)
    assert found == [("script 1 (mã)", "<aaaaaa>aaaaa a = [999]; aaaa(999")]
    assert che_so_lieu.leftovers(page, out) == 1


def test_a_figure_after_cung_ky_is_masked_but_ky_10_stays():
    out, _, _ = che_so_lieu.mask("<p>So với cùng kỳ 672,7 tỷ, kỳ 10 và mục 3; FY 2627</p>", {}, seed=13)
    assert "672,7" not in out and "kỳ 10" in out and "mục 3" in out and "FY 2627" in out


def test_a_data_block_masks_figures_glued_to_units_but_keeps_colours():
    rows = ",".join(f'"r{i}": "giảm {120 + i}tr, #1f77b4"' for i in range(1500))
    out, _, _ = che_so_lieu.mask(f"<script>window.__x={{{rows}}};</script>", {}, seed=14)
    assert "giảm 120tr" not in out and "#1f77b4" in out


def test_bar_widths_in_style_move_but_spacing_stays():
    page = '<i style="left:3.25%;width:87.65%;line-height:1.4;padding:12px"></i><span style="width:calc(41.5% + 8px)">x</span>'
    out, _, _ = che_so_lieu.mask(page, {}, seed=15)
    assert "87.65%" not in out and "41.5%" not in out and "3.25%" not in out
    assert "line-height:1.4" in out and "padding:12px" in out and "+ 8px" in out


def test_the_audit_does_not_count_round_constants_in_code():
    page = "<script>const pct = (v) => fmt(v * 100) + '%'; const w = 960;</script>"
    out, _, _ = che_so_lieu.mask(page, {}, seed=16)
    assert [w for w, _ in che_so_lieu.audit(page, out)] == ["script 1 (mã)"]


def test_words_shown_from_chart_code_are_masked_like_text():
    page = ("<script>g.text('Tỷ lệ 12,5'); h.text('Ngưỡng tối thiểu 150'); k.text('Tháng 10/2026');"
            " e.attr('transform', 'translate(0, 120)'); f.attr('fill', '#1f77b4'); const w = 960;</script>")
    out, _, _ = che_so_lieu.mask(page, {}, seed=17)
    assert "12,5" not in out and "150" not in out
    for kept in ("Tháng 10/2026", "translate(0, 120)", "#1f77b4", "w = 960"):
        assert kept in out, kept


def test_the_audit_reads_a_year_then_a_csv_field_as_a_year():
    rows = ",".join(f'"r{i}": "T10/2026,{i % 9 + 1},{1000 + i}.5"' for i in range(1500))
    page = f"<script>window.__x={{{rows}}};</script>"
    out, _, _ = che_so_lieu.mask(page, {}, seed=18)
    assert "T10/2026," in out and che_so_lieu.leftovers(page, out) <= 3


def test_a_dotted_date_in_a_vietnamese_report_stays_a_date():
    # real report 2026-11-13: "REVISE 11.11" masked to "54.44" read as an English decimal and stopped Clicky
    page = "<p>Doanh thu 672,7 tỷ, lãi 12,5 tỷ, chi 1.234,5 tỷ</p><p>file REVISE 11.11, cập nhật 29.10, tăng 12.5%</p>"
    out, _, _ = che_so_lieu.mask(page, {}, seed=19)
    assert "REVISE 11.11" in out and "29.10" in out and "12.5%" not in out and "672,7" not in out


def test_in_an_english_report_a_dotted_pair_is_a_figure():
    out, _, _ = che_so_lieu.mask("<p>Revenue 672.7 bn, margin 11.11 pts, cost 1,234.5</p>", {}, seed=20)
    assert "11.11" not in out


def test_chart_code_keeps_its_own_settings_so_the_copy_lays_out_the_same():
    # real report 2026-11-13: a quote in one template paired with the next, the code between was read as data,
    # and '100%' widths became '623%': the copy's charts grew 2000px wide on a phone
    page = ("<script>const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);"
            " const x = d3.scaleBand(R.map((r, i) => i), [m.l, W - m.r]).padding(0.25); const m = {t: 20, l: 120};"
            " g.transition().duration(1200); if (innerWidth < 1100) y.nice(); line(672.7); total(41258);"
            " lab.text(`Tổng ${fmt(v)} tỷ`); const k = `<b>${d.name}</b>: ${d.v}`;</script>")
    out, _, _ = che_so_lieu.mask(page, {}, seed=21)
    for kept in ("'100%'", "padding(0.25)", "{t: 20, l: 120}", "duration(1200)", "innerWidth < 1100", "`0 0 ${W} ${H}`"):
        assert kept in out, kept
    assert "672.7" not in out and "41258" not in out
