"""Deterministic connection and tick-freshness health authority."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from numbers import Real


DEFAULT_STALE_AFTER_SECONDS = 10.0


class MarketDataHealthState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED_NO_DATA = "CONNECTED_NO_DATA"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    INVALID = "INVALID"


def _aware_timestamp(value, field_name):
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value


@dataclass(frozen=True)
class MarketDataHealthSnapshot:
    state: MarketDataHealthState
    connected: bool
    last_tick_at: datetime | None
    last_valid_tick_at: datetime | None
    stale_after_seconds: Real

    @property
    def is_healthy(self):
        return self.state is MarketDataHealthState.HEALTHY


class MarketDataHealthTracker:
    """Tracks socket lifecycle and trusted tick freshness without side effects."""

    def __init__(self, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS):
        if isinstance(stale_after_seconds, bool) or not isinstance(
            stale_after_seconds, Real
        ):
            raise TypeError("stale_after_seconds must be a real number.")
        if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive and finite.")
        self._stale_after_seconds = stale_after_seconds
        self._connected = False
        self._connected_at = None
        self._fresh_since_connect = False
        self._invalid = False
        self._last_tick_at = None
        self._last_valid_tick_at = None

    @property
    def stale_after_seconds(self):
        return self._stale_after_seconds

    @property
    def connected_at(self):
        return self._connected_at

    def mark_connected(self, at):
        _aware_timestamp(at, "connected timestamp")
        self._connected = True
        self._connected_at = at
        self._fresh_since_connect = False
        self._invalid = False

    def mark_disconnected(self, at):
        _aware_timestamp(at, "disconnected timestamp")
        self._connected = False
        self._fresh_since_connect = False
        self._invalid = False

    def record_valid_tick(self, tick_at, observed_at):
        _aware_timestamp(tick_at, "tick timestamp")
        _aware_timestamp(observed_at, "observation timestamp")
        self._record_latest_tick(tick_at)
        if tick_at > observed_at:
            self._invalid = True
            return False
        if (
            self._connected
            and self._connected_at is not None
            and tick_at < self._connected_at
        ):
            return False
        if (
            self._last_valid_tick_at is not None
            and tick_at <= self._last_valid_tick_at
        ):
            return False
        self._last_valid_tick_at = tick_at
        self._fresh_since_connect = self._connected
        self._invalid = False
        return True

    def record_invalid_tick(self, tick_at=None):
        if tick_at is not None:
            _aware_timestamp(tick_at, "tick timestamp")
            self._record_latest_tick(tick_at)
        self._invalid = True

    def snapshot(self, now):
        _aware_timestamp(now, "evaluation timestamp")
        if not self._connected:
            state = MarketDataHealthState.DISCONNECTED
        elif self._invalid:
            state = MarketDataHealthState.INVALID
        elif not self._fresh_since_connect or self._last_valid_tick_at is None:
            state = MarketDataHealthState.CONNECTED_NO_DATA
        elif self._last_valid_tick_at > now:
            state = MarketDataHealthState.INVALID
        elif (now - self._last_valid_tick_at).total_seconds() <= (
            self._stale_after_seconds
        ):
            state = MarketDataHealthState.HEALTHY
        else:
            state = MarketDataHealthState.STALE
        return MarketDataHealthSnapshot(
            state=state,
            connected=self._connected,
            last_tick_at=self._last_tick_at,
            last_valid_tick_at=self._last_valid_tick_at,
            stale_after_seconds=self._stale_after_seconds,
        )

    def _record_latest_tick(self, tick_at):
        if self._last_tick_at is None or tick_at > self._last_tick_at:
            self._last_tick_at = tick_at
