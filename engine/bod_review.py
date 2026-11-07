"""Review a BOD pack before it is presented: recompute what the pack claims from the detail and the
client's own rules, and point at every cell that does not hold. Pure Python, Decimal, no Excel.

Nothing about one client lives here. Channels, departments, denominator rules, plan versions, the
no-cut list and the rate convention all come from the client's configuration at run time.

Seven groups of checks (numbers match the review spec):
  1  total cost in the pack = cost detail filtered to the division and the period; the causes of any gap
     must add up to the gap exactly
  2  each % of net revenue uses the denominator the channel rule says
  3  every value has its % of net revenue and its gap in points; points = pct now − pct compared
  4  last year is compared with the plan version the convention names (e.g. that year's revised plan)
  5  revised plan: locked months carry actuals, not plan
  6  three-step cut: no-cut departments untouched; the levers add up to the gap to the ceiling
  7  FX impact uses the rate type the convention names
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Callable, Iterable, Optional

ZERO = Decimal(0)
VND_TOLERANCE = Decimal(1)                 # đồng
PCT_TOLERANCE = Decimal("0.0001")          # 0.01 percentage point, as a fraction


@dataclass
class Finding:
    group: int                  # 1..7
    code: str
    where: str                  # "Sheet!C12" or a row label: what Clicky points at
    message: str                # Vietnamese, one idea
    expected: Optional[Decimal] = None
    actual: Optional[Decimal] = None
    causes: list = field(default_factory=list)    # [(label, amount)] adding up to actual - expected
    file: str = ""              # the workbook the cell is in: the pack, or a source file (an AOPR file row)

    @property
    def diff(self) -> Optional[Decimal]:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected


def D(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def pct(value: Decimal, base: Decimal) -> Optional[Decimal]:
    return None if not base else value / base


def fmt_vnd(x: Decimal) -> str:
    return f"{x:,.0f}".replace(",", ".")


def fmt_pct(x: Optional[Decimal]) -> str:
    return "—" if x is None else f"{x * 100:.2f}%".replace(".", ",")


# ── 1. Total cost against the detail ────────────────────────────────────────────

@dataclass
class Entry:
    """One line of the cost detail export, already read and typed."""
    period: str                 # "YYYY-MM"
    amount: Decimal             # VND
    division: str = ""
    site: str = ""
    channel: str = ""
    dept: str = ""
    element: str = ""
    account: str = ""
    usd: Optional[Decimal] = None
    ref: str = ""
    text: str = ""
    where: str = ""             # "Sheet!row" in the source file


def explain_gap(gap: Decimal, groups: list, max_groups: int = 4) -> tuple:
    """groups: [(label, signed amount the pack has on top of the detail)]. Find the smallest set of
    groups that adds up to the gap exactly. Returns (chosen groups, unexplained remainder)."""
    if abs(gap) <= VND_TOLERANCE:
        return [], ZERO
    cands = [(l, a) for l, a in groups if a]
    for k in range(1, min(max_groups, len(cands)) + 1):
        for combo in combinations(cands, k):
            if abs(sum(a for _, a in combo) - gap) <= VND_TOLERANCE:
                return list(combo), ZERO
    return [], gap


def check_total(pack_total: Decimal, where: str, entries: Iterable[Entry], division: str, periods: set,
                suspects: dict) -> list:
    """suspects: {label: predicate(entry)} for lines a pack commonly gets wrong (next-period reversals,
    unassigned departments, distributor discounts posted as cost, other divisions, other periods).
    The truth is the detail filtered to the division and the periods. A gap must be fully explained."""
    entries = list(entries)
    truth = sum((e.amount for e in entries if e.division == division and e.period in periods), ZERO)
    gap = D(pack_total) - truth
    if abs(gap) <= VND_TOLERANCE:
        return []
    groups = []
    for label, test in suspects.items():
        inside = sum((e.amount for e in entries if e.division == division and e.period in periods and test(e)), ZERO)
        outside = sum((e.amount for e in entries if not (e.division == division and e.period in periods) and test(e)), ZERO)
        if inside:
            groups.append((f"{label} (pack bỏ ra)", -inside))
        if outside:
            groups.append((f"{label} (pack cộng vào)", outside))
    chosen, rest = explain_gap(gap, groups)
    causes = chosen + ([("chưa giải thích được", rest)] if rest else [])
    return [Finding(1, "total_vs_detail", where,
                    f"Tổng chi phí trên bộ BOD lệch sổ chi tiết {fmt_vnd(gap)} đ", truth, D(pack_total), causes)]


# ── 2 and 3. % of net revenue, its denominator, values without % or points ──────

@dataclass
class PackLine:
    """One line of the pack: a value with its % of net revenue and its comparison."""
    label: str
    where: str                              # cell of the value
    value: Optional[Decimal]
    channel: str = ""                       # "" = not a channel line
    pct: Optional[Decimal] = None           # as printed, as a fraction
    pct_where: str = ""
    compare_value: Optional[Decimal] = None   # the same line in the scenario compared with
    compare_pct: Optional[Decimal] = None
    diff_value: Optional[Decimal] = None    # as printed
    diff_pp: Optional[Decimal] = None       # as printed, as a fraction (0.0123 = 1.23 points)
    diff_where: str = ""
    level: str = ""                         # "site" for site-level lines


def check_denominators(lines: Iterable[PackLine], rule: Callable[[PackLine], str], bases: dict) -> list:
    """rule(line) -> base name (e.g. 'division_all_sites', 'site'); bases: {name: net revenue}."""
    out = []
    for l in lines:
        if l.value is None or l.pct is None:
            continue
        name = rule(l)
        base = bases.get(name)
        if base is None:
            out.append(Finding(2, "no_base", l.pct_where or l.where, f"{l.label}: không có doanh thu thuần '{name}' để tính %"))
            continue
        want = pct(D(l.value), D(base))
        if want is not None and abs(want - l.pct) > PCT_TOLERANCE:
            used = next((n for n, b in bases.items() if b and abs(D(l.value) / D(b) - l.pct) <= PCT_TOLERANCE), "")
            why = f" (đang chia cho doanh thu '{used}')" if used else ""
            out.append(Finding(2, "wrong_base", l.pct_where or l.where,
                               f"{l.label}: %DTT phải là {fmt_pct(want)} theo mẫu số '{name}', bộ BOD ghi {fmt_pct(l.pct)}{why}",
                               want, l.pct))
    return out


def check_value_pct_points(lines: Iterable[PackLine]) -> list:
    out = []
    for l in lines:
        if l.value is None:
            continue
        if l.pct is None:
            out.append(Finding(3, "missing_pct", l.where, f"{l.label}: có giá trị nhưng thiếu %DTT"))
        if l.compare_value is not None and l.diff_value is None:
            out.append(Finding(3, "missing_diff", l.diff_where or l.where, f"{l.label}: thiếu chênh lệch bằng tiền"))
        if l.compare_pct is not None and l.pct is not None:
            if l.diff_pp is None:
                out.append(Finding(3, "missing_pp", l.diff_where or l.where, f"{l.label}: thiếu chênh lệch điểm %"))
            else:
                want = l.pct - l.compare_pct
                if abs(want - l.diff_pp) > PCT_TOLERANCE:
                    out.append(Finding(3, "wrong_pp", l.diff_where or l.where,
                                       f"{l.label}: chênh lệch phải là {fmt_pct(want)} điểm (= %DTT kỳ này − %DTT so sánh), "
                                       f"bộ BOD ghi {fmt_pct(l.diff_pp)}", want, l.diff_pp))
    return out


# ── 4. Last year against the right plan version ─────────────────────────────────

def check_scenarios(columns: Iterable[tuple], allowed: dict) -> list:
    """columns: [(where, year role, scenario label as printed)], role = 'current' | 'previous'.
    allowed: {'current': {...labels}, 'previous': {...labels}} from the client's convention."""
    from engine.trial_balance import fold
    out = []
    for where, role, label in columns:
        ok = {fold(a) for a in allowed.get(role, ())}
        if ok and fold(label) not in ok:
            out.append(Finding(4, "wrong_scenario", where,
                               f"Cột '{label}' so {'năm trước' if role == 'previous' else 'năm nay'} với sai phiên bản kế hoạch; "
                               f"phải là {', '.join(sorted(allowed[role]))}"))
    return out


