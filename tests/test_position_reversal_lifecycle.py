from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import (
    ClosedPosition,
    ConfirmedPositionExit,
    PositionExitMismatchError,
)
from trading.open_position_lifecycle import ConfirmedPositionEntry, OpenPositionLifecycle
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import NoActivePositionError, PositionManager
from trading.position_reversal_lifecycle import (
    ConfirmedPositionReversal,
    InvalidPositionReversalError,
    PositionReversalLifecycle,
    PositionReversalResult,
)


EXIT_TIME = datetime(2026, 8, 23, 9, 30, tzinfo=timezone.utc)
ENTRY_TIME = EXIT_TIME + timedelta(minutes=1)


def symbol(side, strike=25000):
    return f"NIFTY26AUG{strike}{side.value}"


def make_active(side=PositionSide.CE, **overrides):
    values = {
        "side": side, "contract_symbol": symbol(side), "quantity": 65,
        "entry_price": 100.0, "entry_time": EXIT_TIME - timedelta(minutes=15),
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def make_exit(side=PositionSide.CE, **overrides):
    values = {
        "side": side, "contract_symbol": symbol(side), "quantity": 65,
        "fill_price": 105.0, "fill_time": EXIT_TIME,
    }
    values.update(overrides)
    return ConfirmedPositionExit(**values)


def make_entry(side=PositionSide.PE, **overrides):
    values = {
        "side": side, "contract_symbol": symbol(side, 25100), "quantity": 130,
        "fill_price": 95.0, "fill_time": ENTRY_TIME,
    }
    values.update(overrides)
    return ConfirmedPositionEntry(**values)


def make_reversal(old_side=PositionSide.CE, new_side=PositionSide.PE, **entry_overrides):
    return ConfirmedPositionReversal(
        confirmed_exit=make_exit(old_side),
        confirmed_entry=make_entry(new_side, **entry_overrides),
    )


def make_closed(side=PositionSide.CE):
    return ClosedPosition(
        side=side, contract_symbol=symbol(side), quantity=65,
        entry_price=100.0, entry_time=EXIT_TIME - timedelta(minutes=15),
        exit_price=105.0, exit_time=EXIT_TIME, state=PositionState.CLOSED,
    )


@pytest.mark.parametrize(
    ("old_side", "new_side"),
    [(PositionSide.CE, PositionSide.PE), (PositionSide.PE, PositionSide.CE)],
)
def test_valid_confirmed_reversal_is_accepted_and_frozen(old_side, new_side):
    reversal = make_reversal(old_side, new_side)
    assert reversal.confirmed_exit.side is old_side
    assert reversal.confirmed_entry.side is new_side
    with pytest.raises(FrozenInstanceError):
        reversal.confirmed_exit = make_exit(old_side)


@pytest.mark.parametrize(
    "overrides",
    [{"confirmed_exit": None}, {"confirmed_exit": object()},
     {"confirmed_entry": None}, {"confirmed_entry": object()}],
)
def test_confirmed_reversal_requires_existing_domain_models(overrides):
    values = {"confirmed_exit": make_exit(), "confirmed_entry": make_entry()}
    values.update(overrides)
    with pytest.raises(TypeError):
        ConfirmedPositionReversal(**values)


def test_valid_reversal_result_is_frozen():
    result = PositionReversalResult(make_closed(), make_active(PositionSide.PE, entry_time=ENTRY_TIME))
    assert result.closed_position.state is PositionState.CLOSED
    assert result.opened_position.state is PositionState.OPEN
    with pytest.raises(FrozenInstanceError):
        result.opened_position = make_active(PositionSide.PE)


@pytest.mark.parametrize("field_name", ["closed_position", "opened_position"])
def test_reversal_result_requires_exact_domain_types(field_name):
    values = {
        "closed_position": make_closed(),
        "opened_position": make_active(PositionSide.PE, entry_time=ENTRY_TIME),
    }
    values[field_name] = object()
    with pytest.raises(TypeError):
        PositionReversalResult(**values)


@pytest.mark.parametrize("invalid_field", ["closed_state", "opened_state"])
def test_reversal_result_requires_closed_and_open_states(invalid_field):
    closed = make_closed()
    opened = make_active(PositionSide.PE, entry_time=ENTRY_TIME)
    if invalid_field == "closed_state":
        object.__setattr__(closed, "state", PositionState.OPEN)
    else:
        object.__setattr__(opened, "state", PositionState.CLOSED)
    with pytest.raises(ValueError):
        PositionReversalResult(closed, opened)


def test_reversal_result_requires_opposite_sides_and_valid_timing():
    closed = make_closed()
    with pytest.raises(ValueError):
        PositionReversalResult(closed, make_active(PositionSide.CE, entry_time=ENTRY_TIME))
    with pytest.raises(ValueError):
        PositionReversalResult(
            closed,
            make_active(PositionSide.PE, entry_time=EXIT_TIME - timedelta(seconds=1)),
        )


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_reversal_lifecycle_requires_position_manager(manager):
    with pytest.raises(TypeError):
        PositionReversalLifecycle(manager)


@pytest.mark.parametrize(
    ("old_side", "new_side"),
    [(PositionSide.CE, PositionSide.PE), (PositionSide.PE, PositionSide.CE)],
)
def test_successful_reversal_closes_old_and_opens_exact_new_position(old_side, new_side):
    manager = PositionManager()
    old_position = make_active(old_side)
    manager.register(old_position)
    reversal = make_reversal(old_side, new_side)

    result = PositionReversalLifecycle(manager).reverse(reversal)

    assert result.closed_position.side is old_side
    assert result.closed_position.state is PositionState.CLOSED
    assert result.opened_position.side is new_side
    assert result.opened_position.state is PositionState.OPEN
    assert result.opened_position.contract_symbol == symbol(new_side, 25100)
    assert result.opened_position.quantity == 130
    assert manager.active_position is result.opened_position
    assert old_position.state is PositionState.OPEN


@pytest.mark.parametrize(
    "reversal",
    [
        ConfirmedPositionReversal(make_exit(), make_entry(PositionSide.CE)),
        ConfirmedPositionReversal(make_exit(PositionSide.PE), make_entry(PositionSide.PE)),
        ConfirmedPositionReversal(
            make_exit(contract_symbol=symbol(PositionSide.CE, 24900)), make_entry()
        ),
        ConfirmedPositionReversal(make_exit(quantity=130), make_entry()),
        ConfirmedPositionReversal(
            make_exit(), make_entry(fill_time=EXIT_TIME - timedelta(seconds=1))
        ),
    ],
)
def test_prevalidation_failure_preserves_exact_active_position(reversal):
    manager = PositionManager()
    active = make_active()
    manager.register(active)
    lifecycle = PositionReversalLifecycle(manager)

    with pytest.raises((InvalidPositionReversalError, PositionExitMismatchError)):
        lifecycle.reverse(reversal)

    assert manager.active_position is active


def test_reversal_without_active_position_does_not_open_entry():
    manager = PositionManager()
    with pytest.raises(NoActivePositionError):
        PositionReversalLifecycle(manager).reverse(make_reversal())
    assert manager.active_position is None


@pytest.mark.parametrize("reversal", [None, object(), "reversal", True])
def test_invalid_reversal_input_preserves_manager(reversal):
    manager = PositionManager()
    active = make_active()
    manager.register(active)
    with pytest.raises(TypeError):
        PositionReversalLifecycle(manager).reverse(reversal)
    assert manager.active_position is active


def test_post_close_open_failure_propagates_without_stale_rollback(monkeypatch):
    manager = PositionManager()
    old_position = make_active()
    manager.register(old_position)

    def fail_open(self, confirmed_entry):
        raise RuntimeError("unexpected open failure")

    monkeypatch.setattr(OpenPositionLifecycle, "open", fail_open)
    with pytest.raises(RuntimeError, match="unexpected open failure"):
        PositionReversalLifecycle(manager).reverse(make_reversal())

    assert manager.active_position is None
    assert old_position.state is PositionState.OPEN


def test_reversal_models_have_only_required_fields():
    assert [field.name for field in fields(ConfirmedPositionReversal)] == [
        "confirmed_exit", "confirmed_entry"
    ]
    assert [field.name for field in fields(PositionReversalResult)] == [
        "closed_position", "opened_position"
    ]


def test_reversal_module_has_no_external_system_or_out_of_scope_behavior():
    source = Path("trading/position_reversal_lifecycle.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "brokerorderstatus", "brokerposition",
        "liveorder", "pendingliveorder", "marketdata", "executionrouter",
        "liveexecutioncoordinator", "livereadinessgate", "place_order", "pnl",
        "portfolio", "synchroniz", "restart", "strategy",
    )
    assert all(term not in source for term in forbidden)
