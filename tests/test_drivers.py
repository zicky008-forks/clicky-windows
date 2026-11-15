"""Volume rates read from a plan's note line, a driver column or the catalogue, and the flexed budget they give
(R6, grader F01): a cost that moves with sales volume is judged against rate x actual volume, said with its formula."""
import csv
from decimal import Decimal

import fpa_samples as fs
from engine import drivers, fpa_report
from test_fpa_report import case

D = Decimal
NAMES = {"VC": "van chuyen", "QC": "quang cao", "CTP": ""}


def test_a_note_line_gives_the_rate_by_element_name_or_initials():
    got = drivers.from_text("Ghi chú: Vận chuyển = 12.000 đồng/thùng × sản lượng kế hoạch", NAMES, "AOP.xlsx")
    assert [(r.element, r.rate, r.unit) for r in got] == [("VC", D(12000), "thùng")]
    got = drivers.from_text("Công tác phí = 10.000 đ/thùng", NAMES, "AOP.xlsx")
    assert [(r.element, r.rate) for r in got] == [("CTP", D(10000))]
    assert drivers.from_text("Ghi chú: ngân sách dự án mới nằm ở khối Marketing", NAMES, "x") == []


def test_a_driver_column_beside_the_element_row():
    rows = [["Khoản mục", "Định mức (đ/thùng)"], ["Vận chuyển", 12000], ["Quảng cáo", None]]
    got = drivers.from_sheet(rows, NAMES, "AOP.xlsx · sheet Dinh muc")
    assert [(r.element, r.rate, r.unit) for r in got] == [("VC", D(12000), "thùng")]


def test_two_different_rates_for_one_element_are_a_question(tmp_path):
    a, b = tmp_path / "AOP.csv", tmp_path / "Dinh muc.csv"
    fs.write_csv(a, [["Ghi chú: Vận chuyển = 12.000 đồng/thùng"]])
    fs.write_csv(b, [["Khoản mục", "Đơn giá đ/thùng"], ["Vận chuyển", "13000"]])

    class Config:
        elements = {"VC": {"ten": "Vận chuyển"}}
        rules, rule_text = {}, {}
    rates = drivers.read([a, b], Config(), {"VC"})
    assert rates.get("VC") is None and [r.rate for r in rates.clash["VC"]] == [D(12000), D(13000)]


def test_the_flexed_budget_is_rate_times_actual_volume_with_its_formula(tmp_path):
    d = case(tmp_path)
    rows = list(csv.reader(open(d / "AOP_2026.csv", encoding="utf-8-sig")))
    rows.append(["Ghi chú: Công tác phí = 10.000 đồng/thùng × sản lượng kế hoạch"])
    fs.write_csv(d / "AOP_2026.csv", rows)
    m = fpa_report.compute(d)
    qa, _qb = fpa_report.sales_volume(m.sales)
    c = next(c for c in m.classified if (c.line.dept, c.line.element) == ("KD", "CTP"))
    assert c.flex == (D(10000) * qa).quantize(D(1)) and c.verdict.endswith("ngân sách linh hoạt")
    text = c.sentence()
    assert "định mức 10.000 đ/thùng × sản lượng thực hiện" in text and "AOP_2026.csv" in text
    # a line with no plan is not flexed (its share of the rate is nothing)
    other = next(c for c in m.classified if (c.line.dept, c.line.element) == ("MKT", "CTP"))
    assert other.flex is None


def test_asked_about_a_rated_element_the_board_hears_the_flexed_budget_even_under_threshold(tmp_path):
    from engine import hoi_dap
    d = case(tmp_path)
    rows = list(csv.reader(open(d / "AOP_2026.csv", encoding="utf-8-sig")))
    first = fpa_report.compute(d)
    _qa, qb = fpa_report.sales_volume(first.sales)
    rate = int(sum(l.plan for l in first.costs.month if l.element == "KH") / qb)    # plan = rate x planned volume
    rows.append([f"Ghi chú: Khấu hao = {rate:,} đồng/thùng × sản lượng kế hoạch".replace(",", ".")])
    fs.write_csv(d / "AOP_2026.csv", rows)
    m = fpa_report.compute(d)
    small = [c for c in m.classified if c.line.element == "KH" and c.flex is not None and not c.material]
    assert small, [(c.line.label, c.verdict) for c in m.classified if c.line.element == "KH"]
    b = hoi_dap.read(fpa_report.write(m)["xlsx"])
    a = hoi_dap.answer(b, "Yếu tố KH tháng này có gì bất thường?")
    assert a.query.intent == "element" and "ngân sách linh hoạt" in a.text and "đ/thùng" in a.text
    top = hoi_dap.answer(b, "Top 5 khoản chi phí chênh lớn nhất?")
    assert "Khấu hao" not in top.text and "KH:" not in top.text
