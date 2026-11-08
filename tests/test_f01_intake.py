"""The shapes real FP&A folders come in, each built from scratch on synthetic numbers: a ledger with Phát sinh Nợ /
Phát sinh Có and its month-end closing entry, plans printed wide by month (by P&L line, by SKU with a price and a
standard cost, by department x cost element), a folder with no plan, and a fiscal year the folder only implies.
Every file's used + left-out lines add up to its lines, and every figure is checked against the generator's own
arithmetic."""
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

import fpa_samples as fs
import sales_samples as ss
from engine import bod_inputs, fiscal, fpa, fpa_report
from engine.trial_balance import read_trial_balance

M = fs.M
D = Decimal
T = [f"T{m:02d}" for m in range(1, 13)]
PS_HEADER = ["Ngày hạch toán", "Số CT", "Tháng", "Năm", "Tài khoản", "TK đối ứng", "Mã phòng ban", "Mã khoản mục",
             "Diễn giải", "Phát sinh Nợ", "Phát sinh Có"]


def two_sided_gl(closing: bool = True) -> list:
    """fs.gl_rows() as an accounting export prints it: one account column, its contra, and the two sides apart. The
    closing entry Nợ 911 / Có 6xx of October is there too, as a real export has it."""
    out = [PS_HEADER]
    for r in fs.gl_rows()[1:]:
        dr, cr, amount = r[4], r[5], r[9]
        if cr.startswith("6"):                                   # the cost written down: on the Có side
            out.append(r[:4] + [cr, dr] + r[6:9] + ["", amount])
        else:
            out.append(r[:4] + [dr, cr] + r[6:9] + [amount, ""])
    if closing:
        for n, (account, amount) in enumerate(sorted(fs.october_debits().items()), 1):
            net = amount - (fs.WRITTEN_DOWN[3] if account == fs.WRITTEN_DOWN[2] else 0)     # the account's balance
            out.append(["31/10/2026", f"KC{n:03d}", "10", "2026", account, "911", "", "", "Kết chuyển chi phí", "",
                        str(net)])
    return out


def receipt(b, name):
    return next(r for r in b.receipt if r[0] == name)


def lines_in(rows) -> int:
    return sum(1 for r in rows[1:] if any(str(v).strip() for v in r if v is not None))


# ── K1: Phát sinh Nợ / Phát sinh Có ──────────────────────────────────────────

def test_a_ledger_with_debit_and_credit_columns_is_read_as_debit_less_credit(tmp_path):
    d = fs.opex_folder(tmp_path / "ps", tb=True)
    rows = two_sided_gl()
    fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
    plain = bod_inputs.load(fs.opex_folder(tmp_path / "plain", tb=True), fy_start=1)
    b = bod_inputs.load(d, fy_start=1)
    assert [(e.period, e.amount, e.account, e.dept, e.element) for e in b.gl] == \
           [(e.period, e.amount, e.account, e.dept, e.element) for e in plain.gl]
    down = [e for e in b.gl if e.side == "cr"]
    assert [(e.account, e.amount) for e in down] == [(fs.WRITTEN_DOWN[2], -fs.WRITTEN_DOWN[3])]
    _n, role, used, skipped = receipt(b, "SoChiTietChiPhi_2026.csv")
    closing = [w for _r, w in skipped if w == bod_inputs.CLOSING_WHY]
    assert role == "Sổ chi tiết chi phí" and len(closing) == len(fs.october_debits())
    assert used + len(skipped) == lines_in(rows)                    # rule 4: used + left out = the file's lines
    m = fpa_report.compute(d)
    assert m.cost_books.ok and m.row("opex").actual == sum(fs.october_debits().values()) - fs.WRITTEN_DOWN[3]


def test_the_closing_entry_to_911_is_never_a_cost_written_down(tmp_path):
    # Nợ 911 / Có 6xx in a TK Nợ / TK Có export: read as a cost on the Có side it would wipe out the month.
    d = fs.folder(tmp_path / "F01")
    rows = fs.gl_rows()
    closing = [["31/10/2026", f"KC{n:03d}", "10", "2026", "911", account, "", "", "Kết chuyển chi phí", str(amount)]
               for n, (account, amount) in enumerate(sorted(fs.october_debits().items()), 1)]
    fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows + closing)
    plain = bod_inputs.load(fs.folder(tmp_path / "plain"), fy_start=1)
    b = bod_inputs.load(d, fy_start=1)
    assert [(e.period, e.amount, e.account) for e in b.gl] == [(e.period, e.amount, e.account) for e in plain.gl]
    _n, _r, used, skipped = receipt(b, "SoChiTietChiPhi_2026.csv")
    assert sum(1 for _r, w in skipped if w == bod_inputs.CLOSING_WHY) == len(closing)
    assert used + len(skipped) == lines_in(rows + closing)
    m = fpa.match_books(b.gl, read_trial_balance(fs.tb_rows()), fs.PERIOD)
    assert m.ok and m.totals[2] == fs.WRITTEN_DOWN[3]


# ── K2: plans printed wide by month ──────────────────────────────────────────

