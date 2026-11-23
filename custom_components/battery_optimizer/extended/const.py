"""Constants used only by the extended optimizer engine."""

from ..const import (  # noqa: F401  (re-exported for the extended package)
    CONF_OPTIMIZER_ENGINE,
    DEFAULT_OPTIMIZER_ENGINE,
    ENGINE_EXTENDED,
    ENGINE_STANDARD,
)

# Config entry keys (stored only when the extended engine is selected).
CONF_SELL_PRICE_SOURCE = "sell_price_source"
CONF_SELL_PRICE_SENSOR = "sell_price_sensor"
CONF_TRANSFER_FEE_SENSOR = "transfer_fee_sensor"
CONF_EXCLUDED_LOAD_SENSOR = "excluded_load_power_sensor"
CONF_FUND_GRID_EXPORTS = "fund_grid_exports"

# Export price source.  "same" reuses the import source (the upstream
# behaviour) and is the default, including for entries saved before the
# option existed, so switching engines never silently moves sell prices.
SELL_PRICE_SOURCE_SAME = "same"

EXTENDED_CONF_KEYS = (
    CONF_SELL_PRICE_SOURCE,
    CONF_SELL_PRICE_SENSOR,
    CONF_TRANSFER_FEE_SENSOR,
    CONF_EXCLUDED_LOAD_SENSOR,
    CONF_FUND_GRID_EXPORTS,
)
