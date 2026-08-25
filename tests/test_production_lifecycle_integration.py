from datetime import datetime, timedelta

import pytest

from core.production_audit import AuditSink
from core.production_health import ProductionHealthInspector, ProductionHealthState
from trading.closed_position_history_store import ClosedPositionHistoryStore
from trading.live_market_factory import build_live_market_data
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_risk import LiveRiskLimits, LiveRiskViolationError
from trading.live_runtime import build_live_runtime
from trading.live_startup import LiveStartupResult
from trading.live_restart_orchestration import (
    LiveRestartOrchestrationState, LiveRestartOrchestrator,
)
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.option_charges import (
    NetPnLCalculator, OptionChargeSchedule, OptionTradeChargesCalculator,
)
from trading.position_store import PositionStore
from trading.position_store import PositionStoreError
from trading.closed_position_history_store import ClosedPositionHistoryStoreError
from trading.live_execution import (
    AmbiguousLiveOrderSubmissionError, InvalidBrokerOrderIdError,
)
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.strategy import SignalAction, StrategyResult


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class RecordingAuditSink(AuditSink):
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)
        return event


class LifecycleClient:
    def __init__(self, status="COMPLETE", place_error=None,
                 order_id="order", status_error=None):
        self.status = status
        self.place_error = place_error
        self.order_id = order_id
        self.status_error = status_error
        self.positions_calls = self.orders_calls = 0
        self.order_history_calls = self.place_order_calls = 0
        self.modify_order_calls = self.cancel_order_calls = 0
        self.exposure = None
        self.requests = {}

    def positions(self):
        self.positions_calls += 1
        net = [] if self.exposure is None else [{
            "tradingsymbol": self.exposure,
            "exchange": "NFO", "quantity": 65,
            "average_price": 100.0, "product": "MIS",
        }]
        return {"net": net, "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        if self.place_error is not None:
            raise self.place_error
        order_id = (
            f"{self.order_id}-{self.place_order_calls}"
            if self.order_id else self.order_id
        )
        self.requests[order_id] = kwargs
        return order_id

    def order_history(self, order_id):
        self.order_history_calls += 1
        if self.status_error is not None:
            raise self.status_error
        request = self.requests[order_id]
        complete = self.status == "COMPLETE"
        if complete:
            self.exposure = (
                request["tradingsymbol"]
                if request["transaction_type"] == "BUY" else None
            )
        return [{
            "order_id": order_id, "status": self.status,
            "quantity": 65,
            "filled_quantity": 65 if complete else 0,
            "pending_quantity": 0 if complete else 65,
            "average_price": 105.0 if complete else 0.0,
            "exchange_update_timestamp": "2026-08-25 09:15:00",
        }]

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1


def risk_pnl():
    return SessionNetPnLAggregator(RealizedNetPnLAggregator(NetPnLCalculator(
        OptionTradeChargesCalculator(OptionChargeSchedule(0, 0, 0, 0, 0, 0))
    )))


def graph(tmp_path, *, status="COMPLETE", max_quantity=65, client=None):
    client = client or LifecycleClient(status)
    audit = RecordingAuditSink()
    position_store = PositionStore(tmp_path / "state" / "position.json")
    history_store = ClosedPositionHistoryStore(tmp_path / "history" / "closed.json")
    runtime = build_live_runtime(
        client, execution_enabled=True,
        position_store=position_store,
        closed_position_history_store=history_store,
        risk_limits=LiveRiskLimits(1, max_quantity, 2, 1000),
        risk_session_net_pnl_aggregator=risk_pnl(), audit_sink=audit,
    )
    market = build_live_market_data(client, object(), runtime)
    market._live_market_data_clock = lambda: NOW
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY26AUG25000CE", "lot_size": 65},
        "PE": {"tradingsymbol": "NIFTY26AUG25000PE", "lot_size": 65},
    }
    market.latest_option_premiums = {"CE": 100.0, "PE": 100.0}
    market.latest_option_timestamps = {"CE": NOW, "PE": NOW}
    return runtime, market, client, audit, position_store, history_store