PNL_PLAN = (("Doanh thu thuần", 9000), ("Giá vốn hàng bán", 6000), ("Lợi nhuận gộp", 3000),
            ("Chi phí bán hàng", 900), ("Chi phí quản lý doanh nghiệp", 450), ("Lợi nhuận hoạt động", 1650))


def _folder(tmp_path, name, plan_sheets, tb=True):
    d = tmp_path / name
    d.mkdir(parents=True)
    fs.write_csv(d / "SoChiTietChiPhi_2026.csv", fs.gl_rows())
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET, "Khoản mục": fs.ELEMENT_SHEET})
    if plan_sheets is not None:
        ss.write_xlsx(d / "KeHoach_AOP_2026.xlsx", plan_sheets)
    if tb:
        fs.write_csv(d / "TB T10 2026.csv", fs.tb_rows())
    return d


def test_a_pnl_plan_by_line_with_month_columns_gives_the_report_its_plan(tmp_path):
    plan = [["Chỉ tiêu"] + T] + [[n] + [v * M] * 12 for n, v in PNL_PLAN]
    d = _folder(tmp_path, "a", {"KH": plan})
    b = bod_inputs.load(d, fy_start=1)
    _n, role, used, skipped = receipt(b, "KeHoach_AOP_2026.xlsx")
    assert role == "Kế hoạch" and used + len(skipped) == len(PNL_PLAN) and not skipped
    m = fpa_report.compute(d)
    opex = m.row("opex")
    assert (opex.plan, opex.ytd_plan) == ((900 + 450) * M, (900 + 450) * M * 10)
    assert opex.has_plan and "Chi phí hoạt động" in opex.name and "file kế hoạch KeHoach_AOP_2026.xlsx" in opex.plan_source
    assert all(not l.plan for l in m.costs.ytd)                   # the P&L lines never land on a department
    assert m.classified == []                                     # no plan by department: no variance to alarm on
    assert any(s.startswith("Kế hoạch chi phí hoạt động lấy theo dòng chi phí hoạt động trên file kế hoạch")
               for s in m.said)
    out = fpa_report.write(m)
    assert out["verified"]
    rows = list(load_workbook(out["xlsx"])[fpa_report.COSTS].iter_rows(values_only=True))
    total = rows[-1]
    assert total[0] == "Tổng cộng" and D(str(total[3])) == opex.plan and all(r[3] is None for r in rows[3:-1])


def test_a_sku_plan_with_a_price_a_standard_cost_and_monthly_volumes_is_unpivoted(tmp_path):
    head = ["Mã hàng", "Tên hàng", "Giá bán", "Giá vốn chuẩn"] + T
    plan = [head] + [[s, n, p, c] + [100 + m for m in range(12)] for s, (n, p, c) in ss.SKUS.items()]
    plan.append(["XX09", "Mã ngừng bán", 99_000, 50_000] + [None] * 12)
    d = _folder(tmp_path, "b", {"KH": plan})
    b = bod_inputs.load(d, fy_start=1)
    _n, role, used, skipped = receipt(b, "KeHoach_AOP_2026.xlsx")
    assert role == "Kế hoạch theo mã hàng" and (used, skipped) == (3, [(5, "không có sản lượng tháng nào")])
    assert len(b.sku_plans) == 36
    oct_nc01 = next(r for r in b.sku_plans if r.sku == "NC01" and r.month == "2026-10")
    _name, price, cost = ss.SKUS["NC01"]
    assert (oct_nc01.qty, oct_nc01.revenue, oct_nc01.cogs) == (109, 109 * price, 109 * cost)
    assert oct_nc01.where == "KH!N2"                               # the month's own cell: traceable


def test_a_cost_plan_by_department_and_element_wide_by_month_is_read_into_the_split(tmp_path):
    plan = [["Mã phòng ban", "Mã khoản mục"] + T] + [[dp, el] + [fs.plan_month(dp, el, mo) for mo in range(1, 13)]
                                                      for dp, el, _a, _p in fs.PLAN]
    d = _folder(tmp_path, "c", {"KH": plan})
    m = fpa_report.compute(d)
    assert m.fy == 1 and "file kế hoạch KeHoach_AOP_2026.xlsx xếp cột tháng từ tháng 1" in m.fy_said
    line = next(l for l in m.costs.month if (l.dept, l.element) == ("MKT", "QC"))
    assert line.plan == fs.plan_month("MKT", "QC", 10)
    assert m.row("opex").ytd_plan == sum(fs.plan_month(dp, el, mo) for dp, el, _a, _p in fs.PLAN for mo in range(1, 11))


# ── K3: no plan ──────────────────────────────────────────────────────────────

def test_without_a_plan_the_plan_cells_are_blank_and_it_is_said(tmp_path):
    d = _folder(tmp_path, "none", None)
    m = fpa_report.compute(d)
    opex = m.row("opex")
    assert not opex.has_plan and m.classified == []
    assert any(s.startswith("Thư mục chưa có kế hoạch cho chi phí hoạt động: em để trống cột kế hoạch") for s in m.said)
    out = fpa_report.write(m)
    assert out["verified"]
    ws = load_workbook(out["xlsx"])[fpa_report.PNL]
    row = next(r for r in ws.iter_rows(min_row=4, values_only=True) if r[0] == "Chi phí hoạt động")
    assert row[2] is None and row[3] is None and row[4] is None and row[-1] == fpa_report.NO_PLAN
    from office import fpa_skill
    assert fpa_skill.beats_for(m, out)[0].note == "Chưa có kế hoạch"


