"""Independent contract import and market export prices."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from . import load_coordinator, load_optimizer

coord, c = load_coordinator()
opt, _ = load_optimizer()
NOW = datetime(2026, 11, 15, 6, tzinfo=timezone(timedelta(hours=1)))


def forecast(tomorrow=True):
    start = NOW.replace(hour=0)
    return SimpleNamespace(
        state="20",
        attributes={
            "unit_of_measurement": "snt/kWh",
            "data": [
                {
                    "start": (start + timedelta(minutes=15 * i)).isoformat(),
                    "price": 20 if i < 96 else 30,
                }
                for i in range(192 if tomorrow else 96)
            ],
        },
    )


@pytest.fixture
def instance(monkeypatch):
    monkeypatch.setattr(coord.dt_util, "now", lambda: NOW)
    inst = object.__new__(coord.BatteryOptimizerCoordinator)
    inst.config_entry = SimpleNamespace(
        data={},
        options={
            c.CONF_PRICE_SOURCE: "sensor",
            c.CONF_SPOT_SENSOR: "input_number.contract",
            c.CONF_SELL_PRICE_SOURCE: "sensor",
            c.CONF_SELL_PRICE_SENSOR: "sensor.export",
        },
    )
    inst._entry = inst.config_entry
    states = {
        "input_number.contract": SimpleNamespace(state="0.08", attributes={}),
        "sensor.export": forecast(),
    }
    inst.hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        config=SimpleNamespace(time_zone="Europe/Stockholm"),
        async_add_executor_job=AsyncMock(return_value=None),
    )
    inst._build_battery_config = Mock(
        return_value=opt.BatteryConfig(
            vat_multiplier=1.255, transfer_fee_buy=0.022, sales_commission=0.003
        )
    )
    inst._resolve_emaldo_entity = Mock(return_value="sensor.battery_schedule")
    inst._locale = SimpleNamespace(currency="EUR")
    monkeypatch.setattr(coord.er, "async_get", lambda hass: None)
    return inst, states


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        ("0.08", "", 0.08),
        ("8", "snt/kWh", 0.08),
        ("80", "EUR/MWh", 0.08),
        ("0", "", 0),
        ("-0.01", "EUR/kWh", -0.01),
    ],
)
def test_fixed_helpers(instance, value, unit, expected):
    inst, states = instance
    states["input_number.contract"] = SimpleNamespace(
        state=value, attributes={"unit_of_measurement": unit}
    )
    assert inst._parse_price_data() == ([expected] * 96, [expected] * 96)


@pytest.mark.parametrize(
    "value", ["unknown", "unavailable", "nan", "inf", "-inf", "", "not-a-price"]
)
def test_invalid_helper_is_not_free_energy(instance, value):
    inst, states = instance
    states["input_number.contract"].state = value
    assert inst._parse_price_data() == (None, None)


def test_empty_forecast_is_not_a_fixed_contract(instance):
    inst, states = instance
    states["input_number.contract"].attributes["data"] = []
    assert inst._parse_price_data() == (None, None)


def test_default_export_is_emaldo(instance):
    inst, _ = instance
    inst.config_entry.options.pop(c.CONF_SELL_PRICE_SOURCE)
    inst._parse_emaldo_price_data = Mock(return_value=([0.2] * 96, None))
    assert inst._parse_export_price_data() == ([0.2] * 96, None)
    inst._parse_emaldo_price_data.assert_called_once_with(allow_fallback=False)


def test_emaldo_export_no_import_fallback(instance):
    inst, states = instance
    inst.config_entry.options[c.CONF_SELL_PRICE_SOURCE] = "emaldo"
    assert inst._parse_export_price_data() == (None, None)
    start = NOW.replace(hour=0)
    states["sensor.battery_schedule"] = SimpleNamespace(
        state="-1",
        attributes={
            "schedule": [
                {
                    "t": (start + timedelta(minutes=15 * i)).isoformat(),
                    "price": 20 if i < 96 else 30,
                }
                for i in range(192)
            ]
        },
    )
    today, tomorrow = inst._parse_export_price_data()
    assert today == [0.2] * 96 and tomorrow == [0.3] * 96
    assert inst._parse_price_data()[0] == [0.08] * 96


class ReachedPlanner(Exception):
    pass


def prepare(inst):
    for name in (
        "_maybe_sweep_expired_rules",
        "_restore_runtime_state",
        "_restore_sell_back_to_grid_after_restart",
        "_capture_run_snapshot",
    ):
        setattr(inst, name, AsyncMock())
    inst._get_battery_soc = Mock(return_value=80.0)
    inst._resolve_solar_scale = Mock()
    inst._get_solcast_forecast = Mock(return_value=[0.0] * 96)
    inst._fetch_auto_base_load_kw = AsyncMock(return_value=None)
    inst._actual_soc = []
    inst._compute_plan_accuracy = Mock(return_value=None)
    inst._resolve_solar_regime = Mock()
    inst._read_emaldo_internal_modes = Mock(return_value=None)
    inst._read_user_rules = Mock(return_value=[])
    inst._last_result_tomorrow = None
    inst._pv_strategy_enabled = False
    inst._solar_confidence_ratio_for_plan = Mock(return_value=None)
    inst._get_solar_confidence_ratio = Mock(return_value=None)


@pytest.mark.parametrize("tomorrow", [True, False])
@pytest.mark.parametrize("source", ["sensor", "emaldo"])
def test_real_planner_receives_independent_prices(
    instance, monkeypatch, tomorrow, source
):
    inst, states = instance
    prepare(inst)
    inst.config_entry.options[c.CONF_SELL_PRICE_SOURCE] = source
    states["sensor.export"] = forecast(tomorrow)
    inst._parse_emaldo_price_data = Mock(
        return_value=([0.2] * 96, [0.3] * 96 if tomorrow else None)
    )
    planner = Mock(side_effect=ReachedPlanner)
    monkeypatch.setattr(coord, "optimize", planner)
    with pytest.raises(ReachedPlanner):
        asyncio.run(inst.run_optimizer())
    buy, sell, solar, cfg = planner.call_args.args
    has_tomorrow = tomorrow
    assert len(buy) == len(sell) == (192 if has_tomorrow else 96)
    assert buy == pytest.approx([0.1224] * len(buy))
    assert sell[:96] == pytest.approx([0.197] * 96)
    if has_tomorrow:
        assert sell[96:] == pytest.approx([0.297] * 96)
    assert inst._cost_buy_prices == buy[:96]
    assert inst._cost_sell_prices == sell[:96]
    assert inst._has_tomorrow_prices() is has_tomorrow


def test_missing_export_blocks_planning(instance, monkeypatch):
    inst, states = instance
    prepare(inst)
    del states["sensor.export"]
    planner = Mock()
    monkeypatch.setattr(coord, "optimize", planner)
    assert asyncio.run(inst.run_optimizer()) is None
    planner.assert_not_called()
    assert inst._capture_run_snapshot.call_args.args[0]["state"] == "no_prices"


def test_explicit_invalid_tomorrow_is_respected(instance):
    inst, states = instance
    states["sensor.export"].attributes["tomorrow_valid"] = False
    assert not inst._has_tomorrow_prices()


@pytest.mark.parametrize("source", ["sensor", "emaldo"])
def test_active_export_changes_trigger_replanning(instance, monkeypatch, source):
    inst, _ = instance
    inst.config_entry.options[c.CONF_SELL_PRICE_SOURCE] = source
    inst._unsub_listeners = []
    inst._unsub_guard = None
    inst._ha_started = True
    for name in (
        "_find_balancing_sensor",
        "_effective_grid_import_sensor",
        "_effective_grid_export_sensor",
    ):
        setattr(inst, name, Mock(return_value=None))
    inst._resolve_emaldo_entity = Mock(
        side_effect=lambda key: (
            "sensor.battery_schedule" if key == "schedule_chart" else None
        )
    )
    inst._schedule_startup_run = Mock()
    inst._restore_manual_selling_after_restart = Mock(return_value="restore")
    inst.hass.async_create_task = Mock()
    tracker = Mock(return_value=Mock())
    monkeypatch.setattr(coord, "async_track_state_change_event", tracker)
    monkeypatch.setattr(coord, "async_track_time_change", lambda *a, **kw: Mock())
    monkeypatch.setattr(coord, "async_track_time_interval", lambda *a, **kw: Mock())
    inst.async_setup_listeners()
    watched = {
        entity: call.args[2]
        for call in tracker.call_args_list
        for entity in call.args[1]
    }
    assert "input_number.contract" in watched
    entity = {
        "sensor": "sensor.export",
        "emaldo": "sensor.battery_schedule",
    }[source]
    assert entity in watched
    if source != "sensor":
        assert "sensor.export" not in watched
    inst.run_optimizer = Mock(return_value="run")
    inst._async_check_locale_money = Mock(return_value="check")
    watched[entity](None)
    inst.run_optimizer.assert_called_once_with(reason="nordpool_update", force=True)


def native_registry(inst, monkeypatch, *, area="FI", unique_id="FI-current_price"):
    entity = SimpleNamespace(
        platform="nordpool", config_entry_id="market-entry", unique_id=unique_id
    )
    registry = SimpleNamespace(
        async_get=lambda entity_id: entity if entity_id == "sensor.export" else None
    )
    monkeypatch.setattr(coord.er, "async_get", lambda hass: registry)
    inst.hass.config_entries = SimpleNamespace(
        async_get_entry=lambda entry_id: SimpleNamespace(data={"areas": [area]})
    )


@pytest.mark.parametrize("area", ["FI", "SE3"])
def test_native_nordpool_units_dates_and_no_scalar_fallback(
    instance, monkeypatch, area
):
    inst, states = instance
    states["sensor.export"] = SimpleNamespace(
        state="999", attributes={"unit_of_measurement": "EUR/kWh"}
    )
    native_registry(inst, monkeypatch, area=area, unique_id=area + "-current_price")
    assert inst._parse_export_price_data() == (
        None,
        None,
    )  # Not fetched yet, never use 999.

    async def service(domain, action, data, **kwargs):
        assert domain == "nordpool" and action == "get_prices_for_date"
        assert data["areas"] == [area] and data["config_entry"] == "market-entry"
        assert data["currency"] == "EUR"
        assert kwargs == {"blocking": True, "return_response": True}
        start = datetime.fromisoformat(data["date"]).replace(tzinfo=NOW.tzinfo)
        return {
            area: [
                {
                    "start": (start + timedelta(minutes=15 * i)).isoformat(),
                    "end": (start + timedelta(minutes=15 * (i + 1))).isoformat(),
                    "price": float(i),
                }
                for i in range(96)
            ]
        }

    inst.hass.services = SimpleNamespace(async_call=AsyncMock(side_effect=service))
    asyncio.run(inst._refresh_native_price_forecasts())
    today, tomorrow = inst._parse_export_price_data()
    assert today == pytest.approx([i / 1000 for i in range(96)])
    assert tomorrow == today
    assert inst.hass.services.async_call.await_count == 3
    assert {
        call.args[2]["date"] for call in inst.hass.services.async_call.call_args_list
    } == {"2026-11-14", "2026-11-15", "2026-11-16"}
    _, sell = opt.compute_prices(today, inst._build_battery_config())
    assert sell[70] == pytest.approx(0.067)
    inst.hass.services.async_call = AsyncMock(side_effect=RuntimeError("not published"))
    asyncio.run(inst._refresh_native_price_forecasts())
    assert inst._parse_export_price_data() == (None, None)


def test_native_nordpool_does_not_reuse_previous_date(instance, monkeypatch):
    inst, states = instance
    states["sensor.export"] = SimpleNamespace(state="0.025", attributes={})
    native_registry(inst, monkeypatch)
    inst._native_price_forecast_date = (NOW - timedelta(days=1)).date()
    inst._native_price_forecasts = {"sensor.export": forecast().attributes["data"]}
    assert inst._parse_export_price_data() == (None, None)


def test_native_unresolved_market_never_becomes_fixed_price(instance, monkeypatch):
    inst, states = instance
    states["sensor.export"] = SimpleNamespace(state="0.025", attributes={})
    native_registry(inst, monkeypatch, unique_id="unknown-format")
    inst.hass.services = SimpleNamespace(async_call=AsyncMock())
    asyncio.run(inst._refresh_native_price_forecasts())
    inst.hass.services.async_call.assert_not_called()
    assert inst._parse_export_price_data() == (None, None)


def test_fixed_import_market_export_and_variable_tariff_together(instance, monkeypatch):
    inst, states = instance
    prepare(inst)
    start = NOW.replace(hour=0)
    inst.config_entry.options[c.CONF_TRANSFER_FEE_SENSOR] = "sensor.tariff"
    states["sensor.tariff"] = SimpleNamespace(
        state="0.022",
        attributes={
            "data": [
                {
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=7)).isoformat(),
                    "price": 0.022,
                },
                {
                    "start": (start + timedelta(hours=7)).isoformat(),
                    "end": (start + timedelta(hours=22)).isoformat(),
                    "price": 0.043,
                },
                {
                    "start": (start + timedelta(hours=22)).isoformat(),
                    "end": (start + timedelta(days=2)).isoformat(),
                    "price": 0.022,
                },
            ]
        },
    )
    planner = Mock(side_effect=ReachedPlanner)
    monkeypatch.setattr(coord, "optimize", planner)
    with pytest.raises(ReachedPlanner):
        asyncio.run(inst.run_optimizer())
    buy, sell, solar, cfg = planner.call_args.args
    assert len(buy) == 192
    assert buy[27:29] == pytest.approx([0.1224, 0.1434])
    assert buy[88:] == pytest.approx([0.1224] * 104)
    assert sell[:96] == pytest.approx([0.197] * 96)
    assert sell[96:] == pytest.approx([0.297] * 96)
    assert inst._cost_buy_prices == buy[:96]
    assert inst._cost_sell_prices == sell[:96]


def test_combined_diagnostics_use_independent_sources_and_tariff(instance):
    inst, states = instance
    inst.config_entry.options[c.CONF_TRANSFER_FEE_SENSOR] = "sensor.tariff"
    states["sensor.tariff"] = SimpleNamespace(state="0.043", attributes={})
    assert inst.current_import_price == pytest.approx(0.1434)
    assert inst.current_export_price == pytest.approx(0.197)
    states["sensor.tariff"].state = "unavailable"
    assert inst.current_import_price is None
    assert inst.current_export_price == pytest.approx(0.197)
    del states["sensor.export"]
    assert inst.current_export_price is None
