"""Gross profit bridge, plan to actual, in four parts: volume, mix, price, cost. By SKU, all channels together.

The convention is the one published with the F01 criteria, so a reader can redo every part by hand:

    Q volume in standard units, P net price per unit, C cost per unit, g = P - C; a actual, b plan;
    ΣQ every SKU's volume, s_b a SKU's share of plan volume, GP gross profit.
    Volume = (ΣQa - ΣQb) x (GP_b / ΣQb)
    Mix    = Σ (Qa - ΣQa x s_b) x g_b
    Price  = Σ (Pa - Pb) x Qa
    Cost   = - Σ (Ca - Cb) x Qa
    Volume + Mix + Price + Cost = GP_a - GP_b

Actual P and C are net revenue and cost of goods sold over volume; the plan's come from the plan file (a price and
a cost per unit, or revenue and cost over volume). The parts are computed without dividing actual figures
((Pa - Pb) x Qa is Revenue_a - Pb x Qa), and the one shared quotient (ΣQa x GP_b / ΣQb) enters volume and mix
with opposite signs, so the four parts add up to the gap exactly. Shown to the đồng, mix takes the rounding of
the other three (under 2 đồng), so the parts on screen add up to the gap on screen too.

A SKU sold with no plan has no planned price or cost: its price and cost are taken as planned (no price or cost
part) and its whole gross profit is mix. A planned SKU not sold adds only to mix. Both are named.
Amounts are Decimal; nothing here is written by the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Optional

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass
class Sku:
    """One SKU's month, all channels together. Volume is in the catalogue's standard unit."""
    qty: Decimal = ZERO
    revenue: Decimal = ZERO                 # net revenue
    cogs: Decimal = ZERO
    price: Optional[Decimal] = None         # per unit, when the plan file gives it
    cost: Optional[Decimal] = None          # per unit, when the plan file gives it
    sources: list = field(default_factory=list)

    def unit_price(self) -> Decimal:
        if self.price is not None:
            return self.price
        return self.revenue / self.qty if self.qty else ZERO

    def unit_cost(self) -> Decimal:
        if self.cost is not None:
            return self.cost
        return self.cogs / self.qty if self.qty else ZERO

    def gp(self) -> Decimal:
        """Gross profit: per unit when the file gives prices, else revenue less cost."""
        if self.price is not None or self.cost is not None:
            return self.qty * (self.unit_price() - self.unit_cost())
        return self.revenue - self.cogs


@dataclass
class Part:
    sku: str
    volume: Decimal = ZERO                  # the volume part is one figure for all SKUs; kept per SKU as 0
    mix: Decimal = ZERO
    price: Decimal = ZERO
    cost: Decimal = ZERO


@dataclass
class Bridge:
    gp_plan: Decimal
    gp_actual: Decimal
    volume: Decimal
    mix: Decimal
    price: Decimal
    cost: Decimal
    by_sku: list = field(default_factory=list)       # [Part]
    unplanned: list = field(default_factory=list)    # SKUs sold with no plan
    unsold: list = field(default_factory=list)       # SKUs planned and not sold
    qty_plan: Decimal = ZERO
    qty_actual: Decimal = ZERO

    @property
    def gap(self) -> Decimal:
        return self.gp_actual - self.gp_plan

    def rounded(self) -> dict:
        """The parts to the đồng, adding up to the gap to the đồng: mix takes the rounding of the other three."""
        r = {k: _dong(getattr(self, k)) for k in ("volume", "price", "cost")}
        gap = _dong(self.gap)
        r["mix"] = gap - r["volume"] - r["price"] - r["cost"]
        r["gap"] = gap
        r["gp_plan"], r["gp_actual"] = _dong(self.gp_plan), _dong(self.gp_actual)
        return r


def _dong(x: Decimal) -> Decimal:
    return x.quantize(ONE, rounding=ROUND_HALF_UP)


def bridge(actual: dict, plan: dict) -> Bridge:
    """actual, plan: {sku: Sku}. The four parts of GP_a - GP_b under the published convention."""
    with localcontext() as ctx:
        ctx.prec = 60                                # per-unit quotients carry far past the đồng
        return _bridge(actual, plan)


def _bridge(actual: dict, plan: dict) -> Bridge:
    skus = sorted(set(actual) | set(plan))
    a = {s: actual.get(s, Sku()) for s in skus}
    b = {s: plan.get(s, Sku()) for s in skus}
    unplanned = [s for s in skus if a[s].qty and not b[s].qty]
    unsold = [s for s in skus if b[s].qty and not a[s].qty]
    # A SKU with no plan: priced and costed as sold, so all of its profit is mix.
    pb = {s: (a[s].unit_price() if s in unplanned else b[s].unit_price()) for s in skus}
    cb = {s: (a[s].unit_cost() if s in unplanned else b[s].unit_cost()) for s in skus}
    gb = {s: pb[s] - cb[s] for s in skus}
    qa = sum((a[s].qty for s in skus), ZERO)
    qb = sum((b[s].qty for s in skus), ZERO)
    if not qb:
        raise ValueError("kế hoạch không có sản lượng: chưa tính được cầu nối theo quy ước")
    gp_b = sum((b[s].qty * gb[s] for s in skus if s not in unplanned), ZERO)
    gp_a = sum((a[s].revenue - a[s].cogs for s in skus), ZERO)
    shared = qa * gp_b / qb                          # ΣQa x GP_b / ΣQb: in volume and in mix, opposite signs
    volume = shared - gp_b                           # (ΣQa - ΣQb) x GP_b / ΣQb
    parts = []
    for s in skus:
        price = a[s].revenue - pb[s] * a[s].qty      # (Pa - Pb) x Qa
        cost = -(a[s].cogs - cb[s] * a[s].qty)       # -(Ca - Cb) x Qa
        parts.append(Part(s, mix=a[s].qty * gb[s] - qa * (b[s].qty / qb) * gb[s], price=price, cost=cost))
    # Σ (Qa - ΣQa x s_b) x g_b = ΣQa·g_b - shared; the per-SKU split above is for showing, the total is exact.
    mix = sum((a[s].qty * gb[s] for s in skus), ZERO) - shared
    return Bridge(gp_b, gp_a, volume, mix, sum((p.price for p in parts), ZERO), sum((p.cost for p in parts), ZERO),
                  parts, unplanned, unsold, qb, qa)
