from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import trading.close_position_lifecycle as close_module
from trading.close_position_lifecycle import (
    ClosedPosition,
    ClosePositionLifecycle,
    ConfirmedPositionExit,
    PositionExitMismatchError,
)
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import NoActivePositionError, PositionManager


ENTRY_TIME = datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc)
EXIT_TIME = datetime(2026, 8, 23, 9, 30, tzinfo=timezone.utc)


def make_active(side=PositionSide.CE, **overrides):
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


def make_exit(side=PositionSide.CE, **overrides):
    symbol_side = side.value if isinstance(side, PositionSide) else "CE"
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{symbol_side}",
        "quantity": 65,
        "fill_price": 105.5,
        "fill_time": EXIT_TIME,
    }
    values.update(overrides)
    return ConfirmedPositionExit(**values)


def make_closed(**overrides):
    values = {
        "side": PositionSide.CE,
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100,
        "entry_time": ENTRY_TIME,
        "exit_price": 105,
        "exit_time": EXIT_TIME,
        "state": PositionState.CLOSED,
    }
    values.update(overrides)
    return ClosedPosition(**values)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_valid_confirmed_exit_is_accepted(side):
    assert make_exit(side).side is side


def test_confirmed_exit_normalizes_values_preserves_time_and_is_frozen():
    exit_fill = make_exit(contract_symbol="  NIFTY26AUG25000CE  ", fill_price=105)
    assert exit_fill.contract_symbol == "NIFTY26AUG25000CE"
    assert exit_fill.fill_price == 105.0
    assert type(exit_fill.fill_price) is float
    assert exit_fill.fill_time is EXIT_TIME
    with pytest.raises(FrozenInstanceError):
        exit_fill.quantity = 130


@pytest.mark.parametrize("side", ["CE", None, 1, True])
def test_confirmed_exit_rejects_invalid_side(side):
    with pytest.raises(ValueError):
        make_exit(side)


@pytest.mark.parametrize(
    ("side", "symbol"),
    [(PositionSide.CE, "NIFTY26AUG25000PE"), (PositionSide.PE, "NIFTY26AUG25000CE")],
)
def test_confirmed_exit_rejects_side_symbol_mismatch(side, symbol):
    with pytest.raises(ValueError):
        make_exit(side, contract_symbol=symbol)


@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_confirmed_exit_rejects_invalid_symbol(symbol):
    with pytest.raises(ValueError):
        make_exit(contract_symbol=symbol)


@pytest.mark.parametrize("quantity", [0, -1, 1.0, True, "65"])
def test_confirmed_exit_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        make_exit(quantity=quantity)


@pytest.mark.parametrize(
    "fill_price",
    [-1, float("nan"), float("inf"), float("-inf"), True, "105", None],
)
def test_confirmed_exit_rejects_invalid_fill_price(fill_price):
    with pytest.raises(ValueError):
        make_exit(fill_price=fill_price)


@pytest.mark.parametrize("fill_time", [datetime(2026, 8, 23, 9, 30), "now", None])
def test_confirmed_exit_rejects_invalid_fill_time(fill_time):
    with pytest.raises(ValueError):
        make_exit(fill_time=fill_time)


def test_valid_closed_position_normalizes_prices_and_is_frozen():
    position = make_closed()
    assert position.entry_price == 100.0
    assert position.exit_price == 105.0
    assert type(position.entry_price) is type(position.exit_price) is float
    with pytest.raises(FrozenInstanceError):
        position.state = PositionState.OPEN


@pytest.mark.parametrize("state", [PositionState.OPEN, "CLOSED", None])
def test_closed_position_requires_closed_state(state):
    with pytest.raises(ValueError):
        make_closed(state=state)


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=1)])
def test_closed_position_accepts_exit_not_before_entry(delta):
    assert make_closed(exit_time=ENTRY_TIME + delta).state is PositionState.CLOSED


def test_closed_position_rejects_exit_before_entry():
    with pytest.raises(ValueError):
        make_closed(exit_time=ENTRY_TIME - timedelta(microseconds=1))


