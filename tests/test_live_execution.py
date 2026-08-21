import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.live_execution import (
    LiveExecutionCoordinator,
    LiveExecutionDisabledError,
)
from trading.live_order import LiveOrderIntent
from trading.strategy import SignalAction
from trading.zerodha_order import ZerodhaOrderRequest
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


TIME = datetime(2026, 8, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_intent():
    return LiveOrderIntent(
        contract_symbol="NIFTY2682125000CE",
        side="CE",
        action=SignalAction.BUY_CE,
        quantity=75,
        reference_price=27.0,
        created_time=TIME,
    )


def make_request():
    return ZerodhaOrderRequest(
        tradingsymbol="NIFTY2682125000CE",
        exchange="NFO",
        transaction_type="BUY",
        quantity=75,
        order_type="MARKET",
        product="MIS",
        validity="DAY",
    )


class SpyAdapter(ZerodhaOrderAdapter):
    def __init__(self, request=None, exception=None):
        self.request = request or make_request()
        self.exception = exception
        self.intents = []

    def to_order_request(self, intent):
        self.intents.append(intent)
        if self.exception is not None:
            raise self.exception
        return self.request


class SpySubmitter(ZerodhaOrderSubmitter):
    def __init__(self, order_id="broker-order-123", exception=None):
        self.order_id = order_id
        self.exception = exception
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        if self.exception is not None:
            raise self.exception
        return self.order_id


def test_live_execution_defaults_to_disabled_and_fails_closed():
    adapter = SpyAdapter()
    submitter = SpySubmitter()
    coordinator = LiveExecutionCoordinator(adapter, submitter)

    with pytest.raises(LiveExecutionDisabledError, match="disabled"):
        coordinator.execute(make_intent())

    assert coordinator.enabled is False
    assert adapter.intents == []
    assert submitter.requests == []


def test_enabled_execution_translates_and_submits_exactly_once():
    request = make_request()
    adapter = SpyAdapter(request=request)
    submitter = SpySubmitter(order_id="broker-order-456")
    coordinator = LiveExecutionCoordinator(adapter, submitter, enabled=True)
    intent = make_intent()

    order_id = coordinator.execute(intent)

    assert order_id == "broker-order-456"
    assert adapter.intents == [intent]
    assert submitter.requests == [request]
    assert intent.reference_price == 27.0
    assert coordinator.enabled is True


@pytest.mark.parametrize("value", [{}, None, make_request()])
def test_execution_accepts_only_live_order_intents(value):
    with pytest.raises(TypeError, match="LiveOrderIntent"):
        LiveExecutionCoordinator(SpyAdapter(), SpySubmitter(), enabled=True).execute(value)


@pytest.mark.parametrize(
    ("adapter", "submitter"),
    [(None, SpySubmitter()), (SpyAdapter(), None)],
)
def test_coordinator_requires_valid_injected_dependencies(adapter, submitter):
    with pytest.raises(TypeError):
        LiveExecutionCoordinator(adapter, submitter)


@pytest.mark.parametrize("enabled", ["true", 1, None])
def test_coordinator_requires_a_boolean_enabled_value(enabled):
    with pytest.raises(TypeError, match="boolean"):
        LiveExecutionCoordinator(SpyAdapter(), SpySubmitter(), enabled=enabled)


@pytest.mark.parametrize(
    ("adapter", "submitter"),
    [(object(), SpySubmitter()), (SpyAdapter(), object())],
)
def test_coordinator_rejects_arbitrary_invalid_dependency_types(
    adapter,
    submitter,
):
    with pytest.raises(TypeError):
        LiveExecutionCoordinator(adapter, submitter)


def test_adapter_failure_prevents_submission_without_retry():
    adapter = SpyAdapter(exception=ValueError("invalid translation"))
    submitter = SpySubmitter()
    coordinator = LiveExecutionCoordinator(adapter, submitter, enabled=True)

    with pytest.raises(ValueError, match="invalid translation"):
        coordinator.execute(make_intent())

    assert len(adapter.intents) == 1
    assert submitter.requests == []


def test_submission_failure_propagates_without_retry_or_fallback():
    adapter = SpyAdapter()
    submitter = SpySubmitter(exception=RuntimeError("broker unavailable"))
    coordinator = LiveExecutionCoordinator(adapter, submitter, enabled=True)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        coordinator.execute(make_intent())

    assert len(adapter.intents) == 1
    assert len(submitter.requests) == 1
    assert coordinator.enabled is True


def test_coordinator_has_no_automatic_market_or_router_integration():
    source = inspect.getsource(LiveExecutionCoordinator)

    assert "KiteConnect" not in source
    assert "MarketData" not in source
    assert "ExecutionRouter" not in source