# ── K4: the fiscal year ──────────────────────────────────────────────────────

def test_the_fiscal_year_is_read_from_the_folder_never_assumed_in_silence(tmp_path):
    # Ledger January to October, plan long with its year, no rule: the plan's twelve months say January.
    d = _folder(tmp_path, "long", {"KH": fs.plan_rows("AOP")[:-1]})
    m = fpa_report.compute(d)
    assert m.fy == 1 and m.costs.months[0] == "2026-01" and not [x for x in m.costs.outside if x[1] < "2026-01"]
    # A plan printed T07 .. T06: July.
    order = T[6:] + T[:6]
    plan = [["Mã phòng ban", "Mã khoản mục"] + order] + [[dp, el] + [1 * M] * 12 for dp, el, _a, _p in fs.PLAN]
    b = bod_inputs.load(_folder(tmp_path, "july", {"KH": plan}))
    assert fiscal.start(b)[0] == 7
    # Nothing in the folder says it: January, and the receipt says so with the one step to change it.
    d = _folder(tmp_path, "silent", None, tb=False)
    m = fpa_report.compute(d)
    assert m.fy == 1 and m.fy_said.startswith("Thư mục không ghi năm tài chính bắt đầu tháng nào, nên em tính từ tháng 1")
    assert m.fy_said in m.said
    # The catalogue rule wins.
    d = _folder(tmp_path, "rule", None, tb=False)
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET, "Khoản mục": fs.ELEMENT_SHEET,
                                        "Tham số": [["Tham số", "Giá trị"], ["Tháng bắt đầu năm tài chính", 4]]})
    assert fiscal.start(bod_inputs.load(d))[0] == 4


def test_the_trial_balance_year_to_date_says_where_the_year_starts(tmp_path):
    ytd = {}
    for r in fs.gl_rows()[1:]:
        if r[4].startswith("6") and r[9] and r[2].isdigit() and int(r[2]) <= 10:
            ytd[r[4]] = ytd.get(r[4], 0) + int(D(r[9]))
    rows = fs.tb_rows()
    tb = [rows[0] + ["Lũy kế phát sinh Nợ", "Lũy kế phát sinh Có"]]
    for r in rows[1:]:
        a = str(r[0])
        dr = sum(v for k, v in ytd.items() if k.startswith(a)) if a[:1] == "6" else 0
        tb.append(r + [dr, dr])
    d = _folder(tmp_path, "tb", None, tb=False)
    fs.write_csv(d / "TB T10 2026.csv", tb)
    b = bod_inputs.load(d)
    n, said = fpa_report.fiscal_start(d, b)
    assert n == 1 and "lũy kế chi phí trên bảng cân đối" in said


def test_a_ledger_that_runs_across_a_new_year_starts_the_year(tmp_path):
    rows = [fs.GL_HEADER]
    for y, mo in [(2025, m) for m in range(7, 13)] + [(2026, m) for m in range(1, 5)]:
        rows.append([f"25/{mo:02d}/{y}", f"PC{y}{mo:02d}", str(mo), str(y), "6418", "331", "MKT", "QC", "QC", str(M)])
    d = tmp_path / "cross"
    d.mkdir()
    fs.write_csv(d / "SoChiTietChiPhi.csv", rows)
    b = bod_inputs.load(d)
    assert fiscal.start(b)[0] == 7


# ── K5: the pack size in the catalogue ───────────────────────────────────────

def test_the_catalogue_pack_size_converts_the_units_exactly(tmp_path):
    from engine.sales_pnl import standard_qty
    p = tmp_path / "DanhMuc.xlsx"
    ss.write_xlsx(p, {"Ma hang": [["Mã hàng", "Tên hàng", "ĐVT", "Quy cách"],
                                  ["NC01", "Nước cam 330ml", "Thùng", "24 chai/thùng"],
                                  ["TR02", "Trà xanh 500ml", "Thùng", "Thùng 12 chai"],
                                  ["SU03", "Sữa đậu nành 1L", "Thùng", "1 thùng = 6 hộp"],
                                  ["DT04", "Nước dừa 330ml", "Lon", "24 lon/thùng"]],
                      "Quy doi": [["Mã hàng", "Tên hàng", "ĐVT", "Số chai/thùng"], ["CF05", "Cà phê sữa", "Thùng", 20]]})
    cfg = bod_inputs.read_config(p)
    assert standard_qty(cfg, "NC01", "Chai", D(48)) == (D(2), "Thùng", "")
    assert standard_qty(cfg, "NC01", "chai", D(10))[0] * 24 == D(10)          # kept exact: divided, never rounded
    assert standard_qty(cfg, "TR02", "chai", D(36))[0] == D(3)
    assert standard_qty(cfg, "SU03", "Hộp", D(12))[0] == D(2)
    assert standard_qty(cfg, "DT04", "Thùng", D(2)) == (D(48), "Lon", "")      # the standard is the small unit
    assert standard_qty(cfg, "CF05", "Chai", D(40))[0] == D(2)
    assert standard_qty(cfg, "NC01", "Lốc", D(4))[2] == "no factor"           # a unit nothing converts: said


