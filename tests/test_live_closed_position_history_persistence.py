from datetime import datetime, timedelta, timezone

import pytest

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus
from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.closed_position_history import ClosedPositionHistory
from trading.closed_position_history_store import (
    ClosedPositionHistoryStore,
    ClosedPositionHistoryStoreCorruptionError,
    ClosedPositionHistoryStoreError,
)
from trading.close_position_lifecycle import ClosedPosition
from trading.execution_mode import ExecutionMode
from trading.live_execution import LiveExecutionCoordinator
from trading.live_order import LiveOrderIntent
from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.live_restart_orchestration import (
    LiveRestartOrchestrationError,
    LiveRestartOrchestrator,
)
from trading.live_startup import initialize_live_session
from trading.market import MarketData
from trading.option_charges import (
    NetPnLCalculator, OptionChargeSchedule, OptionTradeChargesCalculator)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import PositionStore
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.runtime_pnl import RuntimePnLSnapshotBuilder
from trading.runtime_pnl_state import RuntimePnLService, RuntimePnLStateAdapter
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.strategy import SignalAction
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class Client:
    def __init__(self, positions=None):
        self.net = list(positions or [])
        self.positions_calls = self.orders_calls = 0
        self.order_history_calls = self.place_order_calls = 0
    def positions(self):
        self.positions_calls += 1
        return {"net": self.net, "day": []}
    def orders(self):
        self.orders_calls += 1
        return []
    def order_history(self, order_id):
        self.order_history_calls += 1
        return []
    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order-1"


def closed(side=PositionSide.CE):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, 100, NOW,
                          105, NOW + timedelta(minutes=1), PositionState.CLOSED)


def history_store_with(tmp_path, *positions):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    history = ClosedPositionHistory()
    for position in positions:
        history.record(position)
    store.save(history)
    return store


def make_market(history_store):
    client = Client()
    manager = PositionManager()
    gate = LiveReadinessGate()
    market = MarketData(
        None, None, execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=LiveExecutionCoordinator(
            ZerodhaOrderAdapter(), ZerodhaOrderSubmitter(client), enabled=False),
        live_order_status_reader=ZerodhaOrderStatusReader(client),
        live_position_reader=ZerodhaPositionReader(client),
        live_position_reconciler=BrokerPositionReconciler(),
        live_readiness_gate=gate, live_position_manager=manager,
        live_closed_position_history_store=history_store)
    return market, manager, gate, client


def intent(action, side):
    return LiveOrderIntent(f"NIFTY26AUG25000{side}", side, action, 65, 100,
                           NOW - timedelta(seconds=1))


def status(state=BrokerOrderState.COMPLETE, timestamp=NOW, price=105):
    filled = 65 if state is BrokerOrderState.COMPLETE else 0
    pending = 0 if state is BrokerOrderState.COMPLETE else 65
    return BrokerOrderStatus("order-1", state, filled, pending, price, state.value,
                             fill_timestamp=timestamp)


def apply(market, order, broker_status=None):
    return market._apply_live_order_status("order-1", order, broker_status or status())


def pnl_builder():
    schedule = OptionChargeSchedule(20, .0015, .0003553, .000001, .00003, .18)
    realized = RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(schedule)))
    return RuntimePnLSnapshotBuilder(
        PortfolioNetPnLAggregator(realized), SessionNetPnLAggregator(realized))


