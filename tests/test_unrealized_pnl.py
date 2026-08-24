from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_pnl import PositionPnLResult, PositionPnLState
from trading.unrealized_pnl import UnrealizedPnLAggregator, UnrealizedPnLResult


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


def closed():
    return ClosedPosition(
        PositionSide.CE,
        "NIFTY26AUG25000CE",
        65,
        100.0,
        ENTRY_TIME,
        105.0,
        ENTRY_TIME + timedelta(minutes=1),
        PositionState.CLOSED,
    )


def test_flat_state_returns_exact_zero_summary():
    result = UnrealizedPnLAggregator().aggregate(None)
    assert result == UnrealizedPnLResult(position_count=0, total_gross_pnl=0.0)


@pytest.mark.parametrize("reference_price", [100.0, 0, False, "100"])
def test_flat_state_rejects_any_supplied_reference_price(reference_price):
    with pytest.raises(ValueError):
        UnrealizedPnLAggregator().aggregate(None, reference_price)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "reference_price, expected",
    [(105.0, 325.0), (95.0, -325.0), (100.0, 0.0)],
)
def test_open_long_option_valuation_uses_same_formula_for_both_sides(
    side, reference_price, expected
):
    result = UnrealizedPnLAggregator().aggregate(
        managed(side), reference_price
    )
    assert result.position_count == 1
    assert result.total_gross_pnl == expected


def test_fractional_price_uses_exact_quantity_multiplication():
    result = UnrealizedPnLAggregator().aggregate(
        managed(quantity=40, entry_price=100.25), 101.75
    )
    assert result == UnrealizedPnLResult(1, 60.0)


@pytest.mark.parametrize(
    "position",
    [closed(), object(), "position", True, {}, [], [managed()], (managed(),)],
)
def test_aggregate_rejects_non_managed_position_values(position):
    with pytest.raises(TypeError):
        UnrealizedPnLAggregator().aggregate(position, 105.0)


def test_tampered_non_open_managed_position_is_rejected():
    position = managed()
    object.__setattr__(position, "state", PositionState.CLOSED)
    with pytest.raises(ValueError, match="OPEN"):
        UnrealizedPnLAggregator().aggregate(position, 105.0)


@pytest.mark.parametrize(
    "reference_price",
    [None, True, False, "105", 0, -1, float("nan"), float("inf"), float("-inf")],
)
def test_open_position_rejects_invalid_reference_price(reference_price):
    with pytest.raises(ValueError):
        UnrealizedPnLAggregator().aggregate(managed(), reference_price)


def test_result_is_frozen_and_has_exact_fields():
    result = UnrealizedPnLResult(1, 325)
    assert [field.name for field in fields(result)] == [
        "position_count", "total_gross_pnl"
    ]
    assert result.total_gross_pnl == 325.0
    with pytest.raises(FrozenInstanceError):
        result.position_count = 0


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0.0),
        (2, 0.0),
        (True, 0.0),
        (0, 1.0),
        (0, -1.0),
        (1, float("nan")),
        (1, float("inf")),
        (1, float("-inf")),
        (1, True),
    ],
)
def test_result_rejects_invalid_invariants(values):
    with pytest.raises(ValueError):
        UnrealizedPnLResult(*values)


@pytest.mark.parametrize("total", [325.0, -325.0, 0.0])
def test_one_position_accepts_any_finite_pnl_sign(total):
    assert UnrealizedPnLResult(1, total).total_gross_pnl == total


def test_aggregate_reuses_calculator_with_exact_position_without_mutation(monkeypatch):
    position = managed()
    original_values = tuple(position.__dict__.values())
    seen = []

    def fake_calculate(self, actual_position, reference_price):
        seen.append((actual_position, reference_price))
        return PositionPnLResult(
            PositionPnLState.OPEN,
            actual_position.side,
            actual_position.contract_symbol,
            actual_position.quantity,
            actual_position.entry_price,
            reference_price,
            123.5,
        )

    monkeypatch.setattr(
        "trading.unrealized_pnl.PositionPnLCalculator.calculate_open",
        fake_calculate,
    )
    result = UnrealizedPnLAggregator().aggregate(position, 105.0)
    assert seen == [(position, 105.0)]
    assert seen[0][0] is position
    assert result == UnrealizedPnLResult(1, 123.5)
    assert tuple(position.__dict__.values()) == original_values


def test_unrealized_pnl_module_has_only_calculation_domain_dependencies():
    source = Path("trading/unrealized_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", "positionstore", "brokerposition", "marketdata", "order",
        "premium", "websocket", "strategy", "readiness", "authorization", "persist",
        "database", "network", "datetime", "retry", "poll", "thread",
        "from trading.realized_pnl", "import trading.realized_pnl",
        "portfolio", "session", "daily", "capital", "margin", "account", "brokerage",
        "tax", "fee", "net_pnl", "drawdown", "risk",
    )
    assert all(term not in source for term in forbidden)
