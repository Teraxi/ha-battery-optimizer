"""Entities that exist only with the extended optimizer engine.

They are added next to the upstream sensors through the coordinator's
``extra_sensors`` hook, so no upstream entity is replaced or redefined.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..sensor import _BaseOptimizerSensor, _RealizedCostBaseSensor


def extended_sensors(coordinator, entry: ConfigEntry) -> list:
    """Return the extended-only entities for a config entry."""
    return [
        ExportProfitSensor(coordinator, entry, yesterday=False),
        ExportProfitSensor(coordinator, entry, yesterday=True),
        ImportPriceSensor(coordinator, entry),
        ExportPriceSensor(coordinator, entry),
        CurrentTransferFeeSensor(coordinator, entry),
        ActiveSaleSensor(coordinator, entry),
    ]


class ExportProfitSensor(_RealizedCostBaseSensor):
    """Net export income attributable to the local calendar day."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, *, yesterday: bool):
        key = "export_profit_yesterday" if yesterday else "export_profit_forecast_today"
        super().__init__(coordinator, entry, key)
        self._yesterday = yesterday

    @property
    def native_value(self):
        return self.coordinator.export_profit_report(self._yesterday)[0]

    @property
    def extra_state_attributes(self):
        return self.coordinator.export_profit_report(self._yesterday)[1]


class _PriceSensor(_BaseOptimizerSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self._currency}/kWh"


class ImportPriceSensor(_PriceSensor):
    """Diagnostic: effective import price including VAT and grid transfer fees."""

    _attr_suggested_display_precision = 5
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "import_price")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.current_import_price


class ExportPriceSensor(_PriceSensor):
    """Diagnostic: current export price after the configured sales commission."""

    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "export_price")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.current_export_price


class CurrentTransferFeeSensor(_PriceSensor):
    """Diagnostic: grid transfer fee in force now (fixed fee or external tariff).

    The upstream Grid Transfer Fee sensor keeps showing the configured fixed
    fee; this one follows the external transfer tariff when one is set.
    """

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "current_transfer_fee")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.current_transfer_fee


class ActiveSaleSensor(_BaseOptimizerSensor):
    """Diagnostic: decision about continuing an optimizer-owned battery sale."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "active_sale")

    @property
    def _decision(self) -> dict | None:
        result = self._result
        return (result.trace or {}).get("active_sale") if result is not None else None

    @property
    def native_value(self) -> str | None:
        decision = self._decision
        return decision.get("decision") if decision else None

    @property
    def extra_state_attributes(self):
        return dict(self._decision or {})
