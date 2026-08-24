from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone

import pytest

import trading.live_restart_orchestration as orchestration_module
from trading.live_readiness import LiveReadinessState
from trading.live_recovery import LiveRecoveryState
from trading.live_run_authorization import (
    LiveRunAuthorizationState,
    LiveRunAuthorizer,
)
from trading.live_restart_orchestration import (
    LiveRestartOrchestrationResult,
    LiveRestartOrchestrationState,
    LiveRestartOrchestrator,
)
from trading.live_startup import initialize_live_session
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_continuity import PositionContinuityError, PositionContinuityState
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartState
from trading.position_store import PositionStore


ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class FakeKiteClient:
    def __init__(self, positions=None, orders=None):
        self.net_positions = list(positions or [])
        self.listed_orders = list(orders or [])
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": self.net_positions, "day": []}

    def orders(self):
        self.orders_calls += 1
        return self.listed_orders

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order"


def broker(side=PositionSide.CE, symbol=None, quantity=65):
    return {
        "tradingsymbol": symbol or f"NIFTY26AUG25000{side.value}",
        "exchange": "NFO",
        "quantity": quantity,
        "average_price": 105.0,
        "product": "MIS",
    }


def pending_order():
    return {
        "order_id": "order-1",
        "tradingsymbol": "NIFTY26AUG25000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 65,
        "filled_quantity": 0,
        "pending_quantity": 65,
        "average_price": 0.0,
        "product": "MIS",
        "status": "OPEN",
    }


def managed(side=PositionSide.CE):
    return ManagedPosition(
        side,
        f"NIFTY26AUG25000{side.value}",
        65,
        100.0,
        ENTRY_TIME,
        PositionState.OPEN,
    )


def store_with(tmp_path, position=None):
    store = PositionStore(tmp_path / "position.json")
    if position is not None:
        manager = PositionManager()
        manager.register(position)
        store.save(manager)
    return store


@pytest.mark.parametrize("store", [None, object(), "store", True])
def test_orchestrator_requires_position_store(store):
    with pytest.raises(TypeError):
        LiveRestartOrchestrator(store)


def test_construction_has_no_load_or_other_side_effect(monkeypatch, tmp_path):
    store = PositionStore(tmp_path / "position.json")
    monkeypatch.setattr(store, "load", lambda: pytest.fail("load called"))
    LiveRestartOrchestrator(store)


@pytest.mark.parametrize("value", [None, object(), "startup", True])
def test_orchestrate_requires_live_startup_result(value, tmp_path):
    with pytest.raises(TypeError):
        LiveRestartOrchestrator(PositionStore(tmp_path / "state.json")).orchestrate(value)


def test_safe_flat_reuses_startup_snapshot_and_becomes_ready(tmp_path):
    client = FakeKiteClient()
    startup = initialize_live_session(client, object())
    assert startup.runtime.readiness_gate.is_ready
    store = PositionStore(tmp_path / "missing.json")
    loads = 0
    original_load = store.load

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    store.load = counted_load
    result = LiveRestartOrchestrator(store).orchestrate(startup)

    assert result.state is LiveRestartOrchestrationState.READY_FLAT
    assert result.is_ready
    assert result.restart_result.state is PositionRestartState.SAFE_FLAT
    assert result.restoration_result is None
    assert result.continuity_result.state is PositionContinuityState.VERIFIED_FLAT
    assert startup.runtime.position_manager.active_position is None
    assert startup.runtime.readiness_gate.state is LiveReadinessState.READY
    assert loads == 1
    assert (client.positions_calls, client.orders_calls) == (1, 1)


def test_flat_broker_with_stale_store_blocks_and_preserves_file(tmp_path):
    startup = initialize_live_session(FakeKiteClient(), object())
    store = store_with(tmp_path, managed())
    before = store.path.read_bytes()
    result = LiveRestartOrchestrator(store).orchestrate(startup)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.STALE_PERSISTED_STATE
    assert not startup.runtime.readiness_gate.is_ready
    assert startup.runtime.position_manager.active_position is None
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_matching_position_restores_exact_shared_runtime_identity(tmp_path, side):
    client = FakeKiteClient(positions=[broker(side)])
    startup = initialize_live_session(client, object(), execution_enabled=False)
    result = LiveRestartOrchestrator(store_with(tmp_path, managed(side))).orchestrate(
        startup
    )

    persisted = result.restart_result.persisted_position
    assert result.state is LiveRestartOrchestrationState.READY_RESTORED
    assert result.continuity_result.state is PositionContinuityState.VERIFIED_RESTORED
    assert persisted is result.restoration_result.restored_position
    assert persisted is result.continuity_result.runtime_position
    assert persisted is startup.runtime.position_manager.active_position
    assert startup.market.live_position_manager is startup.runtime.position_manager
    assert persisted is startup.market.live_position_manager.active_position
    assert startup.runtime.readiness_gate.is_ready
    assert startup.runtime.execution_coordinator.enabled is False
    assert (client.positions_calls, client.orders_calls) == (1, 1)


