"""Trap checks run before a report is trusted: the things a reviewer would catch in a meeting."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from engine.monthly_report import MAPPED_PREFIXES, fmt
from engine.trial_balance import TrialBalance

TOLERANCE = Decimal(1)          # rounding to the đồng


@dataclass
class Issue:
    severity: str              # "error" | "warning"
    code: str
    message: str
    row: int = 0               # source row to point at, 0 when not about one row


def check(tb: TrialBalance) -> list:
    issues = []
    leaves = tb.leaves

    # 1. Debits equal credits, on leaf accounts so parents are not counted twice.
    for label, dr, cr in (("đầu kỳ", "open_dr", "open_cr"), ("phát sinh", "dr", "cr"), ("cuối kỳ", "close_dr", "close_cr")):
        if dr not in tb.columns:
            continue
        diff = sum(getattr(l, dr) for l in leaves) - sum(getattr(l, cr) for l in leaves)
        if abs(diff) > TOLERANCE:
            issues.append(Issue("error", "unbalanced", f"Tổng Nợ và Có {label} lệch {fmt(diff, sign=True)}"))

    # 2. Opening + movement = closing for every account.
    if {"open_dr", "close_dr"} <= tb.columns.keys():
        for l in tb.lines:
            expected = (l.open_dr - l.open_cr) + (l.dr - l.cr)
            actual = l.close_dr - l.close_cr
            if abs(expected - actual) > TOLERANCE:
                issues.append(Issue("error", "rollforward",
                                    f"TK {l.account}: đầu kỳ + phát sinh ≠ cuối kỳ (lệch {fmt(actual - expected, sign=True)})", l.row))

    # 3. A parent row must equal the sum of its direct children.
    codes = {l.account: l for l in tb.lines}
    def parent_of(account):
        for n in range(len(account) - 1, 2, -1):
            if account[:n] in codes:
                return account[:n]
        return None
    children = {}
    for l in tb.lines:
        p = parent_of(l.account)
        if p:
            children.setdefault(p, []).append(l)
    for account, kids in children.items():
        parent = codes[account]
        for side in ("dr", "cr"):
            diff = getattr(parent, side) - sum(getattr(c, side) for c in kids)
            if abs(diff) > TOLERANCE:
                issues.append(Issue("error", "parent_sum",
                                    f"TK {account} phát sinh {'Nợ' if side == 'dr' else 'Có'} khác tổng TK con {fmt(diff, sign=True)}", parent.row))

    # 4. Same account listed twice.
    for account, n in Counter(l.account for l in tb.lines).items():
        if n > 1:
            issues.append(Issue("error", "duplicate", f"TK {account} xuất hiện {n} lần", codes[account].row))

    # 5. Amounts stored as text (often from copy-paste); they were read, but Excel SUM skips them.
    for row, column in tb.text_amounts[:20]:
        issues.append(Issue("warning", "text_amount", f"Dòng {row}: số tiền đang ở dạng chữ, hàm SUM của Excel sẽ bỏ qua", row))

    # 6. Income-statement accounts outside the mapping would silently drop out of profit.
    for l in leaves:
        if l.account[0] in "5678" and not l.account.startswith(MAPPED_PREFIXES) and l.dr != l.cr:
            issues.append(Issue("warning", "unmapped",
                                f"TK {l.account} {l.name} còn chênh lệch Nợ/Có nhưng chưa nằm trong chỉ tiêu nào", l.row))

    # 7. Income closed to 911 should match income computed from 511/521/515/711. A gap usually
    #    means returns or discounts booked straight to Nợ 511, which inflate gross revenue.
    closing = [l for l in leaves if l.account.startswith("911")]
    if closing and sum(l.cr for l in closing):
        def total(prefix, side):
            return sum(getattr(l, side) for l in leaves if l.account.startswith(prefix))
        income = total("511", "cr") - total("521", "dr") + total("515", "cr") + total("711", "cr")
        closed = sum(l.cr for l in closing)
        # A loss is closed as Nợ 421 / Có 911, which only adds to 911's credits, so only a shortfall is a trap.
        if income - closed > TOLERANCE:
            issues.append(Issue("error", "closing_mismatch",
                                f"Thu nhập kết chuyển sang 911 ({fmt(closed)}) khác thu nhập tính từ 511, 521, 515, 711 ({fmt(income)}); "
                                "kiểm tra hàng bán bị trả lại hoặc chiết khấu ghi thẳng vào Nợ 511", closing[0].row))
    return issues
