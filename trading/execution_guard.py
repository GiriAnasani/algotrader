"""Process-local deterministic guards for LIVE broker submission attempts."""

from collections import deque
from datetime import datetime
import math
from numbers import Real

from trading.live_order import LiveOrderIntent
from trading.ohlc import EXCHANGE_TIMEZONE


DEFAULT_MAX_INTENT_AGE_SECONDS = 3.0
DEFAULT_MAX_ORDERS = 3
DEFAULT_RATE_WINDOW_SECONDS = 1.0


class DuplicateLiveOrderIntentError(RuntimeError):
    """Raised when one process attempts an identical LIVE intent again."""


class StaleLiveOrderIntentError(RuntimeError):
    """Raised when a LIVE intent is stale or future-dated."""


class LiveOrderRateLimitError(RuntimeError):
    """Raised before submission when the local attempt window is full."""


def _positive_real(value, field_name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number.")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be positive and finite.")


class LiveExecutionGuard:
    """Validates and records one-process submission attempts without retry."""

    def __init__(
        self,
        max_intent_age_seconds=DEFAULT_MAX_INTENT_AGE_SECONDS,
        max_orders=DEFAULT_MAX_ORDERS,
        per_seconds=DEFAULT_RATE_WINDOW_SECONDS,
        clock=None,
    ):
        _positive_real(max_intent_age_seconds, "max_intent_age_seconds")
        if isinstance(max_orders, bool) or not isinstance(max_orders, int):
            raise TypeError("max_orders must be an integer.")
        if max_orders <= 0:
            raise ValueError("max_orders must be positive.")
        _positive_real(per_seconds, "per_seconds")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None.")
        self.max_intent_age_seconds = max_intent_age_seconds
        self.max_orders = max_orders
        self.per_seconds = per_seconds
        self._clock = clock or (lambda: datetime.now(EXCHANGE_TIMEZONE))
        self._attempted_fingerprints = set()
        self._attempt_times = deque()

    def preflight(self, intent, observed_at=None):
        """Checks local constraints without consuming attempt capacity."""
        observed = self._observation_time(observed_at)
        self._validate_intent(intent, observed)
        fingerprint = self._fingerprint(intent)
        if fingerprint in self._attempted_fingerprints:
            raise DuplicateLiveOrderIntentError(
                "This LIVE order intent already reached submission."
            )
        self._discard_expired_attempts(observed)
        if len(self._attempt_times) >= self.max_orders:
            raise LiveOrderRateLimitError(
                "The local LIVE order submission limit was reached."
            )
        return observed

    def record_attempt(self, intent, observed_at=None):
        """Records an attempt before the caller invokes the broker."""
        observed = self.preflight(intent, observed_at)
        self._attempted_fingerprints.add(self._fingerprint(intent))
        self._attempt_times.append(observed)

    def _observation_time(self, observed_at):
        observed = self._clock() if observed_at is None else observed_at
        if not isinstance(observed, datetime):
            raise TypeError("Observation time must be a datetime.")
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Observation time must be timezone-aware.")
        return observed

    def _validate_intent(self, intent, observed):
        if not isinstance(intent, LiveOrderIntent):
            raise TypeError("Intent must be a LiveOrderIntent.")
        age = (observed - intent.created_time).total_seconds()
        if age < 0 or age > self.max_intent_age_seconds:
            raise StaleLiveOrderIntentError(
                "LIVE order intent time is outside the submission window."
            )

    def _discard_expired_attempts(self, observed):
        while self._attempt_times and (
            observed - self._attempt_times[0]
        ).total_seconds() >= self.per_seconds:
            self._attempt_times.popleft()

    @staticmethod
    def _fingerprint(intent):
        return (
            intent.action,
            intent.contract_symbol,
            intent.quantity,
            intent.created_time,
        )