def authorize(runtime, *, feed=True):
    runtime.readiness_gate.apply_recovery_result(
        LiveRecoveryResult(LiveRecoveryState.SAFE_FLAT, (), (), "safe")
    )
    if feed:
        runtime.market_data_health_tracker.mark_connected(NOW)
        runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)


def signal(action, at=NOW):
    return StrategyResult("integration", action, at, actions=(action,))


def test_construction_is_inert_and_health_converges_only_after_both_gates(tmp_path):
    runtime, market, client, audit, position_store, history_store = graph(tmp_path)
    inspector = ProductionHealthInspector(runtime, market)
    assert inspector.inspect(NOW).state is ProductionHealthState.NOT_READY
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert audit.events == []
    assert not position_store.path.exists() and not history_store.path.exists()
    runtime.market_data_health_tracker.mark_connected(NOW)
    runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)
    assert inspector.inspect(NOW).state is ProductionHealthState.NOT_READY
    authorize(runtime, feed=False)
    assert inspector.inspect(NOW).state is ProductionHealthState.HEALTHY


def test_real_safe_flat_recovery_bootstrap_controls_readiness_and_health(tmp_path):
    runtime, market, client, audit, _, _ = graph(tmp_path)
    result = runtime.session_bootstrap.initialize()
    assert result.state is LiveRecoveryState.SAFE_FLAT
    assert runtime.readiness_gate.is_ready
    assert client.positions_calls == client.orders_calls == 1
    assert client.place_order_calls == client.order_history_calls == 0
    assert audit.events == []
    inspector = ProductionHealthInspector(runtime, market)
    assert inspector.inspect(NOW).state is ProductionHealthState.NOT_READY
    make_healthy = runtime.market_data_health_tracker
    make_healthy.mark_connected(NOW)
    make_healthy.record_valid_tick(NOW, NOW)
    assert inspector.inspect(NOW).state is ProductionHealthState.HEALTHY


def test_fresh_process_restart_restores_distinct_persisted_position(tmp_path):
    process_a_position = ManagedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 65, 100.0,
        NOW - timedelta(minutes=5), PositionState.OPEN,
    )
    process_a_manager = PositionManager()
    process_a_manager.register(process_a_position)
    durable_store = PositionStore(tmp_path / "state" / "position.json")
    durable_store.save(process_a_manager)

    process_b_client = LifecycleClient()
    process_b_client.exposure = "NIFTY26AUG25000CE"
    runtime, market, _, audit, position_store, _ = graph(
        tmp_path, client=process_b_client
    )
    recovery = runtime.session_bootstrap.initialize()
    startup = LiveStartupResult(runtime, market, recovery)
    result = LiveRestartOrchestrator(position_store).orchestrate(startup)

    restored = runtime.position_manager.active_position
    assert result.state is LiveRestartOrchestrationState.READY_RESTORED
    assert restored is not None and restored is market.live_position_manager.active_position
    assert restored is result.restoration_result.restored_position
    assert restored is not process_a_position
    assert process_b_client.positions_calls == process_b_client.orders_calls == 1
    assert process_b_client.place_order_calls == 0
    assert audit.events == []
    inspector = ProductionHealthInspector(runtime, market)
    assert inspector.inspect(NOW).state is ProductionHealthState.NOT_READY
    runtime.market_data_health_tracker.mark_connected(NOW)
    runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)
    assert inspector.inspect(NOW).state is ProductionHealthState.HEALTHY


def test_risk_rejection_is_local_read_only_and_health_remains_safe(tmp_path):
    runtime, market, client, _, _, _ = graph(tmp_path, max_quantity=64)
    authorize(runtime)
    manager_before = runtime.position_manager.active_position
    history_before = runtime.closed_position_history.positions
    with pytest.raises(LiveRiskViolationError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.positions_calls == client.place_order_calls == 0
    assert client.orders_calls == client.order_history_calls == 0
    assert runtime.position_manager.active_position is manager_before
    assert runtime.closed_position_history.positions == history_before
    assert runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.HEALTHY
    )


