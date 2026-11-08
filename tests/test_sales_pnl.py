"""Revenue, cost of goods sold and gross profit from the sales ledger by SKU x channel, in standard units, against
the plan by SKU (Case F01 shape, synthetic data); the bridge built from the files; the ledger matched to the books.
Every figure is checked against the generator's own arithmetic."""
from decimal import Decimal as D, localcontext

import pytest

import sales_samples as ss
from engine import bod_inputs, sales_pnl
from engine.fpa import PickVersion
from engine.monthly_report import fmt as _fmt
from engine.trial_balance import read_trial_balance


def fmt(x):
    return _fmt(D(x))


def _load(tmp_path, **kw):
    return bod_inputs.load(ss.folder(tmp_path / "F01", **kw), fy_start=1)


def _pnl(b, **kw):
    return sales_pnl.compute(b.sales, b.sku_plans, b.plans, fy_start=1, config=b.config,
                             plan_tables=b.sku_plan_totals, **kw)


def _sku(lines, sku):
    rows = [l for l in lines if l.sku == sku]
    return sum(l.qty for l in rows), sum(l.revenue for l in rows), sum(l.cogs for l in rows)


def test_month_and_year_to_date_by_sku_in_cartons(tmp_path):
    b = _load(tmp_path)
    v = _pnl(b)
    assert (v.period, v.months[0], v.version) == ("2026-10", "2026-01", "AOP")
    for sku, (qty, rev, cogs) in ss.october().items():
        assert _sku(v.month, sku) == (qty, rev, cogs)
    mt = next(l for l in v.month if (l.sku, l.channel) == ("NC01", "MT"))
    assert mt.qty == ss.actual_month("NC01", "MT", 10)[0] and mt.unit == "thùng"       # invoiced in lốc, 4 a carton
    ytd_qty = sum(ss.actual_month(s, c, m)[0] for s, c in ss.PLAN_QTY for m in range(1, 11)) + ss.UNPLANNED[3]
    assert sum(l.qty for l in v.ytd) == ytd_qty
    rev, cogs, gp = v.lines()
    plan = ss.october_plan()
    assert rev.actual == sum(x[1] for x in ss.october().values()) and rev.plan == sum(x[1] for x in plan.values())
    assert cogs.actual == sum(x[2] for x in ss.october().values()) and cogs.plan == sum(x[2] for x in plan.values())
    assert gp.variance == gp.actual - gp.plan == (rev.actual - cogs.actual) - (rev.plan - cogs.plan)
    assert rev.favourable and not cogs.favourable                # cost above plan is not favourable
    assert all(isinstance(x, D) for x in (rev.actual, rev.plan, cogs.actual, gp.plan))
    assert v.basis == "Kế hoạch: file KeHoach_AOP_2026.xlsx, phiên bản AOP, bản kế hoạch duy nhất trong thư mục"


def test_every_ledger_row_is_in_a_figure_or_named(tmp_path):
    b = _load(tmp_path)
    v = _pnl(b)
    _f, _role, used, skipped = next(r for r in b.receipt if r[1] == "Bán hàng")
    rows = ss.ledger_rows()
    assert used == len(rows) - 2 and skipped == [(len(rows), "dòng tổng")]          # the printed total is not added
    in_figures = sum(len(l.actual_from) for l in v.ytd)
    assert in_figures + len(v.outside) + len(v.no_month) == used
    assert v.left_out() == ["1 dòng bán hàng sau tháng 10/2026, doanh thu 5.400.000 đ"]


def test_the_bridge_from_the_files_follows_the_published_formulas(tmp_path):
    v = _pnl(_load(tmp_path))
    br = v.bridge()
    a, p = ss.october(), ss.october_plan()
    qa, qb = sum(x[0] for x in a.values()), sum(x[0] for x in p.values())
    gp_b = sum(x[1] - x[2] for x in p.values())
    with localcontext() as ctx:
        ctx.prec = 60
        g_b = {s: D(x[1] - x[2]) / x[0] for s, x in p.items()}
        g_b[ss.UNPLANNED[0]] = D(ss.UNPLANNED[4] - ss.UNPLANNED[5])          # no plan: priced as sold, all mix
        volume = (qa - qb) * D(gp_b) / qb
        mix = sum((D(a.get(s, (0,))[0]) - D(qa) * D(p.get(s, (0,))[0]) / qb) * g for s, g in g_b.items())
        price = sum(x[1] - D(p[s][1]) / p[s][0] * x[0] for s, x in a.items() if s in p)
        cost = -sum(x[2] - D(p[s][2]) / p[s][0] * x[0] for s, x in a.items() if s in p)
        assert abs(br.volume - volume) < D("1e-30") and abs(br.mix - mix) < D("1e-30")
        assert br.price == price == -1290 * 5_000 and br.cost == cost == -1070 * 6_000
        assert br.volume + br.mix + br.price + br.cost == br.gap                  # exactly
    gp = v.lines()[2]
    r = br.rounded()
    assert r["volume"] + r["mix"] + r["price"] + r["cost"] == r["gap"] == gp.variance       # the P&L's gap, to the đồng
    assert br.unplanned == ["DT04"] and br.unsold == []


