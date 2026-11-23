"""Mid-quarter sale comparisons use only unelapsed energy."""

import pytest

from . import load_extended_optimizer

opt, _ = load_extended_optimizer()


def plan(
    soc=29,
    price=0.1985,
    house=1.5,
    seconds=365,
    n=192,
    slot=56,
    flat_buy=None,
    **settings,
):
    cfg = opt.BatteryConfig(
        capacity_kwh=40,
        soc_min=15,
        base_load_kw=house,
        max_discharge_kw=11,
        idle_power_kw=0,
        charge_efficiency=0.9,
        discharge_efficiency=0.9,
        forced_sell_enabled=True,
        **settings,
    )
    buy = [0.1229 if 28 <= i % 96 < 88 else 0.1019 for i in range(n)]
    if flat_buy is not None:
        buy = [flat_buy] * n
    sell = [0.01] * n
    sell[slot] = price
    return opt.optimize(
        buy,
        sell,
        [0] * n,
        cfg,
        start_slot=slot,
        initial_soc_pct=soc,
        total_slots=n,
        active_sale_seconds=seconds,
    )


def test_profitable_partial_slot_and_day_split():
    result = plan()
    slot = result.slots[56]
    decision = result.trace["active_sale"]
    assert decision["decision"] == "continue"
    assert decision["advantage"] > 0
    assert slot.sell_kwh == pytest.approx((11 - 1.5) * 365 / 3600)
    assert slot.action == "force_sell"
    assert 57 not in result.sell_slots
    assert result.day1_totals["sell_target_kwh"] == pytest.approx(slot.sell_kwh)
    assert decision["continue_seconds"] == pytest.approx(365)


def test_reserve_ends_sale_early_and_blocks_at_floor():
    near = plan(soc=18.5, price=0.9)
    assert 0 < near.trace["active_sale"]["continue_seconds"] < 365
    assert near.slots[56].soc_after == pytest.approx(17.5)
    assert plan(soc=17.5).slots[56].action != "force_sell"


def test_negative_sale_stops_and_tie_continues():
    assert plan(price=-1).slots[56].action != "force_sell"
    tied = plan(price=0, house=0, n=96, slot=95, flat_buy=0, wear_cost_per_kwh=0)
    assert tied.trace["active_sale"]["advantage"] == pytest.approx(0)
    assert tied.slots[95].action == "force_sell"


def test_same_slot_shorter_remainder_sells_less():
    res = plan(seconds=60, price=0.9)
    assert res.slots[56].sell_kwh == pytest.approx(9.5 / 60)


def test_last_cheap_quarter_must_have_enough_refill_capacity():
    # An idle house leaves both tail plans untouched. The only economic
    # difference is the cost and feasibility of replacing the exported energy.
    result = plan(house=0, slot=94, n=96, max_charge_kw=0.1, price=0.9)
    decision = result.trace["active_sale"]
    assert decision["refill_fits"] is False
    assert decision["decision"] == "stop"
