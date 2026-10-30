"""Questions to verify before anyone explains a variance.

The engine knows what moved and by how much (a calculation); it does not know why.
For every material movement this builds a fixed-template question, the evidence
that would answer it and who usually holds that evidence. The explanation columns
stay blank for a person to fill in and label. Nothing here is written by the LLM.
"""
from dataclasses import dataclass
from decimal import Decimal

from engine.monthly_report import fmt

MATERIAL_FLOOR = Decimal(10_000_000)     # đồng
MATERIAL_SHARE = Decimal("0.05")         # of the previous period's line
# Lines where a higher number is good for profit; every other mapped line is a cost.
FAVOURABLE_UP = {"rev", "net_rev", "gross", "fin_inc", "op", "oth_inc", "other", "pbt", "pat"}
LABELS = ("SỰ THẬT", "TÍNH TOÁN", "GIẢI THÍCH CỦA QUẢN LÝ", "GIẢ THUYẾT")

# key → (question, evidence, who holds it). {move} is "tăng 120 triệu (+20,0%)".
TEMPLATES = {
    "rev": ("Doanh thu {move}: do sản lượng, giá bán hay cơ cấu mặt hàng thay đổi?",
            "Sổ chi tiết bán hàng hai kỳ theo mặt hàng: số lượng, đơn giá, khách hàng lớn",
            "Kế toán bán hàng / trưởng kinh doanh"),
    "ded": ("Giảm trừ doanh thu {move}: là chiết khấu thương mại, giảm giá hay hàng bán bị trả lại?",
            "Chi tiết TK 521 theo chứng từ; chương trình khuyến mại đang chạy",
            "Kế toán bán hàng"),
    "cogs": ("Giá vốn {move} trong khi doanh thu thuần {rev_move}; tỷ lệ giá vốn/doanh thu thuần từ {prev_ratio} lên {cur_ratio}. Do giá nhập, cơ cấu hàng bán, chênh lệch kiểm kê hay trích dự phòng?",
             "Bảng tính giá xuất kho, biên bản kiểm kê cuối kỳ, bảng giá nhập hai kỳ",
             "Kế toán kho / mua hàng"),
    "fin_inc": ("Doanh thu tài chính {move}: lãi tiền gửi, chênh lệch tỷ giá hay cổ tức?",
                "Sổ phụ ngân hàng, bảng tính chênh lệch tỷ giá", "Kế toán ngân hàng"),
    "fin_exp": ("Chi phí tài chính {move}: lãi vay tăng do dư nợ hay lãi suất, hay lỗ tỷ giá?",
                "Bảng dư nợ vay và lãi suất từng khoản vay, bảng tính chênh lệch tỷ giá", "Kế toán ngân hàng"),
    "selling": ("Chi phí bán hàng {move}, nhiều nhất ở {driver}. Là khoản phát sinh mới, tăng đơn giá, chi phí một lần hay ghi nhận lệch kỳ?",
                "Sổ chi tiết {driver_acct} hai kỳ theo đối tượng/hợp đồng", "Trưởng bộ phận bán hàng"),
    "admin": ("Chi phí quản lý {move}, nhiều nhất ở {driver}. Là khoản phát sinh mới, tăng đơn giá, chi phí một lần hay ghi nhận lệch kỳ?",
              "Sổ chi tiết {driver_acct} hai kỳ theo đối tượng/hợp đồng", "Hành chính / kế toán tổng hợp"),
    "oth_inc": ("Thu nhập khác {move}: khoản gì, có lặp lại ở các kỳ sau không?",
                "Chứng từ gốc TK 711", "Kế toán tổng hợp"),
    "oth_exp": ("Chi phí khác {move}: khoản gì (phạt, thanh lý tài sản...), có lặp lại không?",
                "Chứng từ gốc TK 811", "Kế toán tổng hợp"),
    "tax": ("Chi phí thuế TNDN {move}; thuế/lợi nhuận trước thuế từ {prev_tax_rate} sang {cur_tax_rate}. Có khớp tờ khai tạm tính không?",
            "Tờ khai tạm tính thuế TNDN, bảng chi phí không được trừ", "Kế toán thuế"),
}


@dataclass
class Question:
    key: str
    label: str
    change: Decimal
    favourable: bool
    question: str
    evidence: str
    owner: str


def favourable(key: str, change: Decimal) -> bool:
    return change > 0 if key in FAVOURABLE_UP else change < 0


def is_material(v) -> bool:
    return abs(v.change) >= max(MATERIAL_FLOOR, abs(v.previous) * MATERIAL_SHARE)


def _move(v) -> str:
    from engine.monthly_report import spoken
    word = "tăng" if v.change > 0 else "giảm"
    pct = f" ({v.pct:+.1f}%)".replace(".", ",") if v.pct is not None else ""
    return f"{word} {spoken(abs(v.change))}{pct}"


def _ratio(part: Decimal, whole: Decimal) -> str:
    return f"{part / whole * 100:.1f}%".replace(".", ",") if whole else "không xác định"


def _rate_gap(v) -> Decimal:
    """Change in tax / profit before tax, in percentage points."""
    def rate(tax, pbt):
        return tax / pbt * 100 if pbt else Decimal(0)
    return abs(rate(v["tax"].current, v["pbt"].current) - rate(v["tax"].previous, v["pbt"].previous))


def build_questions(variances: list) -> list:
    v = {x.key: x for x in variances}
    out = []
    for key, (question, evidence, owner) in TEMPLATES.items():
        x = v.get(key)
        if x is None or not x.change or not is_material(x):
            continue
        if key == "tax" and _rate_gap(v) < 1:
            continue                      # tax simply followed profit at an unchanged rate
        driver = x.drivers[0] if x.drivers else None
        fields = {
            "move": _move(x),
            "rev_move": _move(v["net_rev"]) if v["net_rev"].change else "không đổi",
            "prev_ratio": _ratio(v["cogs"].previous, v["net_rev"].previous),
            "cur_ratio": _ratio(v["cogs"].current, v["net_rev"].current),
            "prev_tax_rate": _ratio(v["tax"].previous, v["pbt"].previous),
            "cur_tax_rate": _ratio(v["tax"].current, v["pbt"].current),
            "driver": f"TK {driver[0]} {driver[1]} ({fmt(driver[2], sign=True)})" if driver else "các tài khoản con",
            "driver_acct": f"TK {driver[0]}" if driver else "các TK chi phí",
        }
        out.append(Question(key, x.label, x.change, favourable(key, x.change),
                            question.format(**fields), evidence.format(**fields), owner))
    return sorted(out, key=lambda q: -abs(q.change))


def questions_grid(questions: list) -> list:
    rows = [["Chỉ tiêu", "Chênh lệch", "Thuận lợi / Bất lợi", "Nhãn số liệu", "Câu hỏi cần xác minh",
             "Bằng chứng cần có", "Hỏi ai", "Giải thích (người phụ trách điền)", "Nhãn giải thích"]]
    for q in questions:
        rows.append([q.label, q.change, "Thuận lợi" if q.favourable else "Bất lợi", "TÍNH TOÁN",
                     q.question, q.evidence, q.owner, "", "Chọn: " + " / ".join(LABELS)])
    if len(rows) == 1:
        rows.append(["", "", "", "", "Không có biến động nào vượt ngưỡng trọng yếu", "", "", "", ""])
    return rows
