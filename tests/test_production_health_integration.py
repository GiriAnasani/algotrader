from datetime import timedelta

import pytest

from core.production_health import ProductionHealthInspector, ProductionHealthState
from tests.test_production_health import NOW, graph, make_healthy, make_ready
from trading.live_order import LiveOrderIntent
from trading.market import LiveExecutionContext
from trading.pending_live_order import PendingLiveOrder
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.strategy import SignalAction


def healthy_graph():
    runtime, market, client, sink = graph()
    make_ready(runtime)
    make_healthy(runtime)
    return runtime, market, client, sink


def test_shared_authorities_and_execution_guard_are_exact():
    runtime, market, _, _ = healthy_graph()
    assert runtime.position_manager is market.live_position_manager
    assert runtime.readiness_gate is market.live_readiness_gate
    assert runtime.execution_coordinator is market.live_execution_coordinator
    assert runtime.closed_position_history is market.live_closed_position_history
    assert runtime.market_data_health_tracker is market.live_market_data_health_tracker
    assert runtime.risk_evaluator is market.live_risk_evaluator
    assert runtime.risk_guard is market.live_risk_guard
    assert runtime.audit_sink is market.live_audit_sink
    assert runtime.execution_guard is runtime.execution_coordinator._execution_guard
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.HEALTHY
    )


def test_pending_order_is_not_ready_and_is_not_reconciled_or_cleared():
    runtime, market, client, _ = healthy_graph()
    pending = PendingLiveOrder(
        "order-1", SignalAction.BUY_CE, "CE", "NIFTY26AUG25000CE",
        65, NOW,
    )
    market.pending_live_order = pending
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is ProductionHealthState.NOT_READY
    assert "PENDING_ORDER" in snapshot.issues
    assert market.pending_live_order is pending
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0


@pytest.mark.parametrize("mismatch", ["missing", "symbol", "quantity", "side"])
def test_position_context_mismatch_is_observed_without_mutation(mismatch):
    runtime, market, _, _ = healthy_graph()
    position = ManagedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 65, 100.0,
        NOW - timedelta(minutes=1), PositionState.OPEN,
    )
    runtime.position_manager.register(position)
    values = {"side": "CE", "contract_symbol": position.contract_symbol,
              "quantity": 65}
    if mismatch == "missing":
        context = None
    else:
        values[mismatch] = {"symbol": "NIFTY26AUG25100CE",
                            "quantity": 130, "side": "PE"}[mismatch]
        if mismatch == "symbol":
            values["contract_symbol"] = values.pop("symbol")
        context = LiveExecutionContext(**values)
    market.live_execution_context = context
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is ProductionHealthState.NOT_READY
    assert "POSITION_CONTEXT_MISMATCH" in snapshot.issues
    assert runtime.position_manager.active_position is position
    assert market.live_execution_context is context


def test_matching_position_and_context_are_locally_consistent():
    runtime, market, _, _ = healthy_graph()
    position = ManagedPosition(
        PositionSide.PE, "NIFTY26AUG25000PE", 65, 100.0,
        NOW - timedelta(minutes=1), PositionState.OPEN,
    )
    runtime.position_manager.register(position)
    context = LiveExecutionContext("PE", position.contract_symbol, 65)
    market.live_execution_context = context
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is ProductionHealthState.HEALTHY
    assert snapshot.active_position and snapshot.position_consistent


def test_audit_identity_mismatch_is_not_ready_without_writes():
    runtime, market, _, sink = healthy_graph()
    _, _, _, other = graph()
    market.live_audit_sink = other
    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)
    assert snapshot.state is ProductionHealthState.NOT_READY
    assert "AUDIT_IDENTITY_MISMATCH" in snapshot.issues
    assert sink.writes == other.writes == 0


@pytest.mark.parametrize(
    "market_attribute,runtime_attribute,issue",
    [
        (
            "live_readiness_gate", "readiness_gate",
            "READINESS_GATE_IDENTITY_MISMATCH",
        ),
        (
            "live_execution_coordinator", "execution_coordinator",
            "EXECUTION_COORDINATOR_IDENTITY_MISMATCH",
        ),
        (
            "live_closed_position_history", "closed_position_history",
            "CLOSED_POSITION_HISTORY_IDENTITY_MISMATCH",
        ),
    ],
)
def test_execution_authority_identity_mismatch_is_not_ready_without_side_effects(
    market_attribute, runtime_attribute, issue
):
    runtime, market, client, sink = healthy_graph()
    other_runtime, _, other_client, _ = graph()
    replacement = getattr(other_runtime, runtime_attribute)
    setattr(market, market_attribute, replacement)
    readiness_before = runtime.readiness_gate.state
    enabled_before = runtime.execution_coordinator.enabled
    position_before = runtime.position_manager.active_position
    history_before = runtime.closed_position_history.positions

    snapshot = ProductionHealthInspector(runtime, market).inspect(NOW)

    assert snapshot.state is ProductionHealthState.NOT_READY
    assert issue in snapshot.issues
    assert getattr(market, market_attribute) is replacement
    assert getattr(market, market_attribute) is not getattr(runtime, runtime_attribute)
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert other_client.positions_calls == other_client.orders_calls == 0
    assert other_client.order_history_calls == other_client.place_order_calls == 0
    assert runtime.readiness_gate.state is readiness_before
    assert runtime.execution_coordinator.enabled is enabled_before
    assert runtime.position_manager.active_position is position_before
    assert runtime.closed_position_history.positions == history_before
    assert sink.writes == 0
