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
