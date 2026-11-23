"""Economic choices and meter accounting with a next-day solar forecast."""

import pytest

from .test_ext_funded_exports import config, opt


def sunny_day(price, *, load=0.5, switch=True, **settings):
    cfg = config(base_load_kw=load, **settings)
    buy, sell, solar = [0.22] * 192, [0.01] * 192, [0.0] * 192
    buy[88:96] = [0.08] * 8
    solar[136:148] = [4.0] * 12
    sell[136:148] = [price] * 12
    return opt.optimize(
        buy,
        sell,
        solar,
        cfg,
        initial_soc_pct=30,
        start_slot=88,
        total_slots=192,
        enable_pv_strategy=switch,
    )


def test_sell_price_changes_previous_nights_target():
    cheap, mid, peak = [sunny_day(p) for p in (0.01, 0.18, 0.5)]
    assert cheap.slots[95].soc_after == pytest.approx(30)
    assert mid.slots[95].soc_after == pytest.approx(90)  # configured maximum
    assert 30 < peak.slots[95].soc_after < 90
    assert cheap.grid_export_kwh < mid.grid_export_kwh < peak.grid_export_kwh
    assert all(cheap.thirdparty_pv_slots)
    assert not any(peak.thirdparty_pv_slots[136:148])
    assert all(
        r.trace["solar_prefill"]["incremental_margin"] > 0 for r in (cheap, mid, peak)
    )


@pytest.mark.parametrize("load", [0.5, 2.5])
def test_export_only_the_surplus_and_reconcile_two_day_costs(load):
    r = sunny_day(0.5, load=load)
    assert r.grid_export_kwh == pytest.approx((4 - load) * 3)
    assert not r.sell_slots  # PV sale must not start Emaldo manual battery selling.
    for slot in r.slots[136:148]:
        assert slot.grid_import_kwh == 0
        assert slot.grid_export_kwh == pytest.approx((4 - load) * 0.25)
    for end, reported in ((96, r.day1_totals["net_profit"]), (192, r.net_profit)):
        rows = r.slots[88:end]
        assert reported == pytest.approx(
            sum(
                s.baseline_cost
                - s.grid_import_kwh * s.buy_price
                + s.grid_export_kwh * s.sell_price
                - s.cycled_kwh * 0.03
                for s in rows
            )
        )
    assert all(20 <= s.soc_after <= 90.000001 for s in r.slots[88:])


@pytest.mark.parametrize("settings", [{"switch": False}, {"pv_min_forecast_kwh": 20}])
def test_no_new_switch_commands_below_threshold_or_when_disabled(settings):
    r = sunny_day(0.5, **settings)
    assert all(r.thirdparty_pv_slots)


def test_existing_controls_and_no_surplus_use_original_planners():
    assert "solar_prefill" not in sunny_day(0.5, fund_grid_exports=False).trace
    assert "solar_prefill" not in sunny_day(0.5, forced_sell_enabled=False).trace
    assert "solar_prefill" not in sunny_day(0.5, pv_cut_when_full=True).trace
    assert "solar_prefill" not in sunny_day(0.5, load=5).trace
