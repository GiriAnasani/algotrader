import json
from datetime import datetime, timedelta, timezone

import pytest

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus
from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.execution_mode import ExecutionMode
from trading.live_execution import LiveExecutionCoordinator
from trading.live_order import LiveOrderIntent
from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.market import LivePositionLifecycleError, MarketData
from trading.position import PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import PositionStore, PositionStoreError
from trading.strategy import SignalAction
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader


FILL_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class FakeKiteClient:
    def positions(self):
        return {"net": [], "day": []}

    def order_history(self, order_id):
        return []

    def place_order(self, **kwargs):
        return "order-1"


def make_market(store=None):
    client = FakeKiteClient()
    manager = PositionManager()
    gate = LiveReadinessGate()
    market = MarketData(
        None,
        None,
        execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=LiveExecutionCoordinator(
            ZerodhaOrderAdapter(), ZerodhaOrderSubmitter(client), enabled=False
        ),
        live_order_status_reader=ZerodhaOrderStatusReader(client),
        live_position_reader=ZerodhaPositionReader(client),
        live_position_reconciler=BrokerPositionReconciler(),
        live_readiness_gate=gate,
        live_position_manager=manager,
        live_position_store=store,
    )
    return market, manager, gate


def order(action, side, quantity=65):
    return LiveOrderIntent(
        contract_symbol=f"NIFTY26AUG25000{side}",
        side=side,
        action=action,
        quantity=quantity,
        reference_price=100.0,
        created_time=FILL_TIME - timedelta(seconds=1),
    )


def status(quantity=65, price=105.5, timestamp=FILL_TIME, state=BrokerOrderState.COMPLETE):
    pending = 0 if state is BrokerOrderState.COMPLETE else quantity
    filled = quantity if state is BrokerOrderState.COMPLETE else 0
    return BrokerOrderStatus(
        "order-1", state, filled, pending, price, state.value,
        fill_timestamp=timestamp,
    )


def apply(market, intent, broker_status=None):
    return market._apply_live_order_status(
        "order-1", intent, broker_status or status(intent.quantity)
    )


@pytest.mark.parametrize(
    "side, action",
    [("CE", SignalAction.BUY_CE), ("PE", SignalAction.BUY_PE)],
)
def test_confirmed_buy_persists_exact_open_manager_truth(tmp_path, side, action):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        return original_save(position_manager)

    store.save = counted_save
    intent = order(action, side)
    apply(market, intent)

    active = manager.active_position
    document = json.loads(store.path.read_text(encoding="utf-8"))
    persisted = document["active_position"]
    assert calls == 1
    assert active.side is PositionSide[side]
    assert active.contract_symbol == intent.contract_symbol
    assert active.quantity == intent.quantity
    assert active.entry_price == 105.5
    assert active.entry_time == FILL_TIME
    assert persisted["side"] == side
    assert persisted["contract_symbol"] == intent.contract_symbol
    assert persisted["quantity"] == intent.quantity
    assert persisted["entry_price"] == 105.5
    assert datetime.fromisoformat(persisted["entry_time"]) == FILL_TIME