# ── K6: a forecast is not the plan ───────────────────────────────────────────

def test_a_forecast_file_has_the_forecast_role_never_the_plans(tmp_path):
    from engine import roles
    assert roles.of_file("LE_T09_2026.csv") == roles.FORECAST and roles.of_file("Uoc TH 2026.xlsx") == roles.FORECAST
    assert roles.of_file("AOP_2026.csv") == roles.PLAN and roles.of_file("Doanh thu ban le.xlsx") != roles.FORECAST
    d = fs.folder(tmp_path / "F01", plans={"AOP_2026.csv": fs.plan_rows("AOP"),
                                           "LE_T09_2026.csv": fs.plan_rows("LE T09", approved="20/09/2026", scale=2)})
    b = bod_inputs.load(d, fy_start=1, purpose="fpa")
    assert receipt(b, "AOP_2026.csv")[1] == roles.PLAN and receipt(b, "LE_T09_2026.csv")[1] == roles.FORECAST


# ── K7: the FP&A receipt speaks for the FP&A month ───────────────────────────

def test_the_fpa_receipt_has_its_own_words_never_the_bod_reviews(tmp_path):
    d = _folder(tmp_path, "words", None)
    ss.write_xlsx(d / "Bao cao BOD T09.xlsx", {"P&L": [["Chỉ tiêu", "Thực tế"], ["Doanh thu", 1]]})
    ss.write_xlsx(d / "Ghi chu.xlsx", {"Sheet1": [["Việc cần làm", "Người làm"], ["Gửi số", "FP&A"]]})
    ss.write_xlsx(d / "KeHoach_2026.xlsx", {"Sheet1": [["Ghi chú"], ["chưa có số"]]})
    m = fpa_report.compute(d)
    out = fpa_report.write(m)
    rows = [r for r in fpa_report.file_rows(m)[1:]]
    words = " ".join(str(x) for r in rows for x in r) + " " + " ".join(m.said)
    assert "BOD" not in words.replace("Bao cao BOD T09.xlsx", "") and "nhóm" not in words and "soát bộ" not in words
    why = {r[0]: r[4] for r in rows}
    assert why["Bao cao BOD T09.xlsx"] == "bản trình Ban Giám đốc: báo cáo FP&A không lấy số từ bản trình"
    assert why["Ghi chu.xlsx"].startswith("em không nhận ra bảng số liệu nào dùng cho báo cáo FP&A trong file này")
    assert why["KeHoach_2026.xlsx"].endswith("Vì vậy cột kế hoạch cần số kế hoạch theo tháng")
    assert out["verified"]


def test_an_opex_total_beside_its_parts_is_taken_once(tmp_path):
    lines = (("Chi phí bán hàng", 900), ("Chi phí quản lý doanh nghiệp", 450), ("Chi phí hoạt động", 1350))
    d = _folder(tmp_path, "total", {"KH": [["Chỉ tiêu"] + T] + [[n] + [v * M] * 12 for n, v in lines]})
    assert fpa_report.compute(d).row("opex").plan == 1350 * M


def test_the_closing_entry_is_found_without_a_contra_column(tmp_path):
    # One account column, Phát sinh Nợ / Có, and no TK đối ứng: the closing entry is still a Có line on each cost
    # account. Found by its text naming 911, or because it zeroes the account on the month's last day. The cost
    # written down on 30/10 stays a cost written down.
    plain = bod_inputs.load(fs.opex_folder(tmp_path / "plain", tb=True), fy_start=1)
    for text in ("K/C 911", "Kết chuyển chi phí"):
        d = fs.opex_folder(tmp_path / text.replace("/", ""), tb=True)
        rows = [r[:5] + r[6:] for r in two_sided_gl()]
        for r in rows[1:]:
            if r[1].startswith("KC"):
                r[7] = text
        fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
        b = bod_inputs.load(d, fy_start=1)
        assert [(e.period, e.amount, e.account) for e in b.gl] == [(e.period, e.amount, e.account) for e in plain.gl]
        _n, _r, used, skipped = receipt(b, "SoChiTietChiPhi_2026.csv")
        why = bod_inputs.CLOSING_WHY if "911" in text else bod_inputs.CLOSING_NETS_WHY
        assert sum(1 for _r, w in skipped if w == why) == len(fs.october_debits())
        assert used + len(skipped) == lines_in(rows)
        assert [e.amount for e in b.gl if e.side == "cr"] == [-fs.WRITTEN_DOWN[3]]
    # An allocation written 'Kết chuyển' that does not zero the account is a real cost written down: kept.
    d = fs.opex_folder(tmp_path / "alloc", tb=True)
    rows = [r[:5] + r[6:] for r in two_sided_gl(closing=False)]
    rows.append(["31/10/2026", "PB001", "10", "2026", "6427", "HC", "THUE", "Kết chuyển chi phí thuê phân bổ", "", "1000000"])
    fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
    b = bod_inputs.load(d, fy_start=1)
    assert sorted(e.amount for e in b.gl if e.side == "cr") == sorted([-fs.WRITTEN_DOWN[3], D(-1000000)])


