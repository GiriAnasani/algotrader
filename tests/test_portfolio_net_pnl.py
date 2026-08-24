from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from math import fsum, inf, nan
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator, PortfolioNetPnLResult
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.realized_net_pnl import RealizedNetPnLAggregator, RealizedNetPnLResult
from trading.unrealized_pnl import UnrealizedPnLAggregator, UnrealizedPnLResult


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry, NOW,
                          exit, NOW, PositionState.CLOSED)


def active(side=PositionSide.CE, entry=100.0):
    return ManagedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry, NOW,
                           PositionState.OPEN)


def realized_aggregator(brokerage=20.0):
    charge = OptionTradeChargesCalculator(
        OptionChargeSchedule(brokerage, .0015, .0003553, .000001, .00003, .18)
    )
    return RealizedNetPnLAggregator(NetPnLCalculator(charge))


def aggregator(brokerage=20.0):
    return PortfolioNetPnLAggregator(realized_aggregator(brokerage))


def result(**overrides):
    values = dict(closed_trade_count=2, open_position_count=1,
                  realized_gross_pnl=100.0, realized_total_charges=20.0,
                  realized_net_pnl=80.0, unrealized_gross_pnl=10.0,
                  portfolio_net_pnl=90.0)
    values.update(overrides)
    return PortfolioNetPnLResult(**values)


@pytest.mark.parametrize("value", [None, object(), True, "aggregator"])
def test_constructor_requires_realized_net_aggregator(value):
    with pytest.raises(TypeError):
        PortfolioNetPnLAggregator(value)


def test_flat_portfolio_returns_exact_zero_result():
    assert aggregator().aggregate([]) == PortfolioNetPnLResult(
        0, 0, 0.0, 0.0, 0.0, 0.0, 0.0
    )


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_realized_only_trade_has_zero_unrealized(side):
    summary = aggregator().aggregate([closed(side)])
    assert summary.closed_trade_count == 1
    assert summary.open_position_count == 0
    assert summary.unrealized_gross_pnl == 0.0
    assert summary.portfolio_net_pnl == summary.realized_net_pnl


def test_mixed_realized_ce_pe_history():
    positions = [closed(PositionSide.CE), closed(PositionSide.PE, 105, 100)]
    summary = aggregator().aggregate(positions)
    assert summary.closed_trade_count == 2
    assert summary.realized_gross_pnl == 0.0
    assert summary.realized_total_charges > 0
    assert summary.realized_net_pnl < 0


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize("reference,sign", [(105, 1), (95, -1)])
def test_unrealized_only_uses_gross_open_position_pnl(side, reference, sign):
    summary = aggregator().aggregate([], active(side), reference)
    assert summary.closed_trade_count == 0
    assert summary.open_position_count == 1
    assert summary.realized_gross_pnl == 0.0
    assert summary.realized_total_charges == 0.0
    assert summary.realized_net_pnl == 0.0
    assert summary.unrealized_gross_pnl * sign > 0
    assert summary.portfolio_net_pnl == summary.unrealized_gross_pnl


@pytest.mark.parametrize("exit_price,reference", [
    (110, 110), (110, 90), (90, 120), (90, 80), (100, 100)])
def test_combined_realized_net_and_unrealized_gross(exit_price, reference):
    summary = aggregator().aggregate(
        [closed(entry=100, exit=exit_price)], active(entry=100), reference
    )
    assert summary.closed_trade_count == summary.open_position_count == 1
    assert summary.portfolio_net_pnl == fsum(
        (summary.realized_net_pnl, summary.unrealized_gross_pnl)
    )


def test_order_counts_are_forwarded_and_change_realized_values(monkeypatch):
    realized = realized_aggregator()
    original = realized.aggregate
    seen = []
    def spy(positions, buy_count, sell_count):
        seen.append((positions, buy_count, sell_count))
        return original(positions, buy_count, sell_count)
    monkeypatch.setattr(realized, "aggregate", spy)
    positions = [closed()]
    custom = PortfolioNetPnLAggregator(realized).aggregate(positions, buy_order_count=2,
                                                           sell_order_count=3)
    normal = aggregator().aggregate(positions)
    assert seen == [(positions, 2, 3)]
    assert custom.realized_total_charges > normal.realized_total_charges
    assert custom.realized_net_pnl < normal.realized_net_pnl


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.5, "1"])
@pytest.mark.parametrize("argument", ["buy_order_count", "sell_order_count"])
def test_invalid_order_counts_propagate(argument, count):
    with pytest.raises(ValueError):
        aggregator().aggregate([], **{argument: count})


