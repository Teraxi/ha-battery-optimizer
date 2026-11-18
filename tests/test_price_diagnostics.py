"""Effective price diagnostics share the planner's normalization and source selection."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import load_coordinator, load_optimizer, load_sensor

coord, const = load_coordinator()
opt, _ = load_optimizer()
sensors = load_sensor()
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone(timedelta(hours=1)))


@pytest.fixture
def coordinator(monkeypatch):
    inst = object.__new__(coord.BatteryOptimizerCoordinator)
    entry = SimpleNamespace(data={}, options={const.CONF_PRICE_SOURCE: 'sensor',
                                              const.CONF_SPOT_SENSOR: 'sensor.prices'})
    inst.config_entry = entry
    inst._entry = entry
    inst._parse_export_price_data = Mock(side_effect=lambda: inst._parse_price_data())
    inst.hass = SimpleNamespace(config=SimpleNamespace(time_zone='Europe/Stockholm'))
    inst._build_battery_config = Mock(return_value=opt.BatteryConfig(
        vat_multiplier=1.255, transfer_fee_buy=0.043, sales_commission=0.003,
        wear_cost_per_kwh=0.03, charge_efficiency=0.9,
    ))
    monkeypatch.setattr(coord.dt_util, 'now', lambda: NOW)
    return inst


@pytest.mark.parametrize('source', ['sensor', 'emaldo'])
def test_diagnostics_read_selected_source_and_current_quarter(coordinator, monkeypatch, source):
    inst = coordinator
    inst.config_entry.options[const.CONF_PRICE_SOURCE] = source
    start = NOW.replace(hour=0)
    # A unique price for each quarter catches incorrect indexing and unit conversion.
    rows = [{'start': (start + timedelta(minutes=15*i)).isoformat(),
             't': (start + timedelta(minutes=15*i)).isoformat(), 'price': i/10}
            for i in range(96)]
    state = SimpleNamespace(state='1', attributes={
        'data': rows, 'schedule': rows, 'unit_of_measurement': 'snt/kWh'})
    inst.hass.states = SimpleNamespace(get=lambda entity_id: state)
    inst._resolve_emaldo_entity = Mock(return_value='sensor.battery_schedule')
    for slot in (0, 48, 95):
        monkeypatch.setattr(coord, '_current_slot_index', lambda: slot)
        assert inst.current_import_price == pytest.approx(slot/1000*1.255 + 0.043)
        assert inst.current_export_price == pytest.approx(slot/1000 - 0.003)


@pytest.mark.parametrize('spot,expected_buy,expected_sell', [
    (0.08, 0.1434, 0.077), (0.0, 0.043, -0.003), (-0.05, -0.007, -0.053),
])
def test_effective_prices_preserve_negative_and_zero_prices(coordinator, spot, expected_buy, expected_sell):
    coordinator._parse_price_data = Mock(return_value=([spot]*96, None))
    assert coordinator.current_import_price == pytest.approx(expected_buy)
    assert coordinator.current_export_price == pytest.approx(expected_sell)


def test_hourly_source_expands_to_current_quarter(coordinator, monkeypatch):
    coordinator._parse_price_data = Mock(return_value=([i/100 for i in range(24)], None))
    monkeypatch.setattr(coord, '_current_slot_index', lambda: 51)
    assert coordinator.current_import_price == pytest.approx(0.12*1.255 + 0.043)
    assert coordinator.current_export_price == pytest.approx(0.117)


@pytest.mark.parametrize('today', [None, []])
def test_missing_today_does_not_reuse_old_cache_or_tomorrow(coordinator, today):
    coordinator._cost_buy_prices = [99.0]*96
    coordinator._cost_sell_prices = [99.0]*96
    coordinator._parse_price_data = Mock(return_value=(today, [0.5]*96))
    assert coordinator.current_import_price is None
    assert coordinator.current_export_price is None
    coordinator._build_battery_config.assert_not_called()


def test_new_day_reads_new_source_instead_of_yesterdays_cache(coordinator):
    coordinator._cost_buy_prices = [99.0]*96
    coordinator._cost_sell_prices = [99.0]*96
    coordinator._parse_price_data = Mock(return_value=([0.1]*96, None))
    assert coordinator.current_import_price == pytest.approx(0.1685)
    assert coordinator.current_export_price == pytest.approx(0.097)
    coordinator._parse_price_data.return_value = ([0.2]*96, None)
    assert coordinator.current_import_price == pytest.approx(0.294)
    assert coordinator.current_export_price == pytest.approx(0.197)


@pytest.mark.parametrize('cls,prop', [
    (sensors.ImportPriceSensor, 'current_import_price'),
    (sensors.ExportPriceSensor, 'current_export_price'),
])
def test_entity_exposes_diagnostic_price_and_active_currency(cls, prop):
    sensor = object.__new__(cls)
    sensor.coordinator = SimpleNamespace(**{prop: -0.003})
    sensor._currency = 'SEK'
    assert sensor.native_value == -0.003
    assert sensor.native_unit_of_measurement == 'SEK/kWh'
    assert sensor._attr_entity_category == sensors.EntityCategory.DIAGNOSTIC
    assert sensor._attr_state_class == sensors.SensorStateClass.MEASUREMENT
    setattr(sensor.coordinator, prop, None)
    assert sensor.native_value is None


def test_price_names_exist_in_all_translations():
    root = Path(__file__).resolve().parents[1]/'custom_components'/'battery_optimizer'
    for path in [root/'strings.json', *(root/'translations').glob('*.json')]:
        data = json.loads(path.read_text(encoding='utf-8'))['entity']['sensor']
        for key in ('import_price', 'export_price'):
            assert data[key]['name'].strip(), (path, key)