# ── K8: actual and plan pair on department x cost element ────────────────────

K8_ELEMENTS = (("Lương bán hàng", "6411"), ("Khấu hao bán hàng", "6414"), ("Chi phí bán hàng khác", "6418"),
               ("Lương quản lý", "6421"), ("Khấu hao quản lý", "6424"), ("Dịch vụ mua ngoài", "6427"))
K8_SPEND = {("KD", "6411"): 300, ("KD", "6414"): 40, ("KD", "6418"): 55, ("MKT", "6418"): 210,
            ("HC", "6421"): 280, ("HC", "6424"): 20, ("HC", "6427"): 115}


def test_actual_by_account_and_plan_by_element_name_pair_on_every_line(tmp_path):
    d = tmp_path / "k8"
    d.mkdir()
    gl = [["Ngày CT", "Số CT", "Tháng", "Năm", "TK Nợ", "TK Có", "Mã phòng ban", "Diễn giải", "Số tiền"]]
    for mo in range(1, 11):
        for (dept, acct), v in K8_SPEND.items():
            gl.append([f"20/{mo:02d}/2026", f"C{mo}{acct}{dept}", str(mo), "2026", acct, "331", dept, "Chi", str(v * M)])
    fs.write_csv(d / "SoChiTiet_2026.csv", gl)
    name = dict((a, n) for n, a in K8_ELEMENTS)
    plan = [["Phiên bản", "Năm", "Tháng", "Phòng ban", "Khoản mục", "Số tiền"]]
    for mo in range(1, 13):
        for (dept, acct), v in K8_SPEND.items():
            plan.append(["AOP", "2026", str(mo), dept, name[acct], str((v + 10) * M)])
    fs.write_csv(d / "AOP_2026.csv", plan)
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET, "Khoản mục": [["Khoản mục", "Tài khoản"]]
                                        + [list(x) for x in K8_ELEMENTS]})
    m = fpa_report.compute(d)
    lines = m.costs.ytd
    assert len(lines) == len(K8_SPEND)
    unmatched = [l for l in lines if not l.actual or not l.plan]
    assert unmatched == []                                        # 0 lines with only an actual or only a plan
    mkt = next(l for l in lines if l.dept == "MKT")
    assert (mkt.element, mkt.actual, mkt.plan) == ("Chi phí bán hàng khác", 2100 * M, 2200 * M)
    assert not [c for c in m.classified if c.material and c.verdict and "vượt" in c.verdict and not c.line.plan]


# The cost element named only in a catalogue map: account -> element, many accounts per element, an account name
# beside it. Synthetic sub-accounts; the element is found by what its column holds, never by its header.
K8_MAP = (("641101", "Lương nhân viên bán hàng", "Lương"), ("641102", "Phụ cấp bán hàng", "Lương"),
          ("641401", "Khấu hao xe bán hàng", "Khấu hao"), ("641801", "Chi phí bán hàng khác", "Chi khác"),
          ("642101", "Lương quản lý", "Lương"), ("642401", "Khấu hao văn phòng", "Khấu hao"),
          ("642701", "Dịch vụ thuê ngoài", "Chi khác"))
K8_MAP_SPEND = {("KD", "641101"): 250, ("KD", "641102"): 50, ("KD", "641401"): 40, ("KD", "641801"): 55,
                ("HC", "642101"): 280, ("HC", "642401"): 20, ("HC", "642701"): 115}


def _map_folder(tmp_path, name, plan_element_head, map_head):
    d = tmp_path / name
    d.mkdir()
    gl = [["Ngày CT", "Số CT", "Tháng", "Năm", "TK Nợ", "TK Có", "Mã phòng ban", "Diễn giải", "Số tiền"]]
    for mo in range(1, 11):
        for (dept, acct), v in K8_MAP_SPEND.items():
            gl.append([f"20/{mo:02d}/2026", f"C{mo}{acct}{dept}", str(mo), "2026", acct, "331", dept, "Chi", str(v * M)])
    fs.write_csv(d / "SoChiTiet_2026.csv", gl)
    element = {a: e for a, _n, e in K8_MAP}
    planned = {}
    for (dept, acct), v in K8_MAP_SPEND.items():
        planned[(dept, element[acct])] = planned.get((dept, element[acct]), 0) + v + 10
    plan = [["Phiên bản", "Năm", "Tháng", "Mã phòng ban", plan_element_head, "Số tiền"]]
    for mo in range(1, 13):
        for (dept, e), v in sorted(planned.items()):
            plan.append(["AOP", "2026", str(mo), dept, e, str(v * M)])
    fs.write_csv(d / "AOP_2026.csv", plan)
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET,
                                        "Map TK": [map_head] + [[a, n, e] for a, n, e in K8_MAP]})
    return d, planned