def test_confirmed_exit_persists_explicit_flat_state(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    opened = manager.active_position

    apply(
        market,
        order(SignalAction.EXIT_CE, "CE"),
        status(price=110.0, timestamp=FILL_TIME + timedelta(minutes=1)),
    )

    document = json.loads(store.path.read_text(encoding="utf-8"))
    assert document["active_position"] is None
    assert manager.active_position is None
    assert market.live_execution_context is None
    assert market.latest_closed_position.entry_time is opened.entry_time
    assert market.latest_closed_position.state is PositionState.CLOSED


def test_reversal_persists_flat_then_opposite_open(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    writes = []
    original_save = store.save

    def recording_save(position_manager):
        original_save(position_manager)
        writes.append(json.loads(store.path.read_text(encoding="utf-8")))

    store.save = recording_save
    apply(market, order(SignalAction.EXIT_CE, "CE"))
    apply(market, order(SignalAction.BUY_PE, "PE"))

    assert [item["active_position"] is None for item in writes] == [True, False]
    assert writes[1]["active_position"]["side"] == "PE"
    assert manager.active_position.side is PositionSide.PE


def test_unresolved_then_confirmed_fill_saves_once(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        original_save(position_manager)

    store.save = counted_save
    intent = order(SignalAction.BUY_CE, "CE")
    apply(market, intent, status(state=BrokerOrderState.OPEN))
    assert calls == 0
    assert manager.active_position is None
    apply(market, intent)
    assert calls == 1
    assert manager.active_position is not None


@pytest.mark.parametrize("state", [BrokerOrderState.REJECTED, BrokerOrderState.CANCELLED])
def test_rejected_or_zero_fill_cancelled_order_does_not_persist(tmp_path, state):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"), status(state=state))
    assert manager.active_position is None
    assert not store.path.exists()


def test_missing_fill_timestamp_does_not_persist(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, gate = make_market(store)
    with pytest.raises(Exception, match="fill timestamp"):
        apply(market, order(SignalAction.BUY_CE, "CE"), status(timestamp=None))
    assert manager.active_position is None
    assert not store.path.exists()
    assert gate.state is LiveReadinessState.NOT_READY


def test_save_failure_after_buy_retains_runtime_truth_and_revokes(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, gate = make_market(store)
    calls = 0

    def fail_save(position_manager):
        nonlocal calls
        calls += 1
        raise PositionStoreError("save failed")

    store.save = fail_save
    with pytest.raises(PositionStoreError, match="save failed"):
        apply(market, order(SignalAction.BUY_CE, "CE"))
    assert calls == 1
    assert manager.active_position is not None
    assert market.live_execution_context.contract_symbol == manager.active_position.contract_symbol
    assert gate.state is LiveReadinessState.NOT_READY


def test_save_failure_after_exit_retains_flat_runtime_truth(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, gate = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    opened = manager.active_position
    calls = 0

    def fail_save(position_manager):
        nonlocal calls
        calls += 1
        raise PositionStoreError("save failed")

    store.save = fail_save
    with pytest.raises(PositionStoreError, match="save failed"):
        apply(market, order(SignalAction.EXIT_CE, "CE"))
    assert calls == 1
    assert manager.active_position is None
    assert market.live_execution_context is None
    assert market.latest_closed_position.entry_time is opened.entry_time
    assert gate.state is LiveReadinessState.NOT_READY


def test_paper_mode_does_not_require_or_use_position_store(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market = MarketData(None, None, live_position_store=store)
    assert market.execution_router.mode is ExecutionMode.PAPER
    assert not store.path.exists()


def test_pending_exit_persists_once_only_after_confirmed_full_fill(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    active = manager.active_position
    context = market.live_execution_context
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        original_save(position_manager)

    store.save = counted_save
    intent = order(SignalAction.EXIT_CE, "CE")
    apply(market, intent, status(state=BrokerOrderState.OPEN))
    assert calls == 0
    assert manager.active_position is active
    assert market.live_execution_context is context

    apply(market, intent)
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert calls == 1
    assert persisted["active_position"] is None
    assert manager.active_position is None
    assert market.live_execution_context is None


@pytest.mark.parametrize("state", [BrokerOrderState.REJECTED, BrokerOrderState.CANCELLED])
def test_rejected_or_zero_fill_cancelled_exit_does_not_persist(tmp_path, state):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    active = manager.active_position
    context = market.live_execution_context
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        original_save(position_manager)

    store.save = counted_save
    apply(
        market,
        order(SignalAction.EXIT_CE, "CE"),
        status(state=state),
    )
    assert calls == 0
    assert manager.active_position is active
    assert market.live_execution_context is context


def test_confirmed_exit_missing_timestamp_does_not_persist(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, gate = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    active = manager.active_position
    context = market.live_execution_context
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        original_save(position_manager)

    store.save = counted_save
    with pytest.raises(LivePositionLifecycleError, match="fill timestamp"):
        apply(
            market,
            order(SignalAction.EXIT_CE, "CE"),
            status(timestamp=None),
        )
    assert calls == 0
    assert manager.active_position is active
    assert market.live_execution_context is context
    assert gate.state is LiveReadinessState.NOT_READY


def test_successful_exit_saves_exactly_once(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, _ = make_market(store)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    calls = 0
    original_save = store.save

    def counted_save(position_manager):
        nonlocal calls
        calls += 1
        original_save(position_manager)

    store.save = counted_save
    apply(market, order(SignalAction.EXIT_CE, "CE"))
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert calls == 1
    assert manager.active_position is None
    assert persisted["active_position"] is None


def test_live_lifecycle_succeeds_without_position_persistence_dependency():
    market, manager, _ = make_market(store=None)
    apply(market, order(SignalAction.BUY_CE, "CE"))
    opened = manager.active_position
    assert opened is not None
    assert market.live_execution_context is not None

    apply(market, order(SignalAction.EXIT_CE, "CE"))
    assert manager.active_position is None
    assert market.live_execution_context is None
    assert market.latest_closed_position.entry_time is opened.entry_time
