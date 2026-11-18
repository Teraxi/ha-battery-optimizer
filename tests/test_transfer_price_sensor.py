"""External grid tariffs, including rolling production-style forecasts."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from . import load_coordinator, load_optimizer

coord, c = load_coordinator()
opt, _ = load_optimizer()
NOW = datetime(2026, 11, 14, 17, 30, tzinfo=timezone(timedelta(hours=2)))


def tariff(tomorrow=True):
    rows = []
    for i in range(192 if tomorrow else 26):
        start = NOW + timedelta(minutes=15 * i)
        rows.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=15)).isoformat(),
                "price": 0.043
                if start.date() == NOW.date() and start.hour < 22
                else 0.022,
            }
        )
    return SimpleNamespace(
        state="0.043", attributes={"unit_of_measurement": "EUR/kWh", "data": rows}
    )


@pytest.fixture
def instance(monkeypatch):
    monkeypatch.setattr(coord.dt_util, "now", lambda: NOW)
    inst = object.__new__(coord.BatteryOptimizerCoordinator)
    inst.config_entry = SimpleNamespace(
        data={}, options={c.CONF_TRANSFER_FEE_SENSOR: "sensor.tariff"}
    )
    inst._entry = inst.config_entry
    states = {"sensor.tariff": tariff()}
    inst.hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        config=SimpleNamespace(time_zone="Europe/Helsinki"),
        async_add_executor_job=AsyncMock(return_value=None),
    )
    inst._build_battery_config = Mock(
        return_value=opt.BatteryConfig(
            vat_multiplier=1.255, transfer_fee_buy=0.01, sales_commission=0.003
        )
    )
    inst._parse_price_data = Mock(return_value=([0.08] * 96, [0.08] * 96))
    return inst, states


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


class ReachedPlanner(Exception):
    """Capture inputs before any device control."""


@pytest.mark.parametrize("tomorrow", [True, False])
def test_tariffs_reach_planner_and_cost_cache(instance, monkeypatch, tomorrow):
    inst, states = instance
    prepare(inst)
    states["sensor.tariff"] = tariff(tomorrow)
    planner = Mock(side_effect=ReachedPlanner)
    monkeypatch.setattr(coord, "optimize", planner)
    with pytest.raises(ReachedPlanner):
        asyncio.run(inst.run_optimizer())
    buy, sell, solar, cfg = planner.call_args.args
    assert len(buy) == (192 if tomorrow else 96)
    assert buy[70:88] == pytest.approx([0.1434] * 18)
    assert buy[88:] == pytest.approx([0.1224] * (len(buy) - 88))
    assert sell == pytest.approx([0.077] * len(sell))
    assert cfg.transfer_fees[88:] == [0.022] * (len(buy) - 88)
    assert inst._cost_buy_prices == buy[:96]
    result = opt.optimize(
        buy, sell, solar, cfg, total_slots=len(buy), initial_soc_pct=80
    )
    for slot in result.slots:
        assert slot.import_transfer_cost == pytest.approx(
            slot.grid_import_kwh * cfg.transfer_fees[slot.index]
        )


@pytest.mark.parametrize(
    "unit,value", [("EUR/kWh", "0.022"), ("snt/kWh", "2.2"), ("EUR/MWh", "22")]
)
def test_numeric_tariff_units(unit, value):
    today, tomorrow = coord.parse_sensor_prices(
        value, {"unit_of_measurement": unit}, NOW, complete=True
    )
    assert today == pytest.approx([0.022] * 96)
    assert tomorrow == pytest.approx([0.022] * 96)


@pytest.mark.parametrize("state", ["unknown", "unavailable", "nan", "inf", ""])
def test_invalid_scalar_is_not_a_zero_tariff(state):
    assert coord.parse_sensor_prices(state, {}, NOW, complete=True) == (None, None)


def test_holes_and_empty_forecasts_never_fall_back(instance, monkeypatch):
    inst, states = instance
    prepare(inst)
    del states["sensor.tariff"].attributes["data"][4]
    assert inst._parse_transfer_prices()[0] is None
    planner = Mock()
    monkeypatch.setattr(coord, "optimize", planner)
    assert asyncio.run(inst.run_optimizer()) is None
    planner.assert_not_called()
    assert inst.current_transfer_fee is None
    states["sensor.tariff"].attributes["data"] = []
    assert inst._parse_transfer_prices() == (None, None)


def test_unknown_tomorrow_and_live_diagnostic(instance, monkeypatch):
    inst, states = instance
    monkeypatch.setattr(coord, "_current_slot_index", lambda: 88)
    assert inst.current_transfer_fee == 0.022
    states["sensor.tariff"] = tariff(False)
    assert inst._parse_transfer_prices()[1] is None
    assert not inst._has_tomorrow_prices()


def test_fixed_fee_default_stays_unchanged(instance):
    inst, _ = instance
    inst.config_entry.options = {c.CONF_TRANSFER_FEE_BUY: 0.05}
    inst._locale = SimpleNamespace(transfer_fee_buy=0.022)
    assert inst.current_transfer_fee == 0.05
    cfg = opt.BatteryConfig(
        vat_multiplier=1.255, transfer_fee_buy=0.05, sales_commission=0.003
    )
    buy, sell = opt.compute_prices([0.08] * 96, cfg)
    assert buy == pytest.approx([0.1504] * 96)
    assert sell == pytest.approx([0.077] * 96)


def test_utc_intervals_and_end_boundary():
    state = tariff()
    for row in state.attributes["data"]:
        row["start"] = (
            datetime.fromisoformat(row["start"]).astimezone(timezone.utc).isoformat()
        )
        row["end"] = (
            datetime.fromisoformat(row["end"]).astimezone(timezone.utc).isoformat()
        )
    today, tomorrow = coord.parse_sensor_prices(
        state.state, state.attributes, NOW, complete=True
    )
    assert today[87:89] == [0.043, 0.022]
    assert tomorrow == [0.022] * 96
