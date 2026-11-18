"""Tariff entity field serializes through Home Assistant's real frontend path."""

import json
from types import SimpleNamespace
import pytest

pytest.importorskip("homeassistant")
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from homeassistant.data_entry_flow import FlowResultType
from custom_components.battery_optimizer import config_flow as flow, const as c
from custom_components.battery_optimizer.money import locale_money


def test_transfer_schema_and_validation():
    money = locale_money(
        override="", ha_currency="EUR", time_zone="Europe/Helsinki", country="FI"
    )
    schema = flow._build_schema(
        None, emaldo_options={"battery": "Battery"}, money=money
    )
    response = FlowManagerIndexView(None)._prepare_result_json(
        {"type": FlowResultType.FORM, "data_schema": schema}
    )
    fields = {f["name"]: f for f in json.loads(json.dumps(response))["data_schema"]}
    assert fields[c.CONF_TRANSFER_FEE_SENSOR]["default"] == ""
    assert (
        schema({c.CONF_TRANSFER_FEE_SENSOR: "sensor.tariff"})[
            c.CONF_TRANSFER_FEE_SENSOR
        ]
        == "sensor.tariff"
    )
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda entity: entity == "sensor.tariff")
    )
    assert flow._transfer_price_errors(hass, {}) == {}
    assert (
        flow._transfer_price_errors(hass, {c.CONF_TRANSFER_FEE_SENSOR: "sensor.tariff"})
        == {}
    )
    assert flow._transfer_price_errors(
        hass, {c.CONF_TRANSFER_FEE_SENSOR: "sensor.missing"}
    ) == {c.CONF_TRANSFER_FEE_SENSOR: "sensor_not_found"}
