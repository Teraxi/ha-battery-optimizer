# Extended optimizer engine

The integration ships two optimizer engines. Pick one in the configuration
form (**Optimizer engine**):

| Engine | What it is |
|---|---|
| `standard` (default) | The upstream optimizer (`optimizer.py`, `coordinator.py`). Maintained by the original author; nothing in this document applies to it. |
| `extended` | An independently maintained copy of the optimizer plus a coordinator subclass, in `custom_components/battery_optimizer/extended/`. It adds the features below. |

Choosing `extended` opens a second form (**Extended optimizer**) with the
settings that only this engine reads. Switching engines reloads the entry; the
extended-only settings are dropped when you switch back to `standard`.

## Maintenance boundary

* `optimizer.py` and `coordinator.py` are not modified by the extended engine.
  The upstream files can be updated from upstream without conflicts.
* `extended/optimizer.py` is a copy of the upstream optimizer with the changes
  below. When upstream optimizer changes should reach the extended engine, merge
  them into that file by hand.
* `extended/coordinator.py` subclasses the upstream coordinator and overrides
  only what differs (price sources, base-load exclusion, active-sale handling,
  export-profit accounting, listeners). The overridden methods are copies and
  need the same hand merge.
* The only edits in shared files are small hooks: the engine selector and
  the extended step in `config_flow.py`, the engine constants in `const.py`,
  coordinator selection and reload on engine change in `__init__.py`, an
  `extra_sensors` hook in `sensor.py`, and translations.

## Features

### Export price source (and upgrade behaviour)

**Export price source** has three values:

* `same` (default) – the export curve comes from the import source, exactly as
  in the standard engine. This is also what an entry saved before the option
  existed gets, so enabling the extended engine never moves sell prices to a
  different source.
* `emaldo` – the linked battery's own price curve, independent of the import
  source. A missing Emaldo export curve never falls back to the import price.
* `sensor` – a separate sensor or `input_number`, entered in the
  **Export price sensor** field.

Import VAT and transfer fees apply only to purchases; the sales commission is
deducted from exports. If an import helper already includes VAT, set the VAT
multiplier to 1. Use the same currency for all sources. Tomorrow is planned
only when import, export and any selected transfer forecast are available.
Changes to any selected source trigger a replan.

For a fixed contract select `sensor` as the import source and an
`input_number` or numeric sensor. A numeric state without forecast attributes
is held constant across the horizon. Timestamped `data` and Nord Pool style
`raw_today` / `raw_tomorrow` forecasts are also supported. Sensors that belong
to Home Assistant's core Nord Pool integration are resolved through the entity
registry and read with `nordpool.get_prices_for_date`; the scalar current-price
state is never substituted for a missing curve.

### External grid transfer tariff

**Grid transfer price sensor** overrides the fixed transfer fee. Leave it empty
to keep the fixed fee. No seasons, weekdays, times or entity IDs are hardcoded:
the tariff rules belong to your sensor or automation. The tariff includes taxes
and is not multiplied by the energy VAT multiplier.

A numeric state without forecast attributes is held constant. For future tariff
changes supply `data` entries with `start`, `end` and `price` (or timestamped
`raw_today` / `raw_tomorrow` entries with `value`). Units may be currency/kWh,
currency/MWh or cents/kWh. A rolling forecast may start at the current quarter
and must cover every remaining quarter today; tomorrow is planned only with a
full day of tariff and energy prices. Missing current or future periods prevent
a new plan instead of silently using the fixed fee. Quarter-hour aligned
intervals are expected; starts are inclusive, ends exclusive; timestamps are
interpreted in local time.

### Buying before a known export opportunity

Enable **Charge from the grid for a later profitable export** together with
**Enable forced sell**. The planner compares earlier grid purchases with a later
battery sale, using the effective import tariff and net feed-in price per
quarter, and ranks qualifying cycles by incremental euros over the ordinary
household plan. The configured minimum profit applies per exported kWh. The
search models one continuous manual sale on one calendar day at full inverter
output. It is a bounded search, not a global optimizer.

On net-import days this handles battery exports. With a forecast solar surplus
there is also a PV alternative: before each surplus period, compare keeping the
household schedule, omitting ordinary grid charging, and buying in increasing
numbers of the cheapest earlier quarters. New PV-switch windows require the PV
sell strategy and its daily forecast threshold. The trace key `solar_prefill`
records the chosen pre-charge slots and the estimated incremental benefit.

Custom schedule rules and the speculative / cheap-charge modes take precedence
(the option is switched off for the run while they are active). The
cut-PV-when-full option also takes precedence over the solar alternative.

### Replanning an active battery sale

During an optimizer-owned sale, a mid-quarter price update compares stopping now
with selling through the remaining minutes (measured SoC, forecast net household
demand, usable battery energy, wear and the cost of the following plan). The
device keeps its existing sale target; a timer ends the continuation at the
quarter boundary or earlier at the projected reserve and requests a fresh plan.
The **Active Sale Decision** sensor exposes the comparison: `advantage` is the
estimated benefit of continuing, `export_kwh` the remaining battery energy
available for export after household demand.

### Excluding a metered load from the automatic base load

With **Auto-tune base load** enabled, select a power sensor (W or kW) in
**Power sensor to subtract** to exclude an irregular load such as an EV charger.
The charger must be included in the household reading, report zero while idle
and provide Recorder mean statistics (`state_class: measurement`). The
optimizer subtracts the charger's mean from the household mean for each matching
hour (minimum zero) and averages complete local calendar days that have hourly
statistics for both sensors. The latest seven eligible days (at least three of
the last fourteen) are averaged and limited to 0.5–2 times the configured base
load.

### Daily export net profit

**Estimated export net profit today** combines calculated net income from
metered grid exports so far with the remaining local-day schedule.
**Realized export net profit yesterday** closes at local midnight and retains the
previous day's result, including losses. A day with incomplete meter or price
coverage has an unknown final value. Metered export earns the effective feed-in
price after commission; battery exports incur the estimated acquisition cost of
stored energy (moving average) plus wear. Avoided household purchases are
excluded, so the optimizer's sell-window advantage can differ from these
accounting values. The diagnostics need grid import and export energy counters,
the battery state of charge and effective prices.

### Additional entities

| Entity | Description |
|---|---|
| Import Price / Export Price | Current effective purchase / sale price per kWh, using the same calculation as the planner. Unknown when today's price data is missing. |
| Current Grid Transfer Fee | The transfer fee in force now (fixed, or the external tariff). The standard Grid Transfer Fee sensor still shows the fixed fee. |
| Active Sale Decision | The `continue` / `stop` decision of the active-sale comparison. |
| Estimated export net profit today / Realized export net profit yesterday | See above. |
