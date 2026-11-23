"""Automatic base load excludes EV charging from the matching household hours."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")
from homeassistant.components import recorder
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from homeassistant.data_entry_flow import FlowResultType
from custom_components.battery_optimizer import const as c
from custom_components.battery_optimizer.extended import const as xc
from custom_components.battery_optimizer.extended import coordinator as module
from custom_components.battery_optimizer.extended.config_flow import (
    build_extended_schema,
    validate_extended,
)


TOTAL = "sensor.household"
EV = "sensor.charger"
coordinator = module.ExtendedBatteryOptimizerCoordinator


def rows(midnight, days):
    first = int((midnight - timedelta(days=days)).timestamp())
    last = int(midnight.timestamp())
    total, charger = [], []
    for i, hour in enumerate(range(first - first % 3600, last, 3600)):
        power = 11000 if i % 49 < 3 else 0
        total.append({"start": hour, "mean": 600 + power})
        charger.append({"start": hour, "mean": power})
    return total, charger


@pytest.mark.parametrize(("zone", "month", "day", "hours"), [
    ("Europe/Helsinki", 3, 30, 71),
    ("Europe/Helsinki", 10, 26, 73),
    ("Asia/Kolkata", 1, 10, 73),
])
def test_charging_and_local_day_boundaries(zone, month, day, hours):
    midnight = datetime(2026, month, day, tzinfo=ZoneInfo(zone))
    total, charger = rows(midnight, 3)
    assert len(total) == hours
    assert coordinator._net_daily_load_kw(total, charger[::-1], midnight) == [0.6] * 3


@pytest.mark.parametrize(("days", "expected"), [(3, 0.6), (2, 1.0)])
def test_recorder_query_and_fallback(monkeypatch, days, expected):
    midnight = datetime(2026, 1, 10, tzinfo=ZoneInfo("Europe/Helsinki"))
    total, charger = rows(midnight, days + 1)
    # A missing charger hour invalidates the oldest day, even on an idle night.
    charger.pop(4)
    query = AsyncMock(return_value={TOTAL: total, EV: charger})
    monkeypatch.setattr(recorder, "get_instance", lambda hass: SimpleNamespace(
        async_add_executor_job=query))
    monkeypatch.setattr(module.dt_util, "now", lambda: midnight + timedelta(hours=13))
    instance = SimpleNamespace(hass=object(), config={
        c.CONF_BASE_LOAD_KW: 1.0, c.CONF_AUTO_BASE_LOAD: True,
        c.CONF_LOAD_ENERGY_SENSOR: TOTAL, xc.CONF_EXCLUDED_LOAD_SENSOR: EV,
    }, _net_daily_load_kw=coordinator._net_daily_load_kw)
    assert asyncio.run(coordinator._fetch_auto_base_load_kw(instance)) == expected
    args = query.await_args.args
    assert args[2] == midnight - timedelta(days=14)
    assert args[3] == midnight
    assert args[4:] == ({TOTAL, EV}, "hour", {"power": "W"}, {"mean"})

    instance.config.pop(xc.CONF_EXCLUDED_LOAD_SENSOR)
    query.return_value = {TOTAL: [{"mean": 800}] * 7}
    assert asyncio.run(coordinator._fetch_auto_base_load_kw(instance)) == pytest.approx(0.8)
    assert query.await_args.args[3:] == (None, {TOTAL}, "day", None, {"mean"})


def test_optional_sensor_selection():
    states = {name: SimpleNamespace(attributes={"unit_of_measurement": unit})
              for name, unit in [(TOTAL, "W"), (EV, "kW"), ("sensor.energy", "kWh")]}
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    key = xc.CONF_EXCLUDED_LOAD_SENSOR
    base = {c.CONF_LOAD_ENERGY_SENSOR: TOTAL}
    for selected, error in [(EV, None), (TOTAL, "invalid_excluded_load"),
                            ("sensor.energy", "invalid_excluded_load"),
                            ("sensor.missing", "sensor_not_found")]:
        values = {key: selected}
        assert validate_extended(hass, values, base) == ({key: error} if error else {})
    cleared = {}
    assert validate_extended(hass, cleared, base) == {}
    assert cleared[key] == ""


def test_selector_serializes_and_can_be_cleared():
    key = xc.CONF_EXCLUDED_LOAD_SENSOR
    schema = build_extended_schema({key: EV})
    result = FlowManagerIndexView(None)._prepare_result_json(
        {"type": FlowResultType.FORM, "data_schema": schema}
    )
    field = next(item for item in result["data_schema"] if item["name"] == key)
    assert "entity" in field["selector"]
    assert not field.get("required")
    assert schema({key: EV})[key] == EV
    assert key not in schema({})