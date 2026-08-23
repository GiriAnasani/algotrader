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
from trading.position_safety import (
    PositionSafetyEvaluator,
    PositionSafetyResult,
    PositionSafetyState,
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
        "average_price": 101.0,
        "product": "MIS",
    }
    values.update(overrides)
    return BrokerPosition(**values)


def recovery_for(state):
    synchronization_by_recovery = {
        PositionRecoveryState.SAFE_FLAT: PositionSynchronizationState.SYNCHRONIZED_FLAT,
        PositionRecoveryState.SAFE_CONTINUE: PositionSynchronizationState.SYNCHRONIZED_OPEN,
        PositionRecoveryState.ADOPTION_REQUIRED: PositionSynchronizationState.BROKER_ONLY,
        PositionRecoveryState.STALE_MANAGED_STATE: PositionSynchronizationState.MANAGED_ONLY,
        PositionRecoveryState.AMBIGUOUS: PositionSynchronizationState.MISMATCH,
    }
    synchronization = PositionSynchronizationResult(
        state=synchronization_by_recovery[state],
        managed_position=None,
        broker_positions=(),
        message="Synchronization diagnostic.",
    )
    return PositionRecoveryResult(
        state=state,
        synchronization_result=synchronization,
        message="Recovery decision.",
    )


def safety_result(**overrides):
    values = {
        "state": PositionSafetyState.SAFE_FLAT,
        "recovery_result": recovery_for(PositionRecoveryState.SAFE_FLAT),
        "message": "Safety decision.",
    }
    values.update(overrides)
    return PositionSafetyResult(**values)


def test_safety_enum_contains_exactly_intended_states():
    assert {state.value for state in PositionSafetyState} == {
        "SAFE_FLAT", "SAFE_OPEN", "BLOCKED_ADOPTION_REQUIRED",
        "BLOCKED_STALE_MANAGED_STATE", "BLOCKED_AMBIGUOUS",
    }


def test_safety_result_is_frozen_and_normalizes_message():
    result = safety_result(message="  Safe.  ")
    assert result.message == "Safe."
    with pytest.raises(FrozenInstanceError):
        result.state = PositionSafetyState.BLOCKED_AMBIGUOUS


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "SAFE_FLAT"},
        {"recovery_result": None},
        {"recovery_result": object()},
        {"message": ""},
        {"message": "   "},
        {"message": None},
    ],
)
def test_safety_result_strictly_validates_fields(overrides):
    with pytest.raises((TypeError, ValueError)):
        safety_result(**overrides)


def test_safety_result_rejects_contradictory_recovery_state():
    with pytest.raises(ValueError):
        safety_result(
            state=PositionSafetyState.SAFE_OPEN,
            recovery_result=recovery_for(PositionRecoveryState.ADOPTION_REQUIRED),
        )


@pytest.mark.parametrize(
    ("recovery_state", "safety_state", "is_safe"),
    [
        (PositionRecoveryState.SAFE_FLAT, PositionSafetyState.SAFE_FLAT, True),
        (PositionRecoveryState.SAFE_CONTINUE, PositionSafetyState.SAFE_OPEN, True),
        (
            PositionRecoveryState.ADOPTION_REQUIRED,
            PositionSafetyState.BLOCKED_ADOPTION_REQUIRED,
            False,
        ),
        (
            PositionRecoveryState.STALE_MANAGED_STATE,
            PositionSafetyState.BLOCKED_STALE_MANAGED_STATE,
            False,
        ),
        (
            PositionRecoveryState.AMBIGUOUS,
            PositionSafetyState.BLOCKED_AMBIGUOUS,
            False,
        ),
    ],
)
def test_evaluator_maps_recovery_exactly(recovery_state, safety_state, is_safe):
    recovery = recovery_for(recovery_state)
    result = PositionSafetyEvaluator().evaluate(recovery)
    assert result.state is safety_state
    assert result.is_safe is is_safe
    assert result.recovery_result is recovery
    assert result.recovery_result.synchronization_result is recovery.synchronization_result


