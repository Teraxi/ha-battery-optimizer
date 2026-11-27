"""Calculated export margin at the meter, in the configured currency."""

from datetime import datetime, timedelta


def account(
    stock,
    end_stock,
    imported,
    exported,
    buy,
    sell,
    basis,
    cfg,
    *,
    charged=None,
    discharged=None,
):
    """Value imported charge at the meter; blend it with zero-cost solar charge."""
    gained = max(0.0, end_stock - stock) if charged is None else charged
    used = max(0.0, stock - end_stock) if discharged is None else discharged
    if gained:
        charge_cost = min(imported, gained / cfg.charge_efficiency) * buy
        basis = (stock * basis + charge_cost) / (stock + gained)
    battery_out = min(exported, used * cfg.discharge_efficiency)
    battery_draw = battery_out / cfg.discharge_efficiency
    revenue = exported * sell
    cost = battery_draw * (basis + cfg.wear_cost_per_kwh)
    return basis, revenue, cost


def _delta(previous, current, key):
    before, after = previous.get(key), current.get(key)
    if before is None or after is None or after < before:
        return None
    return after - before


# Longest gap between the pre-midnight sample and the first sample of the new
# day that still counts as one boundary (a late midnight tick).
_MIDNIGHT_GAP_S = 61


def _in_first_minute(moment):
    return moment.hour == 0 and moment.minute == 0


def capture(book, now, sample, cfg):
    """Accumulate each observation once, closing the old local day before rollover."""
    day = now.date().isoformat()
    stamp = int(now.timestamp())
    old = book.get("sample")
    if old and stamp <= old["timestamp"]:
        return
    previous_day = book.get("date")
    # The midnight tick may run late (busy event loop, slow I/O); any sample in
    # the first minute of the day still closes the previous day.
    midnight = now.hour == 0 and now.minute == 0
    changed_day = previous_day != day
    if not previous_day:
        book.update(
            date=day, revenue=0.0, cost=0.0, complete=midnight and sample is not None
        )
    ok = old is not None and sample is not None and stamp - old["timestamp"] <= 905 and (
        stamp // 900 - old["timestamp"] // 900 <= 1
    )
    if ok:
        imported = sample["import"] - old["import"]
        exported = sample["export"] - old["export"]
        if changed_day and midnight and stamp - old["timestamp"] <= _MIDNIGHT_GAP_S:
            # Daily counters may already have reset. The preceding 23:59:59
            # reading contains their final observable value before the reset.
            imported = max(0.0, imported)
            exported = max(0.0, exported)
        if (
            not changed_day
            and _in_first_minute(datetime.fromtimestamp(old["timestamp"], now.tzinfo))
        ):
            # Some daily meters publish their reset just after the boundary.
            if imported < 0:
                imported = sample["import"]
            if exported < 0:
                exported = sample["export"]
        ok = (
            imported >= -1e-8
            and exported >= -1e-8
            and old["buy"] is not None
            and old["sell"] is not None
            and (not changed_day or midnight)
        )
        if ok:
            basis, revenue, cost = account(
                old["stock"],
                sample["stock"],
                max(0.0, imported),
                max(0.0, exported),
                old["buy"],
                old["sell"],
                book["basis"],
                cfg,
                charged=_delta(old, sample, "charged"),
                discharged=_delta(old, sample, "discharged"),
            )
            book.update(
                basis=basis, revenue=book["revenue"] + revenue, cost=book["cost"] + cost
            )
    if not ok and previous_day:
        book["complete"] = False
    if changed_day and previous_day:
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        book["yesterday"] = {
            "date": previous_day,
            "profit": round(book["revenue"] - book["cost"], 4),
            "complete": book["complete"] and midnight and previous_day == yesterday,
        }
        book.update(
            date=day, revenue=0.0, cost=0.0, complete=midnight and sample is not None
        )
    if sample is not None:
        if not ok and sample["buy"] is not None:
            book["basis"] = sample["buy"] / cfg.charge_efficiency
        book["sample"] = {**sample, "timestamp": stamp}
    else:
        book["sample"] = None


def forecast(book, result, now, cfg):
    """Add unmetered planned exports to the realized portion of the same day."""
    sample = book.get("sample")
    if not sample or "basis" not in book:
        return None
    stock, basis = sample["stock"], book["basis"]
    profit = book["revenue"] - book["cost"]
    slot = now.hour * 4 + now.minute // 15
    remaining = 900 - (now.minute % 15 * 60 + now.second)
    decision = (result.trace or {}).get("active_sale", {})
    for row in result.slots[slot:]:
        if row.action == "none":
            continue
        # Legacy forced sells append sell_kwh to an ordinary plan; funded
        # exports already include it in grid_export_kwh.
        exported = max(row.grid_export_kwh, row.export_kwh + row.sell_kwh)
        if (
            row.index < len(result.thirdparty_pv_slots)
            and not result.thirdparty_pv_slots[row.index]
        ):
            pv_surplus = max(0.0, row.solar_kw - row.load_kw) * 0.25
            exported = max(exported, pv_surplus + row.sell_kwh)
        fraction = 1.0
        if row.index == slot:
            duration = 900
            if decision.get("slot") == slot and decision.get("remaining_seconds"):
                duration = decision["remaining_seconds"]
            fraction = min(1.0, remaining / duration)
            if (
                decision.get("slot") == slot
                and decision.get("decision") == "continue"
                and decision.get("deadline")
            ):
                fraction = min(
                    1.0,
                    max(0.0, decision["deadline"] - now.timestamp())
                    / max(1e-9, decision["continue_seconds"]),
                )
        exported *= fraction
        end_stock = row.soc_after * cfg.capacity_kwh / 100
        basis, revenue, cost = account(
            stock,
            end_stock,
            row.grid_import_kwh * fraction,
            exported,
            row.buy_price,
            row.sell_price,
            basis,
            cfg,
            discharged=fraction * row.sell_kwh / cfg.discharge_efficiency,
        )
        profit += revenue - cost
        stock = end_stock
    return round(profit, 4)