@pytest.mark.parametrize(
    "overrides",
    [
        {"side": "CE"},
        {"contract_symbol": ""},
        {"contract_symbol": "NIFTY26AUG25000PE"},
        {"quantity": 0},
        {"entry_price": float("nan")},
        {"exit_price": -1},
        {"entry_time": datetime(2026, 8, 23, 9, 15)},
        {"exit_time": datetime(2026, 8, 23, 9, 30)},
    ],
)
def test_closed_position_rejects_invalid_domain_values(overrides):
    with pytest.raises(ValueError):
        make_closed(**overrides)


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_close_lifecycle_requires_position_manager(manager):
    with pytest.raises(TypeError):
        ClosePositionLifecycle(manager)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_close_maps_active_and_exit_truth_then_clears_manager(side):
    manager = PositionManager()
    active = make_active(side)
    exit_fill = make_exit(side)
    manager.register(active)

    closed = ClosePositionLifecycle(manager).close(exit_fill)

    assert isinstance(closed, ClosedPosition)
    assert closed.side is active.side
    assert closed.contract_symbol == active.contract_symbol
    assert closed.quantity == active.quantity
    assert closed.entry_price == active.entry_price
    assert closed.entry_time is active.entry_time
    assert closed.exit_price == exit_fill.fill_price
    assert closed.exit_time is exit_fill.fill_time
    assert closed.state is PositionState.CLOSED
    assert manager.active_position is None
    assert manager.has_active_position is False
    assert active.state is PositionState.OPEN


def test_close_with_empty_manager_fails_and_remains_empty():
    manager = PositionManager()
    with pytest.raises(NoActivePositionError):
        ClosePositionLifecycle(manager).close(make_exit())
    assert manager.active_position is None


@pytest.mark.parametrize(
    "exit_fill",
    [
        make_exit(PositionSide.PE),
        make_exit(contract_symbol="NIFTY26SEP25100CE"),
        make_exit(quantity=130),
    ],
)
def test_exit_mismatch_preserves_exact_active_position(exit_fill):
    manager = PositionManager()
    active = make_active()
    manager.register(active)

    with pytest.raises(PositionExitMismatchError):
        ClosePositionLifecycle(manager).close(exit_fill)

    assert manager.active_position is active
    assert manager.active_position.quantity == 65


@pytest.mark.parametrize("exit_fill", [None, object(), "exit", True])
def test_invalid_exit_input_preserves_manager_state(exit_fill):
    manager = PositionManager()
    active = make_active()
    manager.register(active)
    with pytest.raises(TypeError):
        ClosePositionLifecycle(manager).close(exit_fill)
    assert manager.active_position is active


def test_closed_position_construction_failure_preserves_manager_state(monkeypatch):
    manager = PositionManager()
    active = make_active()
    manager.register(active)

    def fail_construction(**kwargs):
        raise ValueError("construction failed")

    monkeypatch.setattr(close_module, "ClosedPosition", fail_construction)
    with pytest.raises(ValueError, match="construction failed"):
        ClosePositionLifecycle(manager).close(make_exit())
    assert manager.active_position is active


def test_close_models_have_only_required_fields():
    assert [field.name for field in fields(ConfirmedPositionExit)] == [
        "side", "contract_symbol", "quantity", "fill_price", "fill_time"
    ]
    assert [field.name for field in fields(ClosedPosition)] == [
        "side", "contract_symbol", "quantity", "entry_price", "entry_time",
        "exit_price", "exit_time", "state",
    ]


def test_close_module_has_no_external_system_or_out_of_scope_behavior():
    source = Path("trading/close_position_lifecycle.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "brokerorderstatus", "brokerposition",
        "liveorder", "pendingliveorder", "marketdata", "executionrouter",
        "liveexecutioncoordinator", "livereadinessgate", "place_order", "pnl",
        "portfolio", "reversal", "synchroniz", "restart", "partial fill",
    )
    assert all(term not in source for term in forbidden)
