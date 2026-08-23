from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from enum import Enum

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_restart import (
    PositionRestartCoordinator,
    PositionRestartResult,
    PositionRestartState,
)
from trading.position_store import PositionStore, PositionStoreCorruptionError


NOW = datetime(2026, 8, 23, 9, 30, tzinfo=timezone.utc)


def managed(side=PositionSide.CE):
    return ManagedPosition(
        side, f"NIFTY26AUG25000{side.value}", 65, 100.0, NOW,
        PositionState.OPEN,
    )


def broker(side=PositionSide.CE, symbol=None, quantity=65, price=101.0):
    return BrokerPosition(
        symbol or f"NIFTY26AUG25000{side.value}", "NFO", quantity, price, "NRML"
    )


def saved_store(tmp_path, position=None):
    store = PositionStore(tmp_path / "position.json")
    manager = PositionManager()
    if position is not None:
        manager.register(position)
    store.save(manager)
    return store


def test_restart_states_are_exact():
    assert [(state.name, state.value) for state in PositionRestartState] == [
        ("SAFE_FLAT", "SAFE_FLAT"),
        ("RESTORE_ALLOWED", "RESTORE_ALLOWED"),
        ("ADOPTION_REQUIRED", "ADOPTION_REQUIRED"),
        ("STALE_PERSISTED_STATE", "STALE_PERSISTED_STATE"),
        ("AMBIGUOUS", "AMBIGUOUS"),
    ]
    assert issubclass(PositionRestartState, Enum)


def test_safe_flat_result_is_frozen_and_has_only_required_fields(tmp_path):
    result = PositionRestartCoordinator(saved_store(tmp_path)).recover([])
    assert result.state is PositionRestartState.SAFE_FLAT
    assert result.persisted_position is None
    assert result.is_safe and not result.can_restore
    assert [field.name for field in fields(result)] == [
        "state", "persisted_position", "recovery_result", "safety_result", "message"
    ]
    with pytest.raises(FrozenInstanceError):
        result.state = PositionRestartState.AMBIGUOUS


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_matching_persisted_position_allows_restore_with_exact_identities(tmp_path, side):
    original = managed(side)
    store = saved_store(tmp_path, original)
    broker_position = broker(side, price=999.0)
    result = PositionRestartCoordinator(store).recover([broker_position])

    assert result.state is PositionRestartState.RESTORE_ALLOWED
    assert result.is_safe and result.can_restore
    assert result.persisted_position is result.recovery_result.synchronization_result.managed_position
    assert result.recovery_result.synchronization_result.broker_positions[0] is broker_position


def test_case_insensitive_symbol_match_allows_restore(tmp_path):
    result = PositionRestartCoordinator(saved_store(tmp_path, managed())).recover(
        [broker(symbol="nifty26aug25000ce")]
    )
    assert result.state is PositionRestartState.RESTORE_ALLOWED


def test_missing_store_with_broker_requires_adoption_without_manufacturing_position(tmp_path):
    result = PositionRestartCoordinator(PositionStore(tmp_path / "missing.json")).recover(
        [broker()]
    )
    assert result.state is PositionRestartState.ADOPTION_REQUIRED
    assert result.persisted_position is None
    assert result.recovery_result.synchronization_result.managed_position is None
    assert not result.is_safe and not result.can_restore


def test_persisted_position_without_broker_is_stale_and_store_unchanged(tmp_path):
    store = saved_store(tmp_path, managed())
    before = store.path.read_bytes()
    result = PositionRestartCoordinator(store).recover([])
    assert result.state is PositionRestartState.STALE_PERSISTED_STATE
    assert result.persisted_position.state is PositionState.OPEN
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "positions",
    [
        [broker(symbol="NIFTY26AUG25100CE")],
        [broker(quantity=130)],
        [broker(PositionSide.PE)],
        [broker(), broker(PositionSide.PE)],
    ],
)
def test_conflicting_broker_state_is_ambiguous(tmp_path, positions):
    result = PositionRestartCoordinator(saved_store(tmp_path, managed())).recover(positions)
    assert result.state is PositionRestartState.AMBIGUOUS
    assert not result.is_safe and not result.can_restore


def test_corrupt_store_propagates_without_becoming_flat(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    store.path.write_text("not json", encoding="utf-8")
    with pytest.raises(PositionStoreCorruptionError):
        PositionRestartCoordinator(store).recover([])


@pytest.mark.parametrize("snapshot", [None, object(), [object()]])
def test_invalid_broker_snapshot_propagates(snapshot, tmp_path):
    with pytest.raises(TypeError):
        PositionRestartCoordinator(saved_store(tmp_path)).recover(snapshot)


@pytest.mark.parametrize("store", [None, object(), "position.json", True])
def test_coordinator_requires_position_store(store):
    with pytest.raises(TypeError):
        PositionRestartCoordinator(store)


def test_load_is_called_once_and_snapshot_is_immutable(monkeypatch, tmp_path):
    store = saved_store(tmp_path, managed())
    calls = 0
    original_load = store.load

    def counted_load():
        nonlocal calls
        calls += 1
        return original_load()

    monkeypatch.setattr(store, "load", counted_load)
    broker_position = broker()
    snapshot = [broker_position]
    result = PositionRestartCoordinator(store).recover(snapshot)
    snapshot.clear()
    assert calls == 1
    assert result.recovery_result.synchronization_result.broker_positions == (broker_position,)
