from dataclasses import FrozenInstanceError, fields
import inspect

import pytest

from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.closed_position_history import ClosedPositionHistory
from trading.live_execution import LiveExecutionCoordinator
from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.live_recovery import LiveRecoveryCoordinator
from trading.live_runtime import LiveRuntimeComponents, build_live_runtime
from trading.live_session_bootstrap import LiveSessionBootstrap
from trading.position_manager import PositionManager
from trading.position_store import PositionStore
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_list_reader import ZerodhaOrderListReader
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader


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


EXPECTED_TYPES = {
    "readiness_gate": LiveReadinessGate,
    "position_reader": ZerodhaPositionReader,
    "order_list_reader": ZerodhaOrderListReader,
    "recovery_coordinator": LiveRecoveryCoordinator,
    "session_bootstrap": LiveSessionBootstrap,
    "order_status_reader": ZerodhaOrderStatusReader,
    "order_adapter": ZerodhaOrderAdapter,
    "order_submitter": ZerodhaOrderSubmitter,
    "execution_coordinator": LiveExecutionCoordinator,
    "position_reconciler": BrokerPositionReconciler,
    "position_manager": PositionManager,
    "closed_position_history": ClosedPositionHistory,
    "position_store": type(None),
}


def assert_no_broker_calls(client):
    assert client.positions_calls == 0
    assert client.orders_calls == 0
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.cancel_order_calls == 0
    assert client.modify_order_calls == 0


def test_bundle_is_immutable_and_exposes_expected_component_types():
    components = build_live_runtime(FakeKiteClient())

    assert {field.name for field in fields(components)} == set(EXPECTED_TYPES)
    for name, expected_type in EXPECTED_TYPES.items():
        assert isinstance(getattr(components, name), expected_type)

    with pytest.raises(FrozenInstanceError):
        components.readiness_gate = LiveReadinessGate()


def test_none_client_is_rejected():
    with pytest.raises(ValueError, match="Kite-compatible client"):
        build_live_runtime(None)


@pytest.mark.parametrize("execution_enabled", [None, 0, 1, "true", object()])
def test_execution_enabled_requires_a_real_bool(execution_enabled):
    with pytest.raises(TypeError, match="boolean"):
        build_live_runtime(FakeKiteClient(), execution_enabled)


@pytest.mark.parametrize(
    ("arguments", "expected_enabled"),
    [({}, False), ({"execution_enabled": False}, False), ({"execution_enabled": True}, True)],
)
def test_execution_enablement_is_explicit_and_independent_of_readiness(
    arguments, expected_enabled
):
    components = build_live_runtime(FakeKiteClient(), **arguments)

    assert components.execution_coordinator.enabled is expected_enabled
    assert components.readiness_gate.state is LiveReadinessState.NOT_READY
    assert components.readiness_gate.is_ready is False
    assert components.readiness_gate.last_recovery_result is None


def test_all_broker_boundaries_share_exact_client_without_calls():
    client = FakeKiteClient()

    components = build_live_runtime(client)

    assert components.position_reader._kite_client is client
    assert components.order_list_reader._kite_client is client
    assert components.order_status_reader._kite_client is client
    assert components.order_submitter._kite_client is client
    assert_no_broker_calls(client)


def test_runtime_retains_exact_optional_position_store(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    components = build_live_runtime(FakeKiteClient(), position_store=store)
    assert components.position_store is store


@pytest.mark.parametrize("store", [object(), "store", True])
def test_runtime_rejects_invalid_position_store(store):
    with pytest.raises(TypeError, match="PositionStore"):
        build_live_runtime(FakeKiteClient(), position_store=store)


def test_internal_dependency_graph_uses_exposed_exact_instances():
    components = build_live_runtime(FakeKiteClient())

    assert components.recovery_coordinator._position_reader is components.position_reader
    assert components.recovery_coordinator._order_reader is components.order_list_reader
    assert (
        components.recovery_coordinator._position_reconciler
        is components.position_reconciler
    )
    assert (
        components.session_bootstrap._recovery_coordinator
        is components.recovery_coordinator
    )
    assert components.session_bootstrap._readiness_gate is components.readiness_gate
    assert components.execution_coordinator._adapter is components.order_adapter
    assert components.execution_coordinator._submitter is components.order_submitter


def test_composition_is_construction_only_with_no_runtime_boundaries():
    client = FakeKiteClient()

    components = build_live_runtime(client, execution_enabled=True)

    assert_no_broker_calls(client)
    assert components.readiness_gate.state is LiveReadinessState.NOT_READY
    source = inspect.getsource(build_live_runtime)
    for forbidden in (
        "MarketData",
        "KiteConnect",
        "initialize(",
        "recover(",
        "apply_recovery_result(",
        "positions(",
        "orders(",
        "order_history(",
        "place_order(",
        "cancel_order(",
        "modify_order(",
    ):
        assert forbidden not in source
