"""Compare grid prefill with storing forecast PV and selling natural overflow."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import NamedTuple

from .const import SLOTS_PER_DAY, SLOT_DURATION_HOURS, SLOT_IDLE


class Flow(NamedTuple):
    action: str
    command: int
    energy: float
    imported: float
    exported: float
    cycled: float


@dataclass
class SolarPlan:
    flows: list[Flow]
    pv_flags: list[bool]
    charge_slots: tuple[int, ...]
    margin: float


def _project(slots, solar, cfg, start, initial, flags, edits):
    """Price the same flow records that will be used to build the schedule."""
    energy = initial
    high = cfg.capacity_kwh * cfg.soc_max / 100
    low = cfg.capacity_kwh * cfg.soc_min / 100
    drain = cfg.idle_drain_per_slot_kwh
    flows = []
    cost = 0.0
    for i in range(start, len(slots)):
        slot = slots[i]
        action = edits.get(i, slot.action)
        command = slot.slot_value
        pv = solar[i] * SLOT_DURATION_HOURS
        load = cfg.base_load_kw * SLOT_DURATION_HOURS
        # Value energy at the household meter. Bypassing battery charging
        # does not turn a simultaneously self-consumed PV kWh into a sale.
        net = load - pv
        imported = max(0.0, net)
        surplus = max(0.0, -net)
        exported = 0.0 if flags[i] else surplus
        if not flags[i]:
            surplus = 0.0
        cycled = 0.0
        if action in ("charge", "charge_floor"):
            if i in edits:
                command = int(cfg.soc_max)
            ceiling = min(high, command * cfg.capacity_kwh / 100)
            ac = min(
                cfg.max_charge_per_slot_kwh,
                max(0.0, ceiling - energy) / cfg.charge_efficiency,
            )
            imported += max(0.0, ac - surplus)
            exported += max(0.0, surplus - ac)
            energy += ac * cfg.charge_efficiency
        elif net > 0 and action == "discharge":
            # Use the same household-discharge convention as optimize():
            # AC draw times discharge efficiency covers load and reduces SoC.
            cycled = min(
                net,
                cfg.max_discharge_per_slot_kwh,
                max(0.0, energy - low) / cfg.discharge_efficiency,
            )
            imported -= cycled * cfg.discharge_efficiency
            energy -= cycled * cfg.discharge_efficiency
        else:
            ac = min(
                surplus,
                cfg.max_charge_per_slot_kwh,
                max(0.0, high - energy) / cfg.charge_efficiency,
            )
            energy += ac * cfg.charge_efficiency
            exported += surplus - ac
        energy = max(0.0, energy - drain)
        if action == "discharge":
            command = (
                256 - int(max(cfg.soc_min, energy * 100 / cfg.capacity_kwh))
            ) & 255
        elif action == "idle":
            command = SLOT_IDLE
        flows.append(Flow(action, command, energy, imported, exported, cycled))
        cost += (
            imported * slot.buy_price
            - exported * slot.sell_price
            + cycled * cfg.wear_cost_per_kwh
        )
    return flows, cost


def plan_solar_prefill(
    slots, solar, cfg, start, initial, pv_flags, competing_margin, allow_pv_switch
):
    """Try the cheapest charge quarters preceding each forecast surplus block."""
    if not any(p > cfg.base_load_kw for p in solar[start:]):
        return None
    # Match the live switch's daily forecast threshold on both days.
    eligible = set()
    if allow_pv_switch:
        for day in range(0, len(slots), SLOTS_PER_DAY):
            end = min(day + SLOTS_PER_DAY, len(slots))
            if sum(solar[day:end]) * SLOT_DURATION_HOURS >= cfg.pv_min_forecast_kwh:
                eligible.update(range(max(day, start), end))
    pv_flags = [on or i not in eligible for i, on in enumerate(pv_flags)]
    routes = [pv_flags]
    if eligible:
        store = [True] * len(slots)
        direct = store.copy()
        for i in eligible:
            if (
                solar[i] > cfg.base_load_kw
                and slots[i].action not in ("charge", "charge_floor")
                and slots[i].sell_price
                > max(slots[i].buy_price, cfg.pv_sell_min_price_spread)
            ):
                direct[i] = False
        for flags in (store, direct):
            if flags not in routes:
                routes.append(flags)
    ref, ref_cost = _project(slots, solar, cfg, start, initial, pv_flags, {})
    low = cfg.capacity_kwh * cfg.soc_min / 100
    replacement = (
        max(0.0, min(s.buy_price for s in slots[start:])) / cfg.charge_efficiency
    )
    best = None
    # Only change the energy source used to fill the pack. An extra kWh at
    # the end is never booked as a sale. Preserve pre-existing floor recovery.
    i = start
    while i < len(slots):
        begin = i
        while i < len(slots) and solar[i] <= cfg.base_load_kw:
            i += 1
        if i == len(slots):
            break
        options = [j for j in range(begin, i) if slots[j].action in ("idle", "charge")]
        while i < len(slots) and solar[i] > cfg.base_load_kw:
            i += 1
        order = sorted(options, key=lambda j: (slots[j].buy_price, -j))
        edits = {j: "idle" for j in options if slots[j].action == "charge"}
        step = cfg.max_charge_per_slot_kwh * cfg.charge_efficiency
        if step <= 0:
            return None
        max_count = ceil(
            (cfg.usable_kwh + len(options) * cfg.idle_drain_per_slot_kwh) / step
        )
        for count in range(min(max_count, len(order)) + 1):
            if count:
                edits[order[count - 1]] = "charge"
            for flags in routes:
                flows, cost = _project(slots, solar, cfg, start, initial, flags, edits)
                if any(
                    f.energy + 1e-8 < min(low, old.energy) for f, old in zip(flows, ref)
                ):
                    continue
                margin = (
                    ref_cost
                    - cost
                    - max(0.0, ref[-1].energy - flows[-1].energy) * replacement
                )
                added_export = sum(
                    f.exported - old.exported for f, old in zip(flows, ref)
                )
                if (
                    margin <= max(1e-8, competing_margin)
                    or margin < max(0.0, added_export) * cfg.forced_sell_min_profit
                    or (best and margin <= best.margin + 1e-8)
                ):
                    continue
                best = SolarPlan(flows, flags, tuple(sorted(order[:count])), margin)
    return best
