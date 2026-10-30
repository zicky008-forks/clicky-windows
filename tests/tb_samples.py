"""Synthetic trial balances laid out like a MISA 'Bảng cân đối tài khoản' export, built from journal entries."""
from collections import defaultdict

M = 1_000_000
NAMES = {
    "111": "Tiền mặt", "112": "Tiền gửi ngân hàng", "131": "Phải thu của khách hàng", "156": "Hàng hóa",
    "331": "Phải trả cho người bán", "333": "Thuế và các khoản phải nộp Nhà nước", "33311": "Thuế GTGT đầu ra",
    "3334": "Thuế thu nhập doanh nghiệp", "334": "Phải trả người lao động", "411": "Vốn đầu tư của chủ sở hữu",
    "421": "Lợi nhuận sau thuế chưa phân phối", "4211": "LNST chưa phân phối năm trước", "4212": "LNST chưa phân phối năm nay",
    "511": "Doanh thu bán hàng và cung cấp dịch vụ", "5111": "Doanh thu bán hàng hóa", "5113": "Doanh thu cung cấp dịch vụ",
    "515": "Doanh thu hoạt động tài chính", "521": "Các khoản giảm trừ doanh thu", "632": "Giá vốn hàng bán",
    "635": "Chi phí tài chính", "641": "Chi phí bán hàng", "6411": "Chi phí nhân viên", "6417": "Chi phí dịch vụ mua ngoài",
    "6418": "Chi phí bằng tiền khác", "642": "Chi phí quản lý doanh nghiệp", "6421": "Chi phí nhân viên quản lý",
    "6428": "Chi phí bằng tiền khác", "711": "Thu nhập khác", "811": "Chi phí khác", "821": "Chi phí thuế TNDN",
    "8211": "Chi phí thuế TNDN hiện hành", "911": "Xác định kết quả kinh doanh",
}
LEAVES = ["111", "112", "131", "156", "331", "33311", "3334", "334", "411", "4212", "5111", "5113", "515", "521",
          "632", "635", "6411", "6417", "6418", "6421", "6428", "711", "811", "8211", "911"]
PARENTS = ["333", "421", "511", "641", "642", "821"]

OPENING_T9 = {"111": 500, "112": 3000, "131": 4000, "156": 7000, "331": -3500, "411": -8000, "4212": -3000}


def month(sales, service, deduction, cogs, purchases, collected, paid, fin_inc, interest,
          selling, admin, other_inc, other_exp, tax, returns_into_511=0):
    """Journal for one month in millions of đồng, closed to 911 at month end."""
    j = [("131", "5111", sales), ("131", "5113", service), ("131", "33311", (sales + service) // 10),
         ("521", "131", deduction), ("632", "156", cogs), ("156", "331", purchases), ("112", "131", collected),
         ("331", "112", paid), ("112", "515", fin_inc), ("635", "112", interest),
         ("6411", "334", selling[0]), ("6417", "331", selling[1]), ("6418", "111", selling[2]),
         ("6421", "334", admin[0]), ("6428", "111", admin[1]), ("111", "711", other_inc), ("811", "111", other_exp),
         ("8211", "3334", tax)]
    if returns_into_511:
        j.append(("5111", "131", returns_into_511))        # the trap: returns booked straight to Nợ 511
    j.append(("5111", "521", deduction))
    j += [("5111", "911", sales - deduction - returns_into_511), ("5113", "911", service),
          ("515", "911", fin_inc), ("711", "911", other_inc)]
    expenses = [("632", cogs), ("635", interest), ("6411", selling[0]), ("6417", selling[1]), ("6418", selling[2]),
                ("6421", admin[0]), ("6428", admin[1]), ("811", other_exp), ("8211", tax)]
    j += [("911", acct, amount) for acct, amount in expenses]
    profit = sales + service - deduction - returns_into_511 + fin_inc + other_inc - sum(a for _, a in expenses)
    j.append(("911", "4212", profit) if profit >= 0 else ("4212", "911", -profit))
    return j


T9 = month(8000, 500, 200, 5300, 5000, 8500, 5200, 20, 60, (700, 300, 150), (500, 160), 10, 5, 231)
T10 = month(8400, 450, 210, 5800, 6000, 9000, 5500, 15, 70, (760, 420, 200), (520, 170), 5, 25, 139)


def balances(opening, journal):
    dr, cr = defaultdict(int), defaultdict(int)
    for d, c, amount in journal:
        dr[d] += amount
        cr[c] += amount
    closing = {a: opening.get(a, 0) + dr[a] - cr[a] for a in LEAVES}
    return dr, cr, {a: v for a, v in closing.items() if v}


def misa_rows(opening, journal, period="Tháng 10 năm 2026"):
    dr, cr, closing = balances(opening, journal)

    def split(v):
        return (v * M, 0) if v > 0 else (0, -v * M)

    data = {}
    for a in LEAVES:
        o = split(opening.get(a, 0))
        c = split(closing.get(a, 0))
        data[a] = [o[0], o[1], dr[a] * M, cr[a] * M, c[0], c[1]]
    for p in PARENTS:
        kids = [data[a] for a in LEAVES if a.startswith(p) and a != p]
        data[p] = [sum(k[i] for k in kids) for i in range(6)]
    rows = [["CÔNG TY TNHH SÔNG HỒNG"], ["BẢNG CÂN ĐỐI TÀI KHOẢN"], [period], [],
            ["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ", None, "Số phát sinh", None, "Số dư cuối kỳ", None],
            [None, None, "Nợ", "Có", "Nợ", "Có", "Nợ", "Có"]]
    for a in sorted(data):
        rows.append([a, NAMES[a]] + data[a])
    leaf_totals = [sum(data[a][i] for a in LEAVES) for i in range(6)]
    rows.append(["Tổng cộng", None] + leaf_totals)
    return rows


def t9_rows():
    return misa_rows(OPENING_T9, T9, "Tháng 09 năm 2026")


def t10_rows(**trap):
    _, _, closing_t9 = balances(OPENING_T9, T9)
    journal = month(8400, 450, 210, 5800, 6000, 9000, 5500, 15, 70, (760, 420, 200), (520, 170), 5, 25, 139, **trap)
    return misa_rows(closing_t9, journal)
