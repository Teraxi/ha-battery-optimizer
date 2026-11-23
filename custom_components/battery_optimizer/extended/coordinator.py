"""Extended Battery Optimizer coordinator.

Subclass of the upstream coordinator, selected with the "Extended optimizer"
engine option.  The upstream ``coordinator.py`` and ``optimizer.py`` are not
modified: everything that differs lives in this package.  The methods below are
copies of the fork's changed coordinator methods and are maintained here.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import logging
import math
import os
from typing import Any

from homeassistant.util import dt as dt_util

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .price_data import has_forecast_attributes, parse_sensor_prices

from ..const import (
    DOMAIN,
    EMALDO_DOMAIN,
    SLOT_NO_OVERRIDE,
    SLOT_IDLE,
    SUBENTRY_TYPE_RULE,
    SUBENTRY_TYPE_DEVICE,
    SLOTS_PER_DAY,
    SLOT_DURATION_HOURS,
    MIDNIGHT_CHECKPOINT,
    CONF_SPOT_SENSOR,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_VAT_MULTIPLIER,
    CONF_TRANSFER_FEE_BUY,
    CONF_SALES_COMMISSION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_CHARGE_EFFICIENCY,
    CONF_DISCHARGE_EFFICIENCY,
    CONF_SOC_MIN,
    CONF_SOC_MAX,
    CONF_BATTERY_RANGE_LOW_MARKER,
    CONF_RESERVED_BOTTOM_KWH,
    CONF_RESERVED_TOP_KWH,
    CONF_BASE_LOAD_KW,
    CONF_BATTERY_WEAR_COST,
    CONF_IDLE_POWER_KW,
    CONF_ENABLE_SOC_SAFEGUARD,
    CONF_SOC_RECOVERY_BUFFER,
    DEFAULT_ENABLE_SOC_SAFEGUARD,
    DEFAULT_SOC_RECOVERY_BUFFER_PCT,
    DEFAULT_VAT_MULTIPLIER,
    DEFAULT_TRANSFER_FEE_BUY,
    DEFAULT_MAX_CHARGE_KW,
    DEFAULT_MAX_DISCHARGE_KW,
    LOW_SOC_RERUN_MARGIN_PCT,
    LOW_SOC_RERUN_THROTTLE_MIN,
    IDLE_GAP_RERUN_THROTTLE_MIN,
    SOC_DIVERGENCE_RERUN_THRESHOLD_PCT,
    SOC_DIVERGENCE_RERUN_THROTTLE_MIN,
    CONF_IDLE_STRATEGY,
    CONF_PRICE_SOURCE,
    PRICE_SOURCE_EMALDO,
    PRICE_SOURCE_SENSOR,
    CONF_SOC_GUARD_INTERVAL,
    CONF_OPTIMIZER_INTERVAL,
    CONF_EMALDO_ENTRY_ID,
    CONF_AUTO_BASE_LOAD,
    CONF_LOAD_ENERGY_SENSOR,
    CONF_ENABLE_PV_STRATEGY,
    CONF_SOLAR_SELL_MIN_FORECAST_KWH,
    CONF_ENABLE_EMALDO_CONTROL,
    CONF_SOLAR_FORECAST_MODE,
    CONF_SOLAR_FORECAST_SOURCE,
    CONF_SOLAR_FORECAST_SCALE,
    CONF_GRID_CHARGE_SOLAR_AWARE,
    CONF_SOLAR_ACTUAL_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_RULE_RETENTION_DAYS,
    CONF_PRECHARGE_ENABLED,
    CONF_PRECHARGE_SAFETY_SOC,
    CONF_PRECHARGE_PRICE_CEILING,
    CONF_PRECHARGE_MAX_KWH_FRAC,
    CONF_PRECHARGE_LOW_SOLAR_FRAC,
    CONF_PRECHARGE_REQUIRE_LOW_SOLAR,
    CONF_PRECHARGE_HORIZON_DAYS,
    CONF_PRECHARGE_PUBLISH_HOUR,
    DEFAULT_PRECHARGE_ENABLED,
    DEFAULT_PRECHARGE_SAFETY_SOC,
    DEFAULT_PRECHARGE_PRICE_CEILING,
    DEFAULT_PRECHARGE_MAX_KWH_FRAC,
    DEFAULT_PRECHARGE_LOW_SOLAR_FRAC,
    DEFAULT_PRECHARGE_REQUIRE_LOW_SOLAR,
    DEFAULT_PRECHARGE_HORIZON_DAYS,
    DEFAULT_PRECHARGE_PUBLISH_HOUR,
    CONF_CHEAP_CHARGE_ENABLED,
    CONF_CHEAP_CHARGE_PRICE_THRESHOLD,
    CONF_CHEAP_CHARGE_ONLY_WHEN_TOMORROW_UNKNOWN,
    DEFAULT_CHEAP_CHARGE_ENABLED,
    DEFAULT_CHEAP_CHARGE_THRESHOLD_FRACTION,
    DEFAULT_CHEAP_CHARGE_ONLY_WHEN_TOMORROW_UNKNOWN,
    CHEAP_CHARGE_MIN_NEED_KWH,
    NORD_POOL_PUBLISH_TZ,
    NORD_POOL_PUBLISH_HOUR,
    PUBLISH_CUTOFF_SLOT,    SOLAR_FORECAST_P10, SOLAR_FORECAST_P50,
    SOLAR_SOURCE_EMALDO,
    DEFAULT_AUTO_BASE_LOAD,
    DEFAULT_LOAD_ENERGY_SENSOR,
    DEFAULT_ENABLE_PV_STRATEGY,
    DEFAULT_ENABLE_EMALDO_CONTROL,
    DEFAULT_SOLAR_SELL_MIN_FORECAST_KWH,
    DEFAULT_SOLAR_FORECAST_MODE,
    DEFAULT_SOLAR_FORECAST_SOURCE,
    DEFAULT_SOLAR_FORECAST_SCALE,
    DEFAULT_GRID_CHARGE_SOLAR_AWARE,
    DEFAULT_GRID_IMPORT_SENSOR,
    DEFAULT_GRID_EXPORT_SENSOR,
    DEFAULT_RULE_RETENTION_DAYS,
    DEFAULT_BASE_LOAD_KW,
    DEFAULT_IDLE_STRATEGY,
    DEFAULT_SOC_GUARD_INTERVAL,
    DEFAULT_SOC_MAX,
    DEFAULT_OPTIMIZER_INTERVAL,
    IDLE_FULL_CONTROL,
    IDLE_SOLAR_GUARD,
    IDLE_SMART_OVERRIDE,
    CONF_FORCED_SELL_ENABLED,
    CONF_FORCED_SELL_MAX_KWH_PD,
    CONF_FORCED_SELL_MIN_PROFIT,
    CONF_FORCED_SELL_MIN_PRICE,
    DEFAULT_FORCED_SELL_ENABLED,
    DEFAULT_FORCED_SELL_MAX_KWH_PD,
    DEFAULT_FORCED_SELL_MIN_PROFIT,
    DEFAULT_FORCED_SELL_MIN_PRICE,
    CONF_STALE_PUSH_RETRY_COUNT,
    DEFAULT_STALE_PUSH_RETRY_COUNT,
    STALE_PUSH_RETRY_BACKOFF_S,
    PUSH_FAIL_REASSERT_WINDOW_S,
    PUSH_SETTLE_S,
    ENFORCEMENT_REASSERT_MIN_S,
    ACCURACY_REASSERT_COOLDOWN_S,
    ACCURACY_REASSERT_SHORTFALL_KWH,
    ACCURACY_REASSERT_RATIO,
    ENFORCEMENT_CHECK_SETTLE_S,
    ENFORCEMENT_REPUSH_MIN_S,
    CONF_ENABLE_PV_CUT_WHEN_FULL,
    CONF_PV_CUT_RESUME_SOC,
    DEFAULT_ENABLE_PV_CUT_WHEN_FULL,
    DEFAULT_PV_CUT_RESUME_SOC,
    CONF_CURRENCY,
)
from homeassistant.helpers import issue_registry as ir

from ..emaldo_solar import emaldo_solar_day
from ..money import (
    ORIGIN_LITERALS,
    currency_source,
    locale_money,
    should_warn_mismatch,
    unreviewed_fields,
)
from .optimizer import (
    BatteryConfig,
    OptimizationResult,
    SlotPlan,
    compute_prices,
    compute_solar_confidence_ratio,
    interpolate_solar_to_15min,
    optimize,
    speculative_precharge_slots,
    cheap_charge_slots,
    _simulate_soc_trajectory,
    _soc_to_charge_target,
)
from ..rules import (
    UserRule,
    rule_from_data,
    rule_title,
    expand_day,
    expired_rule_ids,
    mask_plan,
    LEVEL_DEFAULT,
    SlotWinner,
)
from ..solar_scale import resolve_solar_scale
from ..solar_regime import default_state as solar_regime_default_state
from ..solar_regime import deserialize as solar_regime_deserialize
from ..solar_regime import update_regime
from ..solar_actual import counter_delta_kwh, normalize_to_kwh, resolve_solar_source
from . import export_profit
from ..cost_history import (
    local_slot_index,
    slot_cost,
    prune_history,
    today_records,
    day_totals,
)
from ..runtime_state import prune_plan_slots, rebuild_runtime, serialize_runtime
from ..plan_export import render_analysis
from .. import push_state
from .. import marker_io
from .. import sale_state
from ..battery_range import (
    MARKER_MIN,
    clamp_marker_to_high,
    effective_soc_band,
    resolve_low_marker,
)

from ..coordinator import (
    BatteryOptimizerCoordinator,
    _stage_sell_target_kwh,
    _current_slot_index,
    _split_result_by_day,
    _config_snapshot,
    _INTEGRATION_VERSION,
)

from dataclasses import fields

from .const import (
    CONF_EXCLUDED_LOAD_SENSOR,
    CONF_FUND_GRID_EXPORTS,
    CONF_SELL_PRICE_SENSOR,
    CONF_SELL_PRICE_SOURCE,
    CONF_TRANSFER_FEE_SENSOR,
    ENGINE_EXTENDED,
    SELL_PRICE_SOURCE_SAME,
    normalize_sell_price_source,
)

_LOGGER = logging.getLogger(__name__)

# Shortest gap between forced replans caused by the sources this engine adds
# (export sensor, Emaldo chart as export source, transfer tariff sensor).
_EXTRA_PRICE_MIN_INTERVAL_S = 60


class ExtendedBatteryOptimizerCoordinator(BatteryOptimizerCoordinator):
    """Coordinator for the extended optimizer engine."""

    engine = ENGINE_EXTENDED

    _sale_no_restart_before = 0.0
    _unsub_sale_deadline: CALLBACK_TYPE | None = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry)
        self._export_book: dict = {}
        self._export_book_path = hass.config.path(
            f"battery_optimizer_export_profit_{entry.entry_id}.json"
        )
        self._export_lock = asyncio.Lock()
        self._cost_price_date = None

    # --- Configuration ---

    def _build_battery_config(self) -> BatteryConfig:
        """Upstream config plus the fields only the extended optimizer reads."""
        base = super()._build_battery_config()
        c = self.config
        values = {f.name: getattr(base, f.name) for f in fields(base) if f.init}
        return BatteryConfig(
            **values,
            fund_grid_exports=c.get(CONF_FUND_GRID_EXPORTS, False),
            pv_min_forecast_kwh=c.get(
                CONF_SOLAR_SELL_MIN_FORECAST_KWH, DEFAULT_SOLAR_SELL_MIN_FORECAST_KWH
            ),
            pv_cut_when_full=c.get(
                CONF_ENABLE_PV_CUT_WHEN_FULL, DEFAULT_ENABLE_PV_CUT_WHEN_FULL
            ),
        )

    def extra_sensors(self, entry: ConfigEntry) -> list:
        """Entities that exist only with the extended engine."""
        from .sensor import extended_sensors

        return extended_sensors(self, entry)

    # --- Export price source (with upgrade-safe default) ---

    def _parse_export_price_data(
        self,
    ) -> tuple[list[float] | None, list[float] | None]:
        """Return the export curve for the configured export price source.

        ``same`` (the default, also for entries saved before the option
        existed) reuses the import source, exactly like the upstream
        optimizer, so enabling the extended engine never changes where sell
        prices come from.  ``emaldo`` and ``sensor`` select an independent
        source; a missing independent curve never falls back to the import
        curve.
        """
        source = normalize_sell_price_source(
            self.config.get(CONF_SELL_PRICE_SOURCE, SELL_PRICE_SOURCE_SAME)
        )
        if source == SELL_PRICE_SOURCE_SAME:
            return self._parse_price_data()
        if source == PRICE_SOURCE_EMALDO:
            return self._parse_emaldo_price_data(allow_fallback=False)
        sensor = self.config.get(CONF_SELL_PRICE_SENSOR, "").strip()
        return self._parse_sensor_price_data(sensor) if sensor else (None, None)

    def _sell_source_is_sensor(self) -> bool:
        return self.config.get(CONF_SELL_PRICE_SOURCE) == PRICE_SOURCE_SENSOR

    # --- Lifecycle ---

    async def async_restore_cost_history(self) -> None:
        await super().async_restore_cost_history()
        await self.restore_export_book()

    @callback
    def _on_soc_state_change(self, event) -> None:
        super()._on_soc_state_change(event)
        soc = self._get_battery_soc()
        if (
            soc is not None
            and self._manual_selling_active
            and soc <= self._build_battery_config().soc_min
        ):
            self.hass.async_create_task(self._stop_manual_selling())

    def async_setup_listeners(self) -> None:
        super().async_setup_listeners()
        # The base class tracks the import source.  Add the sources that only
        # exist here, skipping entities it already tracks.
        tracked = {self.config.get(CONF_SPOT_SENSOR)}
        if self.config.get(CONF_PRICE_SOURCE, PRICE_SOURCE_EMALDO) == PRICE_SOURCE_EMALDO:
            tracked.add(self._resolve_emaldo_entity("schedule_chart"))
        extra = []
        if self.config.get(CONF_SELL_PRICE_SOURCE) == PRICE_SOURCE_EMALDO:
            extra.append(self._resolve_emaldo_entity("schedule_chart"))
        if self._sell_source_is_sensor():
            extra.append(self.config.get(CONF_SELL_PRICE_SENSOR))
        extra.append(self.config.get(CONF_TRANSFER_FEE_SENSOR))
        extra = [
            s.strip()
            for s in dict.fromkeys(e for e in extra if e)
            if s.strip() and s.strip() not in tracked
        ]
        self._cancel_extra_price_timer()
        if extra:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass, extra, self._extra_price_change
                )
            )
        for timing in (
            {"minute": "/15", "second": 0},
            {"hour": 23, "minute": 59, "second": 59},
        ):
            self._unsub_listeners.append(
                async_track_time_change(
                    self.hass, self._capture_export_profit, **timing
                )
            )

    @callback
    def _extra_price_change(self, event) -> None:
        """Throttled replan trigger for the sources only this engine watches.

        The first change runs at once; further changes inside the window are
        coalesced into one trailing run, so a helper or tariff sensor that
        updates every few seconds cannot force a full optimization and an
        Emaldo push each time.
        """
        now = dt_util.now().timestamp()
        wait = (
            self.__dict__.get("_extra_price_last", 0.0)
            + _EXTRA_PRICE_MIN_INTERVAL_S
            - now
        )
        if wait <= 0:
            self._extra_price_last = now
            self._nordpool_state_change(event)
            return
        if self.__dict__.get("_extra_price_timer") is not None:
            return

        @callback
        def _fire(_now) -> None:
            self._extra_price_timer = None
            self._extra_price_last = dt_util.now().timestamp()
            self._nordpool_state_change(None)

        self._extra_price_timer = async_call_later(self.hass, wait, _fire)

    def _cancel_extra_price_timer(self) -> None:
        timer = self.__dict__.get("_extra_price_timer")
        if timer is not None:
            timer()
            self._extra_price_timer = None

    def _uses_native_prices(self) -> bool:
        sensors = [self.config.get(CONF_SPOT_SENSOR, "")]
        if self._sell_source_is_sensor():
            sensors.append(self.config.get(CONF_SELL_PRICE_SENSOR, ""))
        return any(s and self._native_price_info(s) is not None for s in sensors)

    @callback
    def _checkpoint_callback(self, now: datetime) -> None:
        """Checkpoint that first refreshes cached Nord Pool curves.

        The base checkpoint decides whether to force a tomorrow plan from
        ``_has_tomorrow_prices()``.  Native Nord Pool curves are cached by
        optimizer runs only, so without a refresh that check cannot see prices
        published since the last run.
        """
        if self._uses_native_prices():
            self.hass.async_create_task(self._checkpoint_after_refresh(now))
            return
        super()._checkpoint_callback(now)

    async def _checkpoint_after_refresh(self, now: datetime) -> None:
        await self._refresh_native_price_forecasts()
        super()._checkpoint_callback(now)

    @callback
    def async_shutdown(self) -> None:
        self._cancel_extra_price_timer()
        if self._unsub_sale_deadline is not None:
            self._unsub_sale_deadline()
            self._unsub_sale_deadline = None
        super().async_shutdown()

    # --- Methods copied from the fork (changed or new) ---
    def _current_effective_prices(self) -> tuple[float | None, float | None]:
        """Return this quarter-hour's prices using the planner's calculation.

        Read today's source instead of the previous run's cache, which may
        belong to yesterday or remain populated after a source goes missing.
        """
        today, _ = self._parse_price_data()
        exports, _ = self._parse_export_price_data()
        if not today and not exports:
            return None, None
        cfg = self._build_battery_config()
        if self.config.get(CONF_TRANSFER_FEE_SENSOR, "").strip():
            tariffs, _ = self._parse_transfer_prices()
            if tariffs is None:
                today = None
            else:
                cfg.transfer_fees = tariffs
        slot = _current_slot_index()
        buy = compute_prices(today, cfg)[0][slot] if today else None
        sell = compute_prices(exports, cfg)[1][slot] if exports else None
        return buy, sell

    @property
    def current_import_price(self) -> float | None:
        """Effective import energy price including VAT and grid transfer fees."""
        return self._current_effective_prices()[0]

    @property
    def current_export_price(self) -> float | None:
        """Effective export energy price after the sales commission."""
        return self._current_effective_prices()[1]

    def _parse_emaldo_price_data(
        self, *, allow_fallback: bool = True
    ) -> tuple[list[float] | None, list[float] | None]:
        """Read prices from sensor.power_store_schedule_chart (Emaldo internal).

        The schedule attribute contains a flat list of 192 × 15-min dicts:
          {"t": "2026-05-06T00:00:00+03:00", "price": 5.0, ...}
        Prices are raw Nord Pool spot in ct/kWh (snt/kWh).  Converted to
        €/kWh by dividing by 100.
        Slots are grouped by local date from the "t" field — the schedule is a
        rolling 48-hour window and the first slot is not necessarily today midnight.
        Returns None for tomorrow if its 96 slots are missing or all zero.
        """
        # Resolve the Emaldo schedule_chart entity dynamically
        sensor_id = self._resolve_emaldo_entity("schedule_chart")
        if not sensor_id:
            _LOGGER.warning("Emaldo schedule_chart sensor not found")
            return self._parse_sensor_price_data() if allow_fallback else (None, None)

        state = self.hass.states.get(sensor_id)
        if state is None:
            _LOGGER.warning("Emaldo schedule_chart sensor %s unavailable", sensor_id)
            return self._parse_sensor_price_data() if allow_fallback else (None, None)

        schedule = state.attributes.get("schedule")
        if not schedule or not isinstance(schedule, list) or len(schedule) < SLOTS_PER_DAY:
            _LOGGER.warning("Emaldo schedule_chart has no usable schedule attribute")
            return self._parse_sensor_price_data() if allow_fallback else (None, None)

        # Group slots by local date using the "t" timestamp in each slot.
        # The schedule is a rolling window — the first slot is NOT necessarily
        # midnight of today, so we must not blindly split at index 96.
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        today_date = dt_util.now().date()
        tomorrow_date = today_date + timedelta(days=1)

        today_slots: list[dict] = []
        tomorrow_slots: list[dict] = []
        for slot in schedule:
            try:
                slot_dt = datetime.fromisoformat(slot["t"])
                slot_date = slot_dt.astimezone(local_tz).date()
            except (KeyError, ValueError, TypeError):
                continue
            if slot_date == today_date:
                today_slots.append(slot)
            elif slot_date == tomorrow_date:
                tomorrow_slots.append(slot)

        if len(today_slots) < SLOTS_PER_DAY:
            _LOGGER.warning(
                "Emaldo schedule_chart has only %d slots for today (expected %d)",
                len(today_slots),
                SLOTS_PER_DAY,
            )
            return self._parse_sensor_price_data() if allow_fallback else (None, None)

        today_prices = [s["price"] / 100.0 for s in today_slots[:SLOTS_PER_DAY]]

        if len(tomorrow_slots) == SLOTS_PER_DAY and any(s["price"] != 0 for s in tomorrow_slots):
            tomorrow_prices: list[float] | None = [s["price"] / 100.0 for s in tomorrow_slots]
        else:
            tomorrow_prices = None

        return today_prices, tomorrow_prices

    def _native_price_info(self, sensor_id: str) -> tuple[str, str] | None:
        """Resolve a core Nord Pool entity without hardcoding its entity ID or area."""
        state = self.hass.states.get(sensor_id)
        if state is None or any(
            key in state.attributes for key in ("data", "raw_today", "raw_tomorrow")
        ):
            return None  # Custom/forecast entities already carry their own curve.
        registry = er.async_get(self.hass)
        entity = registry.async_get(sensor_id) if registry else None
        if entity is None or entity.platform != "nordpool" or not entity.config_entry_id:
            return None
        # Core Nord Pool uses <area>-<sensor key>. Check against the integration's
        # configured areas instead of interpreting a user-editable entity name.
        entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        area = entity.unique_id.partition("-")[0]
        if entry is None or area not in entry.data.get("areas", []):
            return (entity.config_entry_id, "")  # Recognized, but never use its scalar state.
        return entity.config_entry_id, area

    async def _refresh_native_price_forecasts(self) -> None:
        """Read core Nord Pool's HA service (which reuses its cached market data)."""
        # Include the external import fallback retained by the Emaldo reader.
        sensors = [self.config.get(CONF_SPOT_SENSOR, "")]
        if (
            self.config.get(CONF_SELL_PRICE_SOURCE, PRICE_SOURCE_EMALDO)
            == PRICE_SOURCE_SENSOR
        ):
            sensors.append(self.config.get(CONF_SELL_PRICE_SENSOR, ""))
        forecasts = {}
        responses = {}
        now = dt_util.now()
        for sensor in dict.fromkeys(sensors):
            if not sensor or (info := self._native_price_info(sensor)) is None:
                continue
            entry_id, area = info
            forecasts[sensor] = []
            if not area:
                continue
            for offset in (-1, 0, 1):
                # Include yesterday: market days use CET, HA may use e.g. Helsinki.
                day = (now.date() + timedelta(days=offset)).isoformat()
                key = (entry_id, area, day)
                if key not in responses:
                    try:
                        response = await self.hass.services.async_call(
                            "nordpool",
                            "get_prices_for_date",
                            {
                                "config_entry": entry_id,
                                "date": day,
                                "areas": [area],
                                "currency": self.currency,
                            },
                            blocking=True,
                            return_response=True,
                        )
                        rows = response.get(area, []) if isinstance(response, dict) else []
                        responses[key] = rows if isinstance(rows, list) else []
                    except Exception as err:
                        _LOGGER.warning("Nord Pool prices unavailable for %s: %s", day, err)
                        responses[key] = []
                forecasts[sensor].extend(responses[key])
        self._native_price_forecasts = forecasts
        self._native_price_forecast_date = now.date()

    def _parse_sensor_price_data(
        self, sensor_id: str | None = None
    ) -> tuple[list[float] | None, list[float] | None]:
        """Normalize a forecast or fixed sensor/helper in currency/kWh."""
        sensor_id = self.config.get(CONF_SPOT_SENSOR, "") if sensor_id is None else sensor_id
        state = self.hass.states.get(sensor_id) if sensor_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return None, None
        if self._native_price_info(sensor_id) is not None:
            if getattr(self, "_native_price_forecast_date", None) != dt_util.now().date():
                return None, None
            rows = getattr(self, "_native_price_forecasts", {}).get(sensor_id, [])
            # Core service responses are currency/MWh; the current-price sensor is /kWh.
            return parse_sensor_prices(
                "0", {"data": rows, "unit_of_measurement": "currency/MWh"},
                dt_util.now(), complete=True,
            )
        if self._forecast_vanished(sensor_id, state.attributes):
            return None, None
        return parse_sensor_prices(state.state, state.attributes, dt_util.now())

    def _forecast_vanished(self, sensor_id: str, attributes) -> bool:
        """True when a sensor that published a forecast now shows only a value.

        A numeric state without forecast attributes is a fixed price, but a
        market sensor that loses its attributes (reload, partial startup)
        must not silently become a flat all-day price.  Once a forecast has
        been seen for an entity, a later scalar-only state counts as missing
        data until the forecast returns.
        """
        seen = self.__dict__.setdefault("_forecast_sensors", set())
        if has_forecast_attributes(attributes):
            seen.add(sensor_id)
            self.__dict__.setdefault("_forecast_warned", set()).discard(sensor_id)
            return False
        if sensor_id not in seen:
            return False
        warned = self.__dict__.setdefault("_forecast_warned", set())
        if sensor_id not in warned:
            warned.add(sensor_id)
            _LOGGER.warning(
                "%s no longer publishes a price forecast; treating its prices as "
                "unavailable instead of a flat price until the forecast returns",
                sensor_id,
            )
        return True

    def _parse_transfer_prices(self) -> tuple[list[float] | None, list[float] | None]:
        sensor_id = self.config.get(CONF_TRANSFER_FEE_SENSOR, "").strip()
        state = self.hass.states.get(sensor_id) if sensor_id else None
        if state is None:
            return None, None
        if self._forecast_vanished(sensor_id, state.attributes):
            return None, None
        return parse_sensor_prices(state.state, state.attributes, dt_util.now(), complete=True)

    @property
    def current_transfer_fee(self) -> float | None:
        if not self.config.get(CONF_TRANSFER_FEE_SENSOR, "").strip():
            return self.config.get(CONF_TRANSFER_FEE_BUY, self._locale.transfer_fee_buy)
        today, _ = self._parse_transfer_prices()
        return today[_current_slot_index()] if today is not None else None

    def _has_tomorrow_prices(self) -> bool:
        """All selected prices must be available before planning tomorrow."""
        if self.config.get(CONF_TRANSFER_FEE_SENSOR, "").strip():
            if self._parse_transfer_prices()[1] is None:
                return False
        return (
            self._parse_price_data()[1] is not None
            and self._parse_export_price_data()[1] is not None
        )

    @staticmethod
    def _net_daily_load_kw(total_rows, excluded_rows, midnight: datetime) -> list[float]:
        """Average matched hourly watts over complete local calendar days."""
        def by_hour(rows):
            return {
                row["start"]: row["mean"]
                for row in rows
                if row.get("start") is not None
                and isinstance(row.get("mean"), (int, float))
                and math.isfinite(row["mean"])
            }

        total = by_hour(total_rows)
        excluded = by_hour(excluded_rows)
        daily = []
        for offset in range(14, 0, -1):
            day = midnight - timedelta(days=offset)
            start = int(day.timestamp())
            end = int((day + timedelta(days=1)).timestamp())
            # Recorder bins start on UTC hours. Weight overlaps for time zones
            # with fractional offsets and use elapsed time across DST changes.
            hours = range(start - start % 3600, end, 3600)
            if not all(hour in total and hour in excluded for hour in hours):
                continue
            watt_seconds = 0.0
            for hour in hours:
                seconds = min(end, hour + 3600) - max(start, hour)
                net_watts = max(0.0, total[hour] - max(0.0, excluded[hour]))
                watt_seconds += net_watts * seconds
            daily.append(watt_seconds / (end - start) / 1000)
        return daily

    async def _fetch_auto_base_load_kw(self) -> float:
        """Return auto-tuned base load kW from 14-day HA recorder statistics.

        Uses matched hourly means when excluding a load, otherwise the daily
        ``mean`` of the configured household load power sensor
        (W, ``state_class: measurement``) over the last 14 days, converts to kW,
        and returns the 7-day rolling average clamped to 0.5–2 times the configured
        ``base_load_kw``.

        Configure with a combined household load power sensor in Watts, e.g.
        ``sensor.emhass_combined_power``.

        Falls back to the configured ``base_load_kw`` if:
        - auto-tune is disabled,
        - no sensor is configured,
        - the recorder component is unavailable, or
        - fewer than 3 days of data exist.
        """
        configured = self.config.get(CONF_BASE_LOAD_KW, DEFAULT_BASE_LOAD_KW)
        if not self.config.get(CONF_AUTO_BASE_LOAD, DEFAULT_AUTO_BASE_LOAD):
            return configured

        load_sensor = self.config.get(CONF_LOAD_ENERGY_SENSOR, DEFAULT_LOAD_ENERGY_SENSOR)
        if not load_sensor:
            _LOGGER.debug("Auto base load: no sensor configured, using %.2f kW", configured)
            return configured

        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                statistics_during_period,
            )
        except ImportError:
            _LOGGER.debug("Recorder not available — using configured base_load_kw")
            return configured

        now = dt_util.now()
        excluded = self.config.get(CONF_EXCLUDED_LOAD_SENSOR)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=14)
        end = None
        if excluded:
            start = (midnight - timedelta(days=14)).astimezone(timezone.utc).replace(minute=0)
            end = midnight.astimezone(timezone.utc)
            if end.minute:
                end = end.replace(minute=0) + timedelta(hours=1)
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                {load_sensor, excluded} if excluded else {load_sensor},
                "hour" if excluded else "day",
                {"power": "W"} if excluded else None,
                {"mean"},
            )
        except Exception:
            _LOGGER.debug(
                "Recorder query failed — using configured base_load_kw", exc_info=True
            )
            return configured

        rows = stats.get(load_sensor, [])
        # mean is in W; convert to kW and keep only positive (load) values
        if excluded:
            daily_kw = self._net_daily_load_kw(rows, stats.get(excluded, []), midnight)
        else:
            daily_kw = [row["mean"] / 1000.0 for row in rows if row.get("mean") is not None and row["mean"] > 0]

        if len(daily_kw) < 3:
            _LOGGER.debug(
                "Auto base load: only %d days of data (need ≥3) — using %.2f kW",
                len(daily_kw),
                configured,
            )
            return configured

        window = daily_kw[-7:]
        avg_kw = sum(window) / len(window)
        lo = configured * 0.5
        hi = configured * 2.0
        result = max(lo, min(hi, avg_kw))
        _LOGGER.info(
            "Auto base load: %d-day window, daily avg=%.3f kW "
            "(configured=%.3f kW, clamped [%.3f, %.3f]) → %.3f kW",
            len(window),
            avg_kw,
            configured,
            lo,
            hi,
            result,
        )
        return result

    async def restore_export_book(self):
        stored = await self.hass.async_add_executor_job(
            marker_io.read_json_marker, self._export_book_path
        )
        if isinstance(stored, dict) and stored.get("currency") == self.currency:
            self._export_book = stored

    def _sample_export_profit(self, book, now, cfg):
        """Update a ledger or a transient forecast copy from the same meter readings."""
        import_id = self._effective_grid_import_sensor()
        export_id = self._effective_grid_export_sensor()
        imported = self._read_counter_kwh(import_id) if import_id else None
        exported = self._read_counter_kwh(export_id) if export_id else None
        soc = self._get_battery_soc()
        sample = None
        if all(
            v is not None and math.isfinite(v) and v >= 0
            for v in (soc, imported, exported)
        ):
            slot = local_slot_index(now)
            buy = sell = None
            prices = None
            if (
                self._cost_price_date == now.date()
                and self._cost_sell_prices
                and self._cost_buy_prices
            ):
                prices = (self._cost_buy_prices, self._cost_sell_prices)
            elif (
                self._last_run
                and self._last_result_tomorrow
                and dt_util.as_local(self._last_run).date() + timedelta(days=1)
                == now.date()
            ):
                rows = self._last_result_tomorrow.slots
                prices = (
                    [row.buy_price for row in rows],
                    [row.sell_price for row in rows],
                )
            if prices and slot < min(map(len, prices)):
                buys, sells = prices
                buy, sell = buys[slot], sells[slot]
                # Midnight may have seeded counters before today's first plan.
                prev = book.get("sample")
                if (
                    prev
                    and book.get("date") == now.date().isoformat()
                    and prev["buy"] is None
                ):
                    prior = datetime.fromtimestamp(prev["timestamp"], now.tzinfo)
                    idx = local_slot_index(prior)
                    book["sample"] = dict(prev, buy=buys[idx], sell=sells[idx])
                    book.setdefault("basis", buys[idx] / cfg.charge_efficiency)
            sample = {
                "stock": soc * cfg.capacity_kwh / 100,
                "import": imported,
                "export": exported,
                "buy": buy,
                "sell": sell,
            }
            for key in ("charged", "discharged"):
                entity = self._resolve_emaldo_entity(f"battery_{key}_today")
                value = self._read_counter_kwh(entity) if entity else None
                if value is not None and math.isfinite(value) and value >= 0:
                    sample[key] = value
        export_profit.capture(book, now, sample, cfg)

    async def _capture_export_profit(self, now: datetime) -> None:
        now = dt_util.as_local(now)
        async with self._export_lock:
            if self._export_book.get("currency") != self.currency:
                self._export_book = {"currency": self.currency}
            book = self._export_book
            self._sample_export_profit(book, now, self._build_battery_config())
            # Persist only a changed ledger: with unavailable counters the book
            # stays identical and rewriting it every quarter-hour is wasted I/O.
            payload = json.dumps(book, sort_keys=True)
            if payload != self.__dict__.get("_export_book_written"):
                await self.hass.async_add_executor_job(
                    marker_io.atomic_write_json, self._export_book_path, book
                )
                self._export_book_written = payload
            self.async_update_listeners()

    def export_profit_report(self, yesterday: bool):
        now = dt_util.now()
        book = self._export_book
        if yesterday:
            date = (now.date() - timedelta(days=1)).isoformat()
            record = book.get("yesterday", {})
            valid = record.get("date") == date and record.get("complete", False)
            return (record.get("profit") if valid else None), {
                "date": date,
                "complete": valid,
            }
        date = now.date().isoformat()
        result = self._last_result
        if self._last_run is None or not self._ran_this_session:
            return None, {"date": date}
        plan_date = dt_util.as_local(self._last_run).date()
        if plan_date == now.date() - timedelta(days=1):
            result = self._last_result_tomorrow
        elif plan_date != now.date():
            result = None
        if result is None:
            return None, {"date": date}
        cfg = self._build_battery_config()
        # Include the metered part of an in-flight sale without committing
        # extra snapshots or counting that energy again in the plan.
        projection = dict(book) if book.get("currency") == self.currency else {}
        self._sample_export_profit(projection, now, cfg)
        value = export_profit.forecast(projection, result, now, cfg)
        realized = round(projection["revenue"] - projection["cost"], 4)
        return value, {
            "date": date,
            "coverage_from_midnight": projection["complete"],
            "realized_net": realized,
            "remaining_net": round(value - realized, 4) if value is not None else None,
        }

    async def _async_check_locale_money(self) -> None:
        """Report locale money problems without changing anything."""
        entry = self.config_entry
        stored = {**entry.data, **entry.options}

        override = stored.get(CONF_CURRENCY) or ""
        sensor_keys = [CONF_SPOT_SENSOR]
        if stored.get(CONF_SELL_PRICE_SOURCE, PRICE_SOURCE_EMALDO) == PRICE_SOURCE_SENSOR:
            sensor_keys.append(CONF_SELL_PRICE_SENSOR)
        sensor_id, sensor_unit = "", None
        for key in sensor_keys:
            candidate = stored.get(key) or ""
            state = self.hass.states.get(candidate) if candidate else None
            unit = state.attributes.get("unit_of_measurement") if state else None
            if should_warn_mismatch(sensor_unit=unit, currency=self.currency, override=override):
                sensor_id, sensor_unit = candidate, unit
                break

        if should_warn_mismatch(
            sensor_unit=sensor_unit,
            currency=self.currency,
            override=override,
        ):
            _LOGGER.warning(
                "Price sensor %s reports %s but this install uses %s; "
                "set the integration's currency to match",
                sensor_id,
                sensor_unit,
                self.currency,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "price_sensor_currency_mismatch",
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="price_sensor_currency_mismatch",
                translation_placeholders={
                    "sensor": sensor_id,
                    "unit": str(sensor_unit),
                    "currency": self.currency,
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "price_sensor_currency_mismatch")

        flagged = unreviewed_fields(stored, self._locale)
        if flagged:
            _LOGGER.warning(
                "These money settings still hold pre-locale defaults that do not "
                "match this install (%s): %s. Review them in the options flow.",
                self.currency,
                ", ".join(flagged),
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "monetary_defaults_unreviewed",
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="monetary_defaults_unreviewed",
                translation_placeholders={
                    "currency": self.currency,
                    "fields": ", ".join(flagged),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "monetary_defaults_unreviewed")

    async def run_optimizer(
        self, reason: str = "manual", force: bool = True
    ) -> OptimizationResult | None:
        """Run the optimizer and push the schedule to Emaldo.

        Args:
            reason: Why this run was triggered.
            force: If False, skip if conditions haven't changed enough.
        """
        # Issue #23: stamp the ATTEMPT before every guard below.  The SoC gate,
        # _should_reoptimize and the no_change_skip return all mean "we looked
        # and the plan in hand still stands" — and during an evening peak a
        # plan that is being followed is skipped, so _last_run (which only
        # advances when a new plan was produced) goes stale in exactly the
        # steady state forced sell depends on.  The forced-sell age gate reads
        # this timestamp instead.  Stamped on entry, so it also advances on a
        # run that bails out below.
        self._last_optimizer_attempt = dt_util.now()
        await self._maybe_sweep_expired_rules(reason)
        await self._restore_runtime_state()
        # Issue #23: the real safety net for an unclean restart mid-window —
        # async_shutdown cannot await, so nothing else runs after a power loss.
        await self._restore_sell_back_to_grid_after_restart()
        _LOGGER.info("Optimizer triggered: reason=%s, force=%s", reason, force)

        # Battery SoC guard (issue #16): a failed/unavailable SoC read must
        # never degrade into an all-idle plan pushed over a profitable window.
        # On a fresh restart the restored value is only a stale projection, so
        # wait for a live reading instead of running (the startup watcher
        # retries until Emaldo publishes).  Once a live read has succeeded this
        # session, a later transient failure can reuse the last measured value.
        soc = self._get_battery_soc()
        if soc is None:
            if self._soc_live_read_done and self._last_known_soc is not None:
                soc = self._last_known_soc
                soc_input_flag = "last_known"
                _LOGGER.warning(
                    "Battery SoC unreadable — reusing last known value %.1f%%",
                    soc,
                )
            else:
                # Fresh restart with no live read yet: wait for a real SoC
                # instead of pushing a plan from the stale restored projection.
                # The startup watcher retries until Emaldo publishes.  Keep the
                # noise down — no per-trigger warning — and emit a single
                # warning once the wait exceeds 60s.
                now = dt_util.now()
                if self._soc_wait_start is None:
                    self._soc_wait_start = now
                    _LOGGER.debug(
                        "Battery SoC unreadable — waiting for first live reading"
                    )
                elif (now - self._soc_wait_start).total_seconds() > 60:
                    _LOGGER.warning(
                        "Battery SoC still unreadable after 1 minute — Emaldo has "
                        "not published a reading; optimization will retry on the "
                        "next trigger"
                    )
                await self._capture_run_snapshot(
                    {
                        "state": "soc_guard_skip",
                        "planned": False,
                        "reason": reason,
                        "initial_soc_pct": None,
                        "last_known_soc": self._last_known_soc,
                    }
                )
                return None
        else:
            self._last_known_soc = soc
            self._soc_live_read_done = True
            self._soc_wait_start = None  # live reading acquired — wait over
            soc_input_flag = "read"

        # Gather data
        try:
            await self._refresh_native_price_forecasts()
            prices_today, prices_tomorrow = self._parse_price_data()
            sell_today, sell_tomorrow = self._parse_export_price_data()
            if sell_today is None:
                prices_today = None
            if sell_tomorrow is None:
                prices_tomorrow = None
            transfer_today, transfer_tomorrow = None, None
            if self.config.get(CONF_TRANSFER_FEE_SENSOR, "").strip():
                transfer_today, transfer_tomorrow = self._parse_transfer_prices()
                if transfer_today is None:
                    prices_today = None
                if transfer_tomorrow is None:
                    prices_tomorrow = None
        except Exception:
            _LOGGER.exception("Failed to parse price data")
            await self._capture_run_snapshot(
                {
                    "state": "price_parse_error",
                    "planned": False,
                    "reason": reason,
                    "initial_soc_pct": soc,
                }
            )
            return None

        if prices_today is None:
            _LOGGER.error("Cannot optimize: no prices available")
            await self._capture_run_snapshot(
                {
                    "state": "no_prices",
                    "planned": False,
                    "reason": reason,
                    "initial_soc_pct": soc,
                }
            )
            return None

        _LOGGER.info(
            "Prices parsed: %d slots, range %.4f–%.4f €/kWh",
            len(prices_today), min(prices_today), max(prices_today),
        )

        # Solar forecast scale — manual config or auto-tuned from the accuracy
        # sidecar (executor: sync file I/O). Applied inside _get_solcast_forecast.
        self._solar_scale = await self.hass.async_add_executor_job(
            self._resolve_solar_scale
        )
        solar = self._get_solcast_forecast("today")
        self._auto_base_load_value = await self._fetch_auto_base_load_kw()
        cfg = self._build_battery_config()
        if transfer_today is not None:
            cfg.transfer_fees = transfer_today + (transfer_tomorrow or [])
        # Cache today's buy/sell prices for the 15-min cost-capture task.
        # Must run before the force=False skip (below) and the SoC bail:
        # otherwise the cache stays None after a restart and every captured
        # slot is dropped at the `_cost_buy_prices is None` guard, so the
        # realized-cost sensors never receive a state write.
        buy_prices, _ = compute_prices(prices_today, cfg)
        _, sell_prices = compute_prices(sell_today, cfg)
        self._cost_price_date = dt_util.now().date()
        self._cost_buy_prices = buy_prices
        self._cost_sell_prices = sell_prices
        now_slot = _current_slot_index()

        _LOGGER.info("Battery SoC: %.1f%%, start_slot: %d", soc, now_slot)

        # Record actual SoC for dashboard overlay
        self._actual_soc.append({"t": dt_util.now().isoformat(), "soc": round(soc, 1)})
        if len(self._actual_soc) > 192:  # cap at ~2 days of 15-min readings
            self._actual_soc = self._actual_soc[-192:]

        if not force and self._last_result is not None:
            if not self._should_reoptimize(soc, cfg):
                # Read-only: refresh the planned-vs-actual comparison so the
                # divergence check sees the elapsed slots, not the figure from
                # the last full run (issue #36).
                fresh_accuracy = self._compute_plan_accuracy(now_slot)
                if fresh_accuracy is not None:
                    self._last_plan_accuracy_for_reassert = dict(fresh_accuracy)
                reassert, reassert_reason = self._should_reassert_override()
                if reassert:
                    _LOGGER.info(
                        "Plan unchanged but enforcement unconfirmed (%s) — re-asserting override",
                        reassert_reason,
                    )
                    self._last_reassert_reason = reassert_reason
                    push_overrides = await self._push_schedule(
                        self._last_result, self._last_result_tomorrow
                    )
                    self.async_set_updated_data({
                        "result": self._last_result,
                        "result_tomorrow": self._last_result_tomorrow,
                        "last_run": self._last_run.isoformat() if self._last_run else None,
                        "reason": reason,
                        "activated_time": self._activated_time,
                    })
                    await self._capture_run_snapshot(
                        {
                            "state": "reassert_push",
                            "planned": True,
                            "reason": reason,
                            "initial_soc_pct": soc,
                            "start_slot": now_slot,
                            "push_overrides": push_overrides,
                            "reassert_reason": reassert_reason,
                            "push_fail_streak": self._push_fail_streak,
                        }
                    )
                    return self._last_result
                _LOGGER.info("Skipping optimization — no significant changes")
                await self._capture_run_snapshot(
                    {
                        "state": "no_change_skip",
                        "planned": False,
                        "reason": reason,
                        "initial_soc_pct": soc,
                        "start_slot": now_slot,
                    }
                )
                return self._last_result

        # Compute plan accuracy from the previous result before overwriting
        new_accuracy = self._compute_plan_accuracy(now_slot)
        if new_accuracy is not None:
            self._plan_accuracy = new_accuracy
            self._last_plan_accuracy_for_reassert = dict(new_accuracy)
            await self._record_accuracy(new_accuracy)
            _LOGGER.info(
                "Plan accuracy (%d slots): "
                "discharge planned=%.3f actual=%s kWh, "
                "charge planned=%.3f actual=%s kWh, "
                "solar planned=%.3f actual=%s kWh",
                new_accuracy["elapsed_slots"],
                new_accuracy["planned_discharge_kwh"],
                new_accuracy.get("actual_discharge_kwh", "N/A"),
                new_accuracy["planned_charge_kwh"],
                new_accuracy.get("actual_charge_kwh", "N/A"),
                new_accuracy["planned_solar_kwh"],
                new_accuracy.get("actual_solar_kwh", "N/A"),
            )

        # Durable solar regime — once-per-day EWMA over the scaled-forecast
        # solar fraction; engaged → Case A discharge must beat the cheapest
        # known future recharge (executor: sync file I/O on first load).
        self._solar_regime = await self.hass.async_add_executor_job(
            self._resolve_solar_regime, sum(solar) * SLOT_DURATION_HOURS, cfg
        )

        # Run optimizer — prices_today is already 96 x 15-min in €/kWh
        buy_prices, _ = compute_prices(prices_today, cfg)
        _, sell_prices = compute_prices(sell_today, cfg)
        # Cache today's buy/sell prices so the 15-min cost-capture task can
        # price each slot without re-parsing spot data.
        self._cost_buy_prices = buy_prices
        self._cost_sell_prices = sell_prices
        emaldo_modes = self._read_emaldo_internal_modes()

        # ── User schedule layer: apply rules as a mask ──────────────
        user_rules = self._read_user_rules()
        today_date = dt_util.now().date()
        has_user_rules = any(
            r.action != "optimizer" or r.level != LEVEL_DEFAULT
            for r in user_rules
        )
        # These layers rewrite the schedule after optimization. A funded trade
        # requires its charge and sell commands to survive as a pair.
        if (has_user_rules or self.config.get(CONF_PRECHARGE_ENABLED, False)
                or self.config.get(CONF_CHEAP_CHARGE_ENABLED, False)):
            cfg.fund_grid_exports = False
        solar_tomorrow: list[float] | None = None
        previous_result_tomorrow = self._last_result_tomorrow
        active_sale_seconds = None
        decision_time = dt_util.now()
        elapsed = (decision_time.minute % 15) * 60 + decision_time.second + decision_time.microsecond / 1e6
        winner = expand_day(user_rules, today_date)[now_slot] if has_user_rules else None
        if (cfg.forced_sell_enabled and self._manual_selling_active and self._manual_selling_ours
                and self._emaldo_control_enabled
                and soc_input_flag == "read" and self._manual_selling_is_on()
                and elapsed > 0 and now_slot == decision_time.hour * 4 + decision_time.minute // 15
                and self._last_result and now_slot in self._last_result.sell_slots
                and (winner is None or (winner.action == "optimizer" and winner.pv_sell == "inherit"))):
            measured_soc = self._get_battery_soc()
            if measured_soc is not None:
                soc = measured_soc
                active_sale_seconds = 900 - elapsed
        if prices_tomorrow is not None:
            solar_tomorrow = self._get_solcast_forecast("tomorrow")
            buy_tom, _ = compute_prices(prices_tomorrow, cfg, start_slot=SLOTS_PER_DAY)
            _, sell_tom = compute_prices(sell_tomorrow, cfg)
            continuous_result = optimize(
                buy_prices + buy_tom,
                sell_prices + sell_tom,
                solar + solar_tomorrow,
                cfg,
                start_slot=now_slot,
                initial_soc_pct=soc,
                active_sale_seconds=active_sale_seconds,
                enable_pv_strategy=self._pv_strategy_enabled,
                emaldo_modes=emaldo_modes,
                solar_regime_engaged=bool(
                    self._solar_regime and self._solar_regime["engaged"]
                ),
                total_slots=len(buy_prices) + len(buy_tom),
                solar_confidence_ratio=self._solar_confidence_ratio_for_plan(),
            )
            result, result_tomorrow = _split_result_by_day(continuous_result)
            result.reason = reason
            self._last_result_tomorrow = result_tomorrow
        else:
            result = optimize(
                buy_prices,
                sell_prices,
                solar,
                cfg,
                start_slot=now_slot,
                initial_soc_pct=soc,
                active_sale_seconds=active_sale_seconds,
                enable_pv_strategy=self._pv_strategy_enabled,
                emaldo_modes=emaldo_modes,
                solar_regime_engaged=bool(
                    self._solar_regime and self._solar_regime["engaged"]
                ),
                future_min_buy=None,
                future_grid_charge_needed=None,
                # Today-only plan: `solar` and `available_soc` are today's
                # forecast only, so the ratio must be today's too. Taking the
                # min across both days let an overcast *tomorrow* inflate
                # today's overnight charge — the waste #26 exists to stop.
                solar_confidence_ratio=self._get_solar_confidence_ratio("today"),
            )
            result.reason = reason
            self._last_result_tomorrow = None

        if has_user_rules:
            decision = (result.trace or {}).get("active_sale", {})
            partial = decision.get("decision") in ("continue", "stop")
            slot_soc = result.slots[now_slot].soc_after
            self._apply_user_mask(
                result, user_rules, solar, cfg, start_slot=now_slot + int(partial),
                initial_soc_kwh=cfg.capacity_kwh * (slot_soc if partial else soc) / 100.0,
                day=today_date,
            )
            if partial:
                # Only the future full slots are re-simulated by the rule mask.
                slot = result.slots[now_slot]
                slot.soc_after = slot_soc
                result.charge_slots += slot.action in ("charge", "charge_floor")
                result.discharge_slots += slot.action == "discharge"
                result.idle_slots += slot.action == "idle"
        else:
            self._last_sources = None
            self._last_user_winners = None

        self._last_result = result
        self._last_run = dt_util.now()
        self._last_reason = reason

        if self.config.get(CONF_PRECHARGE_ENABLED, DEFAULT_PRECHARGE_ENABLED):
            # tail_result/tail_solar make the forecast continuous across the
            # day boundary: the pre-charge rewrites today's trajectory, and
            # tomorrow's copy has to be re-simulated from the SoC that rewrite
            # leaves behind, not from the pre-pre-charge projection.  Both are
            # None on a today-only run, which keeps that path unchanged.
            self._apply_speculative_precharge(
                result, buy_prices, solar, cfg, soc, now_slot,
                tomorrow_available=bool(prices_tomorrow),
                tomorrow_end_soc_pct=(
                    previous_result_tomorrow.slots[-1].soc_after
                    if previous_result_tomorrow and previous_result_tomorrow.slots
                    else None
                ),
                tail_result=(
                    self._last_result_tomorrow
                    if prices_tomorrow is not None
                    else None
                ),
                tail_solar=solar_tomorrow if prices_tomorrow is not None else None,
            )

        if self.config.get(CONF_CHEAP_CHARGE_ENABLED, DEFAULT_CHEAP_CHARGE_ENABLED):
            # Issue #31: nearly free energy before tomorrow's prices exist.
            # Layered after the pre-charge, with the same cross-day tail so
            # the forecast stays continuous at midnight.
            self._apply_cheap_charge(
                result, buy_prices, solar, cfg, soc, now_slot,
                tomorrow_available=bool(prices_tomorrow),
                tail_result=(
                    self._last_result_tomorrow
                    if prices_tomorrow is not None
                    else None
                ),
                tail_solar=solar_tomorrow if prices_tomorrow is not None else None,
            )

        if prices_tomorrow is not None:
            result_tomorrow = self._last_result_tomorrow
            if has_user_rules and result_tomorrow is not None:
                self._apply_user_mask(
                    result_tomorrow, user_rules, solar_tomorrow or [], cfg,
                    start_slot=0,
                    initial_soc_kwh=cfg.capacity_kwh * (
                        result.slots[-1].soc_after if result.slots else cfg.soc_min
                    ) / 100.0,
                    day=(today_date + timedelta(days=1)),
                    store_last=False,
                    store_last_tomorrow=True,
                )
            _LOGGER.info(
                "Tomorrow optimization: savings=%.4f€, C=%d D=%d I=%d",
                result_tomorrow.total_profit,
                result_tomorrow.charge_slots,
                result_tomorrow.discharge_slots,
                result_tomorrow.idle_slots,
            )
        self._set_sale_deadline(result, decision_time)
        # Retain the scale THIS plan ran under — the next run's accuracy
        # comparison attributes the solar error to it (raw-basis recovery).
        self._last_result_solar_scale = self._solar_scale

        # Snapshot actual values for next accuracy comparison
        self._last_run_slot = now_slot
        self._last_run_initial_soc = soc
        self._last_run_actual_snapshot = {
            "discharge": self._read_emaldo_sensor_float("battery_discharged_today"),
            "charge": self._read_emaldo_sensor_float("battery_charged_today"),
            "solar": self._read_emaldo_sensor_float("solar_energy_today"),
        }
        if self.config.get(CONF_SOLAR_ACTUAL_SENSOR, ""):
            # None when unavailable → accuracy skips the record rather than
            # mixing the Emaldo estimate into the external-source series.
            self._last_run_actual_snapshot["solar_ext"] = (
                self._read_external_solar_kwh()
            )

        await self.hass.async_add_executor_job(self._save_runtime_state)

        # Push today (+ tomorrow if available) to Emaldo
        push_overrides = await self._push_schedule(result, self._last_result_tomorrow)

        # Start/stop emaldo manual selling for the forced-sell window
        await self._manage_manual_selling(result, dt_util.now())

        # Apply PV sell strategy (controls Emaldo third-party PV switch)
        await self._apply_pv_strategy(result)

        # Compute activated time window
        self._compute_activated_time(result, self._last_result_tomorrow)

        # Update HA state
        self.async_set_updated_data({
            "result": result,
            "result_tomorrow": self._last_result_tomorrow,
            "last_run": self._last_run.isoformat(),
            "reason": reason,
            "activated_time": self._activated_time,
        })

        await self._capture_run_snapshot({
            "state": "planned",
            "planned": True,
            "version": _INTEGRATION_VERSION,
            "entry": self._entry.entry_id,
            "reason": reason,
            "start_slot": now_slot,
            "soc_input": soc_input_flag,
            "initial_soc_pct": soc,
            "emaldo_control_enabled": self._emaldo_control_enabled,
            "config": _config_snapshot(cfg),
            "pv_strategy_enabled": self._pv_strategy_enabled,
            "transfer_fees": cfg.transfer_fees,
            "sell_prices_today_raw": sell_today,
            "sell_prices_tomorrow_raw": sell_tomorrow or [],
            "prices_today_raw": prices_today,
            "prices_tomorrow_raw": prices_tomorrow or [],
            "buy_prices": buy_prices,
            "sell_prices": sell_prices,
            "solar_15min": solar,
            "emaldo_modes": emaldo_modes or [],
            "plan": [
                {
                    "index": sp.index,
                    "action": sp.action,
                    "slot_value": sp.slot_value,
                    "buy_price": sp.buy_price,
                    "sell_price": sp.sell_price,
                    "solar_kw": sp.solar_kw,
                    "load_kw": sp.load_kw,
                    "soc_after": sp.soc_after,
                    "profit": sp.profit,
                    "export_kwh": sp.export_kwh,
                }
                for sp in result.slots
            ],
            "trace": result.trace,
            "push_overrides": push_overrides,
            "outcome": {
                "total_profit": result.total_profit,
                "baseline_cost": result.baseline_cost,
                "actual_cost": round(result.baseline_cost - result.total_profit, 4),
                "net_profit": result.net_profit,
                "wear_cost_total": result.wear_cost_total,
                "cycled_kwh": result.cycled_kwh,
                "charge_slots": result.charge_slots,
                "discharge_slots": result.discharge_slots,
                "idle_slots": result.idle_slots,
            },
            "emaldo_plan_cost": result.emaldo_cost,
            "emaldo_plan": {
                "grid_cost": round(result.emaldo_grid_cost, 4),
                "wear_cost": round(result.emaldo_wear_total, 4),
                "cycled_kwh": round(result.emaldo_cycled_kwh, 3),
                "import_kwh": round(result.emaldo_import_kwh, 3),
                "export_kwh": round(result.emaldo_export_kwh, 3),
            },
            "push_state": {
                **self.enforcement_state,
                "ok_at": self._last_push_ok_at.isoformat() if self._last_push_ok_at else None,
                "fail_streak": self._push_fail_streak,
                "reassert_reason": self._last_reassert_reason,
            },
        })

        self._ran_this_session = True
        return result

    def _set_sale_deadline(self, result, decision_time: datetime) -> None:
        """Stop a retained sale at its energy limit or quarter-hour boundary."""
        if self._unsub_sale_deadline is not None:
            self._unsub_sale_deadline()
            self._unsub_sale_deadline = None
        decision = (result.trace or {}).get("active_sale", {})
        if decision.get("decision") != "continue":
            return
        slot = result.slots[decision["slot"]]
        if slot.action != "force_sell":
            decision["decision"] = "overridden"
            result.sell_slots.remove(slot.index)
            result.sell_target_kwh = sum(result.slots[i].sell_kwh for i in result.sell_slots)
            return
        self._sale_no_restart_before = decision_time.timestamp() + decision["remaining_seconds"]
        deadline = decision_time.timestamp() + decision["continue_seconds"]
        decision["deadline"] = deadline

        async def finish(_now):
            async with self._forced_sell_lock:
                if self._last_result is not result:
                    return
                self._unsub_sale_deadline = None
                await self._stop_manual_selling_locked()
            await self.run_optimizer(reason="active_sale_ended", force=True)

        self._unsub_sale_deadline = async_call_later(
            self.hass, max(0.0, deadline - dt_util.now().timestamp()), finish
        )

    async def _manage_manual_selling_locked(
        self,
        result: OptimizationResult,
        now: datetime,
        start_allowed: bool = True,
    ) -> None:
        """Body of _manage_manual_selling; caller already holds the lock.

        Entity-driven: stages the sell target on the manual_selling_target
        number, then toggles the manual_selling switch (verified write).
        Guards: integration disabled, no device id, plan window over,
        plan without forced sell, PV-only window (no battery target —
        inverter auto-exports surplus), start not allowed (stale plan).
        """
        # First: a restart may have lost track of a running sale.  This has to
        # run BEFORE the start path below, or a recovered sale could be opened
        # a second time for the same window.
        await self._reconcile_manual_selling_marker_locked(now)
        # Ahead of every branch below, including the stop ones: self-completion
        # is the NORMAL end of a sale, and the stop branches would issue the
        # turn_off that only exists to interrupt one still running.
        await self._check_manual_selling_self_completion_locked(result, now)
        if not self._emaldo_control_enabled:
            await self._stop_manual_selling_locked()
            return
        winners = getattr(self, "_last_user_winners", None)
        if winners:
            winner = winners[now.hour * 4 + now.minute // 15]
            if winner.action != "optimizer" or winner.pv_sell != "inherit":
                await self._stop_manual_selling_locked()
                return
        window = result.sell_slots or []
        decision = (result.trace or {}).get("active_sale", {})
        if decision.get("decision") == "continue":
            # This partial amount belongs exclusively to the existing device
            # target. A user stop or device self-completion must not reopen it.
            if now.timestamp() < decision["deadline"]:
                return
            if self._manual_selling_active and (
                self._manual_selling_started_at is None
                or self._manual_selling_started_at.timestamp() <= decision["deadline"]
            ):
                await self._stop_manual_selling_locked()
            window = [s for s in window if s != decision["slot"]]
        if result.sell_target_kwh <= 0:
            # PV-only window: the inverter exports on its own.  Stop rather
            # than return — a plan that turns PV-only while a sell window is
            # active must switch the sale off, or the tick re-enters this
            # no-op every 5 minutes forever and the switch stays on.
            await self._stop_manual_selling_locked()
            return
        if not window:
            # The window is never empty here, and that is what keeps the
            # empty window a reliable quarantine discriminator: a real
            # sale marker always carries the window the sale was started
            # for, so the reconcile can tell a quarantined marker (an
            # undeletable file overwritten with an empty-window payload)
            # from a real one by the window alone.
            await self._stop_manual_selling_locked()
            return
        # Deliberately BELOW every stop branch: they need no device, so
        # resolve_emaldo_device() returning False must not strand a sale that is
        # live — losing the binding mid-window used to strand both switches.
        now_slot = int(now.hour * 4 + now.minute // 15)
        if now_slot < window[0] or now_slot > window[-1]:
            await self._stop_manual_selling_locked()
            return
        # It gates opening a new sale only: fail-safe, no selling, device idles.
        if not self.resolve_emaldo_device():
            return
        if not start_allowed:
            # Stale plan (issue #23): outside the window it has already stopped
            # above; inside, the window is still allowed to close but nothing
            # new may be opened from it.
            return
        if self._manual_selling_active:
            return  # already running; re-check handled by plan re-run
        if now.timestamp() < self._sale_no_restart_before:
            return
        # Issue #23: Emaldo ends a manual sale by itself once the staged target
        # has been exported, so _manual_selling_active goes False on its own
        # with the window still open.  Without this key the next tick would see
        # an open window, no active sale, and open a SECOND one — exporting the
        # battery twice for one window.  Cleared by value: a new date or a
        # different window is a different key, so tomorrow's window sells again.
        if self._manual_selling_served == (now.date(), tuple(window)):
            _LOGGER.debug(
                "Forced-sell window %s on %s already served; not starting "
                "another sale", window, now.date(),
            )
            return
        # Issue #23 cause B: Emaldo refuses Manual selling while Sell back to
        # grid is off, so the window exports nothing.  Enable it for the window
        # and remember that we did, so the stop path can put the user's setting
        # back.  Ahead of the start below because the prerequisite has to be
        # in place for the switch call to mean anything.
        sell_back_id = self._sell_back_to_grid_switch_id()
        sb_state = self.hass.states.get(sell_back_id)
        sb_on: bool | None = None
        if sb_state is not None and sb_state.state in ("on", "off"):
            sb_on = sb_state.state == "on"
        if sb_on is None:
            # Same deference as _ensure_pv_switch_matches_plan: with no real
            # state there is no telling "the user owns it" from "we owe a
            # restore", so no write and nothing remembered.  Return without
            # staging the target either: the sale cannot satisfy its
            # prerequisite without the switch, and _manual_selling_active
            # would short-circuit every later tick at the guard below, leaving
            # the enable unapplied for the rest of the window behind a DEBUG
            # line.  Bailing here leaves the tick's next pass free to retry.
            _LOGGER.debug(
                "Sell back to grid state unknown (entity=%s) - deferring the "
                "forced-sell start to the next tick", sell_back_id,
            )
            return
        elif not sb_on:
            # Issue #23: the in-memory flag below cannot survive a power loss
            # and nothing else records that the switch is ours.  Write the
            # marker BEFORE the enable: a crash in between then leaves a
            # marker, which costs one redundant turn_off at the next startup,
            # instead of an enable nobody recorded, which is what strands the
            # switch.  force=True because the flag is deliberately not set
            # until the switch is verified on below.
            await self.hass.async_add_executor_job(
                self._persist_sell_back_to_grid_state, True
            )
            try:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": sell_back_id}, blocking=True,
                )
                self._sell_back_to_grid_saved = True
                _LOGGER.info(
                    "Temporarily enabled %s for the forced-sell window",
                    sell_back_id,
                )
            except Exception as err:  # noqa: BLE001
                # _sell_back_to_grid_saved stays None: nothing was flipped, so
                # nothing is owed this session, and the flag must only claim a
                # restore once the switch is actually on.  The marker written
                # above must therefore go too — it exists to record OUR flip,
                # and no flip happened.  Left in place it would outlive the
                # window and make the next startup issue a turn_off against a
                # switch we never turned on, taking away a setting the user owns.
                # The crash case is unaffected: a power cut between the write
                # and the enable leaves a marker, which is one harmless
                # turn_off.  A prerequisite that will not flip must not skip
                # the sale - the window is time-boxed, so a lost sale is
                # recoverable.
                await self.hass.async_add_executor_job(
                    self._clear_sell_back_to_grid_state
                )
                _LOGGER.error(
                    "Could not enable %s for forced sell: %s", sell_back_id, err
                )
        switch_id = self._manual_selling_switch_id()
        # The sale's own switch, remembered for the read-backs below: the
        # entity resolution is deliberately uncached, so resolving it again
        # could land on a different switch.
        self._manual_selling_switch_id_cached = switch_id
        number_id = self._resolve_emaldo_entity(
            "manual_selling_target", domain="number"
        ) or "number.power_store_manual_selling_target"
        target = result.sell_target_kwh
        if decision.get("decision") == "continue":
            target = sum(result.slots[s].sell_kwh for s in window)
        staged_kwh = _stage_sell_target_kwh(target)
        if staged_kwh <= 0.0:
            # D2: the remaining plan cannot be honoured in whole kWh without
            # exporting more than is left (see the helper). Emaldo rounds
            # whatever is staged with int(round(...)), so any value at all
            # here can overshoot. Opening a sale we cannot size correctly is
            # worse than not selling at all -- the window is time-boxed, so
            # the next run re-evaluates it.
            _LOGGER.info(
                "Not starting forced sell: %.2f kWh left cannot be staged "
                "in whole kWh without overshooting the SoC floor",
                result.sell_target_kwh,
            )
            return
        try:
            # Stage target first (number entity merges intended target while
            # switch is off), then enable the verified-write switch.
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": number_id, "value": staged_kwh},
                blocking=True,
            )
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": switch_id}, blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to start manual selling: %s", err)
            return
        # Flag set only after the switch is verified on, so a failed or
        # interrupted start never leaves a flag claiming to be selling.
        self._manual_selling_active = True
        # This session owns the sale from here on.
        self._manual_selling_ours = True
        # A new start, so nothing leaks in from the last one.
        self._manual_selling_seen_on = False
        self._manual_selling_started_at = now
        self._manual_selling_start_window = (now.date(), tuple(window))
        # Counted HERE, where the attempt actually happens, so a retry does
        # not reset its own budget.  The give-up verdict belongs to the
        # reconcile above, which runs before this path and reads the count
        # this line leaves behind.
        self._manual_selling_start_attempts += 1
        # The window is SERVED only once the sale is OBSERVED running, read back
        # right here.  Stamping it on the turn_on call alone was the trap:
        # turn_on does not raise when Emaldo refuses the sale, so a refused start
        # consumed the window with nothing sold and no retry.  _manual_selling_
        # seen_on is what a later reconcile reads, and only this read-back or a
        # tick that caught the sale running can set it.
        # The read-back reads the switch that was commanded, not a fresh
        # resolution: the resolution is deliberately uncached, so resolving
        # it again could land on a different switch.
        if self._manual_selling_is_on(switch_id):
            self._manual_selling_seen_on = True
            self._manual_selling_served = (now.date(), tuple(window))
            # The restart marker is written HERE, with the served stamp, and NOT
            # one line earlier: it records that a sale was OBSERVED running.
            # A start Emaldo refuses does not raise, so a marker written before
            # this read-back would exist for a sale that never ran, and the
            # recovery would consume the window as if it had been served - with
            # nothing exported.
            await self._write_sale_marker(now, tuple(window))
        _LOGGER.info(
            "Manual selling started: %d slots, target %.1f kWh",
            len(window), staged_kwh,
        )
