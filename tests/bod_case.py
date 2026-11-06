"""A made-up sugar trader for the BOD pack review: one folder with the detail, plans, rates, sales, the
configuration workbook and a draft pack. All names and numbers are invented here; nothing comes from a
real company or from the independent grader's case.

make_case(folder, plant=True) writes the folder and returns the nine mistakes planted in the pack:
[{"group", "code", "where", "expected"}]. plant=False writes the same pack without them.
"""
from __future__ import annotations

import csv
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FY_MONTHS = [f"2026-{m:02d}" for m in range(7, 13)] + [f"2027-{m:02d}" for m in range(1, 7)]
LOCKED = FY_MONTHS[:4]                      # July .. October 2026
PERIOD = "2026-10"
TM, SX = "Thương mại", "Sản xuất"

DEPTS = {"P01": "Kinh doanh B2B", "P02": "Kinh doanh bán lẻ", "P03": "Marketing", "P04": "Hành chính",
         "P05": "Kho vận", "P06": "Nhân sự", "Q01": "Xưởng"}
NO_CUT = ("P05", "P06")
ELEMENTS = {"CE01": ("Lương bán hàng", "6411", "Bán hàng", "Chi phí bán hàng"),
            "CE02": ("Vận chuyển", "6417", "Bán hàng", "Chi phí bán hàng"),
            "CE03": ("Quảng cáo khuyến mãi", "6418", "Bán hàng", "Chi phí bán hàng"),
            "CE04": ("Lương quản lý", "6421", "Vận hành", "Chi phí vận hành"),
            "CE05": ("Thuê văn phòng", "6422", "Vận hành", "Chi phí vận hành"),
            "CE06": ("Phí dịch vụ nước ngoài", "6428", "Vận hành", "Chi phí vận hành")}
CHANNELS = {"B2B lớn": "DTT toàn khối Thương mại, mọi site", "B2B nhỏ": "DTT toàn khối Thương mại, mọi site",
            "Siêu thị": "DTT site S1, mọi khối", "Tạp hoá": "DTT site S1, mọi khối"}
# (division, dept, element): (monthly base in đồng, {channel: share})
COSTS = {(TM, "P01", "CE01"): (120_000_000, {"B2B lớn": 6, "B2B nhỏ": 4}),
         (TM, "P01", "CE02"): (45_000_000, {"B2B lớn": 5, "B2B nhỏ": 5}),
         (TM, "P02", "CE01"): (90_000_000, {"Siêu thị": 5, "Tạp hoá": 5}),
         (TM, "P02", "CE02"): (30_000_000, {"Siêu thị": 7, "Tạp hoá": 3}),
         (TM, "P03", "CE03"): (80_000_000, {"": 1}),
         (TM, "P04", "CE04"): (70_000_000, {"": 1}),
         (TM, "P04", "CE05"): (40_000_000, {"": 1}),
         (TM, "P05", "CE02"): (55_000_000, {"": 1}),
         (TM, "P06", "CE04"): (50_000_000, {"": 1}),
         (SX, "Q01", "CE04"): (100_000_000, {"": 1})}
USD = {m: Decimal(2000 + 100 * i) for i, m in enumerate(FY_MONTHS)}      # CE06, P06, paid in USD
RATES = {m: {"Bình quân": Decimal(25_300 + 10 * i), "Cuối kỳ": Decimal(25_350 + 15 * i), "AOP": Decimal(25_000)}
         for i, m in enumerate(FY_MONTHS)}
# (site, division, channel): monthly gross revenue
SALES = {("S1", TM, "B2B lớn"): 2_400_000_000, ("S2", TM, "B2B lớn"): 1_600_000_000,
         ("S1", TM, "B2B nhỏ"): 900_000_000, ("S2", TM, "B2B nhỏ"): 700_000_000,
         ("S1", TM, "Siêu thị"): 1_100_000_000, ("S1", TM, "Tạp hoá"): 800_000_000,
         ("S2", TM, "Tạp hoá"): 500_000_000, ("S1", SX, "Công nghiệp"): 3_000_000_000}
