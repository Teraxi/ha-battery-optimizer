"""Grid-funded export on net-import days. Prices are per metered AC kWh."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

from ..const import SLOTS_PER_DAY, SLOT_DURATION_HOURS


@dataclass
class ExportCycle:
    charge: dict[int, int]  # slot -> integer SoC target accepted by Emaldo
    # Net meter export; the inverter also supplies the house while selling.
    sell: dict[int, float]
    margin: float


def funded_export_blocker(cfg, solar, start, pv_flags) -> str | None:
    """Why grid-funded export cannot be planned for this horizon, if it cannot.

    The cycle model assumes every remaining slot is a net-import slot with the
    PV switch on, so a slot with PV above base load anywhere in the horizon, or
    a planned PV-sell slot, disables it for the whole horizon.
    """
    if any(p > cfg.base_load_kw for p in solar[start:]):
        return "solar_surplus_in_horizon"
    if not all(pv_flags[start:]):
        return "pv_sell_planned"
    return None


def _charge_prefix(buy, chosen, start, first, initial, limit, low, cfg):
    """Replay integer charge commands and verify the floor before the sale."""
    drain = cfg.idle_drain_per_slot_kwh
    charge_rate = cfg.max_charge_per_slot_kwh
    energy = initial
    cost = 0.0
    charges = {}
    last_draw = last_soc = 0.0
    for i in range(start, first):
        if i in chosen:
            draw = min(
                charge_rate,
                max(0.0, limit - energy + drain) / cfg.charge_efficiency,
            )
            cost += buy[i] * draw
            energy += draw * cfg.charge_efficiency
            if draw > 1e-8:
                charges[i] = int(cfg.soc_max)
                last_draw = draw
        energy -= drain
        if i in charges:
            last_soc = energy
        if energy < low - 1e-8:
            return None
    return energy, cost, charges, last_draw, last_soc


def plan_export_cycle(
    buy, sell, solar, cfg, start, initial, keep_soc, keep_profit, reserve
):
    """Rank one executable sell window and prefixes of the cheapest prior buys.

    Simulate full-rate charging in time order; a whole-kWh export target can
    stop the last sell slot early. All outcomes retain enough energy for idle
    losses to the end of the horizon. Value a terminal shortfall against the
    competing plan at the cost of replenishing it; never credit leftover buys.
    """
    n = len(buy)
    if start >= n or any(p > cfg.base_load_kw for p in solar[start:]):
        return None
    limit = cfg.capacity_kwh * int(cfg.soc_max) / 100
    low = cfg.capacity_kwh * cfg.soc_min / 100
    rate = cfg.max_discharge_per_slot_kwh
    charge_rate = cfg.max_charge_per_slot_kwh
    if (
        min(rate, charge_rate, cfg.round_trip_factor) <= 0
        or not low <= initial <= limit
    ):
        return None

    drain = cfg.idle_drain_per_slot_kwh
    net_load = [(cfg.base_load_kw - p) * SLOT_DURATION_HOURS for p in solar]
    cheapest = min(buy[start:])
    replacement = max(0.0, cheapest) / cfg.charge_efficiency
    wear = cfg.wear_cost_per_kwh
    max_sell = max(sell[start:])
    best = None
    for first in range(start, n):
        if sell[first] < cfg.forced_sell_min_price or net_load[first] >= rate:
            continue
        # Cheap necessary bound, including the full cycle loss and wear.
        if (
            sell[first] * cfg.round_trip_factor - cheapest - wear
            < cfg.forced_sell_min_profit
        ):
            continue
        # On equal prices prefer buying late unless standby would reach the
        # floor before then. At the floor an early charge can be mandatory.
        early = initial - drain * (first - start) < low
        order = sorted(range(start, first), key=lambda i: (buy[i], i if early else -i))
        chosen = set()
        # At full inverter power, this many quarters cover all headroom
        # and standby losses before the sale. Bound the search on flat days.
        max_count = (
            ceil(
                (limit - initial + drain * (first - start))
                / (charge_rate * cfg.charge_efficiency)
            )
            + 1
        )
        for count in range(min(len(order), max_count) + 1):
            if count:
                idx = order[count - 1]
                if (
                    buy[idx] + wear + cfg.forced_sell_min_profit
                    > max_sell * cfg.round_trip_factor
                ):
                    break
                chosen.add(idx)
            prefix = _charge_prefix(buy, chosen, start, first, initial, limit, low, cfg)
            if prefix is None:
                continue
            energy, cost, charges, last_draw, last_soc = prefix
            # Only one civil day's contiguous sell window per device target.
            remaining = min(energy - low - reserve, energy - low - drain * (n - first))
            exported = revenue = avoided = discharged = 0.0
            amounts = {}
            end = min(n, (first // SLOTS_PER_DAY + 1) * SLOTS_PER_DAY)
            for i in range(first, end):
                spare = rate - net_load[i]
                if spare <= 0 or sell[i] < cfg.forced_sell_min_price:
                    break
                # The final reserve must survive standby up to this slot too.
                headroom = min(
                    remaining, energy - low - reserve - drain * (i - first + 1)
                )
                fraction = min(
                    1.0, (headroom - discharged) * cfg.discharge_efficiency / rate
                )
                cap = cfg.forced_sell_max_kwh_pd
                if cap > 0:
                    fraction = min(fraction, (cap - exported) / spare)
                if fraction <= 1e-9:
                    break
                amount = spare * fraction
                whole = floor(exported + amount + 1e-9)
                # Evaluate stopping as soon as the device hits this target.
                last = whole - exported
                if last > 1e-8:
                    runtime = last / spare
                    cycled = discharged + rate * runtime / cfg.discharge_efficiency
                    income = revenue + last * sell[i]
                    savings = avoided + runtime * net_load[i] * buy[i]
                    final = energy - cycled - drain * (n - first)
                    # The final charging quarter can stop at an integer SoC
                    # target instead of buying a full block for a partial sale.
                    saving = 0.0
                    target = int(cfg.soc_max)
                    if charges:
                        last_buy = next(reversed(charges))
                        spare_energy = min(
                            final - max(low, keep_soc), headroom - cycled
                        )
                        if spare_energy > 0 and buy[last_buy] > 0:
                            target = max(
                                1,
                                ceil(
                                    100 * (last_soc - spare_energy) / cfg.capacity_kwh
                                ),
                            )
                            trimmed = min(
                                last_draw,
                                max(0.0, last_soc - target * cfg.capacity_kwh / 100)
                                / cfg.charge_efficiency,
                            )
                            saving = trimmed * buy[last_buy]
                    margin = (
                        income
                        + savings
                        - cost
                        + saving
                        - cycled * wear
                        - keep_profit
                        - max(0.0, keep_soc - final) * replacement
                    )
                    if (
                        margin > 0
                        and margin >= cfg.forced_sell_min_profit * whole
                        and (best is None or margin > best.margin + 1e-9)
                    ):
                        targets = charges.copy()
                        if targets:
                            targets[last_buy] = min(targets[last_buy], target)
                        best = ExportCycle(targets, {**amounts, i: last}, margin)
                amounts[i] = amount
                exported += amount
                revenue += amount * sell[i]
                avoided += fraction * net_load[i] * buy[i]
                discharged += fraction * rate / cfg.discharge_efficiency
                if fraction < 1 - 1e-9:
                    break
            # More full charge slots cannot create additional saleable energy
            # once the prefix already fills the pack (apart from standby).
            if energy >= limit - 1e-8:
                break
    return best