def _pairs_on_every_line(d, planned):
    m = fpa_report.compute(d)
    lines = m.costs.ytd
    assert sorted((l.dept, l.element) for l in lines) == sorted(planned)
    assert not [l for l in lines if not l.actual or not l.plan]         # no "TK 6411xx", no line with one side only
    kd = next(l for l in lines if (l.dept, l.element) == ("KD", "Lương"))
    assert (kd.actual, kd.plan) == (3000 * M, 3200 * M) and kd.label == "Kinh doanh · Lương"
    assert not [x for x in m.costs.left_out() if "không có mã yếu tố" in x]
    return m


def test_a_catalogue_map_gives_each_account_its_element_not_its_account_name(tmp_path):
    d, planned = _map_folder(tmp_path, "k8map", "Yếu tố chi phí",
                             ["Số hiệu TK", "Tên tài khoản", "Yếu tố chi phí quản trị"])
    _pairs_on_every_line(d, planned)


def test_the_element_column_is_found_by_what_it_holds_under_any_header(tmp_path):
    # Other wording, same shapes: the plan says 'Nhóm chi phí' and the map's element column has a header no list
    # holds, beside the account's name under 'Diễn giải' or 'Tên TK'.
    d, planned = _map_folder(tmp_path, "k8other", "Nhóm chi phí", ["Tài khoản", "Diễn giải", "Phân loại QT"])
    _pairs_on_every_line(d, planned)
    d2, planned2 = _map_folder(tmp_path, "k8name", "Nhóm chi phí", ["Tài khoản", "Tên TK", "Phân loại QT"])
    _pairs_on_every_line(d2, planned2)


def test_accounts_written_with_x_map_every_sub_account(tmp_path):
    d, planned = _map_folder(tmp_path, "k8x", "Yếu tố chi phí", ["Số hiệu TK", "Tên tài khoản", "Yếu tố chi phí quản trị"])
    rows = [["Số hiệu TK", "Tên tài khoản", "Yếu tố chi phí quản trị"]] + [[a[:4] + "xx", n, e] for a, n, e in K8_MAP
                                                                         if a.endswith("01")]
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET, "Map TK": rows})
    _pairs_on_every_line(d, planned)


def test_a_plan_by_department_only_is_compared_at_department_level_and_said(tmp_path):
    d, _planned = _map_folder(tmp_path, "k8dept", "Yếu tố chi phí", ["Số hiệu TK", "Tên tài khoản", "Yếu tố chi phí quản trị"])
    plan = [["Phiên bản", "Năm", "Tháng", "Mã phòng ban", "Số tiền"]]
    for mo in range(1, 13):
        plan.append(["AOP", "2026", str(mo), "KD", str(400 * M)])
        plan.append(["AOP", "2026", str(mo), "HC", str(420 * M)])
    fs.write_csv(d / "AOP_2026.csv", plan)
    m = fpa_report.compute(d)
    by = {l.dept: l for l in m.costs.ytd}
    assert sorted(by) == ["HC", "KD"] and all(l.element == fpa.DEPT_LEVEL for l in by.values())
    assert (by["KD"].actual, by["KD"].plan) == (3950 * M, 4000 * M)
    assert (by["HC"].actual, by["HC"].plan) == (4150 * M, 4200 * M)
    assert {l.dept for l in m.costs.month} == {"HC", "KD"} and all(l.plan and l.actual for l in m.costs.month)
    said = [x for x in m.costs.left_out() if "cấp cả bộ phận" in x]
    assert len(said) == 2 and said[0].startswith("Hành chính")
    for c in m.classified:                   # never -100% and 'over' for the same department
        assert c.line.plan and c.line.actual


# ── K9: every plan row in its year, the plan against its own totals ─────────

def test_a_full_year_column_is_never_read_as_the_year_and_checks_the_months(tmp_path):
    head = ["Mã phòng ban", "Mã khoản mục"] + T + ["Cả năm"]
    plan = [head]
    for dp, el, _a, _p in fs.PLAN:
        months = [fs.plan_month(dp, el, mo) for mo in range(1, 13)]
        plan.append([dp, el] + months + [sum(months)])
    plan[1][-1] += 2 * M                                          # the file's own full year is 2 tr off on one row
    assert any("2000" in str(r[-1]) for r in plan[1:])            # a total whose digits hold '2000'
    d = _folder(tmp_path, "k9", {"KH": plan})
    b = bod_inputs.load(d, fy_start=1)
    assert {r.month[:4] for r in b.plans} == {"2026"} and len(b.plans) == 12 * len(fs.PLAN)
    m = fpa_report.compute(d)
    said = " ".join(m.said)
    assert "File kế hoạch KeHoach_AOP_2026.xlsx (sheet KH) không tự khớp: dòng 2: các tháng cộng" in said
    assert "cột cả năm ghi" in said and "ngoài năm tài chính" not in said
    assert m.row("opex").ytd_plan == sum(fs.plan_month(dp, el, mo) for dp, el, _a, _p in fs.PLAN for mo in range(1, 11))


