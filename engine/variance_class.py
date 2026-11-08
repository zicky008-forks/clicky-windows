"""Before a cost line is called over or under plan: is it timing, a one-off, or a variable cost that moved with
volume? (F01 principle 4.) And a line below the threshold, or an entry corrected within the year, is never called an
error or an anomaly (principle 5).

Each month line of engine.fpa's Variance gets the reported variance, the causes found with evidence, and the
underlying variance once those are taken out. A cause is only named when the books show it:

- correction: a credit line this month that reverses, to the đồng, a debit of the same department and cost element
  earlier in the year;
- timing, late: the plan of earlier months was not spent, and this month catches up (the year to date before this
  month is on the other side of plan);
- timing, early: a later month of the plan holds a step for this line (above its neighbours) that this month spent;
- one-off: a single line this month without which the line is within threshold, and with nothing like it (±25%) in
  the earlier months of the year;
- volume: a cost element the catalogue names as variable ('Yếu tố biến đổi'), compared with the plan flexed by the
  driver (actual ÷ plan revenue, given by the caller from the sales P&L).

What is left is the underlying variance. Only that is called 'vượt' or 'thấp hơn' kế hoạch, and only when it is above
the threshold: max('Ngưỡng chênh lệch' đồng, 'Ngưỡng chênh lệch %' of plan), 5 triệu and 5% by default. Amounts are
Decimal; every sentence is code, nothing here is written by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from engine import opex_split
from engine.bod_check import fiscal_year, fy_months
from engine.fpa import _element_of, _month_text, pnl_line
from engine.monthly_report import fmt
from engine.tables import key

ZERO = Decimal(0)
ABS_DEFAULT = Decimal(5_000_000)
PCT_DEFAULT = Decimal(5)
SIMILAR = Decimal("0.25")                    # a line within 25% of an earlier one is not a one-off

SMALL, CORRECTION, LATE, EARLY, ONE_OFF, VOLUME = "small", "correction", "late", "early", "one_off", "volume"
VERDICT = {SMALL: "nhỏ dưới ngưỡng", CORRECTION: "điều chỉnh bút toán", LATE: "lệch thời điểm",
           EARLY: "lệch thời điểm", ONE_OFF: "khoản một lần", VOLUME: "theo sản lượng"}


@dataclass
class Cause:
    kind: str
    amount: Decimal              # the part of the variance it explains, with the variance's sign
    text: str
    where: list = field(default_factory=list)       # source rows behind it


@dataclass
class Classified:
    line: object                 # fpa.Line, the month
    threshold: Decimal
    causes: list = field(default_factory=list)
    ytd_variance: Decimal = ZERO
    flex: Optional[Decimal] = None                  # the flexed plan, for a variable cost

    @property
    def reported(self) -> Decimal:
        return self.line.variance

    @property
    def material(self) -> bool:
        """Above the threshold as reported, or (a variable cost) against the flexed plan."""
        return abs(self.reported) > self.threshold or abs(self.underlying) > self.threshold

    @property
    def underlying(self) -> Decimal:
        return self.reported - sum((c.amount for c in self.causes), ZERO)

    @property
    def verdict(self) -> str:
        if not self.material:
            return VERDICT[SMALL]
        if abs(self.underlying) <= self.threshold and self.causes:
            return VERDICT[max(self.causes, key=lambda c: abs(c.amount)).kind]
        if abs(self.underlying) <= self.threshold:
            return VERDICT[SMALL]
        if self.flex is not None:                    # a variable cost is judged against the flexed plan
            return "vượt ngân sách linh hoạt" if self.underlying > 0 else "thấp hơn ngân sách linh hoạt"
        return "vượt kế hoạch" if self.underlying > 0 else "thấp hơn kế hoạch"

    @property
    def alarm(self) -> bool:
        """Worth a manager's question: the underlying variance is still above the threshold."""
        return self.material and abs(self.underlying) > self.threshold

    def sentence(self) -> str:
        label = self.line.label
        if not self.material:
            return (f"{label}: chênh {fmt(self.reported)} đ so với kế hoạch, dưới ngưỡng {fmt(self.threshold)} đ, "
                    "không phải bất thường.")
        said = [f"{label}: số báo cáo chênh {fmt(self.reported)} đ so với kế hoạch."]
        said += [c.text for c in self.causes]
        if self.causes:
            said.append(f"Số thực chất chênh {fmt(self.underlying)} đ"
                        + (f", {self.verdict}." if self.alarm else ", trong ngưỡng."))
        else:
            said.append(f"Em không thấy lệch thời điểm, khoản một lần hay biến động theo sản lượng: {self.verdict}.")
        return " ".join(said)


