from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.portfolio_pnl import PortfolioPnLAggregator, PortfolioPnLResult
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.realized_pnl import RealizedPnLResult
from trading.unrealized_pnl import UnrealizedPnLResult


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


def active(side=PositionSide.CE, **overrides):
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{side.value}",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": ENTRY_TIME,
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def test_flat_portfolio_returns_all_zero_values():
    result = PortfolioPnLAggregator().aggregate([])
    assert result == PortfolioPnLResult(0, 0, 0.0, 0.0, 0.0)


def test_realized_only_uses_closed_trade_summary():
    positions = [
        closed(exit_price=105.0),
        closed(PositionSide.PE, quantity=40, exit_price=95.0),
    ]
    result = PortfolioPnLAggregator().aggregate(positions)
    assert result.closed_trade_count == 2
    assert result.open_position_count == 0
    assert result.realized_gross_pnl == 125.0
    assert result.unrealized_gross_pnl == 0.0
    assert result.total_gross_pnl == 125.0


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "reference_price, expected", [(105.0, 325.0), (95.0, -325.0)]
)
def test_unrealized_only_supports_profit_and_loss_for_both_sides(
    side, reference_price, expected
):
    result = PortfolioPnLAggregator().aggregate(
        [], active(side), reference_price
    )
    assert result.closed_trade_count == 0
    assert result.open_position_count == 1
    assert result.realized_gross_pnl == 0.0
    assert result.unrealized_gross_pnl == expected
    assert result.total_gross_pnl == expected


@pytest.mark.parametrize(
    "closed_exit, reference_price, expected_realized, expected_unrealized, expected_total",
    [
        (105.0, 105.0, 325.0, 325.0, 650.0),
        (105.0, 95.0, 325.0, -325.0, 0.0),
        (95.0, 105.0, -325.0, 325.0, 0.0),
        (95.0, 95.0, -325.0, -325.0, -650.0),
        (100.0, 105.0, 0.0, 325.0, 325.0),
        (105.0, 100.0, 325.0, 0.0, 325.0),
    ],
)
def test_combined_portfolio_adds_exact_component_totals(
    closed_exit,
    reference_price,
    expected_realized,
    expected_unrealized,
    expected_total,
):
    result = PortfolioPnLAggregator().aggregate(
        [closed(PositionSide.CE, exit_price=closed_exit)],
        active(PositionSide.PE),
        reference_price,
    )
    assert result.realized_gross_pnl == expected_realized
    assert result.unrealized_gross_pnl == expected_unrealized
    assert result.total_gross_pnl == expected_total


@pytest.mark.parametrize("active_side", [PositionSide.CE, PositionSide.PE])
def test_mixed_closed_sides_support_either_active_side(active_side):
    result = PortfolioPnLAggregator().aggregate(
        [closed(PositionSide.CE), closed(PositionSide.PE, exit_price=95.0)],
        active(active_side),
        102.0,
    )
    assert result.closed_trade_count == 2
    assert result.open_position_count == 1


@pytest.mark.parametrize("closed_positions", [None, {}, "positions", [object()]])
def test_invalid_closed_positions_propagate_rejection(closed_positions):
    with pytest.raises(TypeError):
        PortfolioPnLAggregator().aggregate(closed_positions)


@pytest.mark.parametrize("active_position", [closed(), object(), "position", True, []])
def test_invalid_active_position_propagates_rejection(active_position):
    with pytest.raises(TypeError):
        PortfolioPnLAggregator().aggregate([], active_position, 105.0)


def test_missing_open_price_and_flat_supplied_price_propagate_rejection():
    with pytest.raises(ValueError):
        PortfolioPnLAggregator().aggregate([], active())
    with pytest.raises(ValueError):
        PortfolioPnLAggregator().aggregate([], None, 105.0)


@pytest.mark.parametrize(
    "reference_price",
    [True, "105", 0, -1, float("nan"), float("inf"), float("-inf")],
)
def test_invalid_open_price_propagates_rejection(reference_price):
    with pytest.raises(ValueError):
        PortfolioPnLAggregator().aggregate([], active(), reference_price)