def test_pending_status_creates_no_position_and_health_fails_closed(tmp_path):
    runtime, market, client, _, position_store, _ = graph(tmp_path, status="OPEN")
    authorize(runtime)
    assert market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW) == (
        "order-1",
    )
    assert client.place_order_calls == client.order_history_calls == 1
    assert market.pending_live_order is not None
    assert runtime.position_manager.active_position is None
    assert not position_store.path.exists()
    assert not runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )


def test_confirmed_entry_then_exit_updates_all_authorities_once(tmp_path):
    runtime, market, client, audit, position_store, history_store = graph(tmp_path)
    authorize(runtime)
    market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    opened = runtime.position_manager.active_position
    assert opened is not None and market.live_execution_context is not None
    assert runtime.closed_position_history.positions == ()
    assert position_store.path.exists()
    persisted_open = position_store.load()
    assert persisted_open.side is opened.side
    assert persisted_open.contract_symbol == opened.contract_symbol
    assert persisted_open.quantity == opened.quantity
    assert persisted_open.entry_price == opened.entry_price
    assert persisted_open.entry_time == opened.entry_time
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.HEALTHY
    )

    later = NOW + timedelta(minutes=1)
    market.latest_option_timestamps["CE"] = later
    market._live_market_data_clock = lambda: later
    runtime.market_data_health_tracker.record_valid_tick(later, later)
    market._execute_strategy_result(signal(SignalAction.EXIT_CE, later), later)

    assert client.positions_calls == client.place_order_calls == 2
    assert client.order_history_calls == 2 and client.orders_calls == 0
    assert runtime.position_manager.active_position is None
    assert market.live_execution_context is None
    assert len(runtime.closed_position_history.positions) == 1
    assert runtime.closed_position_history.positions[0] is market.latest_closed_position
    assert history_store.path.exists()
    assert position_store.load() is None
    persisted_history = history_store.load()
    assert len(persisted_history) == 1
    assert persisted_history[0] == runtime.closed_position_history.positions[0]
    assert ProductionHealthInspector(runtime, market).inspect(later).state is (
        ProductionHealthState.HEALTHY
    )
    assert len(audit.events) > 0


@pytest.mark.parametrize(
    "client,error_type",
    [
        (LifecycleClient(place_error=RuntimeError("submit failed")),
         AmbiguousLiveOrderSubmissionError),
        (LifecycleClient(order_id=""), InvalidBrokerOrderIdError),
        (LifecycleClient(status_error=RuntimeError("status failed")), RuntimeError),
    ],
)
def test_post_attempt_failures_submit_once_and_fail_health_closed(
    tmp_path, client, error_type
):
    runtime, market, client, _, _, _ = graph(tmp_path, client=client)
    authorize(runtime)
    with pytest.raises(error_type):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.place_order_calls == 1
    assert runtime.position_manager.active_position is None
    assert not runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )


def test_confirmed_buy_position_persistence_failure_keeps_runtime_truth(tmp_path):
    runtime, market, client, _, position_store, _ = graph(tmp_path)
    authorize(runtime)
    position_store.save = lambda manager: (_ for _ in ()).throw(
        PositionStoreError("save failed")
    )
    with pytest.raises(PositionStoreError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.place_order_calls == 1
    assert runtime.position_manager.active_position is not None
    assert market.live_execution_context is not None
    assert not runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )


def test_confirmed_exit_history_persistence_failure_closes_exactly_once(tmp_path):
    runtime, market, client, _, _, history_store = graph(tmp_path)
    authorize(runtime)
    market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    history_store.save = lambda history: (_ for _ in ()).throw(
        ClosedPositionHistoryStoreError("save failed")
    )
    later = NOW + timedelta(minutes=1)
    market.latest_option_timestamps["CE"] = later
    market._live_market_data_clock = lambda: later
    runtime.market_data_health_tracker.record_valid_tick(later, later)
    with pytest.raises(ClosedPositionHistoryStoreError):
        market._execute_strategy_result(signal(SignalAction.EXIT_CE, later), later)
    assert client.place_order_calls == 2
    assert runtime.position_manager.active_position is None
    assert market.live_execution_context is None
    assert len(runtime.closed_position_history.positions) == 1
    assert not runtime.readiness_gate.is_ready
