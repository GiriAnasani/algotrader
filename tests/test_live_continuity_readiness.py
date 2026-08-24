from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.live_continuity_readiness import LiveContinuityReadinessCoordinator
from trading.live_execution import LiveExecutionCoordinator
from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_run_authorization import (
    LiveRunAuthorizationState,
    LiveRunAuthorizer,
)
from trading.live_runtime import LiveRuntimeComponents
from trading.live_startup import LiveStartupResult
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_continuity import PositionContinuityVerifier
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartCoordinator
from trading.position_restorer import PositionRestorer
from trading.position_store import PositionStore


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def position(side=PositionSide.CE):
    return ManagedPosition(
        side,
        f"NIFTY26AUG25000{side.value}",
        65,
        100.0,
        ENTRY_TIME,
        PositionState.OPEN,
    )


def broker(side=PositionSide.CE):
    return BrokerPosition(
        f"NIFTY26AUG25000{side.value}", "NFO", 65, 105.0, "NRML"
    )


def continuity(tmp_path, restored=False, side=PositionSide.CE):
    store = PositionStore(tmp_path / f"{side.value}-{restored}.json")
    manager = PositionManager()
    if restored:
        stored_manager = PositionManager()
        stored_manager.register(position(side))
        store.save(stored_manager)
        restart = PositionRestartCoordinator(store).recover([broker(side)])
        restoration = PositionRestorer(manager).restore(restart)
    else:
        restart = PositionRestartCoordinator(store).recover([])
        restoration = None
    result = PositionContinuityVerifier(manager).verify(restart, restoration)
    return result, manager


@pytest.mark.parametrize("gate", [None, object(), "gate", True])
def test_coordinator_requires_actual_readiness_gate(gate):
    with pytest.raises(TypeError):
        LiveContinuityReadinessCoordinator(gate)


def test_construction_has_no_side_effect():
    gate = LiveReadinessGate()
    LiveContinuityReadinessCoordinator(gate)
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.last_continuity_result is None


@pytest.mark.parametrize("value", [None, object(), "continuity", True])
def test_invalid_input_is_rejected_and_fails_closed(value):
    gate = LiveReadinessGate()
    gate._state = LiveReadinessState.READY
    with pytest.raises(TypeError):
        LiveContinuityReadinessCoordinator(gate).apply(value)
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.last_continuity_result is None


@pytest.mark.parametrize("restored", [False, True])
def test_verified_continuity_makes_ready_and_retains_exact_diagnostic(
    tmp_path, restored
):
    result, manager = continuity(tmp_path, restored=restored)
    active_before = manager.active_position
    gate = LiveReadinessGate()

    returned = LiveContinuityReadinessCoordinator(gate).apply(result)

    assert returned is result
    assert gate.state is LiveReadinessState.READY
    assert gate.last_continuity_result is result
    assert gate.last_recovery_result is None
    assert manager.active_position is active_before


@pytest.mark.parametrize("restored", [False, True])
def test_revoke_and_reapply_require_explicit_valid_continuity(tmp_path, restored):
    result, _ = continuity(tmp_path, restored=restored)
    gate = LiveReadinessGate()
    coordinator = LiveContinuityReadinessCoordinator(gate)
    coordinator.apply(result)
    gate.revoke()
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.last_continuity_result is result
    coordinator.apply(result)
    assert gate.state is LiveReadinessState.READY


def startup_for_authorization(gate, execution_enabled):
    execution = object.__new__(LiveExecutionCoordinator)
    execution.enabled = execution_enabled
    runtime = object.__new__(LiveRuntimeComponents)
    object.__setattr__(runtime, "readiness_gate", gate)
    object.__setattr__(runtime, "execution_coordinator", execution)
    return LiveStartupResult(runtime=runtime, market=object(), recovery_result=object())


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize(
    "execution_enabled, expected",
    [
        (False, LiveRunAuthorizationState.BLOCKED),
        (True, LiveRunAuthorizationState.PERMITTED),
    ],
)
def test_authorization_still_requires_readiness_and_execution(
    tmp_path, restored, execution_enabled, expected
):
    result, _ = continuity(tmp_path, restored=restored)
    gate = LiveReadinessGate()
    LiveContinuityReadinessCoordinator(gate).apply(result)
    startup = startup_for_authorization(gate, execution_enabled)
    authorization = LiveRunAuthorizer().authorize(startup)
    assert authorization.state is expected
    assert authorization.execution_enabled is execution_enabled

    gate.revoke()
    assert LiveRunAuthorizer().authorize(startup).state is LiveRunAuthorizationState.BLOCKED


def test_readiness_module_preserves_recovery_and_continuity_diagnostics(tmp_path):
    result, _ = continuity(tmp_path)
    gate = LiveReadinessGate()
    LiveContinuityReadinessCoordinator(gate).apply(result)
    assert gate.last_continuity_result is result
    assert gate.last_recovery_result is None


def test_continuity_readiness_preserves_existing_recovery_diagnostic(tmp_path):
    recovery_result = LiveRecoveryResult(
        state=LiveRecoveryState.SAFE_FLAT,
        broker_positions=(),
        relevant_order_statuses=(),
        message="Broker recovery is safely flat.",
    )
    continuity_result, _ = continuity(tmp_path)
    gate = LiveReadinessGate()
    gate.apply_recovery_result(recovery_result)

    LiveContinuityReadinessCoordinator(gate).apply(continuity_result)

    assert gate.state is LiveReadinessState.READY
    assert gate.last_recovery_result is recovery_result
    assert gate.last_continuity_result is continuity_result


def test_invalid_reapplication_preserves_previous_continuity_diagnostic(tmp_path):
    continuity_result, _ = continuity(tmp_path)
    gate = LiveReadinessGate()
    coordinator = LiveContinuityReadinessCoordinator(gate)
    coordinator.apply(continuity_result)

    with pytest.raises(TypeError):
        coordinator.apply(object())

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.last_continuity_result is continuity_result


def test_coordinator_has_no_external_or_runtime_side_effect_dependencies():
    source = Path("trading/live_continuity_readiness.py").read_text(
        encoding="utf-8"
    ).lower()
    forbidden = (
        "positionstore", "kiteconnect", "zerodha", "place_order", "order_history",
        "positionmanager", "register(", "clear(", "marketdata", "websocket",
        "strategy", "pnl", "portfolio", "sleep", "retry", "poll", "thread",
    )
    assert all(term not in source for term in forbidden)