def test_the_ledger_matches_the_books(tmp_path):
    b = _load(tmp_path)
    m = sales_pnl.match_books(b.sales, read_trial_balance(ss.tb_rows()), ss.PERIOD, b.config)
    assert m.ok and [l.code for l in m.lines] == ["revenue", "cogs"]
    rev, cogs = sum(x[1] for x in ss.october().values()), sum(x[2] for x in ss.october().values())
    assert m.sentence() == (f"Tháng 10/2026: doanh thu thuần khớp sổ cân đối, {fmt(rev)} đ (Có TK 5111 trừ Nợ TK 521); "
                            f"giá vốn hàng bán khớp sổ cân đối, {fmt(cogs)} đ (Nợ TK 632).")


def test_a_gap_with_the_books_is_named_with_both_figures(tmp_path):
    b = _load(tmp_path)
    m = sales_pnl.match_books(b.sales, read_trial_balance(ss.tb_rows(shift={"632": 4_000_000})), ss.PERIOD, b.config)
    cogs = next(l for l in m.lines if l.code == "cogs")
    assert not m.ok and cogs.diff == -4_000_000 and next(l for l in m.lines if l.code == "revenue").ok
    assert "giá vốn hàng bán lệch sổ cân đối" in m.sentence() and "lệch -4.000.000 đ" in m.sentence()


def test_without_521_the_gross_revenue_is_matched_to_511(tmp_path):
    """TT133 books discounts on the Nợ of 511 with the closing entry: the ledger's gross is matched, and said so."""
    b = _load(tmp_path)
    tb = [r for r in ss.tb_rows() if not str(r[0]).startswith("521")]
    m = sales_pnl.match_books(b.sales, read_trial_balance(tb), ss.PERIOD, b.config)
    rev = next(l for l in m.lines if l.code == "revenue")
    assert m.ok and rev.name == "Doanh thu trước giảm trừ" and "giảm trừ ghi bên Nợ TK 511" in rev.how


def test_a_sku_not_in_the_catalogue_and_a_unit_without_a_factor_are_named_and_the_bridge_waits(tmp_path):
    extra = [["21/10/2026", "HD000950", "KM99", "Combo khuyến mãi", "GT", "hộp", "10", "1000000", "0", "1000000", "600000"],
             ["22/10/2026", "HD000951", "TR02", "Trà xanh 500ml", "ONL", "két", "5", "1800000", "0", "1800000", "1200000"]]
    b = _load(tmp_path, ledger=ss.ledger_rows(extra=extra))
    v = _pnl(b)
    assert v.lines()[0].actual == sum(x[1] for x in ss.october().values()) + 2_800_000      # still in revenue
    assert v.unknown_skus == ["KM99"]
    said = v.left_out()
    assert "Mã hàng chưa có trong danh mục: KM99; em vẫn tính doanh thu và giá vốn, cần chị cho biết đơn vị chuẩn" in said
    assert "Danh mục chưa có hệ số quy đổi: TR02 theo két" in said
    with pytest.raises(sales_pnl.CannotBridge) as waits:
        v.bridge()
    assert waits.value.reasons == ["chưa quy đổi được sản lượng của mã KM99, TR02"]