CEILING = Decimal(5_000_000_000)
TRAP_NO_DEPT = Decimal(12_345_000)
TRAP_DISCOUNT = Decimal(25_000_000)
TRAP_REVERSAL = Decimal(-18_000_000)


def r0(x) -> Decimal:
    return Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def month_amount(base: int, month: str, factor: str = "1") -> Decimal:
    i = FY_MONTHS.index(month) if month in FY_MONTHS else 0
    return r0(Decimal(base) * (1 + Decimal(i) / 100) * Decimal(factor) / 1000) * 1000


def gl_lines() -> list:
    """[(date, ref, month, site, division, dept, element, account, amount, usd, currency, rate, channel, text)]"""
    out, n = [], 0
    for month in LOCKED:
        y, m = month.split("-")
        for (div, dept, ce), (base, split) in COSTS.items():
            total = month_amount(base, month)
            shares = sum(split.values())
            left = total
            for i, (ch, share) in enumerate(split.items()):
                amt = left if i == len(split) - 1 else r0(total * share / shares)
                left -= amt
                n += 1
                out.append((f"{y}-{m}-25", f"PK{n:04d}", int(m), "S1" if dept != "Q01" else "S1", div, dept, ce,
                            ELEMENTS[ce][1], amt, None, "VND", None, ch, f"{ELEMENTS[ce][0]} tháng {m}/{y}"))
        usd = USD[month]
        n += 1
        out.append((f"{y}-{m}-28", f"PK{n:04d}", int(m), "S1", TM, "P06", "CE06", "6428",
                    r0(usd * RATES[month]["Bình quân"]), usd, "USD", RATES[month]["Bình quân"], "",
                    f"Phí dịch vụ phần mềm nước ngoài tháng {m}/{y}"))
    out += [("2026-10-30", "PK9001", 10, "S1", TM, "", "CE05", "6422", TRAP_NO_DEPT, None, "VND", None, "",
             "Phí bảo trì điều hoà văn phòng"),
            ("2026-10-31", "PK9002", 10, "S1", TM, "P03", "CE03", "6418", TRAP_DISCOUNT, None, "VND", None, "",
             "Chiết khấu thương mại cho nhà phân phối NPP-07 theo hợp đồng"),
            ("2026-11-01", "PK9003", 11, "S1", TM, "P04", "CE04", "6421", TRAP_REVERSAL, None, "VND", None, "",
             "Đảo bút toán trích trước lương tháng 10")]
    return out


def act(pred, months) -> Decimal:
    months = set(months)
    return sum((l[8] for l in gl_lines() if l[0][:7] in months and l[4] == TM and pred(l)), Decimal(0))


def revenue(months, site=None, division=None, channel=None) -> Decimal:
    total = Decimal(0)
    for (s, d, ch), gross in SALES.items():
        if (site is None or s == site) and (division is None or d == division) and (channel is None or ch == channel):
            for m in months:
                g = month_amount(gross, m)
                total += g - r0(g * Decimal("0.03")) - Decimal(5_000_000)    # 3 % discount, one return of 5 tr
    return total


def plan(version: str, dept: str, months, element=None) -> Decimal:
    total = Decimal(0)
    for (div, d, ce), (base, _) in COSTS.items():
        if d != dept or div != TM or (element and ce != element):
            continue
        for m in months:
            total += plan_amount(version, div, d, ce, m)
    if dept == "P06" and (element in (None, "CE06")):
        for m in months:
            total += plan_amount(version, TM, "P06", "CE06", m)
    return total


