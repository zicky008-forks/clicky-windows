"""Variance classification (F01 principles 4 and 5) on a made-up year: timing late and early, a one-off, a correction
of an earlier entry, a variable cost against the flexed plan, a real overspend, small wobbles, and a clean copy that
must raise no alarm at all."""
from decimal import Decimal

from engine import fpa, variance_class as vc
from engine.bod_inputs import Config, PlanRow
from engine.bod_review import Entry

M = 1_000_000
D = Decimal
PERIOD = "2026-10"
MONTHS = [f"2026-{m:02d}" for m in range(1, 13)]
DONE = MONTHS[:10]


def year(clean=False):
    """(gl, plans). Each story is one department x cost element; clean=True spends exactly the plan everywhere."""
    gl, plans, n = [], [], [0]

    def plan(dept, el, per_month, step=None):
        for m in MONTHS:
            amt = per_month + ((step[1] if step and m == step[0] else 0))
            plans.append(PlanRow("AOP", m, D(amt * M), dept=dept, element=el, file="AOP.xlsx", where=f"AOP!{m}"))

    def spend(dept, el, m, amt, ref="", text=""):
        n[0] += 1
        gl.append(Entry(m, D(amt * M), dept=dept, element=el, account="642", ref=ref or f"PC{n[0]:04d}",
                        text=text or f"{el} {m}", where=f"GL!{n[0] + 1}"))

    # late: September's rent paid in October
    plan("HC", "THUE", 100)
    for m in DONE:
        spend("HC", "THUE", m, 100 if clean or m not in ("2026-09", "2026-10") else (0 if m == "2026-09" else 200))
    # early: the December customer event, spent in October
    plan("MKT", "SK", 10, step=("2026-12", 200))
    for m in DONE:
        spend("MKT", "SK", m, 10)
    if not clean:
        spend("MKT", "SK", PERIOD, 200, "PC9001", "Hội nghị khách hàng")
    # one-off: a fine, once
    plan("KD", "CTP", 50)
    for m in DONE:
        spend("KD", "CTP", m, 50)
    if not clean:
        spend("KD", "CTP", PERIOD, 30, "PC7777", "Phạt chậm nộp hồ sơ")
    # correction: a September line posted twice, reversed in October
    plan("HC", "LUONG", 300)
    for m in DONE:
        spend("HC", "LUONG", m, 300)
    if not clean:
        spend("HC", "LUONG", "2026-09", 40, "PK500", "Lương T9 ghi trùng")
        spend("HC", "LUONG", PERIOD, -40, "PK610", "Hủy bút toán ghi trùng")
    # variable: freight moves with revenue
    plan("KD", "VC", 100)
    for m in DONE:
        spend("KD", "VC", m, 120 if m == PERIOD and not clean else 100)
    # a real overspend: advertising, two lines like every month, both higher
    plan("KD", "QC", 200)
    for m in DONE:
        for _ in range(2):
            spend("KD", "QC", m, 130 if m == PERIOD and not clean else 100)
    # small wobbles, and a year of small shortfalls that is not a timing story
    plan("HC", "VPP", 20)
    for m in DONE:
        spend("HC", "VPP", m, 23 if m == PERIOD and not clean else 20)
    plan("HC", "DIEN", 100)
    for m in DONE:
        spend("HC", "DIEN", m, 100 if clean else (112 if m == PERIOD else 96))
    return gl, plans


CONFIG = Config(rules={"yeu to bien doi": "VC"})
REVENUE = (D(1200 * M), D(1000 * M))              # revenue 120% of plan in October


def _run(clean=False, driver=REVENUE, config=CONFIG):
    gl, plans = year(clean)
    v = fpa.compute(gl, plans, fy_start=1, config=config)
    return {(c.line.dept, c.line.element): c for c in vc.classify(v, gl, plans, config, driver)}


def test_each_story_gets_its_cause_and_the_underlying_figure():
    c = _run()
    late = c[("HC", "THUE")]
    assert (late.reported, late.underlying, late.verdict, late.alarm) == (100 * M, 0, "lệch thời điểm", False)
    assert "Các tháng trước còn 100.000.000 đ kế hoạch chưa chi" in late.sentence()
    early = c[("MKT", "SK")]
    assert (early.reported, early.underlying, early.verdict) == (200 * M, 0, "lệch thời điểm")
    assert "ở tháng 12/2026" in early.sentence()
    once = c[("KD", "CTP")]
    assert (once.reported, once.underlying, once.verdict) == (30 * M, 0, "khoản một lần")
    assert "PC7777, Phạt chậm nộp hồ sơ" in once.sentence() and once.causes[0].where
    fix = c[("HC", "LUONG")]
    assert (fix.reported, fix.underlying, fix.verdict, fix.alarm) == (-40 * M, 0, "điều chỉnh bút toán", False)
    assert "điều chỉnh bút toán tháng 09/2026 (chi tiết do phòng nhân sự giữ), không phải tiết kiệm" in fix.sentence()
    freight = c[("KD", "VC")]
    assert (freight.reported, freight.flex, freight.underlying, freight.verdict) == (20 * M, 120 * M, 0, "theo sản lượng")
    over = c[("KD", "QC")]
    assert (over.reported, over.underlying, over.verdict, over.alarm, over.causes) == (60 * M, 60 * M, "vượt kế hoạch",
                                                                                      True, [])


