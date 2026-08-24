from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from math import fsum, inf, nan
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.option_charges import (
    NetPnLCalculator,
    NetPnLResult,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.position import PositionSide, PositionState
from trading.realized_net_pnl import RealizedNetPnLAggregator, RealizedNetPnLResult


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0, quantity=65):
    return ClosedPosition(
        side, f"NIFTY26AUG25000{side.value}", quantity, entry, NOW, exit, NOW,
        PositionState.CLOSED,
    )


def net_calculator(brokerage=20.0):
    schedule = OptionChargeSchedule(brokerage, .0015, .0003553, .000001, .00003, .18)
    return NetPnLCalculator(OptionTradeChargesCalculator(schedule))


def aggregator(brokerage=20.0):
    return RealizedNetPnLAggregator(net_calculator(brokerage))


def result(**overrides):
    values = dict(trade_count=3, gross_pnl=10.0, total_charges=3.0, net_pnl=7.0,
                  net_winning_trades=1, net_losing_trades=1,
                  net_breakeven_trades=1)
    values.update(overrides)
    return RealizedNetPnLResult(**values)


@pytest.mark.parametrize("value", [None, object(), True, "calculator"])
def test_aggregator_requires_actual_net_calculator(value):
    with pytest.raises(TypeError):
        RealizedNetPnLAggregator(value)


@pytest.mark.parametrize("positions", [[], ()])
def test_empty_history_returns_exact_zero_result(positions):
    assert aggregator().aggregate(positions) == RealizedNetPnLResult(
        0, 0.0, 0.0, 0.0, 0, 0, 0
    )


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize("entry,exit,classification", [
    (100, 105, "winner"), (105, 100, "loser"), (100, 100, "loser"),
    (100, 100.1, "loser")])
def test_single_trade_classification_uses_net(side, entry, exit, classification):
    summary = aggregator().aggregate([closed(side, entry, exit)])
    assert summary.trade_count == 1
    assert summary.net_winning_trades == (classification == "winner")
    assert summary.net_losing_trades == (classification == "loser")
    assert summary.net_breakeven_trades == 0


def test_exact_zero_net_is_breakeven(monkeypatch):
    calc = net_calculator()
    monkeypatch.setattr(calc, "calculate", lambda *args: NetPnLResult(5, 5, 0))
    summary = RealizedNetPnLAggregator(calc).aggregate([closed()])
    assert summary.net_breakeven_trades == 1
    assert summary.net_winning_trades == summary.net_losing_trades == 0


def test_mixed_ce_pe_aggregation_uses_authoritative_results(monkeypatch):
    positions = [closed(PositionSide.CE), closed(PositionSide.PE), closed(entry=100, exit=100)]
    supplied = [NetPnLResult(100, 10, 90), NetPnLResult(5, 10, -5),
                NetPnLResult(7, 7, 0)]
    calc = net_calculator()
    seen = []
    def calculate(position, buy_count, sell_count):
        seen.append((position, buy_count, sell_count))
        return supplied[len(seen) - 1]
    monkeypatch.setattr(calc, "calculate", calculate)
    summary = RealizedNetPnLAggregator(calc).aggregate(positions, 2, 3)
    assert summary == RealizedNetPnLResult(3, 112, 27, 85, 1, 1, 1)
    assert seen == [(position, 2, 3) for position in positions]


def test_real_calculation_aggregates_exact_values_and_custom_counts():
    positions = [closed(PositionSide.CE, 100, 105),
                 closed(PositionSide.PE, 105, 100)]
    calc = net_calculator()
    individual = [calc.calculate(position, 2, 4) for position in positions]
    summary = RealizedNetPnLAggregator(calc).aggregate(positions, 2, 4)
    assert summary.trade_count == 2
    assert summary.gross_pnl == fsum(item.gross_pnl for item in individual)
    assert summary.total_charges == fsum(item.total_charges for item in individual)
    assert summary.net_pnl == fsum((summary.gross_pnl, -summary.total_charges))
    assert summary.total_charges > aggregator().aggregate(positions).total_charges


def test_reversing_positions_is_value_independent_and_input_is_unchanged():
    positions = [closed(PositionSide.CE, 100, 105),
                 closed(PositionSide.PE, 105, 100), closed(entry=100, exit=101)]
    original = list(positions)
    forward = aggregator().aggregate(positions)
    reverse = aggregator().aggregate(list(reversed(positions)))
    assert forward == reverse
    assert positions == original
    assert all(current is prior for current, prior in zip(positions, original))


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.5, "1"])
@pytest.mark.parametrize("argument", ["buy_order_count", "sell_order_count"])
@pytest.mark.parametrize("positions", [[], [closed()]])
def test_invalid_order_counts_rejected_even_when_empty(positions, argument, count):
    with pytest.raises(ValueError):
        aggregator().aggregate(positions, **{argument: count})


@pytest.mark.parametrize("positions", [None, {}, "positions", {1},
                                         (item for item in []), object()])
def test_invalid_container_rejected(positions):
    with pytest.raises(TypeError):
        aggregator().aggregate(positions)


@pytest.mark.parametrize("positions", [[object()], (object(),)])
def test_invalid_member_rejected(positions):
    with pytest.raises(TypeError):
        aggregator().aggregate(positions)


def test_result_has_exact_fields_and_is_frozen():
    value = result()
    assert [field.name for field in fields(value)] == [
        "trade_count", "gross_pnl", "total_charges", "net_pnl",
        "net_winning_trades", "net_losing_trades", "net_breakeven_trades"]
    with pytest.raises(FrozenInstanceError):
        value.net_pnl = 0


@pytest.mark.parametrize("field", ["trade_count", "net_winning_trades",
                                    "net_losing_trades", "net_breakeven_trades"])
@pytest.mark.parametrize("bad", [True, -1, 1.5, "1", None])
def test_result_rejects_invalid_counts(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


def test_result_rejects_inconsistent_classified_counts():
    with pytest.raises(ValueError):
        result(net_winning_trades=2)


@pytest.mark.parametrize("field", ["gross_pnl", "total_charges", "net_pnl"])
@pytest.mark.parametrize("bad", [True, nan, inf, -inf, object()])
def test_result_rejects_invalid_numeric_fields(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


def test_result_rejects_negative_charges_and_inconsistent_net():
    with pytest.raises(ValueError):
        result(total_charges=-1)
    with pytest.raises(ValueError):
        result(net_pnl=8)


def test_module_is_a_pure_realized_aggregation_boundary():
    source = Path("trading/realized_net_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = ("positionmanager", "positionstore", "brokerposition", "kite",
                 "zerodha", "marketdata", "websocket", "strategy", "readiness",
                 "authorization", "persistence", "network", "database", "datetime",
                 "time.", "retry", "poll", "thread", "unrealized", "portfolio",
                 "session", "capital", "margin", "account")
    assert all(term not in source for term in forbidden)