def test_safe_flat_retains_empty_diagnostic_without_creating_objects():
    recovery = PositionRecoveryCoordinator(PositionManager()).recover([])
    result = PositionSafetyEvaluator().evaluate(recovery)
    assert result.state is PositionSafetyState.SAFE_FLAT
    assert result.recovery_result.synchronization_result.managed_position is None
    assert result.recovery_result.synchronization_result.broker_positions == ()


def test_safe_open_retains_exact_managed_and_broker_identity_without_resynchronizing(
    monkeypatch,
):
    manager = PositionManager()
    managed = managed_position()
    broker = broker_position(average_price=999.0)
    manager.register(managed)
    recovery = PositionRecoveryCoordinator(manager).recover([broker])

    def fail_if_called(self, positions):
        raise AssertionError("synchronization must not run during safety evaluation")

    monkeypatch.setattr(PositionSynchronizer, "synchronize", fail_if_called)
    result = PositionSafetyEvaluator().evaluate(recovery)
    diagnostic = result.recovery_result.synchronization_result
    assert result.state is PositionSafetyState.SAFE_OPEN
    assert diagnostic.managed_position is managed
    assert diagnostic.broker_positions[0] is broker
    assert manager.active_position is managed


@pytest.mark.parametrize(
    ("positions", "expected_state"),
    [
        ([broker_position()], PositionSafetyState.BLOCKED_ADOPTION_REQUIRED),
        (
            [broker_position(), broker_position(tradingsymbol="NIFTY26AUG25000PE")],
            PositionSafetyState.BLOCKED_AMBIGUOUS,
        ),
    ],
)
def test_flat_manager_blocked_states_do_not_adopt_select_or_net(positions, expected_state):
    manager = PositionManager()
    recovery = PositionRecoveryCoordinator(manager).recover(positions)
    result = PositionSafetyEvaluator().evaluate(recovery)
    assert result.state is expected_state
    assert result.is_safe is False
    assert manager.active_position is None
    assert result.recovery_result.synchronization_result.broker_positions == tuple(positions)


def test_stale_managed_state_is_blocked_without_clearing_or_manufacturing_close():
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    recovery = PositionRecoveryCoordinator(manager).recover([])
    result = PositionSafetyEvaluator().evaluate(recovery)
    assert result.state is PositionSafetyState.BLOCKED_STALE_MANAGED_STATE
    assert result.is_safe is False
    assert manager.active_position is managed
    assert managed.state is PositionState.OPEN


def test_ambiguous_mismatch_is_blocked_without_repair():
    manager = PositionManager()
    managed = managed_position()
    manager.register(managed)
    recovery = PositionRecoveryCoordinator(manager).recover(
        [broker_position(quantity=130)]
    )
    result = PositionSafetyEvaluator().evaluate(recovery)
    assert result.state is PositionSafetyState.BLOCKED_AMBIGUOUS
    assert manager.active_position is managed


@pytest.mark.parametrize("value", [None, object(), "recovery", True])
def test_evaluator_accepts_only_position_recovery_result(value):
    with pytest.raises(TypeError):
        PositionSafetyEvaluator().evaluate(value)


def test_evaluator_is_stateless_and_does_not_cache_safe_result():
    evaluator = PositionSafetyEvaluator()
    safe = evaluator.evaluate(recovery_for(PositionRecoveryState.SAFE_FLAT))
    blocked = evaluator.evaluate(recovery_for(PositionRecoveryState.AMBIGUOUS))
    assert safe.state is PositionSafetyState.SAFE_FLAT
    assert blocked.state is PositionSafetyState.BLOCKED_AMBIGUOUS
    assert blocked.is_safe is False


def test_safety_module_has_no_mutating_or_external_dependencies():
    source = Path("trading/position_safety.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", ".register(", ".clear(", "openpositionlifecycle",
        "closepositionlifecycle", "positionreversallifecycle", "positions(",
        "kiteconnect", "zerodha", "place_order", "cancel_order", "modify_order",
        "order_history", "livereadinessgate", "live_startup", "live_runtime",
        "marketdata", "strategyengine", "executionrouter", "pnl", "portfolio",
        "database", "persist", "thread", "retry", "poll",
    )
    assert all(term not in source for term in forbidden)
