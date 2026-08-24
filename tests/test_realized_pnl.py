from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.position import PositionSide, PositionState
from trading.position_pnl import PositionPnLResult, PositionPnLState
from trading.realized_pnl import RealizedPnLAggregator, RealizedPnLResult


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE, **overrides):
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{side.value}",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": ENTRY_TIME,
        "exit_price": 105.0,
        "exit_time": ENTRY_TIME + timedelta(minutes=1),
        "state": PositionState.CLOSED,
    }
    values.update(overrides)
    return ClosedPosition(**values)


@pytest.mark.parametrize("positions", [[], ()])
def test_empty_history_returns_zero_summary(positions):
    result = RealizedPnLAggregator().aggregate(positions)
    assert result == RealizedPnLResult(0, 0, 0, 0, 0.0)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "exit_price, expected_counts, expected_total",
    [
        (105.0, (1, 0, 0), 325.0),
        (95.0, (0, 1, 0), -325.0),
        (100.0, (0, 0, 1), 0.0),
    ],
)
def test_single_trade_classification_for_both_sides(
    side, exit_price, expected_counts, expected_total
):
    result = RealizedPnLAggregator().aggregate(
        [closed(side, exit_price=exit_price)]
    )
    assert result.trade_count == 1
    assert (
        result.winning_trades,
        result.losing_trades,
        result.breakeven_trades,
    ) == expected_counts
    assert result.total_gross_pnl == expected_total


def test_mixed_sides_quantities_and_outcomes_aggregate_exactly():
    positions = [
        closed(PositionSide.CE, quantity=65, exit_price=105.0),
        closed(PositionSide.PE, quantity=40, exit_price=95.0),
        closed(PositionSide.CE, quantity=25, exit_price=100.0),
    ]
    result = RealizedPnLAggregator().aggregate(positions)
    assert result == RealizedPnLResult(
        trade_count=3,
        winning_trades=1,
        losing_trades=1,
        breakeven_trades=1,
        total_gross_pnl=125.0,
    )


def test_reversed_input_produces_identical_summary_without_mutation():
    positions = [
        closed(exit_price=105.25),
        closed(PositionSide.PE, quantity=40, exit_price=94.75),
        closed(quantity=25, exit_price=100.0),
    ]
    original = tuple(positions)
    forward = RealizedPnLAggregator().aggregate(positions)
    reverse = RealizedPnLAggregator().aggregate(list(reversed(positions)))
    assert forward == reverse
    assert tuple(positions) == original
    assert all(actual is expected for actual, expected in zip(positions, original))


@pytest.mark.parametrize(
    "positions",
    [None, {}, "positions", (position for position in ()), set(), object()],
)
def test_aggregate_rejects_invalid_collection_types(positions):
    with pytest.raises(TypeError):
        RealizedPnLAggregator().aggregate(positions)


@pytest.mark.parametrize("positions", [[object()], (object(),)])
def test_aggregate_rejects_invalid_members(positions):
    with pytest.raises(TypeError):
        RealizedPnLAggregator().aggregate(positions)


def test_result_is_frozen_and_has_exact_fields():
    result = RealizedPnLResult(1, 1, 0, 0, 325)
    assert [field.name for field in fields(result)] == [
        "trade_count", "winning_trades", "losing_trades", "breakeven_trades",
        "total_gross_pnl",
    ]
    assert result.total_gross_pnl == 325.0
    with pytest.raises(FrozenInstanceError):
        result.trade_count = 2


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0, 0, 0, 0.0),
        (True, 1, 0, 0, 1.0),
        (1, -1, 1, 1, 0.0),
        (1, 1, -1, 1, 0.0),
        (1, 1, 1, -1, 0.0),
        (2, 1, 0, 0, 1.0),
        (0, 0, 0, 0, float("nan")),
        (0, 0, 0, 0, float("inf")),
        (0, 0, 0, 0, float("-inf")),
        (0, 0, 0, 0, True),
    ],
)
def test_result_rejects_invalid_invariants(values):
    with pytest.raises(ValueError):
        RealizedPnLResult(*values)


def test_aggregate_reuses_position_pnl_calculator(monkeypatch):
    positions = [closed(), closed(PositionSide.PE)]
    seen = []

    def fake_calculate(self, position):
        seen.append(position)
        gross_pnl = 10.0 if position.side is PositionSide.CE else -3.0
        return PositionPnLResult(
            PositionPnLState.CLOSED,
            position.side,
            position.contract_symbol,
            position.quantity,
            position.entry_price,
            position.exit_price,
            gross_pnl,
        )

    monkeypatch.setattr(
        "trading.realized_pnl.PositionPnLCalculator.calculate_closed",
        fake_calculate,
    )
    result = RealizedPnLAggregator().aggregate(positions)
    assert seen[0] is positions[0]
    assert seen[1] is positions[1]
    assert result == RealizedPnLResult(2, 1, 1, 0, 7.0)


def test_realized_pnl_module_has_only_calculation_domain_dependencies():
    source = Path("trading/realized_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", "positionstore", "brokerposition", "marketdata", "order",
        "readiness", "authorization", "strategy", "websocket", "persist", "datetime",
        "network", "database", "retry", "poll", "thread", "unrealized", "portfolio",
        "daily", "session", "capital", "margin", "account", "fee", "brokerage",
        "tax", "charge", "net_pnl", "drawdown", "win_rate", "profit_factor", "sharpe",
    )
    assert all(term not in source for term in forbidden)
