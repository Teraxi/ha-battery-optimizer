"""Engine selection hooks in the shared files and the extended coordinator glue."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

pytest.importorskip("homeassistant")

import custom_components.battery_optimizer as integration  # noqa: E402
from custom_components.battery_optimizer import const as c  # noqa: E402
from custom_components.battery_optimizer import coordinator as base_coord  # noqa: E402
from custom_components.battery_optimizer import optimizer as base_opt  # noqa: E402
from custom_components.battery_optimizer import sensor as base_sensor  # noqa: E402
from custom_components.battery_optimizer.extended import coordinator as ext_coord  # noqa: E402
from custom_components.battery_optimizer.extended import const as xc  # noqa: E402
from custom_components.battery_optimizer.extended import optimizer as ext_opt  # noqa: E402


def entry(data=None, options=None):
    return SimpleNamespace(
        data=data or {}, options=options or {}, entry_id="e1", async_on_unload=Mock()
    )


def test_standard_engine_is_the_default_and_options_win():
    assert integration._selected_engine(entry()) == c.ENGINE_STANDARD
    assert (
        integration._selected_engine(
            entry({c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED})
        )
        == c.ENGINE_EXTENDED
    )
    both = entry(
        {c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED},
        {c.CONF_OPTIMIZER_ENGINE: c.ENGINE_STANDARD},
    )
    assert integration._selected_engine(both) == c.ENGINE_STANDARD


def make_hass():
    async def run(func, *args):
        return func(*args)

    return SimpleNamespace(
        async_add_executor_job=run,
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )


def test_coordinator_class_follows_the_engine(monkeypatch):
    created = []
    monkeypatch.setattr(
        integration,
        "BatteryOptimizerCoordinator",
        lambda hass, e: created.append("standard") or "std",
    )
    monkeypatch.setattr(
        ext_coord.ExtendedBatteryOptimizerCoordinator,
        "__init__",
        lambda self, hass, e: created.append("extended") or None,
    )
    hass = make_hass()
    assert asyncio.run(integration._async_create_coordinator(hass, entry())) == "std"
    result = asyncio.run(
        integration._async_create_coordinator(
            hass, entry({c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED})
        )
    )
    assert isinstance(result, ext_coord.ExtendedBatteryOptimizerCoordinator)
    assert created == ["standard", "extended"]
    assert result.engine == c.ENGINE_EXTENDED
    assert getattr(base_coord.BatteryOptimizerCoordinator, "engine", None) is None


def test_changing_engine_reloads_instead_of_refreshing_listeners():
    hass = make_hass()
    coordinator = SimpleNamespace(
        engine=c.ENGINE_STANDARD,
        async_setup_listeners=Mock(),
        run_optimizer=AsyncMock(),
        _ha_started=True,
    )
    hass.data = {c.DOMAIN: {"e1": coordinator}}
    changed = entry(options={c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED})
    asyncio.run(integration._async_options_updated(hass, changed))
    hass.config_entries.async_reload.assert_awaited_once_with("e1")
    coordinator.async_setup_listeners.assert_not_called()
    coordinator.run_optimizer.assert_not_awaited()

    hass.config_entries.async_reload.reset_mock()
    asyncio.run(integration._async_options_updated(hass, entry()))
    hass.config_entries.async_reload.assert_not_awaited()
    coordinator.async_setup_listeners.assert_called_once()


def test_extended_config_is_a_superset_of_the_standard_config(monkeypatch):
    base = base_opt.BatteryConfig(capacity_kwh=13.5, soc_min=22, wear_cost_per_kwh=0.04)
    monkeypatch.setattr(
        base_coord.BatteryOptimizerCoordinator,
        "_build_battery_config",
        lambda self: base,
    )
    inst = object.__new__(ext_coord.ExtendedBatteryOptimizerCoordinator)
    inst._entry = SimpleNamespace(
        data={},
        options={xc.CONF_FUND_GRID_EXPORTS: True, c.CONF_SOLAR_SELL_MIN_FORECAST_KWH: 7.5},
    )
    inst.config_entry = inst._entry
    assert inst.config[xc.CONF_FUND_GRID_EXPORTS] is True  # property over the entry
    cfg = inst._build_battery_config()
    assert isinstance(cfg, ext_opt.BatteryConfig)
    assert not isinstance(cfg, base_opt.BatteryConfig)
    assert (cfg.capacity_kwh, cfg.soc_min, cfg.wear_cost_per_kwh) == (13.5, 22, 0.04)
    assert cfg.fund_grid_exports is True
    assert cfg.pv_min_forecast_kwh == 7.5
    assert cfg.transfer_fees == []


def test_sensor_hook_adds_entities_only_for_the_extended_engine():
    added = []
    standard = MagicMock(spec=base_coord.BatteryOptimizerCoordinator)
    standard.hass = SimpleNamespace(data={})
    extended = MagicMock()
    extended.extra_sensors = Mock(return_value=["extra-1", "extra-2"])
    for coordinator in (standard, extended):
        coordinator._device_subentry_id = Mock(return_value="sub")
        coordinator.currency = "EUR"
    hass = SimpleNamespace(data={c.DOMAIN: {"std": standard, "ext": extended}})

    def add(entities, **kwargs):
        added.append((list(entities), kwargs))

    asyncio.run(base_sensor.async_setup_entry(hass, entry_with_id("std"), add))
    assert len(added) == 1  # only the standard list
    asyncio.run(base_sensor.async_setup_entry(hass, entry_with_id("ext"), add))
    assert len(added) == 3
    assert added[-1] == (["extra-1", "extra-2"], {"config_subentry_id": "sub"})
    extended.extra_sensors.assert_called_once()


def entry_with_id(entry_id):
    return SimpleNamespace(entry_id=entry_id, data={}, options={})


def test_extended_sensor_list_matches_translation_keys():
    from custom_components.battery_optimizer.extended import sensor as ext_sensor

    coordinator = MagicMock()
    coordinator.currency = "EUR"
    sensors = ext_sensor.extended_sensors(coordinator, entry_with_id("e1"))
    keys = {s._attr_translation_key for s in sensors}
    assert keys == {
        "export_profit_forecast_today",
        "export_profit_yesterday",
        "import_price",
        "export_price",
        "current_transfer_fee",
        "active_sale",
    }
    assert len({s._attr_unique_id for s in sensors}) == len(sensors)


def test_active_sale_sensor_reports_the_trace_decision():
    from custom_components.battery_optimizer.extended import sensor as ext_sensor

    coordinator = MagicMock()
    coordinator.currency = "EUR"
    sensor = ext_sensor.ActiveSaleSensor(coordinator, entry_with_id("e1"))
    coordinator.last_result = None
    assert sensor.native_value is None and sensor.extra_state_attributes == {}
    coordinator.last_result = SimpleNamespace(
        trace={"active_sale": {"decision": "continue", "advantage": 0.12}}
    )
    assert sensor.native_value == "continue"
    assert sensor.extra_state_attributes["advantage"] == 0.12


def test_importing_the_integration_does_not_load_the_extended_engine():
    """The default path must not import the extended optimizer or coordinator."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import custom_components.battery_optimizer as m;"
        "import custom_components.battery_optimizer.config_flow;"
        "loaded = [n for n in sys.modules if n.startswith("
        "'custom_components.battery_optimizer.extended')];"
        "print(','.join(sorted(loaded)))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    loaded = set(filter(None, out.split(",")))
    heavy = {n for n in loaded if n.rsplit(".", 1)[-1] in {"coordinator", "optimizer", "sensor"}}
    assert not heavy, heavy