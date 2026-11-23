"""Config-flow pieces for the extended optimizer engine.

The standard form only carries the engine selector.  When "extended" is
chosen, the flow continues with the ``extended`` step built here, so the
upstream form stays exactly as it was.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from ..const import (
    CONF_LOAD_ENERGY_SENSOR,
    CONF_PRICE_SOURCE,
    CONF_SPOT_SENSOR,
    PRICE_SOURCE_EMALDO,
    PRICE_SOURCE_SENSOR,
)
from .const import (
    CONF_EXCLUDED_LOAD_SENSOR,
    CONF_FUND_GRID_EXPORTS,
    CONF_SELL_PRICE_SENSOR,
    CONF_SELL_PRICE_SOURCE,
    CONF_TRANSFER_FEE_SENSOR,
    SELL_PRICE_SOURCE_SAME,
    normalize_sell_price_source,
)

SELL_PRICE_SOURCE_LABELS = {
    SELL_PRICE_SOURCE_SAME: "Same as the import price source",
    PRICE_SOURCE_EMALDO: "Emaldo (independent export curve)",
    PRICE_SOURCE_SENSOR: "External sensor or input_number",
}


def build_extended_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schema of the extended-engine options step."""
    d = defaults or {}
    excluded = d.get(CONF_EXCLUDED_LOAD_SENSOR)
    return vol.Schema(
        {
            vol.Required(
                CONF_SELL_PRICE_SOURCE,
                default=normalize_sell_price_source(
                    d.get(CONF_SELL_PRICE_SOURCE, SELL_PRICE_SOURCE_SAME)
                ),
            ): vol.In(SELL_PRICE_SOURCE_LABELS),
            vol.Optional(
                CONF_SELL_PRICE_SENSOR,
                default=d.get(CONF_SELL_PRICE_SENSOR, ""),
            ): str,
            vol.Optional(
                CONF_TRANSFER_FEE_SENSOR,
                default=d.get(CONF_TRANSFER_FEE_SENSOR, ""),
            ): str,
            vol.Optional(
                CONF_EXCLUDED_LOAD_SENSOR,
                description={"suggested_value": excluded} if excluded else {},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_FUND_GRID_EXPORTS,
                default=d.get(CONF_FUND_GRID_EXPORTS, False),
            ): bool,
        }
    )


def validate_extended(
    hass: HomeAssistant, values: dict[str, Any], base: dict[str, Any]
) -> dict[str, str]:
    """Validate the extended step; normalizes blank entity fields in place.

    ``base`` holds the values from the standard step (used to reject the
    household load sensor as the excluded load).
    """
    errors: dict[str, str] = {}

    if CONF_SELL_PRICE_SOURCE in values:
        values[CONF_SELL_PRICE_SOURCE] = normalize_sell_price_source(
            values[CONF_SELL_PRICE_SOURCE]
        )

    # The standard form validates the import sensor on first setup only; the
    # options flow does not, so check it here when it is the selected source.
    import_sensor = str(base.get(CONF_SPOT_SENSOR) or "").strip()
    if base.get(CONF_PRICE_SOURCE) == PRICE_SOURCE_SENSOR and (
        not import_sensor or not hass.states.get(import_sensor)
    ):
        errors["base"] = "sensor_not_found"

    sell_sensor = str(values.get(CONF_SELL_PRICE_SENSOR) or "").strip()
    values[CONF_SELL_PRICE_SENSOR] = sell_sensor
    if values.get(CONF_SELL_PRICE_SOURCE) == PRICE_SOURCE_SENSOR and (
        not sell_sensor or not hass.states.get(sell_sensor)
    ):
        errors[CONF_SELL_PRICE_SENSOR] = "sensor_not_found"

    transfer = str(values.get(CONF_TRANSFER_FEE_SENSOR) or "").strip()
    values[CONF_TRANSFER_FEE_SENSOR] = transfer
    if transfer and not hass.states.get(transfer):
        errors[CONF_TRANSFER_FEE_SENSOR] = "sensor_not_found"

    # An empty field must also override a sensor saved earlier.
    excluded = values.setdefault(CONF_EXCLUDED_LOAD_SENSOR, "")
    if excluded:
        state = hass.states.get(excluded)
        if state is None:
            errors[CONF_EXCLUDED_LOAD_SENSOR] = "sensor_not_found"
        elif excluded == base.get(CONF_LOAD_ENERGY_SENSOR) or state.attributes.get(
            "unit_of_measurement"
        ) not in ("W", "kW"):
            errors[CONF_EXCLUDED_LOAD_SENSOR] = "invalid_excluded_load"

    return errors