def test_plan_rows_outside_the_year_are_counted_and_said(tmp_path):
    rows = fs.plan_rows("AOP")[:-1]
    rows += [["AOP", "2000", "10", "MKT", "QC", str(7 * M)], ["AOP", "2025", "12", "KD", "CTP", str(3 * M)]]
    d = _folder(tmp_path, "out", {"KH": rows})
    m = fpa_report.compute(d)
    said = " ".join(m.said)
    assert "File kế hoạch KeHoach_AOP_2026.xlsx: 2 dòng có tháng ngoài năm tài chính này (năm 2000, 2025)" in said
    assert "năm 2000 cách xa năm của sổ, có thể ô năm bị đọc sai" in said


# ── K10: the plan file's own info sheet, two plans of one name, the user's choice ──

def _versions_folder(tmp_path, name, approved_v4=True):
    d = _folder(tmp_path, name, None)
    for v, scale, info in (("v3", 1, [["Phiên bản", "v3"], ["Trạng thái", "Nháp"]]),
                           ("v4", 2, [["Phiên bản", "v4"], ["Trạng thái", "Đã duyệt" if approved_v4 else "Chờ duyệt"]]
                            + ([["Ngày duyệt", "15/09/2026"]] if approved_v4 else []))):
        ss.write_xlsx(d / f"AOP_2026_{v}.xlsx", {"Thong tin": info, "KH": fs.plan_rows("AOP", scale=scale)[:-1]})
    return d


def test_the_info_sheet_names_the_version_and_its_approval(tmp_path):
    from engine import roles
    d = _versions_folder(tmp_path, "info")
    b = bod_inputs.load(d, fy_start=1, purpose="fpa")
    assert sorted(p.version for p in fpa.plan_choices(b.plans)) == ["AOP v3", "AOP v4"]
    assert {p.version: p.approved for p in fpa.plan_choices(b.plans)}["AOP v4"] == "2026-09-15"
    assert receipt(b, "AOP_2026_v4.xlsx")[1] == roles.PLAN
    m = fpa_report.compute(d)
    assert m.costs.version == "AOP v4" and "duyệt ngày 15/09/2026" in m.costs.basis
    line = next(l for l in m.costs.month if (l.dept, l.element) == ("MKT", "QC"))
    assert line.plan == 2 * fs.plan_month("MKT", "QC", 10)


def test_after_asking_which_plan_the_answer_carries_the_report_through(tmp_path):
    from office import fpa_skill
    from office.fpa_skill import Stop
    import pytest
    d = _versions_folder(tmp_path, "ask", approved_v4=False)
    with pytest.raises(Stop) as asked:
        fpa_skill.prepare(d, "làm báo cáo FP&A tháng")
    assert "AOP v3, AOP v4" in asked.value.beat.text and "so với AOP v4" in asked.value.beat.text
    m, out = fpa_skill.prepare(d, "làm báo cáo FP&A tháng so với AOP v3")
    assert m.costs.version == "AOP v3" and out["verified"]
    line = next(l for l in m.costs.month if (l.dept, l.element) == ("MKT", "QC"))
    assert line.plan == fs.plan_month("MKT", "QC", 10)
    fs.write_xlsx(d / "Danh muc.xlsx", {"Phòng ban": fs.DEPT_SHEET, "Khoản mục": fs.ELEMENT_SHEET,
                                        "Tham số": [["Tham số", "Giá trị"], ["Bản kế hoạch", "AOP v4"]]})
    assert fpa_report.compute(d).costs.version == "AOP v4"           # the catalogue rule names it


# ── K11: a plan version read but not compared against is never 'used' ──────────

def test_the_version_not_compared_is_read_not_used_and_lines_still_add_up(tmp_path):
    d = _versions_folder(tmp_path, "k11")
    m = fpa_report.compute(d)
    assert m.costs.version == "AOP v4"
    plan_row = lambda rows, n: next(r for r in rows if r[0] == n and r[1].startswith("Kế hoạch"))   # noqa: E731
    rows = fpa_report.file_rows(m)[1:]
    v3, v4 = plan_row(rows, "AOP_2026_v3.xlsx"), plan_row(rows, "AOP_2026_v4.xlsx")
    assert fpa_report.NOT_COMPARED in v3[1] and v3[2] == 0 and fpa_report.NOT_COMPARED in v3[4]
    assert fpa_report.NOT_COMPARED not in v4[1] and v4[2] > 0
    assert v3[2] + v3[3] == v4[2] + v4[3]                           # the same lines, every one accounted for
    # the forecast's basis is named by the step that uses it, never relabelled
    kept = plan_row(fpa_report.file_rows(m, also_used={"AOP_2026_v3.xlsx"})[1:], "AOP_2026_v3.xlsx")
    assert kept[2] == v4[2] and fpa_report.NOT_COMPARED not in kept[1]