def test_small_and_noisy_lines_are_never_alarms():
    c = _run()
    assert c[("HC", "VPP")].verdict == "nhỏ dưới ngưỡng" and not c[("HC", "VPP")].alarm
    assert c[("HC", "VPP")].sentence().endswith("dưới ngưỡng 5.000.000 đ, không phải bất thường.")
    noisy = c[("HC", "DIEN")]                         # nine months 4 tr under, then 12 tr over: no timing claimed
    assert noisy.causes == [] and noisy.verdict == "vượt kế hoạch"


def test_the_clean_copy_raises_no_alarm():
    c = _run(clean=True, driver=(D(1000 * M), D(1000 * M)))
    assert [k for k, x in c.items() if x.alarm or x.material] == []


def test_a_variable_cost_on_plan_when_volume_fell_is_over_the_flexed_plan():
    gl, plans = year(clean=True)
    v = fpa.compute(gl, plans, fy_start=1, config=CONFIG)
    x = next(c for c in vc.classify(v, gl, plans, CONFIG, (D(800 * M), D(1000 * M))) if c.line.element == "VC")
    assert (x.reported, x.flex, x.underlying, x.material, x.verdict) == (0, 80 * M, 20 * M, True,
                                                                         "vượt ngân sách linh hoạt")


def test_a_variable_cost_below_the_flexed_plan_says_so():
    gl, plans = year(clean=True)
    v = fpa.compute(gl, plans, fy_start=1, config=CONFIG)
    x = next(c for c in vc.classify(v, gl, plans, CONFIG, (D(1300 * M), D(1000 * M))) if c.line.element == "VC")
    assert (x.reported, x.flex, x.underlying, x.verdict) == (0, 130 * M, -30 * M, "thấp hơn ngân sách linh hoạt")
    assert x.sentence().endswith("Số thực chất chênh -30.000.000 đ, thấp hơn ngân sách linh hoạt.")


def test_a_one_off_or_correction_on_pay_never_names_the_person():
    gl, plans = year()
    for e in gl:
        if (e.dept, e.element, e.ref) == ("HC", "LUONG", "PK500"):
            e.text = "Thưởng – Nguyễn Văn An"
    plans += [PlanRow("AOP", m, D(0), dept="KD", element="THUONG", file="AOP.xlsx") for m in MONTHS]
    gl.append(Entry(PERIOD, D(25 * M), dept="KD", element="THUONG", account="6421", ref="PC8888",
                    text="Thưởng dự án – Trần Thị Bình", where="GL!999"))
    cfg = Config(rules={"yeu to bien doi": "VC"}, elements={"THUONG": {"ten": "Thưởng"}, "LUONG": {"ten": "Lương"}})
    v = fpa.compute(gl, plans, fy_start=1, config=cfg)
    c = {(x.line.dept, x.line.element): x for x in vc.classify(v, gl, plans, cfg, REVENUE)}
    bonus, fix = c[("KD", "THUONG")], c[("HC", "LUONG")]
    assert bonus.verdict == "khoản một lần" and fix.verdict == "điều chỉnh bút toán"
    said = bonus.sentence() + fix.sentence()
    for secret in ("Trần Thị Bình", "Nguyễn Văn An", "PC8888", "PK500", "PK610"):
        assert secret not in said, secret
    assert "Khoản một lần ở Thưởng: 25.000.000 đ (chi tiết do phòng nhân sự giữ)" in bonus.sentence()
    assert bonus.causes[0].where == ["GL!999"]                      # the audit trail keeps the cell


def test_without_a_driver_a_variable_cost_is_judged_on_plan():
    x = _run(driver=None)[("KD", "VC")]
    assert x.flex is None and x.verdict == "vượt kế hoạch"


def test_thresholds_come_from_the_catalogue():
    cfg = Config(rules={"nguong chenh lech": "50000000", "nguong chenh lech (%)": "10"},
                 rule_text={"nguong chenh lech": "Ngưỡng chênh lệch", "nguong chenh lech (%)": "Ngưỡng chênh lệch (%)"})
    assert vc.thresholds(cfg) == (D(50 * M), D(10))
    assert vc.thresholds(Config()) == (vc.ABS_DEFAULT, vc.PCT_DEFAULT)
    over = _run(config=Config(rules={"nguong chenh lech": "70000000"}))[("KD", "QC")]
    assert over.verdict == "nhỏ dưới ngưỡng"


def test_summary_reports_both_figures():
    reported, underlying, alarms = vc.summary(list(_run().values()))
    assert reported == (100 + 200 + 30 - 40 + 20 + 60 + 12) * M and underlying == (60 + 12) * M
    assert sorted((a.line.dept, a.line.element) for a in alarms) == [("HC", "DIEN"), ("KD", "QC")]
