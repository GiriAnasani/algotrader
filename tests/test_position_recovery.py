from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_recovery import (
    PositionRecoveryCoordinator,
    PositionRecoveryResult,
    PositionRecoveryState,
)
from trading.position_synchronizer import (
    PositionSynchronizationResult,
    PositionSynchronizationState,
    PositionSynchronizer,
)


def managed_position():
    return ManagedPosition(
        side=PositionSide.CE,
        contract_symbol="NIFTY26AUG25000CE",
        quantity=65,
        entry_price=100.0,
        entry_time=datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc),
        state=PositionState.OPEN,
    )


def broker_position(**overrides):
    values = {
        "tradingsymbol": "NIFTY26AUG25000CE",
        "exchange": "NFO",
        "quantity": 65,
        "average_price": 102.5,
        "product": "MIS",
    }
    values.update(overrides)
    return BrokerPosition(**values)


def synchronization_result(state=PositionSynchronizationState.SYNCHRONIZED_FLAT):
    return PositionSynchronizationResult(
        state=state,
        managed_position=None,
        broker_positions=(),
        message="Synchronization diagnostic.",
    )


def recovery_result(**overrides):
    values = {
        "state": PositionRecoveryState.SAFE_FLAT,
        "synchronization_result": synchronization_result(),
        "message": "Recovery decision.",
    }
    values.update(overrides)
    return PositionRecoveryResult(**values)


def test_recovery_enum_contains_only_required_states():
    assert {state.value for state in PositionRecoveryState} == {
        "SAFE_FLAT", "SAFE_CONTINUE", "ADOPTION_REQUIRED",
        "STALE_MANAGED_STATE", "AMBIGUOUS",
    }


def test_recovery_result_is_frozen_and_normalizes_message():
    result = recovery_result(message="  Safe flat.  ")
    assert result.message == "Safe flat."
    with pytest.raises(FrozenInstanceError):
        result.state = PositionRecoveryState.AMBIGUOUS


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "SAFE_FLAT"},
        {"synchronization_result": None},
        {"synchronization_result": object()},
        {"message": ""},
        {"message": "   "},
        {"message": None},
    ],
)
def test_recovery_result_strictly_validates_fields(overrides):
    with pytest.raises((TypeError, ValueError)):
        recovery_result(**overrides)


def test_recovery_result_rejects_state_inconsistent_with_synchronization():
    with pytest.raises(ValueError):
        recovery_result(
            state=PositionRecoveryState.SAFE_CONTINUE,
            synchronization_result=synchronization_result(),
        )


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_recovery_coordinator_requires_position_manager(manager):
    with pytest.raises(TypeError):
        PositionRecoveryCoordinator(manager)


@pytest.mark.parametrize(
    ("setup_managed", "positions", "expected"),
    [
        (False, [], PositionRecoveryState.SAFE_FLAT),
        (True, [broker_position()], PositionRecoveryState.SAFE_CONTINUE),
        (False, [broker_position()], PositionRecoveryState.ADOPTION_REQUIRED),
        (True, [], PositionRecoveryState.STALE_MANAGED_STATE),
        (
            True,
            [broker_position(tradingsymbol="NIFTY26AUG25100CE")],
            PositionRecoveryState.AMBIGUOUS,
        ),
        (
            False,
            [broker_position(), broker_position(tradingsymbol="NIFTY26AUG25000PE")],
            PositionRecoveryState.AMBIGUOUS,
        ),
    ],
)
def test_recovery_maps_every_synchronization_outcome_without_manager_mutation(
    setup_managed, positions, expected
):
    manager = PositionManager()
    managed = managed_position()
    if setup_managed:
        manager.register(managed)

    result = PositionRecoveryCoordinator(manager).recover(positions)

    assert result.state is expected
    assert manager.active_position is (managed if setup_managed else None)


def test_unrelated_and_zero_quantity_rows_preserve_safe_flat():
    rows = [
        broker_position(quantity=0),
        broker_position(tradingsymbol="RELIANCE", exchange="NSE", quantity=10),
        broker_position(tradingsymbol="NIFTY26AUGFUT", quantity=65),
    ]
    result = PositionRecoveryCoordinator(PositionManager()).recover(rows)
    assert result.state is PositionRecoveryState.SAFE_FLAT
    assert result.synchronization_result.broker_positions == ()


def test_case_insensitive_symbol_and_price_difference_allow_safe_continue():
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    broker = broker_position(
        tradingsymbol="nifty26aug25000ce",
        average_price=999.0,
    )
    result = PositionRecoveryCoordinator(manager).recover([broker])
    assert result.state is PositionRecoveryState.SAFE_CONTINUE
    assert result.synchronization_result.managed_position is managed
    assert result.synchronization_result.broker_positions[0] is broker
    assert manager.active_position is managed


def test_exact_synchronization_result_identity_is_preserved(monkeypatch):
    exact_result = synchronization_result()

    def return_exact(self, positions):
        return exact_result

    monkeypatch.setattr(PositionSynchronizer, "synchronize", return_exact)
    recovery = PositionRecoveryCoordinator(PositionManager()).recover([])
    assert recovery.synchronization_result is exact_result


def test_input_list_mutation_cannot_change_recovery_snapshot_membership():
    broker = broker_position()
    snapshot = [broker]
    result = PositionRecoveryCoordinator(PositionManager()).recover(snapshot)
    snapshot.clear()
    assert result.synchronization_result.broker_positions == (broker,)
    assert result.synchronization_result.broker_positions[0] is broker


@pytest.mark.parametrize("snapshot", [None, {}, "positions", 1, [object()]])
def test_invalid_snapshot_propagates_without_mutating_manager(snapshot):
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    with pytest.raises(TypeError):
        PositionRecoveryCoordinator(manager).recover(snapshot)
    assert manager.active_position is managed


def test_recovery_module_has_no_mutating_or_external_dependencies():
    source = Path("trading/position_recovery.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "place_order", "cancel_order", "modify_order",
        "order_history", ".register(", ".clear(", "livereadinessgate",
        "live_startup", "live_runtime", "marketdata", "strategy", "pnl",
        "portfolio", "sleep(", "timer", "thread", "database", "serialize",
    )
    assert all(term not in source for term in forbidden)
