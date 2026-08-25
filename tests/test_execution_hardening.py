from datetime import datetime, timedelta
import inspect
import math
from pathlib import Path

import pytest

from trading.execution_guard import (
    DEFAULT_MAX_INTENT_AGE_SECONDS,
    DEFAULT_MAX_ORDERS,
    DEFAULT_RATE_WINDOW_SECONDS,
    DuplicateLiveOrderIntentError,
    LiveExecutionGuard,
    LiveOrderRateLimitError,
    StaleLiveOrderIntentError,
)
from trading.live_execution import (
    AmbiguousLiveOrderSubmissionError,
    InvalidBrokerOrderIdError,
    LiveExecutionCoordinator,
)
from trading.live_order import LiveOrderIntent
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.strategy import SignalAction
from trading.zerodha_order import ZerodhaOrderRequest
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def intent(created_time=NOW, action=SignalAction.BUY_CE, price=27.0):
    side = "CE" if action in (SignalAction.BUY_CE, SignalAction.EXIT_CE) else "PE"
    return LiveOrderIntent(
        f"NIFTY26AUG25000{side}", side, action, 65, price, created_time
    )


class Adapter(ZerodhaOrderAdapter):
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def to_order_request(self, value):
        self.calls.append(value)
        if self.error:
            raise self.error
        return ZerodhaOrderRequest(
            value.contract_symbol, "NFO", "BUY", value.quantity,
            "MARKET", "MIS", "DAY",
        )


class Submitter(ZerodhaOrderSubmitter):
    def __init__(self, order_id="order-1", error=None):
        self.order_id = order_id
        self.error = error
        self.calls = []

    def submit(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.order_id


def coordinator(guard=None, submitter=None, adapter=None):
    return LiveExecutionCoordinator(
        adapter or Adapter(), submitter or Submitter(), enabled=True,
        execution_guard=guard,
    )


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("max_intent_age_seconds", 0, ValueError),
        ("max_intent_age_seconds", True, TypeError),
        ("max_intent_age_seconds", math.inf, ValueError),
        ("max_orders", 0, ValueError),
        ("max_orders", True, TypeError),
        ("per_seconds", 0, ValueError),
        ("per_seconds", "1", TypeError),
        ("clock", object(), TypeError),
    ],
)
def test_guard_configuration_is_strict(field, value, error):
    with pytest.raises(error):
        LiveExecutionGuard(**{field: value})


def test_guard_defaults_are_exact_and_conservative():
    guard = LiveExecutionGuard(clock=lambda: NOW)
    assert guard.max_intent_age_seconds == DEFAULT_MAX_INTENT_AGE_SECONDS == 3.0
    assert guard.max_orders == DEFAULT_MAX_ORDERS == 3
    assert guard.per_seconds == DEFAULT_RATE_WINDOW_SECONDS == 1.0


def test_fresh_and_exact_threshold_intents_are_accepted():
    guard = LiveExecutionGuard()
    guard.preflight(intent(NOW), NOW)
    guard.preflight(intent(NOW - timedelta(seconds=3)), NOW)


@pytest.mark.parametrize(
    "created_at",
    [NOW - timedelta(seconds=3, microseconds=1), NOW + timedelta(microseconds=1)],
)
def test_stale_or_future_intent_is_rejected_before_attempt(created_at):
    guard = LiveExecutionGuard()
    with pytest.raises(StaleLiveOrderIntentError):
        guard.record_attempt(intent(created_at), NOW)
    guard.record_attempt(intent(NOW), NOW)


def test_duplicate_fingerprint_excludes_mutable_reference_price():
    guard = LiveExecutionGuard()
    guard.record_attempt(intent(price=27.0), NOW)
    with pytest.raises(DuplicateLiveOrderIntentError):
        guard.preflight(intent(price=99.0), NOW)


def test_different_later_intent_is_allowed():
    guard = LiveExecutionGuard()
    guard.record_attempt(intent(NOW), NOW)
    later = NOW + timedelta(seconds=1)
    guard.record_attempt(intent(later), later)


def test_sliding_window_counts_attempts_and_expires_at_exact_boundary():
    guard = LiveExecutionGuard(max_orders=2, per_seconds=1)
    guard.record_attempt(intent(NOW, SignalAction.BUY_CE), NOW)
    second_time = NOW + timedelta(milliseconds=100)
    guard.record_attempt(intent(second_time, SignalAction.BUY_PE), second_time)
    with pytest.raises(LiveOrderRateLimitError):
        guard.record_attempt(
            intent(NOW + timedelta(milliseconds=200), SignalAction.EXIT_CE),
            NOW + timedelta(milliseconds=200),
        )
    boundary = NOW + timedelta(seconds=1)
    guard.record_attempt(intent(boundary, SignalAction.EXIT_CE), boundary)


def test_adapter_failure_does_not_record_submission_attempt():
    guard = LiveExecutionGuard()
    adapter = Adapter(error=ValueError("local translation failed"))
    execution = coordinator(guard, adapter=adapter)
    with pytest.raises(ValueError):
        execution.execute(intent(), NOW)
    adapter.error = None
    assert execution.execute(intent(), NOW) == "order-1"


def test_attempt_is_recorded_before_broker_exception_and_cannot_retry():
    guard = LiveExecutionGuard()
    submitter = Submitter(error=RuntimeError("ambiguous broker failure"))
    execution = coordinator(guard, submitter=submitter)
    with pytest.raises(AmbiguousLiveOrderSubmissionError):
        execution.execute(intent(), NOW)
    assert len(submitter.calls) == 1
    with pytest.raises(DuplicateLiveOrderIntentError):
        execution.execute(intent(), NOW)
    assert len(submitter.calls) == 1


@pytest.mark.parametrize("order_id", [None, True, 1, "", "   "])
def test_malformed_broker_order_id_fails_closed_and_blocks_duplicate(order_id):
    guard = LiveExecutionGuard()
    submitter = Submitter(order_id=order_id)
    execution = coordinator(guard, submitter=submitter)
    with pytest.raises(InvalidBrokerOrderIdError):
        execution.execute(intent(), NOW)
    with pytest.raises(DuplicateLiveOrderIntentError):
        execution.execute(intent(), NOW)
    assert len(submitter.calls) == 1


def test_valid_broker_order_id_is_stripped_and_submitted_once():
    submitter = Submitter(order_id="  order-1  ")
    execution = coordinator(LiveExecutionGuard(), submitter=submitter)
    assert execution.execute(intent(), NOW) == "order-1"
    assert len(submitter.calls) == 1


def test_guard_source_has_no_retry_or_out_of_scope_behavior():
    source = Path("trading/execution_guard.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "strategyengine", "pnl", "boto3", "static_ip", "secret", "auth",
        "logging", "http", "sleep(", "thread", "retry(", "place_order",
    )
    assert all(term not in source for term in forbidden)
    assert "while true" not in inspect.getsource(LiveExecutionGuard).lower()
