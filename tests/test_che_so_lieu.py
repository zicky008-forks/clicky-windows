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
