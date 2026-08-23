from dataclasses import FrozenInstanceError
import inspect

import pytest

from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_run_authorization import (
    LiveRunAuthorizationResult,
    LiveRunAuthorizationState,
    LiveRunAuthorizer,
)
from trading.live_startup import initialize_live_session


class FakeKiteClient:
    def __init__(self, positions=None):
        self.net_positions = list(positions or [])
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self.modify_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": self.net_positions, "day": []}

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

    @property
    def call_counts(self):
        return (
            self.positions_calls,
            self.orders_calls,
            self.order_history_calls,
            self.place_order_calls,
            self.cancel_order_calls,
            self.modify_order_calls,
        )


def broker_position():
    return {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "quantity": 75,
        "average_price": 27.0,
        "product": "MIS",
    }


def test_authorization_result_is_validated_immutable_and_reflects_state():
    result = LiveRunAuthorizationResult(
        LiveRunAuthorizationState.PERMITTED,
        True,
        True,
        "permitted",
    )

    assert result.is_permitted is True
    with pytest.raises(FrozenInstanceError):
        result.state = LiveRunAuthorizationState.BLOCKED

    blocked = LiveRunAuthorizationResult(
        LiveRunAuthorizationState.BLOCKED,
        False,
        False,
        "blocked",
    )
    assert blocked.is_permitted is False


@pytest.mark.parametrize(
    "values",
    [
        ("PERMITTED", True, True, "message"),
        (LiveRunAuthorizationState.PERMITTED, 1, True, "message"),
        (LiveRunAuthorizationState.PERMITTED, True, 1, "message"),
        (LiveRunAuthorizationState.PERMITTED, True, True, ""),
    ],
)
def test_authorization_result_rejects_invalid_values(values):
    with pytest.raises((TypeError, ValueError)):
        LiveRunAuthorizationResult(*values)


@pytest.mark.parametrize("startup_result", [None, object(), "startup", True])
def test_authorizer_requires_live_startup_result(startup_result):
    with pytest.raises(TypeError, match="LiveStartupResult"):
        LiveRunAuthorizer().authorize(startup_result)


@pytest.mark.parametrize(
    ("ready", "enabled", "expected_state", "message_parts"),
    [
        (True, True, LiveRunAuthorizationState.PERMITTED, ("permitted",)),
        (True, False, LiveRunAuthorizationState.BLOCKED, ("disabled",)),
        (False, True, LiveRunAuthorizationState.BLOCKED, ("NOT_READY",)),
        (
            False,
            False,
            LiveRunAuthorizationState.BLOCKED,
            ("NOT_READY", "disabled"),
        ),
    ],
)
def test_exact_authorization_matrix(
    ready, enabled, expected_state, message_parts
):
    client = FakeKiteClient(
        positions=[] if ready else [broker_position()]
    )
    startup = initialize_live_session(
        client,
        object(),
        execution_enabled=enabled,
    )
    before = client.call_counts

    result = LiveRunAuthorizer().authorize(startup)

    assert result.state is expected_state
    assert result.is_permitted is (
        expected_state is LiveRunAuthorizationState.PERMITTED
    )
    assert result.readiness_ready is ready
    assert result.execution_enabled is enabled
    for part in message_parts:
        assert part in result.message
    assert client.call_counts == before


def test_authorization_observes_revocation_and_external_restoration_each_time():
    client = FakeKiteClient()
    startup = initialize_live_session(
        client,
        object(),
        execution_enabled=True,
    )
    authorizer = LiveRunAuthorizer()
    original_recovery_result = startup.recovery_result
    market_context = startup.market.live_execution_context
    before = client.call_counts

    permitted = authorizer.authorize(startup)
    startup.runtime.readiness_gate.revoke()
    blocked = authorizer.authorize(startup)

    assert permitted.state is LiveRunAuthorizationState.PERMITTED
    assert blocked.state is LiveRunAuthorizationState.BLOCKED
    assert blocked is not permitted

    restored_result = LiveRecoveryResult(
        LiveRecoveryState.SAFE_FLAT,
        (),
        (),
        "External recovery restored readiness.",
    )
    startup.runtime.readiness_gate.apply_recovery_result(restored_result)
    permitted_again = authorizer.authorize(startup)

    assert permitted_again.state is LiveRunAuthorizationState.PERMITTED
    assert startup.runtime.execution_coordinator.enabled is True
    assert startup.recovery_result is original_recovery_result
    assert startup.market.live_execution_context is market_context
    assert client.call_counts == before


def test_authorizer_has_no_runtime_or_broker_side_effect_boundaries():
    source = inspect.getsource(LiveRunAuthorizer)

    for forbidden in (
        "MarketData(",
        "KiteConnect(",
        "initialize(",
        "recover(",
        "apply_recovery_result(",
        ".revoke(",
        "start_live(",
        "connect(",
        "_execute_strategy_result(",
        "positions(",
        "orders(",
        "order_history(",
        "place_order(",
    ):
        assert forbidden not in source
