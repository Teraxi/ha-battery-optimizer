"""Compare the unelapsed part of an optimizer-owned sale with stopping now."""

from dataclasses import fields, replace

from .optimizer import (
    _FORCED_SELL_RESERVE_KWH,
    SLOT_DURATION_HOURS,
    SLOT_IDLE,
    OptimizationResult,
    _decompose_buy,
    _decompose_sell,
    optimize,
)


def plan_active_sale(
    buy, sell, solar, cfg, *, seconds_left, start_slot, initial_soc_pct, **options
):
    """Compare two plans with the same measured starting energy and time horizon."""
    i = start_slot
    ordinary = optimize(
        buy,
        sell,
        solar,
        cfg,
        start_slot=i,
        initial_soc_pct=initial_soc_pct,
        _emaldo_first_hours=seconds_left / 3600,
        enable_forced_sell=False,
        **options,
    )
    slot = ordinary.slots[i]
    # Leave a required floor recovery in place.
    if slot.action == "charge_floor" or i in ordinary.safeguard_slots:
        ordinary.trace["active_sale"] = {"slot": i, "decision": "charge_priority"}
        return ordinary

    hours = seconds_left / 3600
    initial = initial_soc_pct * cfg.capacity_kwh / 100
    drain = cfg.idle_power_kw * hours
    net_kw = cfg.base_load_kw - solar[i]
    floor = cfg.capacity_kwh * cfg.soc_min / 100 + _FORCED_SELL_RESERVE_KWH
    power = cfg.max_discharge_kw
    available = max(0.0, initial - drain - floor)
    duration = (
        min(hours, available * cfg.discharge_efficiency / power) if power > 0 else 0
    )
    draw = power * duration / cfg.discharge_efficiency
    # The meter sees only the surplus after the house takes its share.
    export = max(0.0, power - max(0.0, net_kw)) * duration
    can_sell = export > 0 and sell[i] >= cfg.forced_sell_min_price
    if cfg.forced_sell_max_kwh_pd > 0 and export > cfg.forced_sell_max_kwh_pd:
        duration *= cfg.forced_sell_max_kwh_pd / export
        export = cfg.forced_sell_max_kwh_pd
        draw = power * duration / cfg.discharge_efficiency

    fraction = hours / SLOT_DURATION_HOURS
    stop = slot
    # Scale the alternative action to the remaining time only.
    stop_soc = initial_soc_pct + (stop.soc_after - initial_soc_pct) * fraction
    imp = stop.grid_import_kwh * fraction
    out = stop.grid_export_kwh * fraction
    cycle = stop.cycled_kwh * fraction
    baseline_imp = max(0, net_kw) * hours
    baseline_out = max(0, -net_kw) * hours

    def tail(soc):
        if i + 1 == len(buy):
            return OptimizationResult(
                slots=list(ordinary.slots),
                trace={},
                emaldo_modes=ordinary.emaldo_modes,
                thirdparty_pv_slots=list(ordinary.thirdparty_pv_slots),
            )
        return optimize(
            buy, sell, solar, cfg, start_slot=i + 1, initial_soc_pct=soc, **options
        )

    def cost(plan, imported, exported, cycled):
        future = 0.0
        if i + 1 < len(buy):
            future = plan.baseline_cost - plan.net_profit
            # Legacy manual sales book their incremental benefit separately.
            if "grid_funded_export" not in (plan.trace or {}):
                future -= plan.sell_profit
        return (
            future
            + imported * buy[i]
            - exported * sell[i]
            + cycled * cfg.wear_cost_per_kwh
        )

    result = tail(stop_soc)
    margin = None
    keep = False
    if can_sell:
        sell_soc = 100 * (initial - drain - draw) / cfg.capacity_kwh
        sell_imp = max(0, net_kw - power) * duration + max(0, net_kw) * (
            hours - duration
        )
        sell_out = export + baseline_out
        candidate = tail(sell_soc)
        margin = cost(result, imp, out, cycle) - cost(
            candidate, sell_imp, sell_out, draw
        )
        # Equalize terminal inventory: a tail that simply ends with less
        # stored energy has deferred the purchase, not avoided its cost.
        stop_end = result.slots[-1].soc_after if i + 1 < len(buy) else stop_soc
        sell_end = candidate.slots[-1].soc_after if i + 1 < len(buy) else sell_soc
        deficit = max(0.0, stop_end - sell_end) * cfg.capacity_kwh / 100
        if deficit and i + 1 < len(buy):
            # Price a residual refill against spare charging capacity. A
            # single cheap quarter cannot replenish an arbitrary deficit.
            for j in sorted(range(i + 1, len(buy)), key=buy.__getitem__):
                row = candidate.slots[j]
                if row.action not in ("idle", "none", "charge", "charge_floor"):
                    continue
                previous = sell_soc if j == i + 1 else candidate.slots[j - 1].soc_after
                stored = max(0.0, row.soc_after - previous) * cfg.capacity_kwh / 100
                headroom = (
                    max(0.0, cfg.soc_max - row.soc_after) * cfg.capacity_kwh / 100
                )
                extra = min(
                    deficit,
                    headroom,
                    max(
                        0.0,
                        cfg.max_charge_per_slot_kwh * cfg.charge_efficiency - stored,
                    ),
                )
                margin -= extra * max(0.0, buy[j]) / cfg.charge_efficiency
                deficit -= extra
                if deficit <= 1e-9:
                    break
        # Beyond the published horizon only a replacement estimate is
        # possible. With a known tail, require the refill to fit inside it.
        refill_fits = deficit <= 1e-9 or i + 1 == len(buy)
        if deficit > 0:
            margin -= deficit * max(0.0, buy[-1]) / cfg.charge_efficiency
        keep = refill_fits and margin >= -0.000001
        if keep:
            result = candidate
            imp, out, cycle = sell_imp, sell_out, draw
    trace = {
        "slot": i,
        "decision": "continue" if keep else "stop",
        "remaining_seconds": seconds_left,
        "export_kwh": round(export, 6),
        "advantage": None if margin is None else round(margin, 6),
        "refill_fits": refill_fits if can_sell else None,
        "continue_seconds": duration * 3600 if keep else 0,
    }
    result.trace["active_sale"] = trace

    # A partial slot precedes the chosen tail. Book it once, on today's
    # side of the day split, including the same components as a normal slot.
    ie, it, tax, ic = _decompose_buy(buy[i], cfg, i)
    ee, _, ec = _decompose_sell(sell[i], cfg)
    base = baseline_imp * buy[i] - baseline_out * sell[i]
    profit = base - imp * buy[i] + out * sell[i]
    wear = cycle * cfg.wear_cost_per_kwh
    result.slots[i] = replace(
        stop,
        action="force_sell" if keep else stop.action,
        slot_value=SLOT_IDLE if keep else stop.slot_value,
        soc_after=sell_soc if keep else stop_soc,
        profit=profit,
        baseline_cost=base,
        grid_import_kwh=imp,
        grid_export_kwh=out,
        import_energy_cost=ie * imp,
        import_transfer_cost=it * imp,
        import_tax_cost=tax * imp,
        import_commission_cost=ic * imp,
        export_energy_revenue=ee * out,
        export_commission_cost=ec * out,
        export_kwh=out - export if keep else out,
        sell_kwh=export if keep else 0,
        sell_source="battery" if keep else "",
        cycled_kwh=cycle,
    )
    values = {
        "remaining_slots": 1,
        "total_profit": profit,
        "baseline_cost": base,
        "net_profit": profit - wear,
        "wear_cost_total": wear,
        "cycled_kwh": cycle,
        "grid_import_kwh": imp,
        "grid_export_kwh": out,
        "grid_energy": imp * ie,
        "grid_transfer": imp * it,
        "grid_tax": imp * tax,
        "grid_commission": imp * ic,
        "grid_export_energy": out * ee,
        "grid_export_commission": out * ec,
        "baseline_import_kwh": baseline_imp,
        "baseline_export_kwh": baseline_out,
        "baseline_import_cost": baseline_imp * buy[i],
        "baseline_export_revenue": baseline_out * sell[i],
        "baseline_import_energy": baseline_imp * ie,
        "baseline_import_transfer": baseline_imp * it,
        "baseline_import_tax": baseline_imp * tax,
        "baseline_import_commission": baseline_imp * ic,
        "baseline_export_energy": baseline_out * ee,
        "baseline_export_commission": baseline_out * ec,
    }
    if keep:
        result.sell_slots.insert(0, i)
        values.update(
            sell_target_kwh=export, sell_revenue=export * sell[i], sell_profit=margin
        )
    else:
        count = {
            "discharge": "discharge_slots",
            "charge": "charge_slots",
            "charge_floor": "charge_slots",
        }.get(stop.action, "idle_slots")
        values[count] = 1
    for key, value in values.items():
        setattr(result, key, getattr(result, key) + value)
        if result.day1_totals is not None:
            result.day1_totals[key] = result.day1_totals.get(key, 0) + value
    # Both alternatives share the independent Emaldo benchmark starting from
    # measured SoC, including the partial first slot. The tail by itself
    # would omit that interval and start the benchmark at the optimizer's SoC.
    for field in fields(OptimizationResult):
        key = field.name
        if key.startswith("emaldo_") and key != "emaldo_modes":
            setattr(result, key, getattr(ordinary, key))
            if result.day1_totals is not None and key in (ordinary.day1_totals or {}):
                result.day1_totals[key] = ordinary.day1_totals[key]
    return result
