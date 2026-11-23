"""Regression coverage for buying energy before a known export opportunity."""

import pytest

from . import load_extended_optimizer

opt, _ = load_extended_optimizer()


def config(**changes):
    values = dict(
        capacity_kwh=12,
        soc_min=20,
        soc_max=90,
        base_load_kw=0.5,
        max_charge_kw=5,
        max_discharge_kw=5,
        idle_power_kw=0,
        charge_efficiency=0.9,
        discharge_efficiency=0.9,
        forced_sell_enabled=True,
        forced_sell_max_kwh_pd=6,
        fund_grid_exports=True,
    )
    return opt.BatteryConfig(**(values | changes))


@pytest.mark.parametrize("offset", [0, 96])
def test_buy_before_peak_from_empty_and_book_both_days(offset):
    n = 96 + offset
    first = 12 if not offset else 86
    buy = [0.25] * n
    buy[first : first + 9] = [0.06] * 9
    sell = [0.02] * n
    peak = first + 10
    sell[peak : peak + 8] = [0.40] * 8
    cfg = config(capacity_kwh=14)
    res = opt.optimize(
        buy, sell, [0.0] * n, cfg, start_slot=first, initial_soc_pct=20, total_slots=n
    )
    assert res.sell_slots == list(range(peak, peak + 6))
    assert res.sell_target_kwh == pytest.approx(6)
    buys = [s for s in res.slots if s.action == "charge"]
    assert len(buys) > 1 and all(first <= s.index < first + 9 for s in buys)
    assert res.sell_profit > 0
    for s in res.slots[first:]:
        assert cfg.soc_min - 1e-8 <= s.soc_after <= cfg.soc_max + 1e-8
    for i in res.sell_slots:
        assert res.slots[i].soc_after * cfg.capacity_kwh / 100 >= 3.8 - 1e-8
    grid = sum(
        s.grid_import_kwh * s.buy_price - s.grid_export_kwh * s.sell_price
        for s in res.slots
    )
    assert res.net_profit == pytest.approx(
        res.baseline_cost - grid - res.wear_cost_total
    )
    assert res.grid_export_kwh == pytest.approx(6)
    if offset:
        assert res.day1_totals["sell_target_kwh"] == 0
        assert res.day1_totals["net_profit"] < 0
        assert res.day1_totals["charge_slots"] == len(buys)


@pytest.mark.parametrize(
    "changes",
    [
        {"fund_grid_exports": False},
        {"forced_sell_enabled": False},
        {"forced_sell_min_price": 0.6},
        {"forced_sell_min_profit": 0.6},
        {"forced_sell_max_kwh_pd": 0.9},
    ],
)
def test_no_unexecutable_or_disabled_cycle(changes):
    res = opt.optimize(
        [0.1] * 96,
        [0.01] * 50 + [0.5] * 2 + [0.01] * 44,
        [0.0] * 96,
        config(**changes),
        initial_soc_pct=20,
    )
    assert not res.sell_slots
    assert res.charge_slots == 0


def test_fees_and_losses_can_erase_positive_spread():
    res = opt.optimize(
        [0.15] * 96, [0.19] * 96, [0.0] * 96, config(), initial_soc_pct=20
    )
    assert not res.sell_slots and res.charge_slots == 0


def test_later_more_valuable_window_and_replan_after_charging():
    buy = [0.05] * 12 + [0.2] * 84
    sell = [0.01] * 96
    sell[22:24] = [0.19] * 2
    sell[37:45] = [0.50] * 8
    cfg = config()
    res = opt.optimize(buy, sell, [0.0] * 96, cfg, initial_soc_pct=20)
    assert res.sell_slots[0] == 37
    again = opt.optimize(
        buy,
        sell,
        [0.0] * 96,
        cfg,
        start_slot=12,
        initial_soc_pct=res.slots[11].soc_after,
    )
    assert again.sell_slots[0] == 37


def test_normal_pv_plan_kept_with_forecast_surplus():
    cfg = config()
    solar = [0.0] * 48 + [4.0] * 12 + [0.0] * 36
    args = ([0.12] * 96, [0.12] * 96, solar, cfg)
    funded = opt.optimize(*args, initial_soc_pct=50, enable_pv_strategy=True)
    # The only difference is the diagnostic saying why no cycle was planned.
    assert funded.trace.pop("grid_funded_export_skipped") == "solar_surplus_in_horizon"
    cfg.fund_grid_exports = False
    assert funded == opt.optimize(*args, initial_soc_pct=50, enable_pv_strategy=True)
