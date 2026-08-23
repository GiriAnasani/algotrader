import inspect

import pytest

from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.live_recovery import (
    LiveRecoveryCoordinator,
    LiveRecoveryResult,
    LiveRecoveryState,
)
from trading.live_session_bootstrap import LiveSessionBootstrap


def recovery_result(state):
    return LiveRecoveryResult(state, (), (), f"Recovery state: {state.value}")


class SpyRecoveryCoordinator(LiveRecoveryCoordinator):
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def recover(self, allowed_contract_symbols=None):
        self.calls.append(allowed_contract_symbols)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "coordinator",
    [None, object(), "recovery"],
)
def test_bootstrap_requires_live_recovery_coordinator(coordinator):
    with pytest.raises(TypeError, match="LiveRecoveryCoordinator"):
        LiveSessionBootstrap(coordinator, LiveReadinessGate())


@pytest.mark.parametrize("gate", [None, object(), True])
def test_bootstrap_requires_live_readiness_gate(gate):
    coordinator = SpyRecoveryCoordinator(
        [recovery_result(LiveRecoveryState.SAFE_FLAT)]
    )

    with pytest.raises(TypeError, match="LiveReadinessGate"):
        LiveSessionBootstrap(coordinator, gate)


@pytest.mark.parametrize("allowed_symbols", [None, ("NIFTY-CE", "NIFTY-PE")])
def test_safe_flat_initialization_applies_and_returns_exact_result(allowed_symbols):
    result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    coordinator = SpyRecoveryCoordinator([result])
    gate = LiveReadinessGate()
    bootstrap = LiveSessionBootstrap(coordinator, gate)

    returned = bootstrap.initialize(allowed_contract_symbols=allowed_symbols)

    assert coordinator.calls == [allowed_symbols]
    assert returned is result
    assert gate.state is LiveReadinessState.READY
    assert gate.is_ready is True
    assert gate.last_recovery_result is result


@pytest.mark.parametrize(
    "state",
    [
        LiveRecoveryState.POSITION_PRESENT,
        LiveRecoveryState.PENDING_ORDER,
        LiveRecoveryState.AMBIGUOUS,
        LiveRecoveryState.UNKNOWN,
    ],
)
def test_unsafe_result_is_returned_exactly_and_leaves_gate_not_ready(state):
    safe_result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    unsafe_result = recovery_result(state)
    coordinator = SpyRecoveryCoordinator([safe_result, unsafe_result])
    gate = LiveReadinessGate()
    bootstrap = LiveSessionBootstrap(coordinator, gate)
    bootstrap.initialize()
    assert gate.is_ready is True

    returned = bootstrap.initialize()

    assert returned is unsafe_result
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is unsafe_result
    assert coordinator.calls == [None, None]


def test_recovery_exception_revokes_prior_readiness_and_propagates_without_retry():
    safe_result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    failure = RuntimeError("recovery unavailable")
    coordinator = SpyRecoveryCoordinator([safe_result, failure])
    gate = LiveReadinessGate()
    bootstrap = LiveSessionBootstrap(coordinator, gate)
    bootstrap.initialize()
    assert gate.is_ready is True

    with pytest.raises(RuntimeError, match="recovery unavailable") as raised:
        bootstrap.initialize()

    assert raised.value is failure
    assert coordinator.calls == [None, None]
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is safe_result


def test_repeated_initialization_always_uses_fresh_recovery_truth():
    safe_first = recovery_result(LiveRecoveryState.SAFE_FLAT)
    unsafe = recovery_result(LiveRecoveryState.POSITION_PRESENT)
    safe_again = recovery_result(LiveRecoveryState.SAFE_FLAT)
    coordinator = SpyRecoveryCoordinator([safe_first, unsafe, safe_again])
    gate = LiveReadinessGate()
    bootstrap = LiveSessionBootstrap(coordinator, gate)

    assert bootstrap.initialize() is safe_first
    assert gate.state is LiveReadinessState.READY
    assert bootstrap.initialize() is unsafe
    assert gate.state is LiveReadinessState.NOT_READY
    assert bootstrap.initialize() is safe_again
    assert gate.state is LiveReadinessState.READY
    assert gate.last_recovery_result is safe_again
    assert coordinator.calls == [None, None, None]


def test_invalid_recovery_output_fails_closed_through_existing_gate_validation():
    coordinator = SpyRecoveryCoordinator([object()])
    gate = LiveReadinessGate()
    gate.apply_recovery_result(recovery_result(LiveRecoveryState.SAFE_FLAT))
    bootstrap = LiveSessionBootstrap(coordinator, gate)

    with pytest.raises(TypeError, match="LiveRecoveryResult"):
        bootstrap.initialize()

    assert gate.state is LiveReadinessState.NOT_READY
    assert len(coordinator.calls) == 1


def test_bootstrap_has_only_explicit_recovery_and_readiness_dependencies():
    source = inspect.getsource(LiveSessionBootstrap)

    assert source.count(".recover(") == 1
    assert ".revoke()" in source
    assert ".apply_recovery_result(" in source
    for forbidden in (
        "MarketData",
        "KiteConnect",
        "ZerodhaPositionReader",
        "ZerodhaOrderListReader",
        "place_order",
        "order_history",
        "cancel_order",
        "modify_order",
    ):
        assert forbidden not in source
