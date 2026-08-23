from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_synchronizer import (
    PositionSynchronizationResult,
    PositionSynchronizationState,
    PositionSynchronizer,
)


def managed_position(**overrides):
    values = {
        "side": PositionSide.CE,
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc),
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def broker_position(**overrides):
    values = {
        "tradingsymbol": "NIFTY26AUG25000CE",
        "exchange": "NFO",
        "quantity": 65,
        "average_price": 101.25,
        "product": "MIS",
    }
    values.update(overrides)
    return BrokerPosition(**values)


def result(**overrides):
    values = {
        "state": PositionSynchronizationState.SYNCHRONIZED_FLAT,
        "managed_position": None,
        "broker_positions": (),
        "message": "Synchronized flat.",
    }
    values.update(overrides)
    return PositionSynchronizationResult(**values)


def synchronize(manager=None, positions=()):
    manager = manager or PositionManager()
    return PositionSynchronizer(manager).synchronize(positions)


def test_synchronization_enum_contains_only_intended_states():
    assert {state.value for state in PositionSynchronizationState} == {
        "SYNCHRONIZED_FLAT", "SYNCHRONIZED_OPEN", "MANAGED_ONLY",
        "BROKER_ONLY", "MISMATCH",
    }


def test_result_is_frozen_and_strips_message():
    diagnostic = result(message="  synchronized  ")
    assert diagnostic.message == "synchronized"
    with pytest.raises(FrozenInstanceError):
        diagnostic.state = PositionSynchronizationState.MISMATCH


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "SYNCHRONIZED_FLAT"},
        {"managed_position": object()},
        {"broker_positions": []},
        {"broker_positions": (object(),)},
        {"message": ""},
        {"message": "   "},
        {"message": None},
    ],
)
def test_result_rejects_invalid_fields(overrides):
    with pytest.raises((TypeError, ValueError)):
        result(**overrides)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (PositionSynchronizationState.SYNCHRONIZED_FLAT, True),
        (PositionSynchronizationState.SYNCHRONIZED_OPEN, True),
        (PositionSynchronizationState.MANAGED_ONLY, False),
        (PositionSynchronizationState.BROKER_ONLY, False),
        (PositionSynchronizationState.MISMATCH, False),
    ],
)
def test_is_synchronized_only_for_synchronized_states(state, expected):
    assert result(state=state).is_synchronized is expected


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_synchronizer_requires_position_manager(manager):
    with pytest.raises(TypeError):
        PositionSynchronizer(manager)


@pytest.mark.parametrize("positions", [[], (), [broker_position()], (broker_position(),)])
def test_synchronizer_accepts_list_or_tuple_of_broker_positions(positions):
    assert isinstance(synchronize(positions=positions), PositionSynchronizationResult)


@pytest.mark.parametrize("positions", [None, {}, "positions", 1, object(), [object()]])
def test_synchronizer_rejects_invalid_snapshot(positions):
    with pytest.raises(TypeError):
        synchronize(positions=positions)


@pytest.mark.parametrize("positions", [[], (), [broker_position(quantity=0)]])
def test_flat_manager_and_no_nonzero_relevant_exposure_are_synchronized_flat(positions):
    manager = PositionManager()
    diagnostic = synchronize(manager, positions)
    assert diagnostic.state is PositionSynchronizationState.SYNCHRONIZED_FLAT
    assert diagnostic.is_synchronized is True
    assert diagnostic.managed_position is None
    assert diagnostic.broker_positions == ()
    assert manager.active_position is None


def test_exact_open_exposure_is_synchronized_with_identity_and_no_price_comparison():
    manager = PositionManager()
    managed = managed_position(entry_price=87.5)
    broker = broker_position(average_price=101.25)
    manager.register(managed)

    diagnostic = synchronize(manager, [broker])

    assert diagnostic.state is PositionSynchronizationState.SYNCHRONIZED_OPEN
    assert diagnostic.managed_position is managed
    assert diagnostic.broker_positions[0] is broker
    assert manager.active_position is managed
    assert managed.entry_price == 87.5
    assert broker.average_price == 101.25


def test_symbol_comparison_is_case_insensitive():
    manager = PositionManager()
    manager.register(managed_position())
    diagnostic = synchronize(manager, [broker_position(tradingsymbol="nifty26aug25000ce")])
    assert diagnostic.state is PositionSynchronizationState.SYNCHRONIZED_OPEN


def test_managed_only_is_diagnostic_and_does_not_clear_manager():
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    diagnostic = synchronize(manager, [])
    assert diagnostic.state is PositionSynchronizationState.MANAGED_ONLY
    assert diagnostic.managed_position is managed
    assert manager.active_position is managed


def test_broker_only_is_diagnostic_and_does_not_adopt_position():
    manager = PositionManager()
    broker = broker_position()
    diagnostic = synchronize(manager, [broker])
    assert diagnostic.state is PositionSynchronizationState.BROKER_ONLY
    assert diagnostic.broker_positions == (broker,)
    assert diagnostic.broker_positions[0] is broker
    assert manager.active_position is None


@pytest.mark.parametrize(
    "broker",
    [
        broker_position(tradingsymbol="NIFTY26AUG25100CE"),
        broker_position(quantity=130),
        broker_position(tradingsymbol="NIFTY26AUG25000PE"),
        broker_position(quantity=-65),
    ],
)
def test_disagreeing_open_exposure_is_mismatch_without_manager_mutation(broker):
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    diagnostic = synchronize(manager, [broker])
    assert diagnostic.state is PositionSynchronizationState.MISMATCH
    assert diagnostic.managed_position is managed
    assert manager.active_position is managed


@pytest.mark.parametrize("with_managed", [False, True])
def test_multiple_relevant_exposures_are_mismatch_without_selection_or_netting(with_managed):
    manager = PositionManager()
    managed = managed_position()
    if with_managed:
        manager.register(managed)
    first = broker_position()
    second = broker_position(tradingsymbol="NIFTY26AUG25000PE", quantity=-65)

    diagnostic = synchronize(manager, [first, second])

    assert diagnostic.state is PositionSynchronizationState.MISMATCH
    assert diagnostic.broker_positions == (first, second)
    assert diagnostic.broker_positions[0] is first
    assert diagnostic.broker_positions[1] is second
    assert manager.active_position is (managed if with_managed else None)


def test_unrelated_and_zero_quantity_rows_are_excluded_from_result():
    zero = broker_position(quantity=0)
    equity = broker_position(tradingsymbol="RELIANCE", exchange="NSE", quantity=10)
    future = broker_position(tradingsymbol="NIFTY26AUGFUT", quantity=65)
    diagnostic = synchronize(positions=[zero, equity, future])
    assert diagnostic.state is PositionSynchronizationState.SYNCHRONIZED_FLAT
    assert diagnostic.broker_positions == ()


def test_result_tuple_is_stable_after_input_list_mutation_and_retains_identity():
    broker = broker_position()
    snapshot = [broker]
    diagnostic = synchronize(positions=snapshot)
    snapshot.clear()
    assert diagnostic.broker_positions == (broker,)
    assert diagnostic.broker_positions[0] is broker


def test_synchronizer_module_has_no_mutating_or_external_dependencies():
    source = Path("trading/position_synchronizer.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "place_order", "cancel_order", "modify_order",
        "order_history", "liveorder", "openpositionlifecycle",
        "closepositionlifecycle", "positionreversallifecycle", ".register(",
        ".clear(", "livereadinessgate", "recovery", "marketdata", "strategy",
        "pnl", "portfolio",
    )
    assert all(term not in source for term in forbidden)
