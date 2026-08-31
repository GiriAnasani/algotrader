from datetime import timedelta

import pytest

from core.production_audit import AuditEventType, AuditWriteError
from core.production_health import ProductionHealthInspector, ProductionHealthState
from tests.test_live_dry_run_integration import SelectiveAuditSink
from tests.test_production_lifecycle_integration import (
    LifecycleClient, NOW, authorize, graph, signal,
)
from trading.live_execution import (
    AmbiguousLiveOrderSubmissionError, InvalidBrokerOrderIdError,
)
from trading.market import LiveMarketDataHealthError, LiveReadinessError
from trading.strategy import SignalAction


class RawOrderIdClient(LifecycleClient):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return self.value


@pytest.mark.parametrize(
    "client,error",
    [
        (LifecycleClient(place_error=RuntimeError("submit failed")),
         AmbiguousLiveOrderSubmissionError),
        (RawOrderIdClient(None), InvalidBrokerOrderIdError),
        (RawOrderIdClient("   "), InvalidBrokerOrderIdError),
        (RawOrderIdClient(123), InvalidBrokerOrderIdError),
        (LifecycleClient(status_error=RuntimeError("status failed")), RuntimeError),
    ],
)
def test_submission_uncertainty_fails_closed_and_blocks_followup(
    tmp_path, client, error
):
    runtime, market, client, audit, _, _ = graph(tmp_path, client=client)
    authorize(runtime)
    with pytest.raises(error):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.place_order_calls == 1
    assert client.order_history_calls == int(client.status_error is not None)
    assert runtime.position_manager.active_position is None
    assert runtime.closed_position_history.positions == ()
    assert market.pending_live_order is None
    assert not runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )
    before = client.place_order_calls
    with pytest.raises(LiveReadinessError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.place_order_calls == before
    assert market.strategy_engine.target_points == 2.0
    if isinstance(client, RawOrderIdClient):
        ambiguous = [
            event for event in audit.events
            if event.event_type is AuditEventType.ORDER_SUBMISSION_AMBIGUOUS
        ]
        assert len(ambiguous) == 1
        assert ambiguous[0].data["reason"] == "invalid_order_id"
        assert str(client.value) not in ambiguous[0].to_json()


def test_pending_full_fill_reconciliation_applies_once_without_resubmission(tmp_path):
    runtime, market, client, _, _, _ = graph(tmp_path, status="OPEN")
    authorize(runtime)
    market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    pending = market.pending_live_order
    assert pending is not None and runtime.position_manager.active_position is None
    client.status = "COMPLETE"
    market.reconcile_pending_live_order()
    assert client.place_order_calls == 1
    assert client.order_history_calls == 2
    assert market.pending_live_order is None
    assert runtime.position_manager.active_position is not None
    assert market.live_execution_context is not None
    assert not runtime.readiness_gate.is_ready
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )


def test_pending_reconciliation_failure_preserves_uncertainty(tmp_path):
    runtime, market, client, _, _, _ = graph(tmp_path, status="OPEN")
    authorize(runtime)
    market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    pending = market.pending_live_order
    client.status_error = RuntimeError("status unavailable")
    with pytest.raises(RuntimeError):
        market.reconcile_pending_live_order()
    assert client.place_order_calls == 1
    assert market.pending_live_order is pending
    assert runtime.position_manager.active_position is None
    assert not runtime.readiness_gate.is_ready


def test_stale_market_precedes_broker_activity_and_preserves_ready_state(tmp_path):
    runtime, market, client, audit, _, _ = graph(tmp_path)
    authorize(runtime)
    stale_at = NOW + timedelta(seconds=11)
    market._live_market_data_clock = lambda: stale_at
    readiness = runtime.readiness_gate.state
    audit_count = len(audit.events)
    with pytest.raises(LiveMarketDataHealthError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE, stale_at), stale_at)
    assert client.positions_calls == client.place_order_calls == 0
    assert runtime.readiness_gate.state is readiness
    assert len(audit.events) == audit_count
    assert ProductionHealthInspector(runtime, market).inspect(stale_at).state is (
        ProductionHealthState.NOT_READY
    )


@pytest.mark.parametrize(
    "failure,expected_place",
    [
        (AuditEventType.ORDER_SUBMISSION_ATTEMPTED, 0),
        (AuditEventType.ORDER_SUBMISSION_CONFIRMED, 1),
        (AuditEventType.POSITION_OPENED, 1),
    ],
)
def test_audit_failure_matrix_never_retries_and_preserves_safe_ordering(
    tmp_path, failure, expected_place
):
    runtime, market, client, _, _, _ = graph(tmp_path)
    sink = SelectiveAuditSink(failure)
    runtime.execution_coordinator._audit_sink = sink
    market.live_audit_sink = sink
    authorize(runtime)
    with pytest.raises(AuditWriteError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)
    assert client.place_order_calls == expected_place
    assert not runtime.readiness_gate.is_ready
    if failure is AuditEventType.POSITION_OPENED:
        assert runtime.position_manager.active_position is not None
        assert market.live_execution_context is not None
    else:
        assert runtime.position_manager.active_position is None
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.NOT_READY
    )


def test_confirmed_status_audit_failure_preserves_truth_and_revokes(tmp_path):
    runtime, market, client, _, position_store, _ = graph(tmp_path)
    sink = SelectiveAuditSink(AuditEventType.ORDER_STATUS_RECEIVED)
    runtime.execution_coordinator._audit_sink = sink
    market.live_audit_sink = sink
    authorize(runtime)

    with pytest.raises(AuditWriteError):
        market._execute_strategy_result(signal(SignalAction.BUY_CE), NOW)

    opened = runtime.position_manager.active_position
    assert client.place_order_calls == 1
    assert client.order_history_calls == 1
    assert opened is not None
    assert market.live_execution_context.contract_symbol == opened.contract_symbol
    assert position_store.load().contract_symbol == opened.contract_symbol
    assert not runtime.readiness_gate.is_ready

    with pytest.raises(Exception, match="READY"):
        market._execute_strategy_result(signal(SignalAction.BUY_PE), NOW)
    assert client.place_order_calls == 1
    assert runtime.position_manager.active_position is opened
