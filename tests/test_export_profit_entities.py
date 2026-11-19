"""Exercise both diagnostic entity classes with the Home Assistant runtime."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.const import EntityCategory

from custom_components.battery_optimizer.coordinator import BatteryOptimizerCoordinator
from custom_components.battery_optimizer.sensor import ExportProfitSensor


def test_two_diagnostics_keep_dates_currency_and_signed_states(monkeypatch):
    now = datetime(2026, 11, 20, 9, tzinfo=timezone(timedelta(hours=2)))
    monkeypatch.setattr(
        "custom_components.battery_optimizer.coordinator.dt_util.now", lambda: now
    )
    coordinator = MagicMock()
    coordinator.currency = "EUR"
    coordinator._export_book = {
        "currency": "EUR",
        "yesterday": {"date": "2026-11-19", "complete": True, "profit": -0.1234},
    }
    coordinator._last_run = None
    coordinator.export_profit_report.side_effect = lambda yesterday: (
        BatteryOptimizerCoordinator.export_profit_report(coordinator, yesterday)
    )
    entry = SimpleNamespace(entry_id="battery")
    today = ExportProfitSensor(coordinator, entry, yesterday=False)
    yesterday = ExportProfitSensor(coordinator, entry, yesterday=True)
    assert today.unique_id != yesterday.unique_id
    assert yesterday.entity_category == EntityCategory.DIAGNOSTIC
    assert yesterday.native_unit_of_measurement == "EUR"
    assert yesterday.native_value == -0.1234
    assert yesterday.extra_state_attributes["date"] == "2026-11-19"
    assert today.native_value is None
    coordinator._export_book["yesterday"]["complete"] = False
    assert yesterday.native_value is None
