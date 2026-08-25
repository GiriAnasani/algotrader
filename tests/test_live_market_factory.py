import inspect

import pytest

from trading.execution_mode import ExecutionMode
from trading.live_market_factory import build_live_market_data
from trading.live_readiness import LiveReadinessState
from trading.live_runtime import build_live_runtime
from trading.market import MarketData
from trading.position_store import PositionStore


class FakeKiteClient:
    def __init__(self):
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self.modify_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": [], "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order-1"

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1


def assert_no_broker_calls(client):
    assert client.positions_calls == 0
    assert client.orders_calls == 0
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.cancel_order_calls == 0
    assert client.modify_order_calls == 0


@pytest.mark.parametrize("runtime_components", [None, object(), True])
def test_factory_requires_live_runtime_components(runtime_components):
    with pytest.raises(TypeError, match="LiveRuntimeComponents"):
        build_live_market_data(None, None, runtime_components)


@pytest.mark.parametrize("execution_enabled", [False, True])
def test_factory_builds_one_live_market_with_exact_runtime_dependencies(
    execution_enabled,
):
    client = FakeKiteClient()
    instruments = object()
    runtime = build_live_runtime(
        client,
        execution_enabled=execution_enabled,
    )
    gate = runtime.readiness_gate
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.last_recovery_result is None

    market = build_live_market_data(client, instruments, runtime)

    assert isinstance(market, MarketData)
    assert market.kite is client
    assert market.instruments is instruments
    assert market.execution_router.mode is ExecutionMode.LIVE
    assert market.live_execution_coordinator is runtime.execution_coordinator
    assert market.live_order_status_reader is runtime.order_status_reader
    assert market.live_position_reader is runtime.position_reader
    assert market.live_position_reconciler is runtime.position_reconciler
    assert market.live_readiness_gate is gate
    assert market.live_position_manager is runtime.position_manager
    assert market.live_closed_position_history is runtime.closed_position_history
    assert market.live_closed_position_history_store is runtime.closed_position_history_store
    assert market.live_market_data_health_tracker is runtime.market_data_health_tracker
    assert market.live_risk_evaluator is runtime.risk_evaluator
    assert market.live_risk_guard is runtime.risk_guard
    assert market.live_audit_sink is runtime.audit_sink
    assert runtime.execution_coordinator.enabled is execution_enabled
    assert gate.state is LiveReadinessState.NOT_READY
    assert gate.is_ready is False
    assert gate.last_recovery_result is None
    assert_no_broker_calls(client)


def test_factory_does_not_rebuild_any_runtime_dependency_graph():
    client = FakeKiteClient()
    runtime = build_live_runtime(client)

    market = build_live_market_data(client, object(), runtime)

    assert market.live_position_reader._kite_client is client
    assert market.live_order_status_reader._kite_client is client
    assert (
        market.live_execution_coordinator._submitter
        is runtime.order_submitter
    )
    assert runtime.order_list_reader._kite_client is client
    assert runtime.recovery_coordinator._position_reader is market.live_position_reader
    assert runtime.session_bootstrap._readiness_gate is market.live_readiness_gate
    assert market.live_open_position_lifecycle._position_manager is runtime.position_manager
    assert market.live_close_position_lifecycle._position_manager is runtime.position_manager
    assert_no_broker_calls(client)


def test_factory_injects_exact_runtime_position_store(tmp_path):
    client = FakeKiteClient()
    store = PositionStore(tmp_path / "position.json")
    runtime = build_live_runtime(client, position_store=store)
    market = build_live_market_data(client, object(), runtime)
    assert runtime.position_store is store
    assert market.live_position_store is store


def test_factory_is_assembly_only_without_runtime_startup_calls():
    source = inspect.getsource(build_live_market_data)

    assert source.count("MarketData(") == 1
    for forbidden in (
        "build_live_runtime(",
        "initialize(",
        "recover(",
        "apply_recovery_result(",
        ".revoke(",
        "start_live(",
        "connect(",
        "_execute_strategy_result(",
        "_monitor_live_option_target(",
        "positions(",
        "orders(",
        "order_history(",
        "place_order(",
    ):
        assert forbidden not in source