def plan_amount(version: str, div: str, dept: str, ce: str, m: str) -> Decimal:
    if ce == "CE06":
        aop = r0(USD[m] * RATES[m]["AOP"])
        base_actual = lambda: r0(USD[m] * RATES[m]["Bình quân"])
    else:
        base = COSTS[(div, dept, ce)][0]
        aop = month_amount(base, m, "1.05")
        base_actual = lambda: month_amount(base, m)
    if version == "AOP":
        return aop
    if version == "AOPR1":
        if m in LOCKED:
            return base_actual()
        return r0(aop * Decimal("0.97"))
    ly = {"Thực tế": "0.90", "AOP": "0.95", "AOPR": "0.92"}[version]
    return month_amount(COSTS[(div, dept, ce)][0] if ce != "CE06" else 50_000_000, m, ly)


# ── writers ─────────────────────────────────────────────────────────────────────

def _xlsx(path: Path, sheets: dict) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append([float(v) if isinstance(v, Decimal) and v != v.to_integral() else
                       int(v) if isinstance(v, Decimal) else v for v in row])
    wb.save(path)


def _csv(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)


def write_data(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    gl = [["Ngay_CT", "So_CT", "Thang", "Phap_nhan", "Khoi", "Phong_ban_ma", "Phong_ban_ten", "Cost_element_ma",
           "Cost_element_ten", "TK_No", "TK_Co", "So_tien_VND", "Nguyen_te", "Ma_tien_te", "Ty_gia", "Kenh", "Dien_giai",
           "Nguon"]]
    total = Decimal(0)
    for (d, ref, m, site, div, dept, ce, acct, amt, usd, cur, rate, ch, text) in gl_lines():
        gl.append([d, ref, m, site, div, dept, DEPTS.get(dept, ""), ce, ELEMENTS[ce][0], acct, "3341" if ce in ("CE01", "CE04")
                   else "331", int(amt), "" if usd is None else f"{usd:.2f}", cur, "" if rate is None else int(rate), ch, text,
                   "ERP giả lập"])
        total += amt
    gl.append(["Tổng cộng", "", "", "", "", "", "", "", "", "", "", int(total), "", "", "", "", "", ""])
    _csv(folder / "GL_ChiTiet_ChiPhi_T07-T10.csv", gl)

    sales = [["Ngay_HD", "So_HD", "Site", "Khoi", "Kenh", "Ma_hang", "So_luong", "Doanh_thu", "Chiet_khau", "Doanh_thu_thuan"]]
    n = 0
    for m in LOCKED:
        for (site, div, ch), gross in SALES.items():
            g = month_amount(gross, m)
            ck = r0(g * Decimal("0.03"))
            n += 1
            sales.append([f"{m}-20", f"HD{n:05d}", site, div, ch, "DUONG-50KG", 1000, int(g), int(ck), int(g - ck)])
            n += 1
            sales.append([f"{m}-22", f"HD{n:05d}", site, div, ch, "DUONG-1KG-KM", 50, 0, 0, 0])        # free goods
            n += 1
            sales.append([f"{m}-27", f"TL{n:05d}", site, div, ch, "DUONG-50KG", -10, -5_000_000, 0, -5_000_000])  # return
    sales.append(["Tổng", "", "", "", "", "", "", "", "", sum(int(r[9]) for r in sales[1:])])
    _csv(folder / "Mastersale_T07-T10.csv", sales)

    head = ["Phien_ban", "Ngay_duyet", "Nam", "Thang", "Khoi", "Phong_ban_ma", "Cost_element_ma", "Dong_BOD", "So_tien_VND",
            "Ty_gia_gia_dinh", "Ghi_chu_gia_dinh"]
    keys = list(COSTS) + [(TM, "P06", "CE06")]
    for version, approved, name in (("AOP", "2026-06-20", "KeHoach_AOP_FY2627.xlsx"),
                                    ("AOPR1", "2026-11-03", "KeHoach_AOPR1_FY2627.xlsx")):
        rows = [head]
        for m in FY_MONTHS:
            for div, dept, ce in keys:
                rows.append([version, approved, "FY26/27", int(m[5:]), div, dept, ce, ELEMENTS[ce][3],
                             plan_amount(version, div, dept, ce, m), 25000, "Tỷ giá kế hoạch cả năm"])
        _xlsx(folder / name, {"Data": rows})
    rows = [head]
    for version in ("Thực tế", "AOP", "AOPR"):
        for m in FY_MONTHS:
            ly = f"{int(m[:4]) - 1}{m[4:]}"
            for div, dept, ce in keys:
                rows.append([version, "", "FY25/26", int(ly[5:]), div, dept, ce, ELEMENTS[ce][3],
                             plan_amount(version, div, dept, ce, m), "", ""])
    _xlsx(folder / "NamTruoc_FY2526.xlsx", {"Data": rows})

    fx = [["BẢNG TỶ GIÁ USD/VND (giả lập)"], [], ["Thang", "Loai ty gia", "Ma tien te", "Ty gia"]]
    for m in FY_MONTHS:
        for typ, rate in RATES[m].items():
            fx.append([f"{m[5:]}/{m[:4]}", typ, "USD", int(rate)])
    _xlsx(folder / "TyGia_FY2627.xlsx", {"TyGia": fx})

    _xlsx(folder / "CatGiam_ThamSo.xlsx", {"Tham so": [
        ["THAM SỐ CẮT GIẢM 3 BƯỚC (giả lập)"], [],
        ["Tham số", "Giá trị"],
        ["Trần chi phí khối Thương mại (đ)", int(CEILING)],
        ["Danh sách không cắt", ", ".join(NO_CUT)],
        ["Bước 2", "Cắt phần xin tăng so với cơ sở"], ["Bước 3", "Cắt đều theo cơ sở phần còn thiếu"]]})

    _xlsx(folder / "DanhMuc_CauHinh.xlsx", {
        "Phong ban": [["DANH MỤC PHÒNG BAN"], [], ["Mã phòng ban", "Tên phòng ban", "Khối", "Không cắt giảm"]]
        + [[c, n, SX if c.startswith("Q") else TM, "x" if c in NO_CUT else ""] for c, n in DEPTS.items()],
        "Cost element": [["Mã cost element", "Tên cost element", "Tài khoản", "Nhóm", "Dòng BOD"]]
        + [[c, *v] for c, v in ELEMENTS.items()],
        "Kenh": [["QUY TẮC MẪU SỐ %DTT THEO KÊNH"], ["Kênh", "Mẫu số %DTT"]] + [[k, v] for k, v in CHANNELS.items()],
        "Quy tac": [["Tham số", "Giá trị"], ["Tháng bắt đầu niên độ", 7], ["Khóa sổ đến tháng", "10/2026"],
                    ["So sánh năm trước với phiên bản", "AOPR"], ["Tỷ giá dùng cho ảnh hưởng tỷ giá thực tế", "Bình quân tháng"],
                    ["Ngưỡng trọng yếu (%)", 0.05]],
    })
    (folder / "README.txt").write_text("Bộ dữ liệu giả lập để thử soát bộ BOD.\n", encoding="utf-8")


def cut_plan() -> dict:
    """{dept: (request, base, cut2, cut3)} by the three-step rule, then the totals."""
    req = {"P01": 1_450_000_000, "P02": 1_000_000_000, "P03": 820_000_000, "P04": 1_050_000_000, "P05": 600_000_000,
           "P06": 560_000_000}
    base = {"P01": 1_300_000_000, "P02": 950_000_000, "P03": 700_000_000, "P04": 1_000_000_000, "P05": 560_000_000,
            "P06": 520_000_000}
    req, base = {k: Decimal(v) for k, v in req.items()}, {k: Decimal(v) for k, v in base.items()}
    gap = sum(req.values()) - CEILING
    cut = [d for d in req if d not in NO_CUT]
    cut2 = {d: (max(req[d] - base[d], Decimal(0)) if d in cut else Decimal(0)) for d in req}
    rest = gap - sum(cut2.values())
    weight = sum(base[d] for d in cut)
    cut3, left = {}, rest
    for i, d in enumerate(req):
        if d not in cut:
            cut3[d] = Decimal(0)
        elif d == cut[-1]:
            cut3[d] = left
        else:
            cut3[d] = r0(rest * base[d] / weight)
            left -= cut3[d]
    return {d: (req[d], base[d], cut2[d], cut3[d]) for d in req}


def write_pack(folder: Path, plant: bool) -> list:
    answers = []
    month = [PERIOD]
    ytd = LOCKED
    dtt_tm = revenue(month, division=TM)
    plan_dtt = r0(dtt_tm * Decimal("1.03"))
    depts = ["P01", "P02", "P03", "P04", "P05", "P06"]

    # Tong hop: by department, month, actual vs AOP
    th = [["BÁO CÁO CHI PHÍ BÁN HÀNG VÀ VẬN HÀNH – KHỐI THƯƠNG MẠI"], ["Tháng 10/2026 (bản nháp)"], [],
          [None, "Tháng 10", None, None, None, None, None],
          ["Chỉ tiêu", "Thực tế", "%DTT", "AOP", "%DTT AOP", "Chênh lệch", "Chênh lệch (điểm %)"]]
    first = len(th) + 1
    for d in depts + [""]:
        v = act(lambda l, d=d: l[5] == d, month)
        p = plan("AOP", d, month) if d else Decimal(0)
        label = f"{d} - {DEPTS[d]}" if d else "Chưa gán phòng ban"
        pct, ppct = v / dtt_tm, p / plan_dtt
        th.append([label, v, pct, p, ppct, v - p, pct - ppct])
    truth = act(lambda l: True, month)
    p_total = sum((plan("AOP", d, month) for d in depts), Decimal(0))
    th.append(["Tổng chi phí", truth, truth / dtt_tm, p_total, p_total / plan_dtt, truth - p_total,
               truth / dtt_tm - p_total / plan_dtt])
    total_row = len(th)
    if plant:
        th[-1][1] = truth + TRAP_REVERSAL
        answers.append({"group": 1, "code": "total_vs_detail", "where": f"'Tong hop'!B{total_row}", "expected": truth})
        r = first + depts.index("P04")
        answers.append({"group": 3, "code": "missing_pct", "where": f"'Tong hop'!C{r}", "expected": th[r - 1][2]})
        th[r - 1][2] = None
        r = first + depts.index("P02")
        answers.append({"group": 3, "code": "wrong_pp", "where": f"'Tong hop'!G{r}", "expected": th[r - 1][6]})
        th[r - 1][6] = th[r - 1][6] + Decimal("0.005")

    # Theo kenh: selling cost by channel, % by the channel's own rule
    tk = [["CHI PHÍ BÁN HÀNG THEO KÊNH – KHỐI THƯƠNG MẠI – T10/2026"], [], ["Kênh", "Chi phí thực tế", "%DTT"]]
    for ch, rule in CHANNELS.items():
        v = act(lambda l, ch=ch: l[12] == ch, month)
        base = revenue(month, division=TM) if "Thương mại" in rule else revenue(month, site="S1")
        tk.append([ch, v, v / base])
        if plant and ch == "B2B nhỏ":
            answers.append({"group": 2, "code": "wrong_base", "where": f"'Theo kenh'!C{len(tk)}", "expected": v / base})
            tk[-1][2] = v / revenue(month, site="S1")
    sell = act(lambda l: l[12] != "", month)
    tk.append(["Tổng chi phí bán hàng theo kênh", sell, sell / dtt_tm])

    # So nam truoc: year to date, this year actual vs last year's plan
    ly_version = "AOP" if plant else "AOPR"
    ly_months = [f"2025-{m[5:]}" for m in ytd]
    sn = [["SO SÁNH VỚI NĂM TRƯỚC – KHỐI THƯƠNG MẠI"], [],
          [None, "Lũy kế T7–T10", None, None],
          ["Phòng ban", "Thực tế FY26/27", f"Năm trước {ly_version} FY25/26", "Chênh lệch"]]
    for d in depts:
        v = act(lambda l, d=d: l[5] == d, ytd)
        p = plan(ly_version, d, ytd)
        sn.append([f"{d} - {DEPTS[d]}", v, p, v - p])
    if plant:
        answers.append({"group": 4, "code": "wrong_version", "where": "'So nam truoc'!C4",
                        "expected": plan("AOPR", "P01", ytd)})

    # AOPR theo thang
    ap = [["AOPR1 FY26/27 THEO THÁNG – KHỐI THƯƠNG MẠI"], [],
          ["Phòng ban"] + [f"T{int(m[5:])}" for m in FY_MONTHS] + ["Cả năm"]]
    for d in depts:
        vals = [plan("AOPR1", d, [m]) for m in FY_MONTHS]
        if plant and d == "P02":
            answers.append({"group": 5, "code": "locked_month_plan", "where": f"'AOPR theo thang'!E{len(ap) + 1}",
                            "expected": vals[3]})
            vals[3] = plan("AOP", d, [FY_MONTHS[3]])
        ap.append([f"{d} - {DEPTS[d]}"] + vals + [sum(vals)])

    # Cat giam
    cp = cut_plan()
    cg = [["CẮT GIẢM CHI PHÍ 3 BƯỚC – KHỐI THƯƠNG MẠI – AOPR2 FY26/27"], [],
          ["Phòng ban", "Đề xuất", "Cơ sở năm trước", "Cắt bước 2 (phần xin tăng)", "Cắt bước 3 (cắt đều)", "Sau cắt"]]
    rows = {}
    for d, (req, base, c2, c3) in cp.items():
        rows[d] = [f"{d} - {DEPTS[d]}", req, base, c2, c3, req - c2 - c3]
    gap = sum(v[0] for v in cp.values()) - CEILING
    if plant:
        rows["P05"][4] += 5_000_000
        rows["P04"][4] -= 5_000_000
        rows["P01"][4] -= 7_000_000
        for d in ("P01", "P04", "P05"):
            rows[d][5] = rows[d][1] - rows[d][3] - rows[d][4]
    for d in cp:
        cg.append(rows[d])
        if plant and d == "P05":
            answers.append({"group": 6, "code": "no_cut_touched", "where": f"'Cat giam'!E{len(cg)}", "expected": Decimal(0)})
    cg.append(["Tổng", sum(r[1] for r in rows.values()), sum(r[2] for r in rows.values()),
               sum(r[3] for r in rows.values()), sum(r[4] for r in rows.values()), sum(r[5] for r in rows.values())])
    if plant:
        answers.append({"group": 6, "code": "levers_vs_gap", "where": f"'Cat giam'!E{len(cg)}", "expected": gap})

    # Ty gia
    tg = [["ẢNH HƯỞNG TỶ GIÁ – PHÍ DỊCH VỤ NƯỚC NGOÀI (CE06)"], [],
          ["Khoản mục", "Tháng", "USD", "Tỷ giá dùng", "Tỷ giá AOP", "Ảnh hưởng tỷ giá"]]
    for m in ytd:
        used = "Cuối kỳ" if plant and m == PERIOD else "Bình quân"
        impact = r0(USD[m] * (RATES[m][used] - RATES[m]["AOP"]))
        tg.append(["Phí dịch vụ nước ngoài", f"{m[5:]}/{m[:4]}", USD[m], RATES[m][used], RATES[m]["AOP"], impact])
        if plant and m == PERIOD:
            answers.append({"group": 7, "code": "fx_rate_type", "where": f"'Ty gia'!F{len(tg)}",
                            "expected": USD[m] * (RATES[m]["Bình quân"] - RATES[m]["AOP"])})
    _xlsx(folder / "Pack_BOD_T10_ban_nhap.xlsx", {"Tong hop": th, "Theo kenh": tk, "So nam truoc": sn,
                                                   "AOPR theo thang": ap, "Cat giam": cg, "Ty gia": tg})
    return answers


def make_case(folder: Path, plant: bool = True) -> list:
    folder = Path(folder)
    write_data(folder)
    return write_pack(folder, plant)