def test_result_is_frozen_and_has_exact_fields():
    result = PortfolioPnLResult(2, 1, 125, -50, 75)
    assert [field.name for field in fields(result)] == [
        "closed_trade_count", "open_position_count", "realized_gross_pnl",
        "unrealized_gross_pnl", "total_gross_pnl",
    ]
    assert result == PortfolioPnLResult(2, 1, 125.0, -50.0, 75.0)
    with pytest.raises(FrozenInstanceError):
        result.total_gross_pnl = 0.0


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0, 0.0, 0.0, 0.0),
        (True, 0, 0.0, 0.0, 0.0),
        (0, -1, 0.0, 0.0, 0.0),
        (0, 2, 0.0, 0.0, 0.0),
        (0, True, 0.0, 0.0, 0.0),
        (0, 0, float("nan"), 0.0, 0.0),
        (0, 0, float("inf"), 0.0, 0.0),
        (0, 0, 0.0, float("nan"), 0.0),
        (0, 0, 0.0, float("inf"), 0.0),
        (0, 0, 0.0, 0.0, float("nan")),
        (0, 0, 0.0, 0.0, float("inf")),
        (0, 0, True, 0.0, 0.0),
        (0, 0, 10.0, 5.0, 14.0),
    ],
)
def test_result_rejects_invalid_invariants(values):
    with pytest.raises(ValueError):
        PortfolioPnLResult(*values)


@pytest.mark.parametrize(
    "realized, unrealized, total",
    [(10.0, 5.0, 15.0), (-10.0, 5.0, -5.0), (0.0, 0.0, 0.0)],
)
def test_result_accepts_consistent_finite_component_signs(realized, unrealized, total):
    assert PortfolioPnLResult(0, 0, realized, unrealized, total).total_gross_pnl == total


def test_aggregator_reuses_both_component_aggregators_with_exact_inputs(monkeypatch):
    closed_positions = [closed()]
    active_position = active(PositionSide.PE)
    seen = []

    def fake_realized(self, positions):
        seen.append(("realized", positions))
        return RealizedPnLResult(2, 1, 1, 0, 125.0)

    def fake_unrealized(self, position, reference_price=None):
        seen.append(("unrealized", position, reference_price))
        return UnrealizedPnLResult(1, -50.0)

    monkeypatch.setattr(
        "trading.portfolio_pnl.RealizedPnLAggregator.aggregate", fake_realized
    )
    monkeypatch.setattr(
        "trading.portfolio_pnl.UnrealizedPnLAggregator.aggregate", fake_unrealized
    )
    result = PortfolioPnLAggregator().aggregate(
        closed_positions, active_position, 99.0
    )
    assert seen[0] == ("realized", closed_positions)
    assert seen[0][1] is closed_positions
    assert seen[1] == ("unrealized", active_position, 99.0)
    assert seen[1][1] is active_position
    assert result == PortfolioPnLResult(2, 1, 125.0, -50.0, 75.0)


def test_input_position_objects_are_not_mutated():
    closed_position = closed()
    active_position = active()
    closed_values = tuple(closed_position.__dict__.values())
    active_values = tuple(active_position.__dict__.values())
    PortfolioPnLAggregator().aggregate([closed_position], active_position, 105.0)
    assert tuple(closed_position.__dict__.values()) == closed_values
    assert tuple(active_position.__dict__.values()) == active_values


def test_portfolio_pnl_module_has_only_component_calculation_dependencies():
    source = Path("trading/portfolio_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", "positionstore", "brokerposition", "marketdata", "order",
        "websocket", "strategy", "readiness", "authorization", "persist", "database",
        "network", "datetime", "retry", "poll", "thread", "session", "daily",
        "capital", "margin", "account", "brokerage", "tax", "fee", "charge",
        "net_pnl", "drawdown", "loss_limit", "win_rate", "profit_factor", "sharpe",
    )
    assert all(term not in source for term in forbidden)