def test_confirmed_exit_saves_after_exact_record_and_buy_does_not_save(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    market, manager, _, _ = make_market(store)
    calls = []
    original = store.save
    def spy(history):
        calls.append(history.positions)
        return original(history)
    store.save = spy
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    assert calls == []
    apply(market, intent(SignalAction.EXIT_CE, "CE"))
    exact = market.latest_closed_position
    assert calls == [(exact,)]
    assert calls[0][0] is exact
    assert manager.active_position is None
    loaded = store.load()
    assert loaded == (exact,)


@pytest.mark.parametrize("state", [BrokerOrderState.OPEN, BrokerOrderState.REJECTED,
                                    BrokerOrderState.CANCELLED])
def test_unconfirmed_exit_does_not_save(state, tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    market, _, _, _ = make_market(store)
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    store.save = lambda history: pytest.fail("history save called")
    apply(market, intent(SignalAction.EXIT_CE, "CE"), status(state=state))


def test_missing_timestamp_does_not_save(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    market, _, _, _ = make_market(store)
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    store.save = lambda history: pytest.fail("history save called")
    with pytest.raises(Exception, match="timestamp"):
        apply(market, intent(SignalAction.EXIT_CE, "CE"), status(timestamp=None))


def test_history_save_failure_keeps_flat_memory_and_previous_file(tmp_path):
    previous = closed(PositionSide.PE)
    store = history_store_with(tmp_path, previous)
    before = store.path.read_bytes()
    market, manager, gate, _ = make_market(store)
    store.save = lambda history: (_ for _ in ()).throw(
        ClosedPositionHistoryStoreError("failed"))
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    with pytest.raises(ClosedPositionHistoryStoreError):
        apply(market, intent(SignalAction.EXIT_CE, "CE"))
    assert manager.active_position is None
    assert market.live_execution_context is None
    assert len(market.live_closed_position_history.positions) == 1
    assert gate.state is LiveReadinessState.NOT_READY
    assert store.path.read_bytes() == before


def test_three_exits_save_complete_ordered_history(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    market, _, _, _ = make_market(store)
    lengths = []
    original = store.save
    def spy(history):
        lengths.append(len(history.positions))
        return original(history)
    store.save = spy
    for side, buy, exit_action in [
        ("CE", SignalAction.BUY_CE, SignalAction.EXIT_CE),
        ("PE", SignalAction.BUY_PE, SignalAction.EXIT_PE),
        ("CE", SignalAction.BUY_CE, SignalAction.EXIT_CE)]:
        apply(market, intent(buy, side)); apply(market, intent(exit_action, side))
    assert lengths == [1, 2, 3]
    loaded = store.load()
    assert [position.side for position in loaded] == [
        PositionSide.CE, PositionSide.PE, PositionSide.CE]


def test_construction_does_not_load_and_flat_restart_restores_history(tmp_path, monkeypatch):
    prior = closed()
    store = history_store_with(tmp_path, prior)
    original = store.load
    monkeypatch.setattr(store, "load", lambda: pytest.fail("construction loaded"))
    client = Client()
    startup = initialize_live_session(client, object(),
                                      closed_position_history_store=store)
    assert startup.runtime.closed_position_history.positions == ()
    monkeypatch.setattr(store, "load", original)
    calls = (client.positions_calls, client.orders_calls,
             client.order_history_calls, client.place_order_calls)
    LiveRestartOrchestrator(PositionStore(tmp_path / "position.json"), store).orchestrate(startup)
    view = RuntimePnLStateAdapter(startup.market).view()
    assert len(view.closed_positions) == 1
    assert view.closed_positions[0] is startup.runtime.closed_position_history.positions[0]
    assert (client.positions_calls, client.orders_calls,
            client.order_history_calls, client.place_order_calls) == calls == (1, 1, 0, 0)
    assert RuntimePnLService(RuntimePnLStateAdapter(startup.market), pnl_builder()).snapshot(
        NOW.date()).closed_trade_count == 1


def test_active_restart_and_history_restore_coexist_with_exact_identity(tmp_path):
    store = history_store_with(tmp_path, closed())
    active = ManagedPosition(PositionSide.PE, "NIFTY26AUG25000PE", 65, 90, NOW,
                             PositionState.OPEN)
    position_store = PositionStore(tmp_path / "position.json")
    manager = PositionManager(); manager.register(active); position_store.save(manager)
    broker = {"tradingsymbol": active.contract_symbol, "exchange": "NFO",
              "quantity": 65, "average_price": 90, "product": "MIS"}
    client = Client([broker])
    startup = initialize_live_session(client, object(),
                                      closed_position_history_store=store)
    result = LiveRestartOrchestrator(position_store, store).orchestrate(startup)
    view = RuntimePnLStateAdapter(startup.market).view()
    assert view.active_position is result.restoration_result.restored_position
    assert len(view.closed_positions) == 1
    snapshot = RuntimePnLService(RuntimePnLStateAdapter(startup.market), pnl_builder()).snapshot(
        NOW.date(), reference_price=95)
    assert snapshot.closed_trade_count == snapshot.open_position_count == 1


def test_corrupt_history_revokes_readiness_without_orders(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.path.write_text("not json", encoding="utf-8")
    client = Client()
    startup = initialize_live_session(client, object(),
                                      closed_position_history_store=store)
    with pytest.raises(ClosedPositionHistoryStoreCorruptionError):
        LiveRestartOrchestrator(PositionStore(tmp_path / "position.json"), store).orchestrate(startup)
    assert startup.runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert client.order_history_calls == client.place_order_calls == 0


@pytest.mark.parametrize("source", ["same", "runtime", "explicit"])
def test_unambiguous_history_store_sources_are_accepted(tmp_path, source):
    prior = closed()
    store = history_store_with(tmp_path, prior)
    runtime_store = store if source in ("same", "runtime") else None
    explicit_store = store if source in ("same", "explicit") else None
    client = Client()
    startup = initialize_live_session(
        client, object(), closed_position_history_store=runtime_store
    )
    result = LiveRestartOrchestrator(
        PositionStore(tmp_path / "position.json"), explicit_store
    ).orchestrate(startup)
    assert result.is_ready
    assert startup.runtime.closed_position_history.positions == store.load()
    assert len(startup.runtime.closed_position_history.positions) == 1
    assert (client.positions_calls, client.orders_calls,
            client.order_history_calls, client.place_order_calls) == (1, 1, 0, 0)


def test_conflicting_history_stores_fail_before_any_store_access(tmp_path, monkeypatch):
    runtime_store = ClosedPositionHistoryStore(tmp_path / "runtime.json")
    explicit_store = ClosedPositionHistoryStore(tmp_path / "explicit.json")
    client = Client()
    startup = initialize_live_session(
        client, object(), closed_position_history_store=runtime_store
    )
    calls_before = (client.positions_calls, client.orders_calls,
                    client.order_history_calls, client.place_order_calls)
    position_store = PositionStore(tmp_path / "position.json")
    monkeypatch.setattr(position_store, "load", lambda: pytest.fail("position load"))
    monkeypatch.setattr(runtime_store, "load", lambda: pytest.fail("runtime load"))
    monkeypatch.setattr(explicit_store, "load", lambda: pytest.fail("explicit load"))
    monkeypatch.setattr(runtime_store, "save", lambda history: pytest.fail("runtime save"))
    monkeypatch.setattr(explicit_store, "save", lambda history: pytest.fail("explicit save"))

    with pytest.raises(
        LiveRestartOrchestrationError,
        match="share exact closed-history store",
    ):
        LiveRestartOrchestrator(position_store, explicit_store).orchestrate(startup)

    assert startup.runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert startup.runtime.closed_position_history.positions == ()
    assert (client.positions_calls, client.orders_calls,
            client.order_history_calls, client.place_order_calls) == calls_before
