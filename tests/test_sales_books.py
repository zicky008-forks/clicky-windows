"""The sales ledger (AR12) matched to the books, trial balance and general ledger, with no false alarm: a gap that
is exactly an inventory provision, returned goods the ledger nets, or revenue the ledger does not hold (5113, 5117,
5118) is said as a note, never called an error; anything else is a gap with both figures. Synthetic data only."""
from decimal import Decimal as D

import sales_samples as ss
from engine import bod_inputs, sales_pnl
from engine.monthly_report import fmt as _fmt
from engine.trial_balance import read_trial_balance

M = 1_000_000
RETURN = ["21/10/2026", "HD000960", "TR02", "Trà xanh 500ml", "GT", "thùng", "-10", "-1800000", "0", "-1800000", "-1200000"]


def fmt(x):
    return _fmt(D(x))


def _load(tmp_path, **kw):
    return bod_inputs.load(ss.folder(tmp_path / "F01", **kw), fy_start=1)


def _cogs(m):
    return next(l for l in m.lines if l.code == "cogs")


def _rev(m):
    return next(l for l in m.lines if l.code == "revenue")


def _tb(**kw):
    return read_trial_balance(ss.tb_rows(**kw))


# ── Trial balance ───────────────────────────────────────────────────────────

def test_a_cogs_gap_that_is_the_inventory_provision_is_a_note(tmp_path):
    b = _load(tmp_path)
    tb = _tb(shift={"632": 3 * M}, extra=[["229", "Dự phòng tổn thất tài sản", 0, 0, 0, 3 * M, 0, 3 * M],
                                          ["2294", "Dự phòng giảm giá hàng tồn kho", 0, 0, 0, 3 * M, 0, 3 * M]])
    m = sales_pnl.match_books(b.sales, tb, ss.PERIOD, b.config)
    assert m.ok and _cogs(m).diff == -3 * M
    assert m.notes() == ["Lưu ý: giá vốn hàng bán trên sổ cao hơn sổ bán hàng 3.000.000 đ, bằng đúng trích lập dự "
                         "phòng giảm giá hàng tồn kho (Có TK 2294) 3.000.000 đ. Đây không phải lỗi, sổ bán hàng không "
                         "ghi các khoản này"]
    assert "chênh lệch đã rõ nguyên nhân" in m.sentence() and "lệch sổ cân đối" not in m.sentence()


def test_a_cogs_gap_that_is_the_returns_the_ledger_nets_is_a_note(tmp_path):
    b = _load(tmp_path, ledger=ss.ledger_rows(extra=[RETURN]))
    m = sales_pnl.match_books(b.sales, _tb(shift={"5211": 1_800_000}), ss.PERIOD, b.config)   # returns in 5213
    assert m.ok and _rev(m).diff == 0 and _cogs(m).diff == -1_200_000
    assert m.notes()[0].endswith("bằng đúng giá vốn hàng bán bị trả lại mà sổ bán hàng đã trừ 1.200.000 đ. "
                                 "Đây không phải lỗi, sổ bán hàng không ghi các khoản này")


def test_a_provision_and_returns_together_explain_the_gap(tmp_path):
    b = _load(tmp_path, ledger=ss.ledger_rows(extra=[RETURN]))
    tb = _tb(shift={"5211": 1_800_000, "632": 3 * M}, extra=[["2294", "Dự phòng giảm giá HTK", 0, 0, 0, 3 * M, 0, 3 * M]])
    m = sales_pnl.match_books(b.sales, tb, ss.PERIOD, b.config)
    assert m.ok and sorted(a for _w, a in _cogs(m).causes) == [-3 * M, -1_200_000]


def test_an_unexplained_cogs_gap_is_still_a_gap(tmp_path):
    b = _load(tmp_path)
    tb = _tb(shift={"632": 4 * M}, extra=[["2294", "Dự phòng giảm giá HTK", 0, 0, 0, 3 * M, 0, 3 * M]])
    m = sales_pnl.match_books(b.sales, tb, ss.PERIOD, b.config)
    assert not m.ok and _cogs(m).causes == [] and m.notes() == []
    assert "giá vốn hàng bán lệch sổ cân đối" in m.sentence()


def test_other_511_revenue_is_named_not_matched(tmp_path):
    b = _load(tmp_path)
    tb = _tb(extra=[["5113", "Doanh thu cung cấp dịch vụ", 0, 0, 7 * M, 7 * M, 0, 0],
                    ["5118", "Doanh thu khác", 0, 0, 2 * M, 2 * M, 0, 0]])
    m = sales_pnl.match_books(b.sales, tb, ss.PERIOD, b.config)
    assert m.ok and _rev(m).accounts == ["5111", "5211"]
    assert m.other == [("5113", 7 * M), ("5118", 2 * M)]
    assert m.notes() == ["Lưu ý: TK 5113 7.000.000 đ, TK 5118 2.000.000 đ là doanh thu khác, không có trong sổ bán "
                         "hàng, em không so với sổ bán hàng"]


