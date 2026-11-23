"""Engine selection and the extended step of the configuration flow."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.battery_optimizer import config_flow as flow  # noqa: E402
from custom_components.battery_optimizer import const as c  # noqa: E402
from custom_components.battery_optimizer.extended import const as xc  # noqa: E402
from custom_components.battery_optimizer.extended.config_flow import (  # noqa: E402
    build_extended_schema,
    validate_extended,
)
from custom_components.battery_optimizer.money import locale_money  # noqa: E402

MONEY = locale_money(
    override="", ha_currency="EUR", time_zone="Europe/Helsinki", country="FI"
)


def serialized(schema):
    result = FlowManagerIndexView(None)._prepare_result_json(
        {"type": FlowResultType.FORM, "data_schema": schema}
    )
    return {f["name"]: f for f in json.loads(json.dumps(result))["data_schema"]}


def main_schema(defaults=None):
    return flow._build_schema(
        defaults, emaldo_options={"battery": "Battery"}, money=MONEY
    )


def test_standard_engine_is_the_default_and_extended_fields_stay_out():
    schema = main_schema()
    fields = serialized(schema)
    assert fields[c.CONF_OPTIMIZER_ENGINE]["default"] == c.ENGINE_STANDARD
    assert [o[0] for o in fields[c.CONF_OPTIMIZER_ENGINE]["options"]] == [
        c.ENGINE_STANDARD,
        c.ENGINE_EXTENDED,
    ]
    for key in xc.EXTENDED_CONF_KEYS:
        assert key not in fields, f"{key} belongs to the extended step"
    assert schema({})[c.CONF_OPTIMIZER_ENGINE] == c.ENGINE_STANDARD


def test_extended_schema_defaults_and_serialization():
    schema = build_extended_schema()
    fields = serialized(schema)
    assert fields[xc.CONF_SELL_PRICE_SOURCE]["default"] == xc.SELL_PRICE_SOURCE_SAME
    assert [o[0] for o in fields[xc.CONF_SELL_PRICE_SOURCE]["options"]] == [
        xc.SELL_PRICE_SOURCE_SAME,
        "emaldo",
        "sensor",
    ]
    assert fields[xc.CONF_TRANSFER_FEE_SENSOR]["default"] == ""
    assert fields[xc.CONF_SELL_PRICE_SENSOR]["default"] == ""
    assert fields[xc.CONF_FUND_GRID_EXPORTS]["default"] is False
    assert "static_price" not in fields
    values = schema({})
    assert values[xc.CONF_SELL_PRICE_SOURCE] == xc.SELL_PRICE_SOURCE_SAME


@pytest.mark.parametrize("source", ["same", "emaldo", "sensor"])
def test_export_selector_roundtrip(source):
    defaults = {
        xc.CONF_SELL_PRICE_SOURCE: source,
        xc.CONF_SELL_PRICE_SENSOR: "sensor.market",
    }
    fields = serialized(build_extended_schema(defaults))
    assert fields[xc.CONF_SELL_PRICE_SOURCE]["default"] == source


def test_sell_sensor_and_tariff_validation():
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity: entity in ("input_number.contract", "sensor.tariff")
        )
    )
    base = {}
    for source in ("same", "emaldo"):
        values = {
            xc.CONF_SELL_PRICE_SOURCE: source,
            xc.CONF_SELL_PRICE_SENSOR: "sensor.missing",
        }
        assert validate_extended(hass, values, base) == {}
    values = {
        xc.CONF_SELL_PRICE_SOURCE: "sensor",
        xc.CONF_SELL_PRICE_SENSOR: "sensor.missing",
    }
    assert validate_extended(hass, values, base) == {
        xc.CONF_SELL_PRICE_SENSOR: "sensor_not_found"
    }
    values[xc.CONF_SELL_PRICE_SENSOR] = ""
    assert xc.CONF_SELL_PRICE_SENSOR in validate_extended(hass, values, base)
    values[xc.CONF_SELL_PRICE_SENSOR] = " input_number.contract "
    assert validate_extended(hass, values, base) == {}
    assert values[xc.CONF_SELL_PRICE_SENSOR] == "input_number.contract"

    assert validate_extended(hass, {xc.CONF_SELL_PRICE_SOURCE: "same"}, base) == {}
    assert validate_extended(
        hass,
        {xc.CONF_SELL_PRICE_SOURCE: "same", xc.CONF_TRANSFER_FEE_SENSOR: "sensor.tariff"},
        base,
    ) == {}
    assert validate_extended(
        hass,
        {xc.CONF_SELL_PRICE_SOURCE: "same", xc.CONF_TRANSFER_FEE_SENSOR: "sensor.nope"},
        base,
    ) == {xc.CONF_TRANSFER_FEE_SENSOR: "sensor_not_found"}


def make_config_flow(monkeypatch):
    f = object.__new__(flow.BatteryOptimizerConfigFlow)
    f.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: True))
    f.async_set_unique_id = AsyncMock()
    f._abort_if_unique_id_configured = Mock()
    f.async_create_entry = Mock(return_value={"type": "create_entry"})
    f.async_show_form = Mock(return_value={"type": "form"})
    monkeypatch.setattr(flow, "_get_emaldo_options", lambda hass: {})
    monkeypatch.setattr(flow, "_locale_money_for", lambda hass, data: MONEY)
    return f


@pytest.mark.asyncio
async def test_standard_engine_creates_the_entry_directly(monkeypatch):
    f = make_config_flow(monkeypatch)
    user = {c.CONF_OPTIMIZER_ENGINE: c.ENGINE_STANDARD}
    await f.async_step_user(user)
    f.async_create_entry.assert_called_once_with(title="Battery Optimizer", data=user)


@pytest.mark.asyncio
async def test_extended_engine_continues_with_the_extended_step(monkeypatch):
    f = make_config_flow(monkeypatch)
    user = {c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED}
    await f.async_step_user(user)
    f.async_create_entry.assert_not_called()
    assert f.async_show_form.call_args.kwargs["step_id"] == "extended"

    f.async_show_form.reset_mock()
    bad = {xc.CONF_SELL_PRICE_SOURCE: "sensor", xc.CONF_SELL_PRICE_SENSOR: ""}
    f.hass.states = SimpleNamespace(get=lambda entity: None)
    await f.async_step_extended(bad)
    f.async_create_entry.assert_not_called()
    assert f.async_show_form.call_args.kwargs["errors"] == {
        xc.CONF_SELL_PRICE_SENSOR: "sensor_not_found"
    }

    good = {xc.CONF_SELL_PRICE_SOURCE: "same", xc.CONF_FUND_GRID_EXPORTS: True}
    await f.async_step_extended(good)
    data = f.async_create_entry.call_args.kwargs["data"]
    assert data[c.CONF_OPTIMIZER_ENGINE] == c.ENGINE_EXTENDED
    assert data[xc.CONF_FUND_GRID_EXPORTS] is True
    assert data[xc.CONF_SELL_PRICE_SOURCE] == "same"


@pytest.mark.asyncio
async def test_options_flow_routes_through_the_extended_step(monkeypatch):
    f = object.__new__(flow.BatteryOptimizerOptionsFlow)
    entry = SimpleNamespace(
        data={c.CONF_OPTIMIZER_ENGINE: c.ENGINE_STANDARD}, options={}
    )
    monkeypatch.setattr(
        flow.BatteryOptimizerOptionsFlow, "config_entry", entry, raising=False
    )
    f.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: True))
    f.async_create_entry = Mock(return_value={"type": "create_entry"})
    f.async_show_form = Mock(return_value={"type": "form"})
    monkeypatch.setattr(flow, "_get_emaldo_options", lambda hass: {})
    monkeypatch.setattr(flow, "_locale_money_for", lambda hass, data: MONEY)

    await f.async_step_init({c.CONF_OPTIMIZER_ENGINE: c.ENGINE_STANDARD})
    f.async_create_entry.assert_called_once_with(
        title="", data={c.CONF_OPTIMIZER_ENGINE: c.ENGINE_STANDARD}
    )

    f.async_create_entry.reset_mock()
    await f.async_step_init({c.CONF_OPTIMIZER_ENGINE: c.ENGINE_EXTENDED})
    f.async_create_entry.assert_not_called()
    assert f.async_show_form.call_args.kwargs["step_id"] == "extended"
    await f.async_step_extended({xc.CONF_SELL_PRICE_SOURCE: "same"})
    data = f.async_create_entry.call_args.kwargs["data"]
    assert data[c.CONF_OPTIMIZER_ENGINE] == c.ENGINE_EXTENDED
    assert data[xc.CONF_SELL_PRICE_SOURCE] == "same"


def test_extended_translations_exist_in_every_file():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components" / "battery_optimizer"
    for path in [root / "strings.json", *(root / "translations").glob("*.json")]:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for where in (doc["config"], doc["options"]):
            step = where["step"]["extended"]
            for key in xc.EXTENDED_CONF_KEYS:
                assert step["data"][key].strip(), (path, key)
            assert "invalid_excluded_load" in where["error"], path
        for tree in (doc["config"]["step"]["user"], doc["options"]["step"]["init"]):
            assert tree["data"][c.CONF_OPTIMIZER_ENGINE].strip(), path
        sensors = doc["entity"]["sensor"]
        for key in (
            "import_price", "export_price", "current_transfer_fee", "active_sale",
            "export_profit_forecast_today", "export_profit_yesterday",
        ):
            assert sensors[key]["name"].strip(), (path, key)