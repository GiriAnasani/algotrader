from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_continuity import (
    PositionContinuityError,
    PositionContinuityResult,
    PositionContinuityState,
    PositionContinuityVerifier,
)
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartCoordinator, PositionRestartState
from trading.position_restorer import PositionRestorer
from trading.position_store import PositionStore


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def managed(side=PositionSide.CE):
    return ManagedPosition(
        side=side,
        contract_symbol=f"NIFTY26AUG25000{side.value}",
        quantity=65,
        entry_price=100.0,
        entry_time=ENTRY_TIME,
        state=PositionState.OPEN,
    )


def broker(side=PositionSide.CE):
    return BrokerPosition(
        f"NIFTY26AUG25000{side.value}", "NFO", 65, 105.0, "NRML"
    )


def restart(tmp_path, side=PositionSide.CE, positions=None, stored=True, name="state"):
    store = PositionStore(tmp_path / f"{name}.json")
    if stored:
        manager = PositionManager()
        manager.register(managed(side))
        store.save(manager)
    if positions is None:
        positions = [broker(side)] if stored else []
    return PositionRestartCoordinator(store).recover(positions)


def restored_chain(tmp_path, side=PositionSide.CE, name="state"):
    restart_result = restart(tmp_path, side, name=name)
    manager = PositionManager()
    restoration_result = PositionRestorer(manager).restore(restart_result)
    return restart_result, restoration_result, manager


def test_continuity_states_are_exact():
    assert [(state.name, state.value) for state in PositionContinuityState] == [
        ("VERIFIED_FLAT", "VERIFIED_FLAT"),
        ("VERIFIED_RESTORED", "VERIFIED_RESTORED"),
    ]


def test_real_safe_flat_chain_verifies_and_result_is_frozen(tmp_path):
    restart_result = restart(tmp_path, stored=False)
    manager = PositionManager()
    result = PositionContinuityVerifier(manager).verify(restart_result)

    assert result.state is PositionContinuityState.VERIFIED_FLAT
    assert result.restart_result is restart_result
    assert result.restoration_result is None
    assert result.runtime_position is None
    assert manager.active_position is None
    assert [field.name for field in fields(result)] == [
        "state", "restart_result", "restoration_result", "runtime_position", "message"
    ]
    with pytest.raises(FrozenInstanceError):
        result.state = PositionContinuityState.VERIFIED_RESTORED


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_real_restored_chain_verifies_exact_identities_without_mutation(tmp_path, side):
    restart_result, restoration_result, manager = restored_chain(tmp_path, side)
    active_before = manager.active_position

    result = PositionContinuityVerifier(manager).verify(
        restart_result, restoration_result
    )

    assert result.state is PositionContinuityState.VERIFIED_RESTORED
    assert result.restart_result is restart_result
    assert result.restoration_result is restoration_result
    assert result.runtime_position is restart_result.persisted_position
    assert result.runtime_position is restoration_result.restored_position
    assert manager.active_position is active_before
    synchronization = restart_result.recovery_result.synchronization_result
    assert synchronization.managed_position is result.runtime_position
    assert synchronization.broker_positions[0] is not None
    assert restart_result.safety_result.recovery_result is restart_result.recovery_result


def test_safe_flat_rejects_occupied_runtime(tmp_path):
    restart_result = restart(tmp_path, stored=False)
    manager = PositionManager()
    active = managed()
    manager.register(active)
    with pytest.raises(PositionContinuityError):
        PositionContinuityVerifier(manager).verify(restart_result)
    assert manager.active_position is active


def test_safe_flat_rejects_restoration_result(tmp_path):
    flat = restart(tmp_path, stored=False, name="flat")
    _, restoration, manager = restored_chain(tmp_path, name="open")
    with pytest.raises(PositionContinuityError):
        PositionContinuityVerifier(manager).verify(flat, restoration)


def test_restore_allowed_requires_restoration_and_exact_runtime_object(tmp_path):
    restart_result = restart(tmp_path)
    flat_manager = PositionManager()
    verifier = PositionContinuityVerifier(flat_manager)
    with pytest.raises(PositionContinuityError):
        verifier.verify(restart_result)

    equivalent = managed()
    flat_manager.register(equivalent)
    restoration = PositionRestorer(PositionManager()).restore(restart_result)
    with pytest.raises(PositionContinuityError):
        verifier.verify(restart_result, restoration)
    assert flat_manager.active_position is equivalent


def test_restoration_from_another_chain_is_rejected(tmp_path):
    first_restart, _, first_manager = restored_chain(tmp_path, name="first")
    _, second_restoration, _ = restored_chain(tmp_path, name="second")
    with pytest.raises(PositionContinuityError):
        PositionContinuityVerifier(first_manager).verify(
            first_restart, second_restoration
        )


@pytest.mark.parametrize(
    "positions, stored, expected",
    [
        ([broker()], False, PositionRestartState.ADOPTION_REQUIRED),
        ([], True, PositionRestartState.STALE_PERSISTED_STATE),
        ([broker(PositionSide.PE)], True, PositionRestartState.AMBIGUOUS),
    ],
)
def test_blocked_restart_states_fail_without_manager_mutation(
    tmp_path, positions, stored, expected
):
    restart_result = restart(tmp_path, positions=positions, stored=stored)
    manager = PositionManager()
    with pytest.raises(PositionContinuityError):
        PositionContinuityVerifier(manager).verify(restart_result)
    assert restart_result.state is expected
    assert manager.active_position is None


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_verifier_requires_actual_manager(manager):
    with pytest.raises(TypeError):
        PositionContinuityVerifier(manager)


@pytest.mark.parametrize("value", [None, object(), "restart", True, managed()])
def test_invalid_restart_input_is_rejected_without_mutation(value):
    manager = PositionManager()
    with pytest.raises(TypeError):
        PositionContinuityVerifier(manager).verify(value)
    assert manager.active_position is None


def test_invalid_restoration_input_is_rejected_without_mutation(tmp_path):
    restart_result = restart(tmp_path, stored=False)
    manager = PositionManager()
    with pytest.raises(TypeError):
        PositionContinuityVerifier(manager).verify(restart_result, object())
    assert manager.active_position is None


@pytest.mark.parametrize("message", [None, "", "   "])
def test_result_requires_non_empty_message(tmp_path, message):
    restart_result = restart(tmp_path, stored=False)
    with pytest.raises(ValueError):
        PositionContinuityResult(
            PositionContinuityState.VERIFIED_FLAT,
            restart_result,
            None,
            None,
            message,
        )


def test_result_rejects_contradictory_flat_and_restored_values(tmp_path):
    flat = restart(tmp_path, stored=False, name="flat")
    open_restart, restoration, manager = restored_chain(tmp_path, name="open")
    with pytest.raises(ValueError):
        PositionContinuityResult(
            PositionContinuityState.VERIFIED_FLAT,
            flat,
            restoration,
            None,
            "Verified.",
        )
    with pytest.raises(ValueError):
        PositionContinuityResult(
            PositionContinuityState.VERIFIED_RESTORED,
            open_restart,
            None,
            manager.active_position,
            "Verified.",
        )


def test_continuity_module_has_no_external_or_mutating_dependencies():
    source = Path("trading/position_continuity.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionstore", "kiteconnect", "zerodha", "place_order", "cancel_order",
        "modify_order", "order_history", "livereadinessgate", "liverunauthorizer",
        "marketdata", "websocket", "strategy", "pnl", "portfolio", "save(",
        "load(", "register(", "clear(", "sleep", "retry", "poll", "thread",
    )
    assert all(term not in source for term in forbidden)