def test_the_fiscal_year_comes_from_the_plan_compared_against(tmp_path):
    # v4 (approved) runs January to December with its year; the draft v3 is printed T07 .. T06. July is v3's, not
    # the year of the plan the month is measured against.
    d = _folder(tmp_path, "k11fy", None)
    ss.write_xlsx(d / "AOP_2026_v4.xlsx", {"Thong tin": [["Phiên bản", "v4"], ["Trạng thái", "Đã duyệt"],
                                                         ["Ngày duyệt", "15/09/2026"]],
                                           "KH": fs.plan_rows("AOP")[:-1]})
    order = T[6:] + T[:6]
    wide = [["Mã phòng ban", "Mã khoản mục"] + order] + [[dp, el] + [1 * M] * 12 for dp, el, _a, _p in fs.PLAN]
    ss.write_xlsx(d / "AOP_2026_v3.xlsx", {"Thong tin": [["Phiên bản", "v3"], ["Trạng thái", "Nháp"]], "KH": wide})
    m = fpa_report.compute(d)
    assert m.costs.version == "AOP v4" and m.fy == 1 and "AOP_2026_v4.xlsx" in m.fy_said
    assert "AOP_2026_v3.xlsx" not in m.fy_said


# ── K12: the approval date in a 'Căn cứ Nghị quyết ... ngày ...' line ────────

def test_the_approval_date_is_read_from_the_resolution_line(tmp_path):
    d = _folder(tmp_path, "k12", None)
    for v, head in (("v3", [["KẾ HOẠCH CHI PHÍ NĂM 2026 (bản v3, nháp)"], ["Phiên bản", "v3"], ["Trạng thái", "Nháp"]]),
                    ("v4", [["KẾ HOẠCH CHI PHÍ NĂM 2026"],
                            ["Căn cứ Nghị quyết số 12/2026/NQ-HĐQT ngày 15/09/2026 của Hội đồng quản trị phê duyệt "
                             "kế hoạch năm 2026"], ["Phiên bản", "v4"]])):
        ss.write_xlsx(d / f"AOP_2026_{v}.xlsx", {"Thong tin": head, "KH": fs.plan_rows("AOP")[:-1]})
    m = fpa_report.compute(d)
    assert m.costs.version == "AOP v4" and "duyệt ngày 15/09/2026" in m.costs.basis
    assert "không ghi ngày duyệt" not in m.costs.basis
    # other wording: a Quyết định with the date written out, in two cells
    assert bod_inputs.resolution_date({"S": [["Căn cứ", "Quyết định số 45/QĐ-TGĐ ngày 20 tháng 9 năm 2026"]]}) \
        == "2026-09-20"
    assert bod_inputs.resolution_date({"S": [["Kế hoạch 2026, lập ngày 01/08/2026"]]}) == ""


# ── K7: the user's working files ─────────────────────────────────────────────

def test_working_files_are_named_as_such_in_the_written_receipt(tmp_path):
    d = _folder(tmp_path, "k7", {"KH": fs.plan_rows("AOP")[:-1]})
    working = ["FPA_T10_Workings.xlsx", "BC FPA T10 (nháp).xlsx", "BCQT_T10_dang_lam.xlsx", "Copy of AOP_2026.xlsx",
               "Bao cao T10 - Ban sao.xlsx", "T10_draft2.xlsx"]
    for n in working:
        ss.write_xlsx(d / n, {"P&L": [["Chỉ tiêu", "T10"], ["Chi phí", 1]]})
    ss.write_xlsx(d / "So chi tiet nhap kho.xlsx", {"S": [["Mã hàng", "SL"], ["A", 1]]})
    ss.write_xlsx(d / "Thue thu nhap ca nhan.xlsx", {"S": [["Mã NV", "Thuế"], ["A", 1]]})
    m = fpa_report.compute(d)
    out = fpa_report.write(m, tmp_path / "out")
    wb = load_workbook(out["xlsx"], read_only=True)
    rows = {r[0]: r for r in wb[fpa_report.RECEIPT].iter_rows(values_only=True) if r and r[0]}
    wb.close()
    for n in working:
        assert rows[n][1] == "File làm việc của chị, không lấy số", n
    assert rows["So chi tiet nhap kho.xlsx"][1] != "File làm việc của chị, không lấy số"
    assert rows["Thue thu nhap ca nhan.xlsx"][1] != "File làm việc của chị, không lấy số"


def test_a_working_file_has_the_fpa_wording(tmp_path):
    d = _folder(tmp_path, "work", None)
    ss.write_xlsx(d / "Working T10 FPA.xlsx", {"P&L": [["Chỉ tiêu", "T10"], ["Chi phí", 1]]})
    ss.write_xlsx(d / "So chi tiet nhap kho.xlsx", {"S": [["Mã hàng", "SL"], ["A", 1]]})
    m = fpa_report.compute(d)
    rows = {r[0]: r for r in fpa_report.file_rows(m)[1:]}
    assert rows["Working T10 FPA.xlsx"][1] == "File làm việc của chị, không lấy số"
    assert rows["Working T10 FPA.xlsx"][4] == bod_inputs.WORKING_WHY
    assert rows["So chi tiet nhap kho.xlsx"][1] == "Không dùng"     # 'nhập' (stock in) is not a draft
