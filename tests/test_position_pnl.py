from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_pnl import (
    PositionPnLCalculator,
    PositionPnLResult,
    PositionPnLState,
)


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def managed(side=PositionSide.CE, **overrides):
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


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "reference_price, expected",
    [(105.0, 325.0), (95.0, -325.0), (100.0, 0.0)],
)
def test_open_long_option_pnl_uses_same_formula_for_both_sides(
    side, reference_price, expected
):
    result = PositionPnLCalculator().calculate_open(managed(side), reference_price)
    assert result.state is PositionPnLState.OPEN
    assert result.side is side
    assert result.reference_price == reference_price
    assert result.gross_pnl == expected


def test_open_fractional_price_uses_exact_quantity_multiplication():
    result = PositionPnLCalculator().calculate_open(
        managed(quantity=40, entry_price=100.25), 101.75
    )
    assert result.gross_pnl == 60.0


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "exit_price, expected",
    [(105.0, 325.0), (95.0, -325.0), (100.0, 0.0)],
)
def test_closed_long_option_pnl_uses_exit_price_for_both_sides(
    side, exit_price, expected
):
    result = PositionPnLCalculator().calculate_closed(
        closed(side, exit_price=exit_price)
    )
    assert result.state is PositionPnLState.CLOSED
    assert result.side is side
    assert result.reference_price == exit_price
    assert result.gross_pnl == expected


@pytest.mark.parametrize("position", [None, object(), "position", True])
def test_open_calculation_requires_managed_position(position):
    with pytest.raises(TypeError):
        PositionPnLCalculator().calculate_open(position, 105.0)


def test_open_calculation_rejects_non_open_position():
    position = managed()
    object.__setattr__(position, "state", PositionState.CLOSED)
    with pytest.raises(ValueError, match="OPEN"):
        PositionPnLCalculator().calculate_open(position, 105.0)


@pytest.mark.parametrize("position", [None, object(), "position", True, managed()])
def test_closed_calculation_requires_closed_position(position):
    with pytest.raises(TypeError):
        PositionPnLCalculator().calculate_closed(position)


@pytest.mark.parametrize(
    "reference_price",
    [None, "105", True, False, 0, -1, float("nan"), float("inf"), float("-inf")],
)
def test_open_calculation_rejects_invalid_reference_price(reference_price):
    with pytest.raises(ValueError):
        PositionPnLCalculator().calculate_open(managed(), reference_price)


def valid_result(**overrides):
    values = {
        "state": PositionPnLState.OPEN,
        "side": PositionSide.CE,
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100.0,
        "reference_price": 105.0,
        "gross_pnl": 325.0,
    }
    values.update(overrides)
    return PositionPnLResult(**values)


def test_result_is_frozen_and_has_exact_fields():
    result = valid_result()
    assert [field.name for field in fields(result)] == [
        "state", "side", "contract_symbol", "quantity", "entry_price",
        "reference_price", "gross_pnl",
    ]
    with pytest.raises(FrozenInstanceError):
        result.gross_pnl = 0.0


@pytest.mark.parametrize(
    "overrides, exception",
    [
        ({"state": object()}, TypeError),
        ({"side": object()}, TypeError),
        ({"contract_symbol": ""}, ValueError),
        ({"quantity": 0}, ValueError),
        ({"quantity": True}, ValueError),
        ({"entry_price": 0}, ValueError),
        ({"entry_price": float("nan")}, ValueError),
        ({"reference_price": -1}, ValueError),
        ({"reference_price": float("inf")}, ValueError),
        ({"gross_pnl": float("nan")}, ValueError),
        ({"gross_pnl": float("inf")}, ValueError),
        ({"gross_pnl": True}, ValueError),
    ],
)
def test_result_rejects_invalid_invariants(overrides, exception):
    with pytest.raises(exception):
        valid_result(**overrides)


def test_position_pnl_module_has_only_domain_and_calculation_dependencies():
    source = Path("trading/position_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", "positionstore", "brokerposition", "order", "marketdata",
        "websocket", "readiness", "authorization", "strategy", "persist", "network",
        "retry", "poll", "thread", "datetime.now", "portfolio", "capital", "margin",
        "brokerage", "tax", "fee", "drawdown", "sharpe",
    )
    assert all(term not in source for term in forbidden)