def thresholds(config) -> tuple:
    """(đồng, percent of plan) from the catalogue's rules, or the defaults."""
    from engine.bod_inputs import num
    amount, pct = ABS_DEFAULT, PCT_DEFAULT
    for k, v in (getattr(config, "rules", {}) or {}).items():
        if "nguong" not in k:
            continue
        printed = (getattr(config, "rule_text", {}) or {}).get(k, k)
        n = num(v)
        if n is None:
            continue
        if "%" in printed or "phan tram" in k or "ty le" in k:
            pct = n * 100 if n < 1 else n
        else:
            amount = n
    return amount, pct


def variable(config) -> set:
    """Cost element codes the catalogue names as variable (rule 'Yếu tố biến đổi', or a group 'biến phí')."""
    listed = config.rule("yeu to bien doi", default="") if config is not None else ""
    out = {x.strip() for x in re.split(r"[;,\n]", str(listed or "")) if x.strip()}
    for code, e in (getattr(config, "elements", {}) or {}).items():
        if key(e.get("nhom", "")) in ("bien phi", "chi phi bien doi", "variable"):
            out.add(code)
    return out


def _series(v, gl: list, plans: list) -> tuple:
    """(actual by line and month, plan by line and month over the whole fiscal year, entries by line this year)."""
    start = int(v.months[0][5:7])
    year = fy_months(fiscal_year(v.period, start), start)
    actual, plan, entries = {}, {}, {}
    for e in gl:
        if e.period in v.months:
            k = (e.dept, _element_of(e))
            actual.setdefault(k, {}).setdefault(e.period, ZERO)
            actual[k][e.period] += e.amount
            entries.setdefault(k, []).append(e)
    if v.plan is not None:
        for r in plans:
            if key(r.version or "") != key(v.plan.version) or r.file != v.plan.file or r.month not in year:
                continue
            if pnl_line(r):
                continue
            k = (r.dept, r.element)
            plan.setdefault(k, {}).setdefault(r.month, ZERO)
            plan[k][r.month] += r.amount
    return year, actual, plan, entries


def _same(a: Decimal, b: Decimal) -> bool:
    return b != 0 and abs(a - b) <= abs(b) * SIMILAR


