"""Trap checks run before a report is trusted: the things a reviewer would catch in a meeting."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from engine.monthly_report import MAPPED_PREFIXES, fmt
from engine.trial_balance import TrialBalance

TOLERANCE = Decimal(1)          # rounding to the đồng
WRONG_SIDE_CR = ("111", "112", "113", "151", "152", "153", "154", "155", "156", "157", "158")
# Entries that come back every month; a month without them usually means they were not booked yet.
RECURRING = (("khấu hao tài sản cố định", "214", "cr"), ("trích lương", "334", "cr"),
             ("phân bổ chi phí trả trước", "242", "cr"))
DROP_RATIO = Decimal("0.5")


@dataclass
class Issue:
    severity: str              # "error" | "warning"
    code: str
    message: str
    row: int = 0               # source row to point at, 0 when not about one row
    what: str = ""             # short name of the thing found, for summaries that list several
    amount: Decimal = Decimal(0)   # money involved, for summaries that add several up


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

    # 5b. Rows with money but an account code the reader cannot use never reach the report.
    for row, account, moved in tb.skipped[:20]:
        issues.append(Issue("error", "unreadable_account",
                            f"Dòng {row}: mã TK \"{account}\" không đọc được, {fmt(moved)} chưa được đưa vào báo cáo", row))

    # 6. Income-statement accounts outside the mapping would silently drop out of profit.
    for l in leaves:
        if l.account[0] in "5678" and not l.account.startswith(MAPPED_PREFIXES) and l.dr != l.cr:
            income = l.account[0] in "57"
            net = (l.cr - l.dr) if income else (l.dr - l.cr)
            effect = "chưa cộng khoản này" if (net > 0) == income else "chưa trừ khoản này"
            issues.append(Issue("warning", "unmapped",
                                f"TK {l.account} {l.name} còn {fmt(abs(net))} chưa nằm trong chỉ tiêu nào, "
                                f"lợi nhuận sau thuế {effect}", l.row))

    # 8. Balances on the impossible side: cash, bank and stock cannot be negative.
    if {"close_dr", "close_cr"} <= tb.columns.keys():
        for l in leaves:
            if l.account.startswith(WRONG_SIDE_CR) and l.close_cr - l.close_dr > TOLERANCE:
                issues.append(Issue("error", "wrong_side",
                                    f"TK {l.account} {l.name} dư Có {fmt(l.close_cr - l.close_dr)}: tiền và hàng tồn kho "
                                    "không thể âm, thường do ghi thiếu một khoản thu, nhập kho hoặc ghi nhầm tài khoản", l.row))

    # 9. Books not closed: income and expense accounts keep a balance, or 911 does not net to zero.
    if {"close_dr", "close_cr"} <= tb.columns.keys():
        open_ = [l for l in leaves if l.account[0] in "5678" or l.account.startswith("911")]
        left = [l for l in open_ if abs(l.close_dr - l.close_cr) > TOLERANCE]
        if left:
            names = ", ".join(f"TK {l.account}" for l in left[:4])
            issues.append(Issue("warning", "not_closed",
                                f"Chưa khoá sổ: {names} còn số dư cuối kỳ. Báo cáo này là số sơ bộ", left[0].row))

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


def check_against_previous(cur: TrialBalance, prev: TrialBalance) -> list:
    """Recurring entries (depreciation, payroll accrual, prepaid allocation) missing or far below last month."""
    issues = []
    for label, prefix, side in RECURRING:
        now = sum(getattr(l, side) for l in cur.leaves if l.account.startswith(prefix))
        before = sum(getattr(l, side) for l in prev.leaves if l.account.startswith(prefix))
        if before > TOLERANCE and now < before * DROP_RATIO:
            row = next((l.row for l in cur.leaves if l.account.startswith(prefix)), 0)
            issues.append(Issue("error", "recurring_missing",
                                f"Kỳ này {'chưa thấy' if now <= TOLERANCE else 'mới có'} {label} (phát sinh "
                                f"{'Có' if side == 'cr' else 'Nợ'} TK {prefix} {fmt(now)}, kỳ trước {fmt(before)}); "
                                f"nếu chưa ghi thì chi phí đang thiếu khoảng {fmt(before - now)} và lợi nhuận đang cao hơn thực tế",
                                row, label, before - now))
    return issues


def check_profit_tie(tb: TrialBalance, profit_after_tax: Decimal) -> list:
    """Profit from the income and expense accounts must equal what was closed into 4212 this period."""
    closing = [l for l in tb.leaves if l.account.startswith("911")]
    if not closing or not sum(l.dr + l.cr for l in closing):
        return []
    retained = [l for l in tb.leaves if l.account.startswith("4212")] or [l for l in tb.leaves if l.account.startswith("421")]
    if not retained:
        return []
    moved = sum(l.cr - l.dr for l in retained)
    diff = profit_after_tax - moved
    if abs(diff) <= TOLERANCE:
        return []
    return [Issue("error", "profit_tie",
                  f"Lợi nhuận sau thuế tính từ các TK kết quả là {fmt(profit_after_tax)} nhưng kết chuyển vào TK 4212 "
                  f"là {fmt(moved)}, lệch {fmt(diff, sign=True)}", retained[0].row)]
