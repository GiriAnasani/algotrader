from dataclasses import FrozenInstanceError

import pytest

from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState


def recovery_result(state):
    return LiveRecoveryResult(state, (), (), f"Recovery state: {state.value}")


def test_new_gate_starts_fail_closed_without_a_recovery_result():
    gate = LiveReadinessGate()

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is None


def test_safe_flat_result_makes_gate_ready_and_is_retained_exactly():
    gate = LiveReadinessGate()
    result = recovery_result(LiveRecoveryState.SAFE_FLAT)

    gate.apply_recovery_result(result)

    assert gate.state is LiveReadinessState.READY
    assert gate.is_ready is True
    assert gate.last_recovery_result is result


@pytest.mark.parametrize(
    "unsafe_state",
    [
        LiveRecoveryState.POSITION_PRESENT,
        LiveRecoveryState.PENDING_ORDER,
        LiveRecoveryState.AMBIGUOUS,
        LiveRecoveryState.UNKNOWN,
    ],
)
def test_unsafe_result_keeps_new_gate_not_ready(unsafe_state):
    gate = LiveReadinessGate()
    result = recovery_result(unsafe_state)

    gate.apply_recovery_result(result)

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is result


@pytest.mark.parametrize(
    "unsafe_state",
    [
        LiveRecoveryState.POSITION_PRESENT,
        LiveRecoveryState.PENDING_ORDER,
        LiveRecoveryState.AMBIGUOUS,
        LiveRecoveryState.UNKNOWN,
    ],
)
def test_unsafe_result_revokes_readiness_from_earlier_safe_result(unsafe_state):
    gate = LiveReadinessGate()
    gate.apply_recovery_result(recovery_result(LiveRecoveryState.SAFE_FLAT))
    unsafe_result = recovery_result(unsafe_state)

    gate.apply_recovery_result(unsafe_result)

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is unsafe_result


def test_revoke_fails_closed_and_retains_last_recovery_diagnostic():
    gate = LiveReadinessGate()
    result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    gate.apply_recovery_result(result)

    gate.revoke()

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is result


def test_revoke_is_idempotent_before_any_recovery():
    gate = LiveReadinessGate()

    gate.revoke()
    gate.revoke()

    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is None


def test_revoke_requires_another_explicit_safe_result_to_restore_readiness():
    gate = LiveReadinessGate()
    first_result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    gate.apply_recovery_result(first_result)
    gate.revoke()

    assert gate.is_ready is False
    assert gate.last_recovery_result is first_result

    second_result = recovery_result(LiveRecoveryState.SAFE_FLAT)
    gate.apply_recovery_result(second_result)

    assert gate.is_ready is True
    assert gate.last_recovery_result is second_result


@pytest.mark.parametrize("invalid_result", [None, object(), "SAFE_FLAT", True, False])
@pytest.mark.parametrize("initially_ready", [False, True])
def test_invalid_input_is_rejected_without_mutating_gate(invalid_result, initially_ready):
    gate = LiveReadinessGate()
    previous_result = recovery_result(
        LiveRecoveryState.SAFE_FLAT
        if initially_ready
        else LiveRecoveryState.POSITION_PRESENT
    )
    gate.apply_recovery_result(previous_result)
    previous_state = gate.state

    with pytest.raises(TypeError, match="LiveRecoveryResult"):
        gate.apply_recovery_result(invalid_result)

    assert gate.state is previous_state
    assert gate.is_ready is initially_ready
    assert gate.last_recovery_result is previous_result


def test_gate_does_not_mutate_the_immutable_recovery_result():
    gate = LiveReadinessGate()
    result = recovery_result(LiveRecoveryState.SAFE_FLAT)

    gate.apply_recovery_result(result)
    gate.revoke()

    assert result.state is LiveRecoveryState.SAFE_FLAT
    with pytest.raises(FrozenInstanceError):
        result.state = LiveRecoveryState.UNKNOWN
