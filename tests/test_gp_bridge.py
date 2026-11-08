"""Gross profit bridge under the published F01 convention: volume, mix, price, cost by SKU across channels.
The four parts add up to the gap exactly, and to the đồng as shown."""
import random
from decimal import Decimal as D, localcontext

import pytest

from engine.gp_bridge import Sku, bridge


def _plan():
    return {"A": Sku(qty=D(100), price=D(50), cost=D(30)), "B": Sku(qty=D(100), price=D(80), cost=D(40))}


def _actual():
    return {"A": Sku(qty=D(150), revenue=D(7200), cogs=D(4650)), "B": Sku(qty=D(70), revenue=D(5950), cogs=D(2870))}


def test_each_part_by_the_published_formula_worked_by_hand():
    b = bridge(_actual(), _plan())
    assert (b.gp_plan, b.gp_actual, b.gap) == (6000, 5630, -370)
    assert b.volume == 600            # (220 - 200) x 6000 / 200
    assert b.mix == -800              # (150 - 110) x 20 + (70 - 110) x 40
    assert b.price == 50              # (48 - 50) x 150 + (85 - 80) x 70
    assert b.cost == -220             # -((31 - 30) x 150 + (41 - 40) x 70)
    assert b.volume + b.mix + b.price + b.cost == b.gap
    assert [(p.sku, p.mix, p.price, p.cost) for p in b.by_sku] == [("A", 800, -300, -150), ("B", -1600, 350, -70)]


def test_a_plan_given_as_revenue_and_cost_bridges_the_same():
    plan = {s: Sku(qty=x.qty, revenue=x.qty * x.price, cogs=x.qty * x.cost) for s, x in _plan().items()}
    b = bridge(_actual(), plan)
    assert (b.volume, b.mix, b.price, b.cost) == (600, -800, 50, -220)


def test_the_four_parts_add_up_to_the_gap_exactly_on_awkward_numbers():
    """Volumes and prices that do not divide evenly, cases as fractions of cartons, thousands of SKUs."""
    rng = random.Random(1108)
    plan, actual = {}, {}
    for i in range(2000):
        qb, qa = D(rng.randint(1, 9000)) / 3, D(rng.randint(0, 9000)) / 7
        plan[f"S{i}"] = Sku(qty=qb, revenue=D(rng.randint(10**6, 10**10)), cogs=D(rng.randint(10**5, 10**9)))
        if qa:
            actual[f"S{i}"] = Sku(qty=qa, revenue=D(rng.randint(10**6, 10**10)), cogs=D(rng.randint(10**5, 10**9)))
    b = bridge(actual, plan)
    with localcontext() as ctx:
        ctx.prec = 60                                                  # the precision the bridge works in
        assert abs(b.volume + b.mix + b.price + b.cost - b.gap) < D("1e-30")
    r = b.rounded()
    assert r["volume"] + r["mix"] + r["price"] + r["cost"] == r["gap"]           # to the đồng, exactly
    assert abs(r["mix"] - b.mix) < 2                                   # mix only takes the rounding
    assert all(x == x.to_integral_value() for x in r.values())


def test_a_sku_sold_with_no_plan_is_all_mix_and_named():
    actual = dict(_actual(), N=Sku(qty=D(10), revenue=D(900), cogs=D(500)))
    b = bridge(actual, _plan())
    n = next(p for p in b.by_sku if p.sku == "N")
    assert b.unplanned == ["N"] and n.price == n.cost == 0 and n.mix == 400
    assert b.volume + b.mix + b.price + b.cost == b.gap == 5630 + 400 - 6000


def test_a_planned_sku_not_sold_adds_only_to_mix():
    actual = {"A": _actual()["A"]}
    b = bridge(actual, _plan())
    assert b.unsold == ["B"] and b.volume + b.mix + b.price + b.cost == b.gap == 2550 - 6000
    assert next(p for p in b.by_sku if p.sku == "B").price == 0


def test_no_plan_volume_is_said_not_divided_by_zero():
    with pytest.raises(ValueError, match="không có sản lượng"):
        bridge(_actual(), {})
