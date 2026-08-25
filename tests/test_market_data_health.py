from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta
from enum import Enum
import math
from pathlib import Path

import pytest

from trading.market_data_health import (
    DEFAULT_STALE_AFTER_SECONDS,
    MarketDataHealthSnapshot,
    MarketDataHealthState,
    MarketDataHealthTracker,
)
from trading.ohlc import EXCHANGE_TIMEZONE


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def snapshot(tracker, at=NOW):
    return tracker.snapshot(at)


def test_health_states_are_exact():
    assert issubclass(MarketDataHealthState, Enum)
    assert [(state.name, state.value) for state in MarketDataHealthState] == [
        ("DISCONNECTED", "DISCONNECTED"),
        ("CONNECTED_NO_DATA", "CONNECTED_NO_DATA"),
        ("HEALTHY", "HEALTHY"),
        ("STALE", "STALE"),
        ("INVALID", "INVALID"),
    ]


def test_tracker_starts_disconnected_with_frozen_exact_snapshot():
    result = snapshot(MarketDataHealthTracker())
    assert [field.name for field in fields(result)] == [
        "state", "connected", "last_tick_at", "last_valid_tick_at",
        "stale_after_seconds",
    ]
    assert result.state is MarketDataHealthState.DISCONNECTED
    assert not result.connected and not result.is_healthy
    assert result.last_tick_at is result.last_valid_tick_at is None
    assert result.stale_after_seconds == DEFAULT_STALE_AFTER_SECONDS == 10.0
    with pytest.raises(FrozenInstanceError):
        result.state = MarketDataHealthState.HEALTHY


@pytest.mark.parametrize("value", [None, True, False, "10", object()])
def test_stale_threshold_requires_real_number(value):
    with pytest.raises(TypeError):
        MarketDataHealthTracker(value)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
def test_stale_threshold_requires_positive_finite_value(value):
    with pytest.raises(ValueError):
        MarketDataHealthTracker(value)


@pytest.mark.parametrize(
    "operation",
    [
        lambda tracker, value: tracker.mark_connected(value),
        lambda tracker, value: tracker.mark_disconnected(value),
        lambda tracker, value: tracker.record_valid_tick(value, NOW),
        lambda tracker, value: tracker.snapshot(value),
    ],
)
@pytest.mark.parametrize("value", [None, True, "time", object()])
def test_timestamp_operations_require_datetime(operation, value):
    with pytest.raises(TypeError):
        operation(MarketDataHealthTracker(), value)


@pytest.mark.parametrize("value", [True, "time", object()])
def test_supplied_invalid_tick_timestamp_requires_datetime(value):
    with pytest.raises(TypeError):
        MarketDataHealthTracker().record_invalid_tick(value)


@pytest.mark.parametrize(
    "observed_at", [None, True, "time", object(), datetime(2026, 8, 25, 9, 15)]
)
def test_valid_tick_requires_explicit_aware_observation_time(observed_at):
    with pytest.raises((TypeError, ValueError)):
        MarketDataHealthTracker().record_valid_tick(NOW, observed_at)


@pytest.mark.parametrize(
    "operation",
    [
        lambda tracker, value: tracker.mark_connected(value),
        lambda tracker, value: tracker.mark_disconnected(value),
        lambda tracker, value: tracker.record_valid_tick(value, NOW),
        lambda tracker, value: tracker.snapshot(value),
    ],
)
def test_timestamp_operations_reject_naive_datetime(operation):
    with pytest.raises(ValueError):
        operation(MarketDataHealthTracker(), datetime(2026, 8, 25, 9, 15))


def test_connect_valid_tick_disconnect_and_reconnect_require_new_data():
    tracker = MarketDataHealthTracker()
    tracker.mark_connected(NOW)
    assert snapshot(tracker).state is MarketDataHealthState.CONNECTED_NO_DATA
    assert tracker.record_valid_tick(NOW, NOW)
    assert snapshot(tracker).state is MarketDataHealthState.HEALTHY
    tracker.mark_disconnected(NOW + timedelta(seconds=1))
    assert snapshot(tracker).state is MarketDataHealthState.DISCONNECTED
    tracker.mark_connected(NOW + timedelta(seconds=2))
    result = snapshot(tracker, NOW + timedelta(seconds=2))
    assert result.state is MarketDataHealthState.CONNECTED_NO_DATA
    assert result.last_valid_tick_at is NOW
    assert not tracker.record_valid_tick(NOW, NOW + timedelta(seconds=2))
    assert not tracker.record_valid_tick(
        NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)
    )
    assert snapshot(tracker, NOW + timedelta(seconds=2)).state is (
        MarketDataHealthState.CONNECTED_NO_DATA
    )
    new_tick = NOW + timedelta(seconds=3)
    assert tracker.record_valid_tick(new_tick, new_tick)
    assert snapshot(tracker, new_tick).state is MarketDataHealthState.HEALTHY


def test_exact_threshold_is_healthy_and_exceeded_threshold_is_stale():
    tracker = MarketDataHealthTracker(10)
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    assert snapshot(tracker, NOW + timedelta(seconds=10)).is_healthy
    assert snapshot(tracker, NOW + timedelta(seconds=10, microseconds=1)).state is (
        MarketDataHealthState.STALE
    )


def test_future_tick_remains_invalid_until_a_subsequent_valid_tick():
    tracker = MarketDataHealthTracker()
    tracker.mark_connected(NOW)
    future = NOW + timedelta(seconds=1)
    assert not tracker.record_valid_tick(future, NOW)
    result = snapshot(tracker, NOW)
    assert result.state is MarketDataHealthState.INVALID
    assert result.last_valid_tick_at is None
    assert snapshot(tracker, future).state is MarketDataHealthState.INVALID
    assert snapshot(tracker, future + timedelta(seconds=1)).state is (
        MarketDataHealthState.INVALID
    )
    genuine = future + timedelta(seconds=2)
    assert tracker.record_valid_tick(genuine, genuine)
    assert snapshot(tracker, genuine).state is MarketDataHealthState.HEALTHY


def test_out_of_order_and_duplicate_ticks_do_not_move_or_extend_freshness():
    tracker = MarketDataHealthTracker(10)
    tracker.mark_connected(NOW)
    exact = NOW
    tracker.record_valid_tick(exact, exact)
    assert not tracker.record_valid_tick(NOW - timedelta(seconds=1), NOW)
    assert not tracker.record_valid_tick(exact, NOW)
    result = snapshot(tracker, NOW + timedelta(seconds=11))
    assert result.state is MarketDataHealthState.STALE
    assert result.last_tick_at is exact
    assert result.last_valid_tick_at is exact


def test_invalid_event_fails_closed_until_newer_valid_tick():
    tracker = MarketDataHealthTracker()
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    tracker.record_invalid_tick(NOW + timedelta(seconds=1))
    assert snapshot(tracker, NOW + timedelta(seconds=1)).state is (
        MarketDataHealthState.INVALID
    )
    tracker.record_valid_tick(
        NOW + timedelta(seconds=2), NOW + timedelta(seconds=2)
    )
    assert snapshot(tracker, NOW + timedelta(seconds=2)).is_healthy


def test_health_module_has_no_external_or_out_of_scope_authorities():
    source = Path("trading/market_data_health.py").read_text(
        encoding="utf-8"
    ).lower()
    forbidden = (
        "kiteconnect", "secret", "positionstore", "closedpositionhistorystore",
        "strategy", "pnl", "risk", "boto3", "static_ip", "http", "logging",
        "place_order", "sleep", "thread",
    )
    assert all(term not in source for term in forbidden)