# ── General ledger ──────────────────────────────────────────────────────────

def test_ar12_matches_the_general_ledger_and_every_posting_is_counted(tmp_path):
    b = _load(tmp_path, gl=ss.gl_rows())
    m = sales_pnl.match_gl(b.sales, b.postings, ss.PERIOD, b.config)
    assert m.ok and m.source == "sổ cái" and m.notes() == []
    rev, cogs = sum(x[1] for x in ss.october().values()), sum(x[2] for x in ss.october().values())
    assert (_rev(m).books, _cogs(m).books) == (rev, cogs)
    r = m.receipt
    assert r["used"] + sum(n for _r, n, _a in r["left"]) == r["total"]
    assert [(reason, n) for reason, n, _a in r["left"]] == [("kết chuyển cuối kỳ (đối ứng TK 911)", 2),
                                                            ("kết chuyển giảm trừ doanh thu (521 sang 511)", 2)]
    assert m.sentence().startswith(f"Tháng 10/2026: doanh thu thuần khớp sổ cái, {fmt(rev)} đ")


def test_the_general_ledger_nets_returns_as_the_ledger_does(tmp_path):
    ledger = ss.ledger_rows(extra=[RETURN])
    b = _load(tmp_path, ledger=ledger, gl=ss.gl_rows(ledger))
    m = sales_pnl.match_gl(b.sales, b.postings, ss.PERIOD, b.config)
    assert m.ok and _cogs(m).diff == 0 and _cogs(m).causes == []


def test_a_provision_in_the_general_ledger_is_a_note_by_its_contra_account(tmp_path):
    provision = ["31/10/2026", "PK00777", "10", "2026", "632", "2294", "", "", "Trích lập dự phòng", str(3 * M)]
    b = _load(tmp_path, gl=ss.gl_rows(extra=[provision]))
    m = sales_pnl.match_gl(b.sales, b.postings, ss.PERIOD, b.config)
    assert m.ok and _cogs(m).causes == [("bút toán Nợ TK 632 đối ứng TK 2294", -3 * M)]
    assert m.notes()[0].startswith("Lưu ý: giá vốn hàng bán trên sổ cao hơn sổ bán hàng 3.000.000 đ")


def test_other_revenue_in_the_general_ledger_is_named_and_left_out_with_its_reason(tmp_path):
    service = ["30/10/2026", "HD009999", "10", "2026", "131", "5113", "", "", "Phí vận chuyển thu khách", str(5 * M)]
    b = _load(tmp_path, gl=ss.gl_rows(extra=[service]))
    m = sales_pnl.match_gl(b.sales, b.postings, ss.PERIOD, b.config)
    assert m.ok and m.other == [("5113", 5 * M)]
    assert ("TK 5113: doanh thu khác, không có trong sổ bán hàng", 1, 5 * M) in m.receipt["left"]
    assert m.receipt["used"] + sum(n for _r, n, _a in m.receipt["left"]) == m.receipt["total"]


def test_a_sale_missing_from_ar12_is_a_gap_never_explained_away(tmp_path):
    extra = ["30/10/2026", "HD009998", "10", "2026", "131", "5111", "", "", "Hóa đơn chưa có trong AR12", str(6 * M)]
    b = _load(tmp_path, gl=ss.gl_rows(extra=[extra]))
    m = sales_pnl.match_gl(b.sales, b.postings, ss.PERIOD, b.config)
    assert not m.ok and _rev(m).diff == -6 * M and "doanh thu thuần lệch sổ cái" in m.sentence()


# ── The plan against itself ─────────────────────────────────────────────────

def test_plan_rows_without_volume_are_named_when_the_stated_revenue_differs(tmp_path):
    rows = ss.plan_rows(total=None)
    rows[0] = rows[0] + ["Doanh thu"]
    rows.append(["AOP", 2026, 10, "DV99", "GT", "", "", "", 5 * M])          # a revenue with no volume
    made = sum(x[1] for x in ss.october_plan().values())
    plan = {"KH theo ma hang": rows, "PL": [["Phiên bản", "Năm", "Tháng", "Chỉ tiêu", "Số tiền"],
                                           ["AOP", 2026, 10, "Doanh thu thuần", made + 5 * M]]}
    b = _load(tmp_path, plan=plan)
    v = sales_pnl.compute(b.sales, b.sku_plans, b.plans, fy_start=1, config=b.config, plan_tables=b.sku_plan_totals)
    assert v.lines()[0].plan == made                                   # the row without volume is not in it
    assert len(v.plan_issues) == 1 and ("Chênh lệch này bằng đúng 1 dòng kế hoạch không có sản lượng "
                                        f"(KH theo ma hang!{len(rows)}, doanh thu thuần 5.000.000 đ) em chưa cộng") \
        in v.plan_issues[0]