def check_scenario_values(cells: Iterable[tuple], versions: dict, right: str) -> list:
    """cells: [(where, label, printed value)], versions: {version: {label: value}}. A printed value that
    equals another version and not the right one names the version that was used."""
    out = []
    for where, label, value in cells:
        good = versions.get(right, {}).get(label)
        if good is None or value is None or abs(D(value) - D(good)) <= VND_TOLERANCE:
            continue
        used = next((v for v, m in versions.items() if v != right and m.get(label) is not None
                     and abs(D(m[label]) - D(value)) <= VND_TOLERANCE), "")
        out.append(Finding(4, "wrong_version_value", where,
                           f"{label}: số so sánh phải lấy từ {right} ({fmt_vnd(D(good))}), bộ BOD lấy "
                           + (f"từ {used}" if used else "số khác") + f" ({fmt_vnd(D(value))})", D(good), D(value)))
    return out


# ── 5. Revised plan: locked months are actuals ──────────────────────────────────

def check_locked_months(revised: dict, actual: dict, plan: dict, locked: Iterable[str], where: Callable[[str, str], str]) -> list:
    """revised/actual/plan: {(line, month): value}. A locked month of the revised plan must equal actual;
    when it equals plan instead, say so."""
    out = []
    locked = set(locked)
    for (line, month), value in sorted(revised.items()):
        if month not in locked:
            continue
        act = actual.get((line, month))
        if act is None or abs(D(value) - D(act)) <= VND_TOLERANCE:
            continue
        p = plan.get((line, month))
        still_plan = p is not None and abs(D(value) - D(p)) <= VND_TOLERANCE
        out.append(Finding(5, "locked_month_plan" if still_plan else "locked_month_mismatch", where(line, month),
                           f"{line}, tháng {month} đã khoá sổ: AOPR phải là số thực tế {fmt_vnd(D(act))}, đang là "
                           + ("số kế hoạch " if still_plan else "") + fmt_vnd(D(value)), D(act), D(value)))
    return out


