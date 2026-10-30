"""Monthly management P&L with variance, computed by code from trial balances.

Every figure keeps its sources (account, source row, amount) so the report can
show where a number came from. The account mapping follows the VAS chart of
accounts (TT200, carried into TT99/2025 for these P&L accounts); confirm it with
the client's chief accountant before relying on it.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from engine.trial_balance import ZERO, TrialBalance

# (key, label, [(account prefix, side, sign)], or a formula over earlier keys)
PNL = [
    ("rev", "Doanh thu bán hàng và cung cấp dịch vụ", [("511", "cr", 1)]),
    ("ded", "Các khoản giảm trừ doanh thu", [("521", "dr", 1)]),
    ("net_rev", "Doanh thu thuần", "rev - ded"),
    ("cogs", "Giá vốn hàng bán", [("632", "dr", 1)]),
    ("gross", "Lợi nhuận gộp", "net_rev - cogs"),
    ("fin_inc", "Doanh thu hoạt động tài chính", [("515", "cr", 1)]),
    ("fin_exp", "Chi phí tài chính", [("635", "dr", 1)]),
    ("selling", "Chi phí bán hàng", [("641", "dr", 1)]),
    ("admin", "Chi phí quản lý doanh nghiệp", [("642", "dr", 1)]),
    ("op", "Lợi nhuận thuần từ hoạt động kinh doanh", "gross + fin_inc - fin_exp - selling - admin"),
    ("oth_inc", "Thu nhập khác", [("711", "cr", 1)]),
    ("oth_exp", "Chi phí khác", [("811", "dr", 1)]),
    ("other", "Lợi nhuận khác", "oth_inc - oth_exp"),
    ("pbt", "Tổng lợi nhuận kế toán trước thuế", "op + other"),
    ("tax", "Chi phí thuế TNDN", [("821", "dr", 1)]),
    ("pat", "Lợi nhuận sau thuế", "pbt - tax"),
]
MAPPED_PREFIXES = tuple(p for _, _, spec in PNL if isinstance(spec, list) for p, _, _ in spec)


@dataclass
class Figure:
    key: str
    label: str
    value: Decimal
    sources: list = field(default_factory=list)    # (account, name, row, column, amount)
    formula: str = ""


def _eval(expr: str, values: dict) -> Decimal:
    total, sign = ZERO, 1
    for token in expr.split():
        if token in "+-":
            sign = 1 if token == "+" else -1
        else:
            total += sign * values[token]
    return total


def compute(tb: TrialBalance) -> list:
    """P&L for the period from period movements (Phát sinh) of leaf accounts."""
    figures, values = [], {}
    for key, label, spec in PNL:
        if isinstance(spec, str):
            value = _eval(spec, values)
            figures.append(Figure(key, label, value, formula=spec))
        else:
            sources, value = [], ZERO
            for prefix, side, sign in spec:
                for line in tb.leaves:
                    if line.account.startswith(prefix):
                        amount = getattr(line, side)
                        if amount:
                            sources.append((line.account, line.name, line.row, side, amount))
                            value += sign * amount
            figures.append(Figure(key, label, value, sources))
        values[key] = figures[-1].value
    return figures


@dataclass
class Variance:
    key: str
    label: str
    current: Decimal
    previous: Decimal
    drivers: list         # (account, name, change) biggest movers first

    @property
    def change(self) -> Decimal:
        return self.current - self.previous

    @property
    def pct(self):
        return None if not self.previous else self.change / abs(self.previous) * 100


def compare(current: list, previous: list) -> list:
    prev = {f.key: f for f in previous}
    out = []
    for f in current:
        p = prev[f.key]
        by_account = {}
        for acct, name, _row, _col, amount in f.sources:
            by_account[acct] = [name, by_account.get(acct, [name, ZERO])[1] + amount]
        for acct, name, _row, _col, amount in p.sources:
            entry = by_account.setdefault(acct, [name, ZERO])
            entry[1] -= amount
        drivers = sorted(((a, n, c) for a, (n, c) in by_account.items() if c), key=lambda d: -abs(d[2]))
        out.append(Variance(f.key, f.label, f.value, p.value, drivers[:3]))
    return out


INCOME_KEYS = {"rev", "fin_inc", "oth_inc"}


def summary_vi(variances: list, issues: list, n_questions: int = 0) -> str:
    """One spoken paragraph: conclusion first, then what moved profit most, then traps.
    It states calculations only; causes are left to the questions sheet."""
    v = {x.key: x for x in variances}
    pat = v["pat"]
    direction = "tăng" if pat.change > 0 else "giảm" if pat.change < 0 else "không đổi"
    pct = f" ({pat.pct:+.1f}%)".replace(".", ",") if pat.pct is not None else ""
    text = f"Lợi nhuận sau thuế kỳ này {spoken(pat.current)}, {direction} {spoken(abs(pat.change))}{pct} so với kỳ trước."
    # Effect of each mapped line on profit: income up helps, cost up hurts.
    effects = [(x, x.change if x.key in INCOME_KEYS else -x.change) for x in variances if x.drivers]
    same_way = sorted((e for e in effects if e[1] * pat.change > 0), key=lambda e: -abs(e[1]))[:2]
    if same_way:
        parts = []
        for x, _ in same_way:
            acct, name, change = x.drivers[0]
            word = "tăng" if x.change > 0 else "giảm"
            parts.append(f"{x.label.lower()} {word} {spoken(abs(x.change))}, nhiều nhất ở TK {acct} {name}")
        text += f" Khoản kéo lợi nhuận {direction} nhiều nhất: " + "; ".join(parts) + "."
        offset = max((e for e in effects if e[1] * pat.change < 0), key=lambda e: abs(e[1]), default=None)
        if offset:
            x = offset[0]
            text += f" Bù lại một phần: {x.label.lower()} {'tăng' if x.change > 0 else 'giảm'} {spoken(abs(x.change))}."
    if n_questions:
        text += f" Em đã ghi {n_questions} câu hỏi cần xác minh nguyên nhân trước khi giải thích với sếp."
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        text += f" Cảnh báo: có {len(errors)} lỗi cần xem trước khi gửi, ví dụ: {errors[0].message}."
    elif issues:
        text += f" Có {len(issues)} điểm cần lưu ý trong sheet Kiem tra."
    return text


def spoken(amount: Decimal) -> str:
    """Read-aloud size: 1.090.000.000 → '1,09 tỷ', 556.000.000 → '556 triệu'."""
    a = abs(amount)
    sign = "âm " if amount < 0 else ""
    if a >= 10 ** 9:
        return sign + f"{a / 10 ** 9:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " tỷ"
    if a >= 10 ** 6:
        return sign + f"{a / 10 ** 6:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " triệu"
    return sign + fmt(a) + " đồng"


def fmt(amount: Decimal, sign: bool = False) -> str:
    """Vietnamese thousands separators: 1234567 → 1.234.567."""
    q = int(amount.quantize(Decimal(1)))
    s = f"{abs(q):,}".replace(",", ".")
    prefix = "-" if q < 0 else ("+" if sign and q > 0 else "")
    return prefix + s
