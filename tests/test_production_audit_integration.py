from datetime import datetime, timezone

import pytest

from core.production_audit import AuditSink, AuditEventType, AuditWriteError
from trading.execution_guard import DuplicateLiveOrderIntentError, LiveExecutionGuard
from trading.live_execution import (
    AmbiguousLiveOrderSubmissionError,
    InvalidBrokerOrderIdError,
    LiveExecutionCoordinator,
)
from trading.live_order import LiveOrderIntent
from trading.strategy import SignalAction
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=timezone.utc)


class RecordingSink(AuditSink):
    def __init__(self, calls, fail_on=None):
        self.calls = calls
        self.fail_on = fail_on

    def write(self, event):
        self.calls.append(("audit", event.event_type, event.correlation_id))
        if event.event_type is self.fail_on:
            raise AuditWriteError("audit failed")
        return event


class Client:
    def __init__(self, calls, error=None, result="order-1"):
        self.calls = calls
        self.error = error
        self.result = result

    def place_order(self, **kwargs):
        self.calls.append(("broker", "place_order"))
        if self.error is not None:
            raise self.error
        return self.result


def intent():
    return LiveOrderIntent(
        "NIFTY26AUG25000CE", "CE", SignalAction.BUY_CE,
        65, 100.0, NOW,
    )


def coordinator(calls, sink, error=None, result="order-1"):
    return LiveExecutionCoordinator(
        ZerodhaOrderAdapter(), ZerodhaOrderSubmitter(Client(calls, error, result)),
        enabled=True, execution_guard=LiveExecutionGuard(), audit_sink=sink,
    )


def test_attempt_precedes_broker_and_confirmation_uses_same_correlation():
    calls = []
    sink = RecordingSink(calls)
    assert coordinator(calls, sink).execute(intent(), NOW) == "order-1"
    assert calls == [
        ("audit", AuditEventType.ORDER_SUBMISSION_ATTEMPTED, calls[0][2]),
        ("broker", "place_order"),
        ("audit", AuditEventType.ORDER_SUBMISSION_CONFIRMED, calls[0][2]),
    ]
    assert calls[0][2] == calls[2][2]


def test_pre_submit_audit_failure_blocks_broker_call():
    calls = []
    sink = RecordingSink(calls, AuditEventType.ORDER_SUBMISSION_ATTEMPTED)
    with pytest.raises(AuditWriteError):
        coordinator(calls, sink).execute(intent(), NOW)
    assert calls[0][:2] == ("audit", AuditEventType.ORDER_SUBMISSION_ATTEMPTED)
    assert len(calls) == 1


def test_broker_failure_records_ambiguous_without_retry():
    calls = []
    sink = RecordingSink(calls)
    with pytest.raises(AmbiguousLiveOrderSubmissionError):
        coordinator(calls, sink, RuntimeError("broker failed")).execute(intent(), NOW)
    assert [call[:2] for call in calls] == [
        ("audit", AuditEventType.ORDER_SUBMISSION_ATTEMPTED),
        ("broker", "place_order"),
        ("audit", AuditEventType.ORDER_SUBMISSION_AMBIGUOUS),
    ]


def test_post_submit_confirmation_audit_failure_never_retries_order():
    calls = []
    sink = RecordingSink(calls, AuditEventType.ORDER_SUBMISSION_CONFIRMED)
    with pytest.raises(AuditWriteError):
        coordinator(calls, sink).execute(intent(), NOW)
    assert calls.count(("broker", "place_order")) == 1


def test_ambiguous_audit_failure_preserves_attempt_guard_without_retry():
    calls = []
    sink = RecordingSink(calls, AuditEventType.ORDER_SUBMISSION_AMBIGUOUS)
    value = coordinator(calls, sink, RuntimeError("broker failed"))
    proposed = intent()
    with pytest.raises(AuditWriteError):
        value.execute(proposed, NOW)
    with pytest.raises(DuplicateLiveOrderIntentError):
        value.execute(proposed, NOW)
    assert calls.count(("broker", "place_order")) == 1


def test_invalid_broker_order_id_is_audited_as_ambiguous_without_retry():
    calls = []
    sink = RecordingSink(calls)
    with pytest.raises(InvalidBrokerOrderIdError):
        coordinator(calls, sink, result=" ").execute(intent(), NOW)
    assert [call[1] for call in calls if call[0] == "audit"] == [
        AuditEventType.ORDER_SUBMISSION_ATTEMPTED,
        AuditEventType.ORDER_SUBMISSION_AMBIGUOUS,
    ]
    assert calls.count(("broker", "place_order")) == 1
