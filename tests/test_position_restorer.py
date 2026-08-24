from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartCoordinator, PositionRestartState
from trading.position_restorer import (
    PositionRestorationError,
    PositionRestorationResult,
    PositionRestorationState,
    PositionRestorer,
)
from trading.position_store import PositionStore


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def position(side=PositionSide.CE, **overrides):
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{side.value}",
        "quantity": 65,
        "entry_price": 100.25,
        "entry_time": ENTRY_TIME,
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def broker(side=PositionSide.CE):
    return BrokerPosition(
        f"NIFTY26AUG25000{side.value}", "NFO", 65, 999.0, "NRML"
    )


def restart_result(tmp_path, side=PositionSide.CE, broker_positions=None):
    store = PositionStore(tmp_path / "position.json")
    stored_manager = PositionManager()
    stored_manager.register(position(side))
    store.save(stored_manager)
    if broker_positions is None:
        broker_positions = [broker(side)]
    return PositionRestartCoordinator(store).recover(broker_positions)


def test_restoration_state_and_result_shape_are_exact_and_frozen(tmp_path):
    restart = restart_result(tmp_path)
    result = PositionRestorer(PositionManager()).restore(restart)
    assert [(state.name, state.value) for state in PositionRestorationState] == [
        ("RESTORED", "RESTORED")
    ]
    assert [field.name for field in fields(result)] == [
        "state", "restart_result", "restored_position", "message"
    ]
    with pytest.raises(FrozenInstanceError):
        result.state = object()


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_restore_registers_exact_persisted_object_and_preserves_values(tmp_path, side):
    restart = restart_result(tmp_path, side)
    persisted = restart.persisted_position
    original_values = (
        persisted.side,
        persisted.contract_symbol,
        persisted.quantity,
        persisted.entry_price,
        persisted.entry_time,
    )
    manager = PositionManager()

    result = PositionRestorer(manager).restore(restart)

    assert result.state is PositionRestorationState.RESTORED
    assert result.restart_result is restart
    assert result.restored_position is persisted
    assert manager.active_position is persisted
    assert (
        persisted.side,
        persisted.contract_symbol,
        persisted.quantity,
        persisted.entry_price,
        persisted.entry_time,
    ) == original_values


@pytest.mark.parametrize("value", [None, object(), "restart", True])
def test_restore_rejects_invalid_input_without_mutation(value):
    manager = PositionManager()
    with pytest.raises(TypeError):
        PositionRestorer(manager).restore(value)
    assert manager.active_position is None


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_restorer_requires_manager_without_mutating_any_manager(manager):
    with pytest.raises(TypeError):
        PositionRestorer(manager)


def test_constructor_retains_flat_manager_without_mutation():
    manager = PositionManager()
    PositionRestorer(manager)
    assert manager.active_position is None


@pytest.mark.parametrize("occupied_kind", ["same", "equivalent", "different"])
def test_occupied_manager_is_rejected_and_existing_position_retained(
    tmp_path, occupied_kind
):
    restart = restart_result(tmp_path)
    if occupied_kind == "same":
        occupied = restart.persisted_position
    elif occupied_kind == "equivalent":
        occupied = position()
    else:
        occupied = position(PositionSide.PE)
    manager = PositionManager()
    manager.register(occupied)

    with pytest.raises(PositionRestorationError):
        PositionRestorer(manager).restore(restart)
    assert manager.active_position is occupied


def test_safe_flat_is_rejected(tmp_path):
    restart = PositionRestartCoordinator(
        PositionStore(tmp_path / "missing.json")
    ).recover([])
    manager = PositionManager()
    with pytest.raises(PositionRestorationError):
        PositionRestorer(manager).restore(restart)
    assert restart.state is PositionRestartState.SAFE_FLAT
    assert manager.active_position is None


@pytest.mark.parametrize(
    "broker_positions, expected_state",
    [
        ([], PositionRestartState.STALE_PERSISTED_STATE),
        ([broker(PositionSide.PE)], PositionRestartState.AMBIGUOUS),
    ],
)
def test_stale_and_ambiguous_states_are_rejected(
    tmp_path, broker_positions, expected_state
):
    restart = restart_result(tmp_path, broker_positions=broker_positions)
    manager = PositionManager()
    with pytest.raises(PositionRestorationError):
        PositionRestorer(manager).restore(restart)
    assert restart.state is expected_state
    assert manager.active_position is None


def test_adoption_required_is_rejected(tmp_path):
    restart = PositionRestartCoordinator(
        PositionStore(tmp_path / "missing.json")
    ).recover([broker()])
    manager = PositionManager()
    with pytest.raises(PositionRestorationError):
        PositionRestorer(manager).restore(restart)
    assert restart.state is PositionRestartState.ADOPTION_REQUIRED
    assert manager.active_position is None


def test_tampered_authorization_is_rechecked_before_mutation(tmp_path):
    restart = restart_result(tmp_path)
    object.__setattr__(restart.safety_result, "state", object())
    manager = PositionManager()
    with pytest.raises(PositionRestorationError):
        PositionRestorer(manager).restore(restart)
    assert manager.active_position is None


@pytest.mark.parametrize("message", [None, "", "   "])
def test_result_requires_non_empty_message(tmp_path, message):
    restart = restart_result(tmp_path)
    with pytest.raises(ValueError):
        PositionRestorationResult(
            PositionRestorationState.RESTORED,
            restart,
            restart.persisted_position,
            message,
        )


def test_result_requires_exact_persisted_open_position(tmp_path):
    restart = restart_result(tmp_path)
    with pytest.raises(ValueError):
        PositionRestorationResult(
            PositionRestorationState.RESTORED, restart, position(), "Restored."
        )
    object.__setattr__(restart.persisted_position, "state", PositionState.CLOSED)
    with pytest.raises(ValueError):
        PositionRestorationResult(
            PositionRestorationState.RESTORED,
            restart,
            restart.persisted_position,
            "Restored.",
        )


def test_restorer_source_has_no_external_or_out_of_scope_dependencies():
    source = Path("trading/position_restorer.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionstore", "kiteconnect", "zerodha", "place_order", "cancel_order",
        "modify_order", "order_history", "livereadinessgate", "liverunauthorizer",
        "marketdata", "strategy", "p&l", "portfolio", "sleep", "retry", "poll",
        "thread",
    )
    assert all(term not in source for term in forbidden)
