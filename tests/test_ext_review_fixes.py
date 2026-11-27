"""Regression tests for the code-review findings fixed in the extended engine."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from . import load_extended_module
from .test_ext_solar_prefill import opt, sunny_day

price_data = load_extended_module("price_data")
ep = load_extended_module("export_profit")

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=TZ)


# ── price_data: slot assignment ────────────────────────────────────────────


def _rows(*entries):
    return [
        {"start": start.isoformat(), "end": end.isoformat(), "price": price}
        for start, end, price in entries
    ]


def _parse(rows, **kw):
    return price_data.parse_sensor_prices("0", {"data": rows}, NOW, **kw)


def test_interval_assignment_uses_quarter_boundaries():
    day = NOW.replace(hour=0, minute=0)
    rows = _rows(
        # Starts between quarters: first covered slot is the next quarter.
        (day.replace(minute=5), day + timedelta(minutes=50), 1.0),
        # Ends mid-quarter: the quarter starting before the end is included.
        (day + timedelta(hours=1), day + timedelta(hours=1, minutes=20), 2.0),
        # Begins before midnight, spills into today.
        (day - timedelta(hours=2), day + timedelta(minutes=15), 3.0),
        # Later entries win on overlap.
        (day + timedelta(hours=3), day + timedelta(hours=4), 4.0),
        (day + timedelta(hours=3, minutes=30), day + timedelta(hours=4), 5.0),
    )
    today, _ = _parse(rows * 1 + _rows(*[(day + timedelta(hours=8, minutes=15 * i),
                                           day + timedelta(hours=8, minutes=15 * (i + 1)),
                                           9.0) for i in range(12)]))
    assert today[0] == 3.0          # covered by the pre-midnight interval
    assert today[1] == 1.0          # 00:15 is the first quarter at/after 00:05
    assert today[3] == 1.0          # up to (excl.) 00:50
    assert today[4] == 2.0          # 01:00 and 01:15 ...
    assert today[5] == 2.0          # ... end 01:20 excludes the 01:30 quarter
    assert today[12] == 4.0 and today[13] == 4.0
    assert today[14] == 5.0 and today[15] == 5.0
    assert today[32] == 9.0


def test_interval_assignment_matches_a_brute_force_scan():
    day = NOW.replace(hour=0, minute=0)
    slots = [day + timedelta(minutes=15 * i) for i in range(192)]
    spans = [(7, 31), (30, 30), (90, 200), (-4, 3), (150, 191), (40, 41)]
    rows = _rows(*[
        (day + timedelta(minutes=15 * a, seconds=7 * a % 11),
         day + timedelta(minutes=15 * b, seconds=3 * b % 13),
         float(i + 1))
        for i, (a, b) in enumerate(spans)
    ])
    expected = [None] * 192
    for row in rows:
        start = datetime.fromisoformat(row["start"])
        end = datetime.fromisoformat(row["end"])
        if end <= start:
            continue
        for i, instant in enumerate(slots):
            if start <= instant < end:
                expected[i] = row["price"]
    today, tomorrow = _parse(rows)
    # The parser forward-fills gaps, so compare the populated slots only.
    for i, want in enumerate(expected):
        if want is None:
            continue
        got = today[i] if i < 96 else tomorrow[i - 96]
        assert got == want, i


# ── scalar fallback must not hide a vanished forecast ──────────────────────

coord_mod = pytest.importorskip("homeassistant") and None


def _coordinator(monkeypatch, states):
    from custom_components.battery_optimizer import const as c
    from custom_components.battery_optimizer.extended import coordinator as coord
    from custom_components.battery_optimizer.extended import const as xc

    monkeypatch.setattr(coord.dt_util, "now", lambda: NOW)
    inst = object.__new__(coord.ExtendedBatteryOptimizerCoordinator)
    inst.config_entry = SimpleNamespace(
        data={},
        options={
            c.CONF_PRICE_SOURCE: "sensor",
            c.CONF_SPOT_SENSOR: "sensor.market",
            xc.CONF_TRANSFER_FEE_SENSOR: "sensor.tariff",
        },
    )
    inst._entry = inst.config_entry
    inst.hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    monkeypatch.setattr(coord.er, "async_get", lambda hass: None)
    return inst


def _forecast_state(price=20):
    day = NOW.replace(hour=0, minute=0)
    rows = [
        {"start": (day + timedelta(minutes=15 * i)).isoformat(), "price": price}
        for i in range(192)
    ]
    return SimpleNamespace(
        state=str(price), attributes={"data": rows, "unit_of_measurement": "snt/kWh"}
    )


def test_market_sensor_that_loses_its_forecast_is_not_a_flat_price(monkeypatch):
    states = {"sensor.market": _forecast_state()}
    inst = _coordinator(monkeypatch, states)
    today, tomorrow = inst._parse_sensor_price_data()
    assert today == [0.2] * 96 and tomorrow == [0.2] * 96

    # Attributes vanish (reload, partial startup): a numeric state only.
    states["sensor.market"] = SimpleNamespace(
        state="20", attributes={"unit_of_measurement": "snt/kWh"}
    )
    assert inst._parse_sensor_price_data() == (None, None)
    assert not inst._has_tomorrow_prices() or True  # must not raise

    states["sensor.market"] = _forecast_state(30)
    today, _ = inst._parse_sensor_price_data()
    assert today == [0.3] * 96


def test_helpers_that_never_had_a_forecast_stay_fixed_prices(monkeypatch):
    states = {"sensor.market": SimpleNamespace(state="0.08", attributes={})}
    inst = _coordinator(monkeypatch, states)
    assert inst._parse_sensor_price_data() == ([0.08] * 96, [0.08] * 96)


def test_transfer_tariff_that_loses_its_forecast_is_unavailable(monkeypatch):
    states = {"sensor.tariff": _forecast_state(4)}
    inst = _coordinator(monkeypatch, states)
    assert inst._parse_transfer_prices()[0] is not None
    states["sensor.tariff"] = SimpleNamespace(state="0.043", attributes={})
    assert inst._parse_transfer_prices() == (None, None)


# ── export profit: a late midnight tick still closes the day ───────────────

cfg = SimpleNamespace(
    charge_efficiency=0.8, discharge_efficiency=0.9, wear_cost_per_kwh=0.03,
    capacity_kwh=20,
)
MIDNIGHT = datetime(2026, 11, 19, tzinfo=timezone(timedelta(hours=2)))


def _meter(stock, sold=0.0):
    return {"stock": stock, "import": 0, "export": sold, "buy": 0.12, "sell": 0.25}


@pytest.mark.parametrize("late_seconds", [0, 5, 59])
def test_late_midnight_tick_still_settles_yesterday(late_seconds):
    book = {}
    for slot in range(96):
        ep.capture(book, MIDNIGHT + timedelta(minutes=15 * slot), _meter(10), cfg)
    ep.capture(book, MIDNIGHT + timedelta(days=1, seconds=-1), _meter(9, 0.9), cfg)
    tick = MIDNIGHT + timedelta(days=1, seconds=late_seconds)
    ep.capture(book, tick, _meter(9, 0.9), cfg)
    assert book["yesterday"] == {"date": "2026-11-19", "profit": 0.045, "complete": True}
    assert book["date"] == "2026-11-20"


def test_tick_after_the_first_minute_does_not_claim_a_complete_day():
    book = {}
    for slot in range(96):
        ep.capture(book, MIDNIGHT + timedelta(minutes=15 * slot), _meter(10), cfg)
    ep.capture(book, MIDNIGHT + timedelta(days=1, seconds=-1), _meter(9, 0.9), cfg)
    ep.capture(book, MIDNIGHT + timedelta(days=1, minutes=2), _meter(9, 0.9), cfg)
    assert not book["yesterday"]["complete"]


# ── throttled replans from the extra price sources ─────────────────────────


def _throttle_fixture(monkeypatch):
    from custom_components.battery_optimizer.extended import coordinator as coord

    clock = {"now": NOW}
    monkeypatch.setattr(coord.dt_util, "now", lambda: clock["now"])
    timers = []

    def later(hass, delay, action):
        cancel = Mock()
        timers.append((delay, action, cancel))
        return cancel

    monkeypatch.setattr(coord, "async_call_later", later)
    inst = object.__new__(coord.ExtendedBatteryOptimizerCoordinator)
    inst.hass = SimpleNamespace()
    inst._nordpool_state_change = Mock()
    return inst, clock, timers


def test_extra_price_changes_are_throttled_and_coalesced(monkeypatch):
    inst, clock, timers = _throttle_fixture(monkeypatch)
    inst._extra_price_change("e1")
    assert inst._nordpool_state_change.call_count == 1          # leading edge

    clock["now"] = NOW + timedelta(seconds=10)
    for _ in range(5):
        inst._extra_price_change("e2")
    assert inst._nordpool_state_change.call_count == 1          # coalesced
    assert len(timers) == 1
    assert timers[0][0] == pytest.approx(50)

    clock["now"] = NOW + timedelta(seconds=60)
    timers[0][1](None)                                          # trailing run
    assert inst._nordpool_state_change.call_count == 2
    inst._nordpool_state_change.assert_called_with(None)

    clock["now"] = NOW + timedelta(seconds=200)                 # window over
    inst._extra_price_change("e3")
    assert inst._nordpool_state_change.call_count == 3


def test_pending_trailing_run_is_cancelled_on_relisten(monkeypatch):
    inst, clock, timers = _throttle_fixture(monkeypatch)
    inst._extra_price_change("e1")
    clock["now"] = NOW + timedelta(seconds=5)
    inst._extra_price_change("e2")
    cancel = timers[0][2]
    inst._cancel_extra_price_timer()
    cancel.assert_called_once()
    inst._cancel_extra_price_timer()                            # idempotent
    cancel.assert_called_once()


# ── checkpoint sees newly published native Nord Pool curves ────────────────


def test_checkpoint_refreshes_native_curves_before_deciding(monkeypatch):
    from custom_components.battery_optimizer import coordinator as base
    from custom_components.battery_optimizer.extended import coordinator as coord

    order = []
    monkeypatch.setattr(
        base.BatteryOptimizerCoordinator,
        "_checkpoint_callback",
        lambda self, now: order.append("base_checkpoint"),
    )
    inst = object.__new__(coord.ExtendedBatteryOptimizerCoordinator)
    inst._refresh_native_price_forecasts = AsyncMock(
        side_effect=lambda: order.append("refresh")
    )
    tasks = []
    inst.hass = SimpleNamespace(async_create_task=tasks.append)

    inst._uses_native_prices = Mock(return_value=False)
    inst._checkpoint_callback(NOW)
    assert order == ["base_checkpoint"] and not tasks

    order.clear()
    inst._uses_native_prices = Mock(return_value=True)
    inst._checkpoint_callback(NOW)
    assert order == [] and len(tasks) == 1                      # deferred
    asyncio.run(tasks[0])
    assert order == ["refresh", "base_checkpoint"]


# ── optimizer: no redundant probes, visible skip reason ────────────────────


def test_solar_prefill_rerun_does_not_repeat_night_drain_probes(monkeypatch):
    calls = []
    real = opt.optimize

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(opt, "optimize", spy)
    result = sunny_day(0.5)
    assert "solar_prefill" in result.trace
    rerun = [k for k in calls if k.get("_solar_plan") is not None]
    assert len(rerun) == 1
    assert rerun[0]["enable_night_drain"] is False


def test_surplus_routes_compete_with_grid_funding():
    result = sunny_day(0.5)
    assert "solar_prefill" in result.trace
    assert not result.sell_slots


# ── solar-prefill projection must stay in step with optimize() ─────────────


@pytest.mark.parametrize("load", [0.5, 2.5])
def test_prefill_projection_reproduces_the_ordinary_plan(load):
    """_project() re-implements optimize()'s per-slot flows; guard against drift.

    With no edits it must reproduce the plan the ordinary optimizer builds for
    the same inputs (energy, grid import/export per slot).
    """
    from .test_ext_funded_exports import config

    solar_prefill = load_extended_module("solar_prefill")
    # No forced sell: _project() prices the pre-sale plan that optimize() hands
    # to plan_solar_prefill(), not a plan that already contains battery sales.
    cfg = config(base_load_kw=load, fund_grid_exports=False, forced_sell_enabled=False)
    buy, sell, solar = [0.22] * 192, [0.01] * 192, [0.0] * 192
    buy[88:96] = [0.08] * 8
    solar[136:148] = [4.0] * 12
    sell[136:148] = [0.5] * 12
    start, soc = 88, 30
    plan = opt.optimize(
        buy, sell, solar, cfg, initial_soc_pct=soc, start_slot=start,
        total_slots=192, enable_pv_strategy=False,
    )
    flows, _ = solar_prefill._project(
        plan.slots, solar, cfg, start, cfg.capacity_kwh * soc / 100,
        [True] * 192, {},
    )
    for slot, flow in zip(plan.slots[start:], flows):
        assert flow.energy / cfg.capacity_kwh * 100 == pytest.approx(
            slot.soc_after, abs=1e-6
        ), slot.index
        assert flow.imported == pytest.approx(slot.grid_import_kwh, abs=1e-6), slot.index
        assert flow.exported == pytest.approx(slot.grid_export_kwh, abs=1e-6), slot.index


# ── entries saved by the earlier separate-import-export-prices branch ──────


def test_legacy_import_value_means_same_as_import(monkeypatch):
    from custom_components.battery_optimizer.extended import const as xc

    states = {"sensor.market": _forecast_state()}
    inst = _coordinator(monkeypatch, states)
    inst.config_entry.options[xc.CONF_SELL_PRICE_SOURCE] = "import"
    assert inst._parse_export_price_data() == inst._parse_price_data()
    assert inst._parse_export_price_data()[0] == [0.2] * 96


def test_legacy_value_is_normalized_in_the_form():
    pytest.importorskip("homeassistant")
    from custom_components.battery_optimizer.extended import const as xc
    from custom_components.battery_optimizer.extended.config_flow import (
        build_extended_schema,
        validate_extended,
    )

    schema = build_extended_schema({xc.CONF_SELL_PRICE_SOURCE: "import"})
    assert schema({})[xc.CONF_SELL_PRICE_SOURCE] == xc.SELL_PRICE_SOURCE_SAME
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: True))
    values = {xc.CONF_SELL_PRICE_SOURCE: "import"}
    assert validate_extended(hass, values, {}) == {}
    assert values[xc.CONF_SELL_PRICE_SOURCE] == xc.SELL_PRICE_SOURCE_SAME


def test_import_sensor_is_validated_in_the_extended_step():
    pytest.importorskip("homeassistant")
    from custom_components.battery_optimizer import const as c
    from custom_components.battery_optimizer.extended import const as xc
    from custom_components.battery_optimizer.extended.config_flow import (
        validate_extended,
    )

    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda entity: entity == "sensor.market")
    )
    same = {xc.CONF_SELL_PRICE_SOURCE: "same"}
    sensor_import = {c.CONF_PRICE_SOURCE: "sensor", c.CONF_SPOT_SENSOR: "sensor.market"}
    assert validate_extended(hass, dict(same), sensor_import) == {}
    missing = {c.CONF_PRICE_SOURCE: "sensor", c.CONF_SPOT_SENSOR: "sensor.gone"}
    assert validate_extended(hass, dict(same), missing) == {"base": "sensor_not_found"}
    blank = {c.CONF_PRICE_SOURCE: "sensor", c.CONF_SPOT_SENSOR: " "}
    assert validate_extended(hass, dict(same), blank) == {"base": "sensor_not_found"}
    # An Emaldo import source needs no sensor.
    emaldo = {c.CONF_PRICE_SOURCE: "emaldo", c.CONF_SPOT_SENSOR: "sensor.gone"}
    assert validate_extended(hass, dict(same), emaldo) == {}