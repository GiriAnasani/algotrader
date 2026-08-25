from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta

import pytest

from core.production_audit import AuditSink
from core.production_health import (
    ProductionHealthInspector,
    ProductionHealthSnapshot,
    ProductionHealthState,
)
from trading.live_market_factory import build_live_market_data
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_risk import LiveRiskLimits
from trading.live_runtime import build_live_runtime
from trading.market_data_health import MarketDataHealthState
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.option_charges import (
    NetPnLCalculator, OptionChargeSchedule, OptionTradeChargesCalculator,
)
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class Client:
    def __init__(self):
        self.positions_calls = self.orders_calls = self.order_history_calls = 0
        self.place_order_calls = self.modify_order_calls = self.cancel_order_calls = 0


class Sink(AuditSink):
    def __init__(self):
        self.writes = 0

    def write(self, event):
        self.writes += 1


def pnl():
    return SessionNetPnLAggregator(RealizedNetPnLAggregator(NetPnLCalculator(
        OptionTradeChargesCalculator(OptionChargeSchedule(0, 0, 0, 0, 0, 0))
    )))


def graph(enabled=True, configured=True):
    client = Client()
    sink = Sink() if configured else None
    arguments = {}
    if configured:
        arguments = {
            "risk_limits": LiveRiskLimits(1, 65, 2, 1000),
            "risk_session_net_pnl_aggregator": pnl(),
            "audit_sink": sink,
        }
    runtime = build_live_runtime(client, execution_enabled=enabled, **arguments)
    market = build_live_market_data(client, object(), runtime)
    return runtime, market, client, sink


def make_ready(runtime):
    runtime.readiness_gate.apply_recovery_result(
        LiveRecoveryResult(LiveRecoveryState.SAFE_FLAT, (), (), "safe")
    )


def make_healthy(runtime):
    runtime.market_data_health_tracker.mark_connected(NOW)
    runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)


def test_states_and_snapshot_schema_are_exact_and_frozen():
    assert [(item.name, item.value) for item in ProductionHealthState] == [
        ("HEALTHY", "HEALTHY"), ("DEGRADED", "DEGRADED"),
        ("NOT_READY", "NOT_READY"),
    ]
    runtime, market, _, _ = graph()
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert [item.name for item in fields(snapshot)] == [
        "observed_at", "state", "readiness_state", "market_data_state",
        "execution_enabled", "pending_order", "active_position",
        "position_consistent", "risk_configured", "audit_configured", "issues",
    ]
    with pytest.raises(FrozenInstanceError):
        snapshot.state = ProductionHealthState.HEALTHY


@pytest.mark.parametrize("now", [None, "now", datetime(2026, 8, 25)])
def test_inspection_requires_explicit_aware_datetime(now):
    runtime, market, _, _ = graph()
    with pytest.raises((TypeError, ValueError)):
        ProductionHealthInspector(runtime, market).inspect(now)


def test_serialization_is_deterministic_fresh_and_plain():
    runtime, market, _, _ = graph()
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    first = snapshot.to_dict()
    second = snapshot.to_dict()
    assert first == second and first is not second
    assert snapshot.to_json() == snapshot.to_json()
    assert isinstance(first["issues"], list)


def test_fresh_runtime_and_ready_without_feed_are_not_ready():
    runtime, market, _, _ = graph()
    inspector = ProductionHealthInspector(runtime, market)
    assert inspector.inspect(NOW).state is ProductionHealthState.NOT_READY
    make_ready(runtime)
    snapshot = inspector.inspect(NOW)
    assert snapshot.state is ProductionHealthState.NOT_READY
    assert snapshot.market_data_state is MarketDataHealthState.DISCONNECTED


@pytest.mark.parametrize("state", [
    MarketDataHealthState.CONNECTED_NO_DATA,
    MarketDataHealthState.STALE,
    MarketDataHealthState.INVALID,
])
def test_each_unhealthy_market_state_is_inherited_and_not_ready(state):
    runtime, market, _, _ = graph()
    make_ready(runtime)
    tracker = runtime.market_data_health_tracker
    if state is MarketDataHealthState.STALE:
        earlier = NOW - timedelta(seconds=11)
        tracker.mark_connected(earlier)
        tracker.record_valid_tick(earlier, earlier)
    else:
        tracker.mark_connected(NOW)
        if state is MarketDataHealthState.INVALID:
            tracker.record_invalid_tick(NOW)
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.market_data_state is state
    assert snapshot.state is ProductionHealthState.NOT_READY


@pytest.mark.parametrize("enabled, expected", [
    (True, ProductionHealthState.HEALTHY),
    (False, ProductionHealthState.DEGRADED),
])
def test_fully_safe_graph_classifies_execution_enablement(enabled, expected):
    runtime, market, _, sink = graph(enabled=enabled)
    make_ready(runtime)
    make_healthy(runtime)
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is expected
    assert snapshot.risk_configured and snapshot.audit_configured
    assert sink.writes == 0


def test_missing_risk_and_audit_are_reported_not_ready():
    runtime, market, _, _ = graph(configured=False)
    make_ready(runtime)
    make_healthy(runtime)
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is ProductionHealthState.NOT_READY
    assert "RISK_NOT_CONFIGURED" in snapshot.issues
    assert "AUDIT_NOT_CONFIGURED" in snapshot.issues


def test_inspection_is_zero_call_and_zero_mutation():
    runtime, market, client, sink = graph()
    make_ready(runtime)
    make_healthy(runtime)
    readiness = runtime.readiness_gate.state
    history = runtime.closed_position_history.positions
    enabled = runtime.execution_coordinator.enabled
    target = market.strategy_engine.target_points
    ProductionHealthInspector(runtime, market).inspect(NOW)
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert runtime.readiness_gate.state is readiness
    assert runtime.closed_position_history.positions == history
    assert runtime.execution_coordinator.enabled is enabled
    assert market.strategy_engine.target_points == target == 2.0
    assert sink.writes == 0
