from datetime import timedelta

import pytest

from core.production_health import ProductionHealthInspector, ProductionHealthState
from tests.test_production_lifecycle_integration import (
    LifecycleClient, NOW, RecordingAuditSink, risk_pnl, signal,
)
from trading.closed_position_history_store import ClosedPositionHistoryStore
from trading.execution_guard import DuplicateLiveOrderIntentError
from trading.execution_mode import ExecutionMode
from trading.live_market_factory import build_live_market_data
from trading.live_order import LiveOrderIntent
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_risk import LiveRiskLimits, LiveRiskViolationError
from trading.live_runtime import build_live_runtime
from trading.market import MarketData
from trading.market_data_health import MarketDataHealthState
from trading.position_store import PositionStore
from trading.strategy import SignalAction


CYCLES = 25


def soak_graph(tmp_path):
    client = LifecycleClient()
    audit = RecordingAuditSink()
    position_store = PositionStore(tmp_path / "state" / "position.json")
    history_store = ClosedPositionHistoryStore(tmp_path / "history" / "closed.json")
    runtime = build_live_runtime(
        client, execution_enabled=True, position_store=position_store,
        closed_position_history_store=history_store,
        risk_limits=LiveRiskLimits(1, 65, CYCLES, 1_000_000),
        risk_session_net_pnl_aggregator=risk_pnl(), audit_sink=audit,
    )
    market = build_live_market_data(client, object(), runtime)
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY26AUG25000CE", "lot_size": 65},
        "PE": {"tradingsymbol": "NIFTY26AUG25000PE", "lot_size": 65},
    }
    market.latest_option_premiums = {"CE": 100.0, "PE": 100.0}
    runtime.readiness_gate.apply_recovery_result(
        LiveRecoveryResult(LiveRecoveryState.SAFE_FLAT, (), (), "safe")
    )
    runtime.market_data_health_tracker.mark_connected(NOW)
    return runtime, market, client, audit, position_store, history_store


def tick(runtime, market, at):
    market._live_market_data_clock = lambda: at
    market.latest_option_timestamps = {"CE": at, "PE": at}
    runtime.market_data_health_tracker.record_valid_tick(at, at)


def test_twenty_five_complete_cycles_preserve_all_runtime_authorities(tmp_path):
    runtime, market, client, audit, position_store, history_store = soak_graph(tmp_path)
    inspector = ProductionHealthInspector(runtime, market)
    submitted_intents = []

    for cycle in range(CYCLES):
        buy_at = NOW + timedelta(seconds=cycle * 4)
        tick(runtime, market, buy_at)
        market._execute_strategy_result(signal(SignalAction.BUY_CE, buy_at), buy_at)
        opened = runtime.position_manager.active_position
        assert opened is not None
        assert market.live_execution_context.contract_symbol == opened.contract_symbol
        assert market.live_execution_context.quantity == opened.quantity
        assert market.pending_live_order is None and runtime.readiness_gate.is_ready
        assert inspector.inspect(buy_at).state is ProductionHealthState.HEALTHY
        persisted = position_store.load()
        assert persisted.contract_symbol == opened.contract_symbol
        submitted_intents.append(LiveOrderIntent(
            opened.contract_symbol, "CE", SignalAction.BUY_CE,
            65, 100.0, buy_at,
        ))

        exit_at = buy_at + timedelta(seconds=2)
        tick(runtime, market, exit_at)
        market._execute_strategy_result(signal(SignalAction.EXIT_CE, exit_at), exit_at)
        assert runtime.position_manager.active_position is None
        assert market.live_execution_context is None
        assert market.pending_live_order is None and runtime.readiness_gate.is_ready
        assert len(runtime.closed_position_history.positions) == cycle + 1
        assert len(history_store.load()) == cycle + 1
        assert position_store.load() is None
        assert inspector.inspect(exit_at).state is ProductionHealthState.HEALTHY

    assert client.place_order_calls == 2 * CYCLES
    assert client.order_history_calls == 2 * CYCLES
    assert client.positions_calls == 2 * CYCLES
    assert client.orders_calls == client.modify_order_calls == client.cancel_order_calls == 0
    assert len({id(item) for item in runtime.closed_position_history.positions}) == CYCLES
    assert len(history_store.load()) == CYCLES

    attempted = [e for e in audit.events if e.event_type.value == "ORDER_SUBMISSION_ATTEMPTED"]
    confirmed = [e for e in audit.events if e.event_type.value == "ORDER_SUBMISSION_CONFIRMED"]
    opened_events = [e for e in audit.events if e.event_type.value == "POSITION_OPENED"]
    closed_events = [e for e in audit.events if e.event_type.value == "POSITION_CLOSED"]
    assert len(attempted) == len(confirmed) == 2 * CYCLES
    assert len(opened_events) == len(closed_events) == CYCLES
    assert len({event.correlation_id for event in attempted}) == 2 * CYCLES
    assert {event.correlation_id for event in attempted} == {
        event.correlation_id for event in confirmed
    }

    before = client.place_order_calls
    with pytest.raises(DuplicateLiveOrderIntentError):
        runtime.execution_coordinator.execute(submitted_intents[-1], submitted_intents[-1].created_time)
    assert client.place_order_calls == before
    assert inspector.inspect(NOW + timedelta(seconds=100)).state is ProductionHealthState.HEALTHY

    with pytest.raises(LiveRiskViolationError):
        at = NOW + timedelta(seconds=102)
        tick(runtime, market, at)
        market._execute_strategy_result(signal(SignalAction.BUY_CE, at), at)
    assert client.place_order_calls == before
    assert runtime.closed_position_history.positions == history_store.load()


def test_market_health_time_progression_and_inspection_are_observational(tmp_path):
    runtime, market, client, audit, position_store, history_store = soak_graph(tmp_path)
    tracker = runtime.market_data_health_tracker
    inspector = ProductionHealthInspector(runtime, market)
    tick(runtime, market, NOW)
    assert inspector.inspect(NOW).state is ProductionHealthState.HEALTHY
    later = NOW + timedelta(seconds=11)
    assert tracker.snapshot(later).state is MarketDataHealthState.STALE
    assert inspector.inspect(later).state is ProductionHealthState.NOT_READY
    tick(runtime, market, later)
    assert inspector.inspect(later).state is ProductionHealthState.HEALTHY
    tracker.mark_disconnected(later)
    assert inspector.inspect(later).state is ProductionHealthState.NOT_READY
    tracker.mark_connected(later)
    tick(runtime, market, later + timedelta(seconds=1))
    assert inspector.inspect(later + timedelta(seconds=1)).state is ProductionHealthState.HEALTHY
    assert client.positions_calls == client.orders_calls == 0
    assert client.place_order_calls == client.order_history_calls == 0
    assert audit.events == []
    assert not position_store.path.exists() and not history_store.path.exists()


def test_paper_default_remains_dependency_free():
    market = MarketData(None, None)
    assert market.execution_router.mode is ExecutionMode.PAPER
    assert market.live_risk_evaluator is market.live_audit_sink is None
    assert market.strategy_engine.target_points == 2.0