@pytest.mark.parametrize("positions", [None, {}, "positions", {1}, object(), [object()]])
def test_invalid_closed_history_propagates(positions):
    with pytest.raises(TypeError):
        aggregator().aggregate(positions)


@pytest.mark.parametrize("position", [object(), True, "position"])
def test_invalid_active_position_propagates(position):
    with pytest.raises(TypeError):
        aggregator().aggregate([], position, 100)


def test_missing_or_spurious_reference_price_propagates():
    with pytest.raises(ValueError):
        aggregator().aggregate([], None, 100)
    with pytest.raises(ValueError):
        aggregator().aggregate([], active(), None)


@pytest.mark.parametrize("price", [True, 0, -1, nan, inf, "100"])
def test_invalid_reference_price_propagates(price):
    with pytest.raises(ValueError):
        aggregator().aggregate([], active(), price)


def test_exact_dependency_values_and_identities_are_consumed(monkeypatch):
    realized = realized_aggregator()
    positions = [closed()]
    position = active(PositionSide.PE)
    realized_result = RealizedNetPnLResult(1, 100, 20, 80, 1, 0, 0)
    unrealized_result = UnrealizedPnLResult(1, -30)
    realized_seen = []
    unrealized_seen = []
    def realized_spy(supplied, buy_count, sell_count):
        realized_seen.append((supplied, buy_count, sell_count))
        return realized_result
    def unrealized_spy(self, supplied, reference):
        unrealized_seen.append((supplied, reference))
        return unrealized_result
    monkeypatch.setattr(realized, "aggregate", realized_spy)
    monkeypatch.setattr(UnrealizedPnLAggregator, "aggregate", unrealized_spy)
    summary = PortfolioNetPnLAggregator(realized).aggregate(
        positions, position, 95, 2, 3
    )
    assert summary == PortfolioNetPnLResult(1, 1, 100, 20, 80, -30, 50)
    assert realized_seen == [(positions, 2, 3)]
    assert unrealized_seen == [(position, 95)]


def test_inputs_are_not_mutated():
    positions = [closed()]
    position = active()
    before_positions = tuple(positions)
    before_position = position
    aggregator().aggregate(positions, position, 105)
    assert tuple(positions) == before_positions
    assert positions[0] is before_positions[0]
    assert position is before_position


def test_result_is_frozen_with_exact_fields():
    value = result()
    assert [field.name for field in fields(value)] == [
        "closed_trade_count", "open_position_count", "realized_gross_pnl",
        "realized_total_charges", "realized_net_pnl", "unrealized_gross_pnl",
        "portfolio_net_pnl"]
    with pytest.raises(FrozenInstanceError):
        value.portfolio_net_pnl = 0


@pytest.mark.parametrize("field,bad", [
    ("closed_trade_count", -1), ("closed_trade_count", True),
    ("open_position_count", -1), ("open_position_count", 2),
    ("open_position_count", True)])
def test_result_rejects_invalid_counts(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


@pytest.mark.parametrize("field", ["realized_gross_pnl", "realized_total_charges",
    "realized_net_pnl", "unrealized_gross_pnl", "portfolio_net_pnl"])
@pytest.mark.parametrize("bad", [True, nan, inf, -inf, object()])
def test_result_rejects_invalid_numeric_fields(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


def test_result_rejects_negative_charges_and_inconsistent_totals():
    with pytest.raises(ValueError):
        result(realized_total_charges=-1)
    with pytest.raises(ValueError):
        result(realized_net_pnl=81)
    with pytest.raises(ValueError):
        result(portfolio_net_pnl=91)


def test_source_is_pure_and_has_no_open_charge_accrual():
    source = Path("trading/portfolio_net_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = ("optioncharges", "optiontradecharges", "charge schedule",
                 "positionmanager", "positionstore", "brokerposition", "kite",
                 "zerodha", "marketdata", "websocket", "strategy", "readiness",
                 "authorization", "persistence", "network", "database", "datetime",
                 "time.", "retry", "poll", "thread", "session", "capital", "margin",
                 "account")
    assert all(term not in source for term in forbidden)
