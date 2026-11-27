"""Daily export accounting, with a restart, midnight and a running sale."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from . import load_extended_module

ep = load_extended_module("export_profit")
cfg = SimpleNamespace(
    charge_efficiency=0.8,
    discharge_efficiency=0.9,
    wear_cost_per_kwh=0.03,
    capacity_kwh=20,
)
midnight = datetime(2026, 11, 19, tzinfo=timezone(timedelta(hours=2)))


def meter(stock, bought=0, sold=0, buy=0.12, sell=0.25):
    return {"stock": stock, "import": bought, "export": sold, "buy": buy, "sell": sell}


def test_cost_basis_and_household_allocation():
    basis, _, _ = ep.account(10, 14, 2.5, 0, 0.2, 0.3, 0.1, cfg)
    assert basis == pytest.approx(1.5 / 14)
    _, revenue, cost = ep.account(14, 12, 0, 1, 0.2, 0.3, basis, cfg)
    assert revenue == 0.3
    assert cost == pytest.approx((basis + 0.03) / 0.9)
    assert ep.account(12, 12, 0, 1, 0.12, -0.1, basis, cfg)[1:] == (-0.1, 0)


@pytest.mark.parametrize("resets", [True, False])
def test_settles_last_quarter_then_freezes_yesterday(resets):
    book = {}
    for slot in range(96):
        ep.capture(book, midnight + timedelta(minutes=15 * slot), meter(10), cfg)
    ep.capture(book, midnight + timedelta(days=1, seconds=-1), meter(9, sold=0.9), cfg)
    book = json.loads(json.dumps(book))
    next_day = midnight + timedelta(days=1)
    ep.capture(book, next_day, meter(9, sold=0 if resets else 0.9), cfg)
    expected = {"date": "2026-11-19", "profit": 0.045, "complete": True}
    assert book["yesterday"] == expected
    ep.capture(book, next_day, meter(9), cfg)
    ep.capture(book, next_day + timedelta(minutes=15), meter(9, sold=1), cfg)
    assert book["yesterday"] == expected
    assert book["revenue"] == pytest.approx(0.25 if resets else 0.025)


def test_gap_and_first_partial_day_never_claim_complete_profit():
    book = {}
    ep.capture(book, midnight + timedelta(hours=20), meter(10), cfg)
    ep.capture(book, midnight + timedelta(days=1), meter(8, sold=2), cfg)
    assert not book["yesterday"]["complete"]
    assert book["revenue"] == 0


def test_forecast_adds_only_unsold_fraction_and_keeps_negative_margin():
    book = {}
    start = midnight + timedelta(hours=14)
    now = start + timedelta(minutes=7, seconds=30)
    ep.capture(book, start, meter(10, sell=0.15), cfg)
    ep.capture(book, now, meter(9, sold=0.9, sell=0.15), cfg)
    row = SimpleNamespace(
        action="force_sell",
        index=56,
        soc_after=40,
        grid_import_kwh=0,
        grid_export_kwh=1.8,
        export_kwh=0,
        sell_kwh=1.8,
        buy_price=0.12,
        sell_price=0.15,
    )
    result = SimpleNamespace(
        slots=[None] * 56 + [row], trace={}, thirdparty_pv_slots=[]
    )
    assert ep.forecast(book, result, now, cfg) == pytest.approx(
        1.8 * 0.15 - 2 * (0.15 + 0.03)
    )


def test_planned_direct_solar_export_credits_only_household_surplus():
    book = {}
    ep.capture(book, midnight, meter(10), cfg)
    row = SimpleNamespace(
        action="idle",
        index=0,
        solar_kw=6,
        load_kw=2,
        soc_after=50,
        grid_import_kwh=0,
        grid_export_kwh=0,
        export_kwh=0,
        sell_kwh=0,
        buy_price=0.12,
        sell_price=0.25,
    )
    result = SimpleNamespace(slots=[row], trace={}, thirdparty_pv_slots=[False])
    assert ep.forecast(book, result, midnight, cfg) == pytest.approx(0.25)


@pytest.mark.parametrize("seconds,complete", [(901, True), (1800, False)])
def test_meter_tick_jitter(seconds, complete):
    book = {}
    ep.capture(book, midnight, meter(10), cfg)
    ep.capture(book, midnight + timedelta(seconds=seconds), meter(9, sold=0.9), cfg)
    assert book["complete"] is complete
    assert book["revenue"] == pytest.approx(0.225 if complete else 0)