def test_restore_allowed_loads_position_store_exactly_once(tmp_path):
    client = FakeKiteClient(positions=[broker()])
    startup = initialize_live_session(client, object())
    store = store_with(tmp_path, managed())
    loads = 0
    original_load = store.load

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    store.load = counted_load
    result = LiveRestartOrchestrator(store).orchestrate(startup)

    assert loads == 1
    assert result.state is LiveRestartOrchestrationState.READY_RESTORED


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("execution_enabled", [False, True])
def test_orchestration_preserves_enablement_and_authorization_boundary(
    tmp_path, restored, execution_enabled
):
    positions = [broker()] if restored else []
    client = FakeKiteClient(positions=positions)
    startup = initialize_live_session(
        client,
        object(),
        execution_enabled=execution_enabled,
    )
    store = (
        store_with(tmp_path, managed())
        if restored
        else PositionStore(tmp_path / "missing.json")
    )

    result = LiveRestartOrchestrator(store).orchestrate(startup)
    authorization = LiveRunAuthorizer().authorize(startup)

    expected_orchestration = (
        LiveRestartOrchestrationState.READY_RESTORED
        if restored
        else LiveRestartOrchestrationState.READY_FLAT
    )
    expected_authorization = (
        LiveRunAuthorizationState.PERMITTED
        if execution_enabled
        else LiveRunAuthorizationState.BLOCKED
    )
    assert result.state is expected_orchestration
    assert startup.runtime.execution_coordinator.enabled is execution_enabled
    assert authorization.state is expected_authorization
    assert client.positions_calls == 1
    assert client.orders_calls == 1
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0


def test_broker_position_without_local_state_blocks_adoption(tmp_path):
    startup = initialize_live_session(
        FakeKiteClient(positions=[broker()]), object()
    )
    result = LiveRestartOrchestrator(
        PositionStore(tmp_path / "missing.json")
    ).orchestrate(startup)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.ADOPTION_REQUIRED
    assert startup.runtime.position_manager.active_position is None
    assert not startup.runtime.readiness_gate.is_ready


@pytest.mark.parametrize(
    "broker_row",
    [
        broker(symbol="NIFTY26AUG25100CE"),
        broker(quantity=130),
        broker(PositionSide.PE),
    ],
)
def test_mismatched_position_blocks_without_repair(tmp_path, broker_row):
    startup = initialize_live_session(FakeKiteClient(positions=[broker_row]), object())
    result = LiveRestartOrchestrator(store_with(tmp_path, managed())).orchestrate(startup)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.AMBIGUOUS
    assert startup.runtime.position_manager.active_position is None
    assert not startup.runtime.readiness_gate.is_ready


@pytest.mark.parametrize("kind", ["pending", "ambiguous"])
def test_unresolved_startup_blocks_without_loading_store(tmp_path, monkeypatch, kind):
    positions = [broker()] if kind == "ambiguous" else []
    client = FakeKiteClient(positions=positions, orders=[pending_order()])
    startup = initialize_live_session(client, object())
    store = PositionStore(tmp_path / "state.json")
    monkeypatch.setattr(store, "load", lambda: pytest.fail("load called"))

    result = LiveRestartOrchestrator(store).orchestrate(startup)

    expected = (
        LiveRecoveryState.PENDING_ORDER
        if kind == "pending"
        else LiveRecoveryState.AMBIGUOUS
    )
    assert startup.recovery_result.state is expected
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result is None
    assert startup.runtime.position_manager.active_position is None
    assert not startup.runtime.readiness_gate.is_ready


def test_continuity_failure_revokes_but_retains_restored_position(
    tmp_path, monkeypatch
):
    startup = initialize_live_session(
        FakeKiteClient(positions=[broker()]), object()
    )
    failure = PositionContinuityError("continuity failed")
    monkeypatch.setattr(
        orchestration_module.PositionContinuityVerifier,
        "verify",
        lambda self, *args: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(PositionContinuityError) as raised:
        LiveRestartOrchestrator(store_with(tmp_path, managed())).orchestrate(startup)

    assert raised.value is failure
    assert startup.runtime.position_manager.active_position is not None
    assert not startup.runtime.readiness_gate.is_ready


def test_result_is_frozen_and_has_exact_fields(tmp_path):
    startup = initialize_live_session(FakeKiteClient(), object())
    result = LiveRestartOrchestrator(
        PositionStore(tmp_path / "missing.json")
    ).orchestrate(startup)
    assert [field.name for field in fields(result)] == [
        "state", "startup_result", "restart_result", "restoration_result",
        "continuity_result", "message",
    ]
    with pytest.raises(FrozenInstanceError):
        result.state = LiveRestartOrchestrationState.BLOCKED
    assert isinstance(result, LiveRestartOrchestrationResult)
