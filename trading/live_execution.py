"""Explicitly authorized orchestration for isolated live execution."""

from hashlib import sha256

from core.production_audit import AuditEvent, AuditEventType, AuditSink
from trading.execution_guard import LiveExecutionGuard
from trading.live_order import LiveOrderIntent
from trading.pending_live_order import PendingLiveOrder
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


class LiveExecutionDisabledError(RuntimeError):
    """Raised when live execution is attempted without explicit authorization."""


class AmbiguousLiveOrderSubmissionError(RuntimeError):
    """Raised when a broker submission attempt has an unknown outcome."""


class InvalidBrokerOrderIdError(RuntimeError):
    """Raised when an attempted submission returns no usable order ID."""


def live_order_correlation_id(intent):
    """Derive a non-authoritative stable audit correlation for one intent."""
    if not isinstance(intent, (LiveOrderIntent, PendingLiveOrder)):
        raise TypeError("Intent must be a LiveOrderIntent or PendingLiveOrder.")
    value = "|".join((
        intent.contract_symbol, intent.side, intent.action.value,
        str(intent.quantity), intent.created_time.isoformat(),
    ))
    return sha256(value.encode("utf-8")).hexdigest()


class LiveExecutionCoordinator:
    """Coordinates intent translation and submission only when enabled."""

    def __init__(self, adapter, submitter, enabled=False, execution_guard=None,
                 audit_sink=None):
        if not isinstance(adapter, ZerodhaOrderAdapter):
            raise TypeError("Adapter must be a ZerodhaOrderAdapter.")

        if not isinstance(submitter, ZerodhaOrderSubmitter):
            raise TypeError("Submitter must be a ZerodhaOrderSubmitter.")

        if not isinstance(enabled, bool):
            raise TypeError("Enabled must be a boolean.")
        if execution_guard is not None and not isinstance(
            execution_guard, LiveExecutionGuard
        ):
            raise TypeError("Execution guard must be a LiveExecutionGuard or None.")
        if audit_sink is not None and not isinstance(audit_sink, AuditSink):
            raise TypeError("Audit sink must be an AuditSink or None.")

        self._adapter = adapter
        self._submitter = submitter
        self.enabled = enabled
        self._execution_guard = execution_guard
        self._audit_sink = audit_sink

    def preflight(self, intent, observed_at=None):
        """Checks process-local constraints before any broker read or submit."""
        if not isinstance(intent, LiveOrderIntent):
            raise TypeError("Intent must be a LiveOrderIntent.")
        if not self.enabled:
            raise LiveExecutionDisabledError("Live execution is disabled.")
        if self._execution_guard is not None:
            self._execution_guard.preflight(intent, observed_at)

    def execute(self, intent, observed_at=None):
        """Submits one live intent only when explicit authorization is enabled."""
        if not isinstance(intent, LiveOrderIntent):
            raise TypeError("Intent must be a LiveOrderIntent.")

        self.preflight(intent, observed_at)

        order_request = self._adapter.to_order_request(intent)
        if self._execution_guard is not None:
            self._execution_guard.record_attempt(intent, observed_at)
        correlation_id = live_order_correlation_id(intent)
        self._audit(
            AuditEventType.ORDER_SUBMISSION_ATTEMPTED,
            observed_at or intent.created_time,
            intent,
            correlation_id,
        )
        try:
            order_id = self._submitter.submit(order_request)
        except Exception as error:
            self._audit(
                AuditEventType.ORDER_SUBMISSION_AMBIGUOUS,
                observed_at or intent.created_time,
                intent,
                correlation_id,
            )
            if self._execution_guard is None:
                raise
            raise AmbiguousLiveOrderSubmissionError(
                "The LIVE broker submission outcome is unknown."
            ) from error
        if not isinstance(order_id, str) or not order_id.strip():
            self._audit(
                AuditEventType.ORDER_SUBMISSION_AMBIGUOUS,
                observed_at or intent.created_time,
                intent,
                correlation_id,
                reason="invalid_order_id",
            )
            raise InvalidBrokerOrderIdError(
                "The broker returned an invalid LIVE order ID."
            )
        order_id = order_id.strip()
        self._audit(
            AuditEventType.ORDER_SUBMISSION_CONFIRMED,
            observed_at or intent.created_time,
            intent,
            correlation_id,
            order_id=order_id,
        )
        return order_id

    def _audit(self, event_type, occurred_at, intent, correlation_id, **extra):
        if self._audit_sink is None:
            return
        data = {
            "action": intent.action,
            "symbol": intent.contract_symbol,
            "quantity": intent.quantity,
            **extra,
        }
        self._audit_sink.write(
            AuditEvent.create(event_type, occurred_at, data, correlation_id)
        )