def test_a_plan_gross_profit_unlike_its_sku_lines_is_a_plan_issue_never_mix(tmp_path):
    p = ss.october_plan()
    made = sum(x[1] - x[2] for x in p.values())
    clean = _pnl(_load(tmp_path / "a", plan={"KH theo ma hang": ss.plan_rows(),
                                             "PL": [["Phiên bản", "Năm", "Tháng", "Chỉ tiêu", "Số tiền"],
                                                    ["AOP", 2026, 10, "Lợi nhuận gộp", made]]}))
    assert clean.plan_issues == []                                                  # agrees: nothing said
    off = _pnl(_load(tmp_path / "b", plan={"KH theo ma hang": ss.plan_rows(),
                                           "PL": [["Phiên bản", "Năm", "Tháng", "Chỉ tiêu", "Số tiền"],
                                                  ["AOP", 2026, 10, "Lợi nhuận gộp", made + 5_000_000]]}))
    assert off.plan_issues == [f"File kế hoạch KeHoach_AOP_2026.xlsx ghi lợi nhuận gộp tháng 10/2026 là "
                               f"{fmt(made + 5_000_000)} đ (PL!E2), nhưng cộng các dòng mã hàng được {fmt(made)} đ, lệch "
                               "-5.000.000 đ. Em dùng các dòng mã hàng cho cầu nối, chênh lệch này không đưa vào phần "
                               "cơ cấu; cần chị cho biết số nào đúng"]
    assert off.plan_issues[0] in off.left_out()
    a, b = clean.bridge(), off.bridge()
    assert (b.gp_plan, b.mix, b.volume) == (a.gp_plan, a.mix, a.volume) and b.gp_plan == made


def test_a_printed_plan_total_is_checked_never_added(tmp_path):
    assert _pnl(_load(tmp_path / "a")).plan_issues == []                            # its total agrees
    v = _pnl(_load(tmp_path / "b", plan={"KH theo ma hang": ss.plan_rows(total=7)}))
    rows = ss.plan_rows()
    assert v.plan_issues == [f"File kế hoạch KeHoach_AOP_2026.xlsx, sheet KH theo ma hang dòng {len(rows)}: dòng tổng "
                             f"in sẵn ghi sản lượng {fmt(sum(r[5] for r in rows[1:-1]) + 7)}, cộng các dòng mã hàng phía "
                             f"trên được {fmt(sum(r[5] for r in rows[1:-1]))}. Em không dùng dòng tổng, chỉ cộng các dòng "
                             "mã hàng"]
    assert v.lines()[0].plan == sum(x[1] for x in ss.october_plan().values())


def test_two_plan_versions_are_asked_never_picked(tmp_path):
    rows = ss.plan_rows("AOP", total=None) + ss.plan_rows("AOP2", total=None)[1:]
    b = _load(tmp_path, plan={"KH theo ma hang": rows})
    with pytest.raises(PickVersion) as asked:
        _pnl(b)
    assert asked.value.versions == ["AOP", "AOP2"]
    assert _pnl(b, version="AOP2").version == "AOP2"
    rows = ss.plan_rows("AOP", total=None) + ss.plan_rows("LE T09", total=None)[1:]
    b = _load(tmp_path / "le", plan={"KH theo ma hang": rows})
    assert _pnl(b).version == "AOP" and _pnl(b, version="LE T09").version == "LE T09"   # a forecast only by name


def test_mixed_units_without_a_sku_catalogue_wait_for_one(tmp_path):
    b = _load(tmp_path, catalogue=[["Ghi chú"], ["Danh mục chưa có mã hàng"]])
    v = _pnl(b)
    assert v.mixed_units == ["lốc", "thùng"]
    with pytest.raises(sales_pnl.CannotBridge, match="nhiều đơn vị"):
        v.bridge()


def test_a_customer_code_is_never_read_as_the_sku():
    header = ["Ngày HĐ", "Mã khách hàng", "Kênh", "Số lượng", "Doanh thu thuần"]
    t = bod_inputs.read_table([header, ["20/10/2026", "KH001", "GT", "5", "1000"]], "S", bod_inputs.SALES_SPEC, 3)
    sale = bod_inputs.read_sales(t, fy_start=1)[0]
    assert sale.sku == "" and sale.qty == 5


def test_a_cost_per_unit_column_is_not_taken_as_the_cost():
    header = ["Ngày", "Mã hàng", "Kênh", "ĐVT", "Số lượng", "Doanh thu thuần", "Giá vốn/thùng"]
    t = bod_inputs.read_table([header, ["20/10/2026", "NC01", "GT", "thùng", "10", "2400000", "150000"]], "S",
                              bod_inputs.SALES_SPEC, 3)
    assert bod_inputs.read_sales(t, fy_start=1)[0].cogs == 1_500_000