# ── 6. Three-step cut ───────────────────────────────────────────────────────────

@dataclass
class CutLine:
    dept: str
    where: str
    request: Decimal                # what the department asked for
    base: Decimal                   # last year's or the reference amount
    cut_step2: Decimal = ZERO       # as printed: cut of the increase asked
    cut_step3: Decimal = ZERO       # as printed: flat cut
    after: Optional[Decimal] = None


def check_cut(lines: list, ceiling: Decimal, no_cut: Iterable[str], where_total: str) -> list:
    """Step 1 the ceiling is fixed; step 2 cuts what was asked above base; step 3 cuts flat. Departments on
    the no-cut list keep their request. The levers (all cuts) must add up to request − ceiling."""
    from engine.trial_balance import fold
    out = []
    keep = {fold(d) for d in no_cut}
    for l in lines:
        if fold(l.dept) in keep and (D(l.cut_step2) or D(l.cut_step3)):
            out.append(Finding(6, "no_cut_touched", l.where,
                               f"{l.dept} thuộc danh sách không cắt nhưng bị cắt {fmt_vnd(D(l.cut_step2) + D(l.cut_step3))}",
                               ZERO, D(l.cut_step2) + D(l.cut_step3)))
    gap = sum((D(l.request) for l in lines), ZERO) - D(ceiling)
    levers = sum((D(l.cut_step2) + D(l.cut_step3) for l in lines), ZERO)
    if abs(levers - gap) > VND_TOLERANCE:
        out.append(Finding(6, "levers_vs_gap", where_total,
                           f"Tổng đòn bẩy cắt {fmt_vnd(levers)} không bằng tổng chênh so với trần {fmt_vnd(gap)}",
                           gap, levers))
    return out


# ── 7. FX impact ────────────────────────────────────────────────────────────────

RATE_NAMES = {"binh quan": "bình quân", "cuoi ky": "cuối kỳ", "dau ky": "đầu kỳ", "aop": "AOP", "binh_quan": "bình quân"}


def check_fx(rows: Iterable[tuple], rates: dict, convention: tuple) -> list:
    """rows: [(where, label, month, usd, printed impact)]. rates: {(month, type): rate}.
    convention: (actual rate type, plan rate type), e.g. ('binh_quan', 'aop').
    impact = usd × (actual rate − plan rate)."""
    out = []
    a_type, p_type = convention
    for where, label, month, usd, impact in rows:
        ra, rp = rates.get((month, a_type)), rates.get((month, p_type))
        if ra is None or rp is None or impact is None:
            continue
        want = D(usd) * (D(ra) - D(rp))
        if abs(want - D(impact)) <= VND_TOLERANCE:
            continue
        used = ""
        for (m, t), r in rates.items():
            if m == month and t not in (a_type, p_type) and abs(D(usd) * (D(r) - D(rp)) - D(impact)) <= VND_TOLERANCE:
                used = t
        name = lambda t: RATE_NAMES.get(t, t)
        when = f"{month[5:]}/{month[:4]}" if len(month) == 7 and month[4] == "-" else month
        text = label if when in label else f"{label} tháng {when}"
        out.append(Finding(7, "fx_rate_type", where,
                           f"{text}: ảnh hưởng tỷ giá phải dùng tỷ giá {name(a_type)} ({fmt_vnd(want)} đ)"
                           + (f", bộ BOD dùng tỷ giá {name(used)}" if used else "") + f" ({fmt_vnd(D(impact))} đ)", want, D(impact)))
    return out
