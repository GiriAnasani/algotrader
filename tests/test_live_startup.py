from dataclasses import FrozenInstanceError
import inspect

import pytest

import trading.live_startup as live_startup_module
from trading.execution_mode import ExecutionMode
from trading.live_readiness import LiveReadinessState
from trading.live_recovery import LiveRecoveryState
from trading.live_runtime import build_live_runtime
from trading.live_startup import LiveStartupResult, initialize_live_session


def position(**overrides):
    values = {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "quantity": 75,
        "average_price": 27.0,
        "product": "MIS",
    }
    values.update(overrides)
    return values


def order(**overrides):
    values = {
        "order_id": "order-1",
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 75,
        "filled_quantity": 0,
        "pending_quantity": 75,
        "average_price": 0.0,
        "product": "MIS",
        "status": "OPEN",
    }
    values.update(overrides)
    return values


class FakeKiteClient:
    def __init__(
        self,
        positions=None,
        orders=None,
        position_exception=None,
        order_exception=None,
    ):
        self.net_positions = list(positions or [])
        self.listed_orders = list(orders or [])
        self.position_exception = position_exception
        self.order_exception = order_exception
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self.modify_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        if self.position_exception is not None:
            raise self.position_exception
        return {"net": self.net_positions, "day": []}

    def orders(self):
        self.orders_calls += 1
        if self.order_exception is not None:
            raise self.order_exception
        return self.listed_orders

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "submitted-order"

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1


def assert_no_broker_writes(client):
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.cancel_order_calls == 0
    assert client.modify_order_calls == 0


@pytest.mark.parametrize("execution_enabled", [False, True])
def test_safe_flat_startup_returns_shared_ready_session_without_trading(
    execution_enabled,
):
    client = FakeKiteClient()
    instruments = object()

    startup = initialize_live_session(
        client,
        instruments,
        execution_enabled=execution_enabled,
    )

    assert isinstance(startup, LiveStartupResult)
    assert startup.recovery_result.state is LiveRecoveryState.SAFE_FLAT
    assert startup.runtime.execution_coordinator.enabled is execution_enabled
    assert startup.runtime.readiness_gate.state is LiveReadinessState.READY
    assert startup.is_ready is True
    assert startup.market.kite is client
    assert startup.market.instruments is instruments
    assert startup.market.execution_router.mode is ExecutionMode.LIVE
    assert startup.market.live_readiness_gate is startup.runtime.readiness_gate
    assert (
        startup.market.live_execution_coordinator
        is startup.runtime.execution_coordinator
    )
    assert startup.market.live_order_status_reader is startup.runtime.order_status_reader
    assert startup.market.live_position_reader is startup.runtime.position_reader
    assert (
        startup.market.live_position_reconciler
        is startup.runtime.position_reconciler
    )
    assert client.positions_calls == 1
    assert client.orders_calls == 1
    assert_no_broker_writes(client)

    with pytest.raises(FrozenInstanceError):
        startup.recovery_result = None


@pytest.mark.parametrize(
    ("positions", "orders", "expected_state"),
    [
        ([position()], [], LiveRecoveryState.POSITION_PRESENT),
        ([], [order()], LiveRecoveryState.PENDING_ORDER),
        ([position()], [order()], LiveRecoveryState.AMBIGUOUS),
    ],
)
def test_unsafe_startup_returns_diagnostics_without_adoption_or_repair(
    positions, orders, expected_state
):
    client = FakeKiteClient(positions=positions, orders=orders)

    startup = initialize_live_session(client, object())

    assert startup.recovery_result.state is expected_state
    assert startup.runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert startup.market.live_readiness_gate is startup.runtime.readiness_gate
    assert startup.market.live_execution_context is None
    assert startup.market.pending_live_order is None
    assert client.positions_calls == 1
    assert client.orders_calls == 1
    assert_no_broker_writes(client)


def test_allowed_contract_symbols_are_forwarded_without_hiding_exposure():
    client = FakeKiteClient(
        positions=[position(tradingsymbol="NIFTY2682124900CE")]
    )
    allowed_symbols = ("NIFTY2682125000CE", "NIFTY2682125000PE")

    startup = initialize_live_session(
        client,
        object(),
        allowed_contract_symbols=allowed_symbols,
    )

    assert startup.recovery_result.state is LiveRecoveryState.POSITION_PRESENT
    assert startup.is_ready is False


@pytest.mark.parametrize("failure_point", ["positions", "orders"])
def test_recovery_failure_propagates_without_retry_and_leaves_gate_not_ready(
    failure_point, monkeypatch
):
    failure = RuntimeError(f"{failure_point} unavailable")
    client = FakeKiteClient(
        position_exception=failure if failure_point == "positions" else None,
        order_exception=failure if failure_point == "orders" else None,
    )
    captured = {}
    real_builder = build_live_runtime

    def capturing_builder(kite_client, execution_enabled=False):
        runtime = real_builder(kite_client, execution_enabled=execution_enabled)
        captured["runtime"] = runtime
        return runtime

    monkeypatch.setattr(
        live_startup_module,
        "build_live_runtime",
        capturing_builder,
    )

    with pytest.raises(RuntimeError, match=f"{failure_point} unavailable") as raised:
        initialize_live_session(client, object())

    assert raised.value is failure
    assert captured["runtime"].readiness_gate.state is LiveReadinessState.NOT_READY
    assert client.positions_calls == 1
    assert client.orders_calls == (1 if failure_point == "orders" else 0)
    assert_no_broker_writes(client)


def test_startup_order_is_explicit_and_contains_no_runtime_start_operations():
    source = inspect.getsource(initialize_live_session)
    runtime_index = source.index("build_live_runtime(")
    market_index = source.index("build_live_market_data(")
    bootstrap_index = source.index("session_bootstrap.initialize(")
    result_index = source.index("LiveStartupResult(")

    assert runtime_index < market_index < bootstrap_index < result_index
    for forbidden in (
        "start_live(",
        "connect(",
        "warm_indicators_from_dataframe(",
        "_process_completed_candle(",
        "_execute_strategy_result(",
        "_monitor_live_option_target(",
        "place_order(",
        "order_history(",
    ):
        assert forbidden not in source
