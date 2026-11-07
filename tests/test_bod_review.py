"""BOD pack review: each check catches its own error, with the right cell and the right number, and
stays quiet on a clean pack."""
from decimal import Decimal as D

from engine import bod_review as br


def E(period, amount, **kw):
    return br.Entry(period, D(amount), **kw)


def test_total_gap_fully_explained_by_causes():
    entries = [E("2026-10", 100, division="TM", dept="A"), E("2026-10", 30, division="TM", dept=""),
               E("2026-11", -20, division="TM", dept="A", text="Đảo bút toán"), E("2026-10", 50, division="SX")]
    suspects = {"chi phí chưa gán phòng ban": lambda e: not e.dept,
                "bút toán đảo kỳ sau": lambda e: "dao" in e.text.lower().replace("đ", "d").replace("ả", "a"),
                "khối khác": lambda e: e.division != "TM"}
    # truth = 130; the pack left out the unassigned 30 and added the next-period reversal -20
    out = br.check_total(D(80), "BOD!C10", entries, "TM", {"2026-10"}, suspects)
    assert len(out) == 1 and out[0].diff == D(-50) and out[0].where == "BOD!C10"
    assert sum(a for _, a in out[0].causes) == D(-50)
    assert {l for l, _ in out[0].causes} == {"chi phí chưa gán phòng ban (pack bỏ ra)", "bút toán đảo kỳ sau (pack cộng vào)"}
    assert br.check_total(D(130), "BOD!C10", entries, "TM", {"2026-10"}, suspects) == []


def test_unexplained_remainder_is_said():
    out = br.check_total(D(107), "X", [E("2026-10", 100, division="TM")], "TM", {"2026-10"}, {})
    assert out[0].causes == [("chưa giải thích được", D(7))]


def L(label, value, pct=None, **kw):
    return br.PackLine(label, f"BOD!{label}", None if value is None else D(value),
                       pct=None if pct is None else D(str(pct)), pct_where=f"BOD!{label}%", **kw)


def test_denominator_rule_names_the_base_used():
    bases = {"khoi_moi_site": D(1000), "site": D(400)}
    rule = lambda l: "khoi_moi_site" if l.channel in ("MNC", "Trader") else "site"
    lines = [L("MNC", 50, 0.05, channel="MNC"), L("Trader", 20, 0.05, channel="Trader"), L("GT", 40, 0.1, channel="GT")]
    out = br.check_denominators(lines, rule, bases)
    assert [(f.where, f.expected) for f in out] == [("BOD!Trader%", D("0.02"))]
    assert "site" in out[0].message


def test_values_need_pct_and_points_and_points_are_differences():
    lines = [L("A", 10, None),
             L("B", 10, 0.05, compare_value=D(8), compare_pct=D("0.04"), diff_value=D(2), diff_pp=D("0.25")),
             L("C", 10, 0.05, compare_value=D(8), compare_pct=D("0.04"), diff_value=D(2), diff_pp=D("0.01")),
             L("Dd", 10, 0.05, compare_value=D(8), compare_pct=D("0.04"))]
    codes = [(f.code, f.where) for f in br.check_value_pct_points(lines)]
    assert ("missing_pct", "BOD!A") in codes
    assert ("wrong_pp", "BOD!B") in codes                 # 25% = relative change, not points
    assert not any(w == "BOD!C" for _, w in codes)
    assert ("missing_diff", "BOD!Dd") in codes and ("missing_pp", "BOD!Dd") in codes


def test_last_year_compared_with_right_version():
    allowed = {"current": {"AOP", "AOPR", "MUF"}, "previous": {"AOP revised"}}
    out = br.check_scenarios([("BOD!E3", "previous", "AOP"), ("BOD!F3", "previous", "AOP Revised"),
                              ("BOD!G3", "current", "AOPR")], allowed)
    assert [f.where for f in out] == ["BOD!E3"]
    vals = br.check_scenario_values([("BOD!E10", "Chi phí", D(90))], {"AOPR_LY": {"Chi phí": D(95)}, "AOP_LY": {"Chi phí": D(90)}}, "AOPR_LY")
    assert vals[0].expected == D(95) and "AOP_LY" in vals[0].message


def test_locked_month_still_plan():
    revised = {("Chi phí", "2026-07"): D(10000), ("Chi phí", "2026-08"): D(12000), ("Chi phí", "2026-11"): D(15000)}
    actual = {("Chi phí", "2026-07"): D(10000), ("Chi phí", "2026-08"): D(11000)}
    plan = {("Chi phí", "2026-08"): D(12000), ("Chi phí", "2026-11"): D(15000)}
    out = br.check_locked_months(revised, actual, plan, {"2026-07", "2026-08", "2026-09", "2026-10"},
                                 lambda line, m: f"AOPR!{m}")
    assert [(f.code, f.where, f.expected) for f in out] == [("locked_month_plan", "AOPR!2026-08", D(11000))]


def test_cut_rule():
    k = D(1000)
    lines = [br.CutLine("IT", "Cat!C5", 50 * k, 40 * k, 5 * k, 2 * k), br.CutLine("Pháp chế", "Cat!C6", 20 * k, 20 * k, 0 * k, 1 * k),
             br.CutLine("MKT", "Cat!C7", 100 * k, 90 * k, 10 * k, 3 * k)]
    out = br.check_cut(lines, 150 * k, ["pháp chế"], "Cat!C20")
    assert [(f.code, f.where) for f in out] == [("no_cut_touched", "Cat!C6"), ("levers_vs_gap", "Cat!C20")]
    assert out[1].expected == 20 * k and out[1].actual == 21 * k


def test_fx_rate_type():
    rates = {("2026-10", "binh_quan"): D(25000), ("2026-10", "cuoi_ky"): D(25300), ("2026-10", "aop"): D(24500)}
    rows = [("FX!D5", "Nhập khẩu", "2026-10", D(100), D(100) * D(800)), ("FX!D6", "Bao bì", "2026-10", D(10), D(10) * D(500))]
    out = br.check_fx(rows, rates, ("binh_quan", "aop"))
    assert [f.where for f in out] == ["FX!D5"] and "cuoi_ky" in out[0].message and out[0].expected == D(50000)