def classify(v, gl: list, plans: list, config=None, driver: Optional[tuple] = None) -> list:
    """Every month line of v, classified. driver: (actual, plan) revenue of the month, for variable costs."""
    amount_t, pct_t = thresholds(config)
    var_codes = variable(config)
    year, actual, plan, entries = _series(v, gl, plans)
    before = v.months[:-1]
    ytd = {(l.dept, l.element): l for l in v.ytd}
    out = []
    for l in v.month:
        k = (l.dept, l.element)
        thr = max(amount_t, abs(l.plan) * pct_t / 100)
        c = Classified(l, thr, ytd_variance=ytd[k].variance if k in ytd else l.variance)
        out.append(c)
        # volume first: a variable cost is judged against the flexed plan
        if l.element in var_codes and driver and driver[1]:
            ratio = Decimal(driver[0]) / Decimal(driver[1])
            c.flex = (l.plan * ratio).quantize(Decimal(1))
            c.causes.append(Cause(VOLUME, c.flex - l.plan,       # either side: the rest is spending against the flex
                 f"Đây là chi phí biến đổi: doanh thu đạt {(ratio * 100).quantize(Decimal('0.1'))}% kế hoạch, "
                 f"ngân sách linh hoạt {fmt(c.flex)} đ, phần theo sản lượng {fmt(c.flex - l.plan)} đ."))
        if not c.material:
            continue
        private = opex_split.sensitive(l.element, next(iter(l.accounts), "") if l.accounts else "", config)
        mine = entries.get(k, [])
        now = [e for e in mine if e.period == v.period]
        earlier = [e for e in mine if e.period in before]
        p = plan.get(k, {})
        a = actual.get(k, {})
        left = c.underlying

        def take(kind, amount, text, where=()):
            nonlocal left
            if amount and (amount > 0) == (left > 0) and abs(amount) <= abs(left):
                c.causes.append(Cause(kind, amount, text, list(where)))
                left -= amount

        # a correction: a credit this month that reverses an earlier debit to the đồng
        if left < 0:
            for e in sorted((e for e in now if e.amount < 0), key=lambda e: e.amount):
                hit = next((x for x in earlier if x.amount == -e.amount), None)
                if hit is not None and abs(e.amount) <= abs(left):
                    refs = (not private) and bool(e.ref or hit.ref)
                    take(CORRECTION, e.amount, f"Có {fmt(-e.amount)} đ ghi giảm"
                         + (f" ({e.ref})" if refs and e.ref else "")
                         + f" điều chỉnh bút toán tháng {_month_text(hit.period)}"
                         + (f" ({hit.ref})" if refs and hit.ref else "")
                         + (f" ({opex_split.HELD.lower()})" if private else "") + ", không phải tiết kiệm.",
                         [e.where, hit.where])
        # timing, late: the year to date before this month is on the other side of plan
        prev = sum((a.get(m, ZERO) - p.get(m, ZERO) for m in before), ZERO)
        gaps = [m for m in before if abs(a.get(m, ZERO) - p.get(m, ZERO)) > thr
                and (a.get(m, ZERO) - p.get(m, ZERO) > 0) == (prev > 0)]      # a real step, not a year of noise
        if prev and gaps and abs(left) > thr and (prev > 0) != (left > 0) and abs(prev) > thr:
            part = min(abs(prev), abs(left)) * (1 if left > 0 else -1)
            take(LATE, part, (f"Các tháng trước còn {fmt(-prev)} đ kế hoạch chưa chi, tháng này chi bù {fmt(part)} đ."
                              if prev < 0 else f"Các tháng trước đã chi trước {fmt(prev)} đ so với kế hoạch, "
                                               f"tháng này chi ít hơn {fmt(-part)} đ."))
        # timing, early: a later month of the plan holds a step for this line that this month spent
        if left > thr:
            later = year[year.index(v.period) + 1:]
            for m in later:
                i = year.index(m)
                around = [p.get(year[j], ZERO) for j in (i - 1, i + 1) if 0 <= j < len(year)]
                step = p.get(m, ZERO) - sum(around, ZERO) / len(around)
                if step > thr and _same(step, left):
                    take(EARLY, min(step, left).quantize(Decimal(1)),
                         f"Kế hoạch đặt khoản {fmt(step)} đ ở tháng {_month_text(m)}; tháng này đã chi trước.")
                    break
        # one-off: one line, the rest within threshold, nothing like it earlier in the year
        if abs(left) > thr:
            near = max(thr, abs(left) * Decimal("0.2"))
            for e in sorted(now, key=lambda e: -abs(e.amount)):
                if not e.amount or (e.amount > 0) != (left > 0) or abs(left - e.amount) > near:
                    continue                                  # a one-off is one line about the size of what is left
                if any(_same(x.amount, e.amount) for x in earlier):
                    continue                                  # the same kind of line came before: not a one-off
                part = min(abs(e.amount), abs(left)) * (1 if left > 0 else -1)
                if private:                           # pay names people: no reference, no text in a sentence
                    text = (f"Khoản một lần ở {l.element_name or l.element}: {fmt(e.amount)} đ "
                            f"({opex_split.HELD.lower()}), các tháng trước không có khoản tương tự.")
                else:
                    named = ", ".join(x for x in (e.ref, e.text) if x)
                    text = ("Khoản một lần" + (f" ({named})" if named else "")
                            + f": {fmt(e.amount)} đ, các tháng trước không có khoản tương tự.")
                take(ONE_OFF, part, text, [e.where])
                break
    return out


def summary(classified: list) -> tuple:
    """(reported, underlying) total variance over the material lines, and the lines still worth a question."""
    material = [c for c in classified if c.material]
    return (sum((c.reported for c in material), ZERO), sum((c.underlying for c in material), ZERO),
            [c for c in classified if c.alarm])
