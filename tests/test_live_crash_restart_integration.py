import json
from datetime import datetime, timedelta, timezone

import pytest

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus
from trading.live_market_factory import build_live_market_data
from trading.live_order import LiveOrderIntent
from trading.live_readiness import LiveReadinessState
from trading.live_restart_orchestration import (
    LiveRestartOrchestrationState,
    LiveRestartOrchestrator,
)
from trading.live_run_authorization import (
    LiveRunAuthorizationState,
    LiveRunAuthorizer,
)
from trading.live_runtime import build_live_runtime
from trading.live_startup import initialize_live_session
from trading.position import PositionSide
from trading.position_restart import PositionRestartState
from trading.position_store import (
    PositionStore,
    PositionStoreCorruptionError,
    PositionStoreError,
)
from trading.strategy import SignalAction


FILL_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class FakeKiteClient:
    def __init__(self, positions=None, orders=None):
        self.net_positions = list(positions or [])
        self.listed_orders = list(orders or [])
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0

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
        return "order-1"

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1


def broker(side="CE", strike=25000, quantity=65):
    return {
        "tradingsymbol": f"NIFTY26AUG{strike}{side}",
        "exchange": "NFO",
        "quantity": quantity,
        "average_price": 105.5,
        "product": "MIS",
    }


def pending_order():
    return {
        "order_id": "pending-1",
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


def intent(action, side):
    return LiveOrderIntent(
        f"NIFTY26AUG25000{side}",
        side,
        action,
        65,
        100.0,
        FILL_TIME - timedelta(seconds=1),
    )


def fill(price=105.5, timestamp=FILL_TIME):
    return BrokerOrderStatus(
        "order-1",
        BrokerOrderState.COMPLETE,
        65,
        0,
        price,
        "COMPLETE",
        fill_timestamp=timestamp,
    )


def apply_fill(market, action, side, price=105.5, timestamp=FILL_TIME):
    return market._apply_live_order_status(
        "order-1",
        intent(action, side),
        fill(price, timestamp),
    )


def process_a(store):
    client = FakeKiteClient()
    runtime = build_live_runtime(client, position_store=store)
    market = build_live_market_data(client, object(), runtime)
    return runtime, market


def process_b(store_path, positions=None, orders=None, execution_enabled=False):
    client = FakeKiteClient(positions=positions, orders=orders)
    startup = initialize_live_session(
        client,
        object(),
        execution_enabled=execution_enabled,
    )
    store = PositionStore(store_path)
    return client, startup, store


def assert_no_restart_order_work(client):
    assert client.positions_calls == 1
    assert client.orders_calls == 1
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.modify_order_calls == 0
    assert client.cancel_order_calls == 0


@pytest.mark.parametrize(
    "side, action",
    [("CE", SignalAction.BUY_CE), ("PE", SignalAction.BUY_PE)],
)
def test_confirmed_buy_survives_new_process_with_exact_new_identity(
    tmp_path, side, action
):
    path = tmp_path / "position.json"
    runtime_a, market_a = process_a(PositionStore(path))
    apply_fill(market_a, action, side)
    position_a = runtime_a.position_manager.active_position
    durable_before = path.read_bytes()

    client_b, startup_b, store_b = process_b(path, positions=[broker(side)])
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    position_b = startup_b.runtime.position_manager.active_position

    assert result.state is LiveRestartOrchestrationState.READY_RESTORED
    assert position_b is not position_a
    assert position_b.side is PositionSide[side]
    assert position_b.contract_symbol == position_a.contract_symbol
    assert position_b.quantity == position_a.quantity
    assert position_b.entry_price == position_a.entry_price
    assert position_b.entry_time == position_a.entry_time
    assert (
        result.restart_result.persisted_position
        is result.restoration_result.restored_position
        is result.continuity_result.runtime_position
        is position_b
        is startup_b.market.live_position_manager.active_position
    )
    assert path.read_bytes() == durable_before
    assert_no_restart_order_work(client_b)


def test_confirmed_exit_restarts_safely_flat(tmp_path):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    apply_fill(
        market_a,
        SignalAction.EXIT_CE,
        "CE",
        timestamp=FILL_TIME + timedelta(minutes=1),
    )
    before = path.read_bytes()
    assert json.loads(before)["active_position"] is None

    client_b, startup_b, store_b = process_b(path)
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.READY_FLAT
    assert startup_b.runtime.position_manager.active_position is None
    assert path.read_bytes() == before
    assert_no_restart_order_work(client_b)


def test_reversal_crash_after_exit_does_not_infer_opposite_position(tmp_path):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    apply_fill(market_a, SignalAction.EXIT_CE, "CE")

    _, startup_b, store_b = process_b(path)
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.READY_FLAT
    assert startup_b.runtime.position_manager.active_position is None


@pytest.mark.parametrize(
    "old_side, exit_action, new_side, buy_action",
    [
        ("CE", SignalAction.EXIT_CE, "PE", SignalAction.BUY_PE),
        ("PE", SignalAction.EXIT_PE, "CE", SignalAction.BUY_CE),
    ],
)
def test_completed_reversal_restores_final_opposite_position(
    tmp_path, old_side, exit_action, new_side, buy_action
):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    first_buy = SignalAction.BUY_CE if old_side == "CE" else SignalAction.BUY_PE
    apply_fill(market_a, first_buy, old_side)
    apply_fill(market_a, exit_action, old_side)
    apply_fill(market_a, buy_action, new_side)

    _, startup_b, store_b = process_b(path, positions=[broker(new_side)])
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.READY_RESTORED
    assert startup_b.runtime.position_manager.active_position.side is PositionSide[new_side]


def test_buy_persistence_failure_restarts_blocked_for_adoption(tmp_path):
    path = tmp_path / "position.json"
    store_a = PositionStore(path)
    runtime_a, market_a = process_a(store_a)
    store_a.save = lambda manager: (_ for _ in ()).throw(
        PositionStoreError("save failed")
    )
    with pytest.raises(PositionStoreError):
        apply_fill(market_a, SignalAction.BUY_CE, "CE")
    assert runtime_a.position_manager.active_position is not None
    assert not path.exists()

    _, startup_b, store_b = process_b(path, positions=[broker()])
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.ADOPTION_REQUIRED
    assert startup_b.runtime.position_manager.active_position is None
    assert not startup_b.runtime.readiness_gate.is_ready


def test_exit_persistence_failure_restarts_blocked_with_stale_state(tmp_path):
    path = tmp_path / "position.json"
    store_a = PositionStore(path)
    runtime_a, market_a = process_a(store_a)
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    durable_open = path.read_bytes()
    store_a.save = lambda manager: (_ for _ in ()).throw(
        PositionStoreError("save failed")
    )
    with pytest.raises(PositionStoreError):
        apply_fill(market_a, SignalAction.EXIT_CE, "CE")
    assert runtime_a.position_manager.active_position is None
    assert path.read_bytes() == durable_open

    _, startup_b, store_b = process_b(path)
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.STALE_PERSISTED_STATE
    assert startup_b.runtime.position_manager.active_position is None
    assert path.read_bytes() == durable_open


@pytest.mark.parametrize(
    "broker_row",
    [broker(strike=25100), broker(quantity=130), broker(side="PE")],
)
def test_durable_and_broker_mismatch_blocks_without_store_mutation(
    tmp_path, broker_row
):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    before = path.read_bytes()

    _, startup_b, store_b = process_b(path, positions=[broker_row])
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result.state is PositionRestartState.AMBIGUOUS
    assert startup_b.runtime.position_manager.active_position is None
    assert not startup_b.runtime.readiness_gate.is_ready
    assert path.read_bytes() == before


def test_multiple_exposures_block_at_authoritative_startup_boundary(tmp_path):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    _, startup_b, store_b = process_b(
        path, positions=[broker(), broker(side="PE")]
    )
    loads = 0
    original_load = store_b.load

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    store_b.load = counted_load
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result is None
    assert loads == 0
    assert startup_b.runtime.position_manager.active_position is None


@pytest.mark.parametrize(
    "contents",
    [b"not json", b'{"version": 999, "active_position": null}'],
)
def test_corrupt_or_unsupported_store_fails_closed_without_rewrite(tmp_path, contents):
    path = tmp_path / "position.json"
    path.write_bytes(contents)
    _, startup_b, store_b = process_b(path)
    with pytest.raises(PositionStoreCorruptionError):
        LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert path.read_bytes() == contents
    assert startup_b.runtime.position_manager.active_position is None
    assert startup_b.runtime.readiness_gate.state is LiveReadinessState.NOT_READY


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unresolved_order_startup_blocks_without_store_load(tmp_path, ambiguous):
    path = tmp_path / "position.json"
    _, market_a = process_a(PositionStore(path))
    apply_fill(market_a, SignalAction.BUY_CE, "CE")
    positions = [broker()] if ambiguous else []
    client_b, startup_b, store_b = process_b(
        path, positions=positions, orders=[pending_order()]
    )
    loads = 0
    original_load = store_b.load

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    store_b.load = counted_load
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    assert result.state is LiveRestartOrchestrationState.BLOCKED
    assert result.restart_result is None
    assert loads == 0
    assert not startup_b.runtime.readiness_gate.is_ready
    assert_no_restart_order_work(client_b)


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("execution_enabled", [False, True])
def test_restart_readiness_preserves_explicit_authorization_boundary(
    tmp_path, restored, execution_enabled
):
    path = tmp_path / "position.json"
    if restored:
        _, market_a = process_a(PositionStore(path))
        apply_fill(market_a, SignalAction.BUY_CE, "CE")
    positions = [broker()] if restored else []
    client_b, startup_b, store_b = process_b(
        path, positions=positions, execution_enabled=execution_enabled
    )
    loads = 0
    original_load = store_b.load

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    store_b.load = counted_load
    result = LiveRestartOrchestrator(store_b).orchestrate(startup_b)
    authorization = LiveRunAuthorizer().authorize(startup_b)
    expected = (
        LiveRunAuthorizationState.PERMITTED
        if execution_enabled
        else LiveRunAuthorizationState.BLOCKED
    )
    assert result.is_ready
    assert authorization.state is expected
    assert startup_b.runtime.execution_coordinator.enabled is execution_enabled
    assert loads == 1
    assert_no_restart_order_work(client_b)
