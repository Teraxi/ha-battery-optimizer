"""A continued partial sale keeps running until its deadline (extended engine)."""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from . import test_forced_sell_tick as base  # noqa: E402  (reuse its fixtures)

INSIDE = base.INSIDE


class ExtendedActiveSaleTests(unittest.TestCase):
    def setUp(self):
        from custom_components.battery_optimizer.extended import coordinator as module

        self.coord_mod = module
        self.C = module.ExtendedBatteryOptimizerCoordinator

    def _coord(self, **kwargs):
        return base.ForcedSellTickTests._coord(self, **kwargs)

    def test_partial_sale_keeps_running_until_deadline_without_restaging(self):
        c = self._coord(active=True)
        result = c._last_result
        decision = {"slot": 31, "decision": "continue", "remaining_seconds": 365,
                    "continue_seconds": 180}
        result.trace = {"active_sale": decision}
        result.slots[31].action = "force_sell"
        c._check_manual_selling_self_completion_locked = AsyncMock()
        c.run_optimizer = AsyncMock()
        with patch.object(self.coord_mod.dt_util, "now", return_value=INSIDE), \
                patch.object(self.coord_mod, "async_call_later") as later:
            c._set_sale_deadline(result, INSIDE)
            _, delay, finish = later.call_args.args
            self.assertEqual(delay, 180)
            asyncio.run(c._manage_manual_selling_locked(result, INSIDE, True))
            c._stop_manual_selling_locked.assert_not_awaited()
            # A device or user stop is final during this remainder.
            c._manual_selling_active = False
            asyncio.run(c._manage_manual_selling_locked(result, INSIDE, True))
            c.hass.services.async_call.assert_not_awaited()
            asyncio.run(finish(INSIDE + timedelta(seconds=180)))
            c._stop_manual_selling_locked.assert_awaited_once()
            c.run_optimizer.assert_awaited_once_with(
                reason="active_sale_ended", force=True
            )

    def test_deadline_timer_is_cancelled_on_shutdown(self):
        c = self._coord()
        cancel = unittest.mock.Mock()
        c._unsub_sale_deadline = cancel
        with patch.object(self.C.__mro__[1], "async_shutdown") as parent:
            c.async_shutdown()
        cancel.assert_called_once()
        self.assertIsNone(c._unsub_sale_deadline)
        parent.assert_called_once()