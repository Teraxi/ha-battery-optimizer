"""Price sensor normalization, independent of Home Assistant and tariff rules."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

from ..const import SLOTS_PER_DAY


FORECAST_KEYS = (
    "data",
    "raw_today",
    "raw_tomorrow",
    "today",
    "tomorrow",
    "tomorrow_valid",
)


def has_forecast_attributes(attributes: dict[str, Any]) -> bool:
    """True when the entity publishes a price forecast, not just a current value."""
    return any(key in attributes for key in FORECAST_KEYS)


def numeric_price(value: Any) -> float | None:
    """Reject unavailable, empty and non-finite values, retaining zero/negative prices."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) else None


def parse_sensor_prices(
    state: str,
    attributes: dict[str, Any],
    now: datetime,
    *,
    complete: bool = False,
) -> tuple[list[float] | None, list[float] | None]:
    """Return today's/tomorrow's prices in currency/kWh on 96 local-clock slots.

    A scalar with no forecast attributes is held constant until the sensor
    changes. Forecasts use data[{start, end?, price}] or Nord Pool raw_today /
    raw_tomorrow [{start, end?, value}]. Explicit intervals support sparse
    tariff schedules without embedding seasons or weekdays in the integration.

    Transfer forecasts (complete=True) must cover all remaining slots today
    and the whole of tomorrow. Missing elapsed slots use the current tariff. Energy price
    forecasts retain the integration's legacy >=10 samples / forward-fill rule.
    """
    if state in ("unknown", "unavailable"):
        return None, None
    unit = str(attributes.get("unit_of_measurement", "")).lower()
    cents = any(
        token in unit for token in ("snt", "cent", "c/kwh", "ct/kwh", "öre", "øre")
    )
    divisor = 1000.0 if "/mwh" in unit else 100.0 if cents else 1.0
    if not has_forecast_attributes(attributes):
        price = numeric_price(state)
        if price is None:
            return None, None
        return [price / divisor] * SLOTS_PER_DAY, [price / divisor] * SLOTS_PER_DAY

    data = attributes.get("data")
    if not isinstance(data, list) or not data:
        data = []
        for key in ("raw_today", "raw_tomorrow"):
            entries = attributes.get(key)
            if isinstance(entries, list):
                data.extend(entries)

    def local_time(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=now.tzinfo)
        return parsed.astimezone(now.tzinfo)

    entries = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        price = numeric_price(entry.get("price", entry.get("value")))
        if price is None:
            continue
        try:
            start = local_time(entry["start"])
            end = local_time(entry["end"]) if entry.get("end") is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if end is not None and end <= start:
            continue
        entries.append((start, end, price / divisor))

    # Infer an hourly interval only for a regular hourly series. Otherwise
    # entries without an explicit end cover a single quarter-hour.
    starts = sorted({start for start, _, _ in entries})
    steps = {(b - a).total_seconds() for a, b in zip(starts, starts[1:])}
    duration = timedelta(hours=1) if steps == {3600} else timedelta(minutes=15)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [
        midnight + timedelta(minutes=15 * slot) for slot in range(2 * SLOTS_PER_DAY)
    ]
    prices: list[float | None] = [None] * len(slots)
    for start, end, price in entries:
        end = end or start + duration
        # First quarter at or after the interval start; walk until its end.
        first = max(0, math.ceil((start - midnight).total_seconds() / 900))
        for slot in range(first, len(slots)):
            if slots[slot] >= end:
                break
            prices[slot] = price  # Later entries win if intervals overlap.

    def day_result(values: list[float | None]) -> list[float] | None:
        if complete and None in values:
            return None
        if sum(value is not None for value in values) < 10:
            return None
        previous = next(value for value in values if value is not None)
        result = []
        for value in values:
            if value is not None:
                previous = value
            result.append(previous)
        return result

    # Rolling forecasts may omit elapsed slots, but must never omit a
    # current/future slot. Pad only the elapsed prefix for the optimizer's
    # fixed day array; these values are estimates, not historical tariffs.
    current_slot = now.hour * 4 + now.minute // 15
    if complete and prices[current_slot] is not None:
        for slot in range(current_slot):
            if prices[slot] is None:
                prices[slot] = prices[current_slot]
    today = day_result(prices[:SLOTS_PER_DAY])
    tomorrow = day_result(prices[SLOTS_PER_DAY:])
    if attributes.get("tomorrow_valid") is False:
        tomorrow = None
    return today, tomorrow
