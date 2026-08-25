from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import trading.market as market_module
from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.execution_mode import ExecutionMode
from trading.execution_guard import (
    LiveExecutionGuard,
    LiveOrderRateLimitError,
    StaleLiveOrderIntentError,
)
from trading.live_execution import LiveExecutionCoordinator, LiveExecutionDisabledError
from trading.live_readiness import LiveReadinessGate
from trading.live_order import LiveOrderIntent
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_runtime import build_live_runtime
from trading.market import LiveMarketDataHealthError, MarketData
from trading.market_data_health import MarketDataHealthState, MarketDataHealthTracker
from trading.position_manager import PositionManager
from trading.strategy import SignalAction, StrategyResult
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader
from trading.ohlc import EXCHANGE_TIMEZONE


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class FakeClient:
    def __init__(self):
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0
        self.history = {}

    def positions(self):
        self.positions_calls += 1
        return {"net": [], "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        order_id = "order-1"
        self.history[order_id] = [{
            "order_id": order_id,
            "status": "COMPLETE",
            "quantity": kwargs["quantity"],
            "filled_quantity": kwargs["quantity"],
            "pending_quantity": 0,
            "average_price": 27.0,
            "exchange_update_timestamp": "2026-08-25 09:15:00",
        }]
        return order_id

    def order_history(self, order_id):
        self.order_history_calls += 1
        return self.history[order_id]


def ready_gate():
    gate = LiveReadinessGate()
    gate.apply_recovery_result(LiveRecoveryResult(
        LiveRecoveryState.SAFE_FLAT, (), (), "Explicit test readiness."
    ))
    return gate


def live_market(enabled=True, tracker=None, execution_guard=None):
    client = FakeClient()
    tracker = tracker or MarketDataHealthTracker()
    manager = PositionManager()
    market = MarketData(
        None,
        None,
        execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=LiveExecutionCoordinator(
            ZerodhaOrderAdapter(), ZerodhaOrderSubmitter(client), enabled=enabled,
            execution_guard=execution_guard,
        ),
        live_order_status_reader=ZerodhaOrderStatusReader(client),
        live_position_reader=ZerodhaPositionReader(client),
        live_position_reconciler=BrokerPositionReconciler(),
        live_readiness_gate=ready_gate(),
        live_position_manager=manager,
        live_market_data_health_tracker=tracker,
        live_market_data_clock=lambda: NOW,
    )
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY26AUG25000CE", "lot_size": 65},
        "PE": {"tradingsymbol": "NIFTY26AUG25000PE", "lot_size": 65},
    }
    market.latest_option_premiums = {"CE": 27.0, "PE": 31.0}
    market.latest_option_timestamps = {"CE": NOW, "PE": NOW}
    return market, manager, client, tracker


def buy_result(action=SignalAction.BUY_CE):
    return StrategyResult("test", action, NOW, (action,))


@pytest.mark.parametrize(
    "state",
    [
        MarketDataHealthState.DISCONNECTED,
        MarketDataHealthState.CONNECTED_NO_DATA,
        MarketDataHealthState.STALE,
        MarketDataHealthState.INVALID,
    ],
)
def test_unhealthy_live_feed_blocks_before_any_broker_activity(state):
    market, manager, client, tracker = live_market()
    if state is not MarketDataHealthState.DISCONNECTED:
        tracker.mark_connected(NOW)
    if state is MarketDataHealthState.STALE:
        connected_at = NOW - timedelta(seconds=11)
        tracker.mark_connected(connected_at)
        tracker.record_valid_tick(connected_at, connected_at)
    elif state is MarketDataHealthState.INVALID:
        tracker.record_invalid_tick(NOW)
    assert tracker.snapshot(NOW).state is state
    active_before = manager.active_position
    history_before = market.live_closed_position_history.positions
    target_before = market.strategy_engine.target_points
    with pytest.raises(LiveMarketDataHealthError):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.positions_calls == 0
    assert client.orders_calls == 0
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert manager.active_position is active_before
    assert market.live_closed_position_history.positions == history_before
    assert market.strategy_engine.target_points == target_before == 2.0
    assert market.pending_live_order is None


def test_healthy_enabled_feed_permits_existing_submission_path():
    market, manager, client, tracker = live_market(enabled=True)
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    assert market._execute_strategy_result(buy_result(), NOW) == ("order-1",)
    assert client.positions_calls == 1
    assert client.place_order_calls == 1
    assert client.order_history_calls == 1
    assert manager.active_position is not None


def test_healthy_feed_does_not_override_disabled_execution():
    market, _, client, tracker = live_market(enabled=False)
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    with pytest.raises(LiveExecutionDisabledError):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.place_order_calls == 0


def test_not_ready_still_blocks_healthy_feed_before_broker_activity():
    market, _, client, tracker = live_market()
    market.live_readiness_gate.revoke()
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    with pytest.raises(Exception, match="READY"):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.positions_calls == client.place_order_calls == 0


def test_runtime_and_market_factory_share_one_tracker_without_calls():
    client = FakeClient()
    runtime = build_live_runtime(client, execution_enabled=True)
    from trading.live_market_factory import build_live_market_data
    market = build_live_market_data(client, object(), runtime)
    assert market.live_market_data_health_tracker is runtime.market_data_health_tracker
    assert runtime.market_data_health_tracker.snapshot(NOW).state is (
        MarketDataHealthState.DISCONNECTED
    )
    assert client.positions_calls == client.orders_calls == 0
    assert client.place_order_calls == client.order_history_calls == 0


class FakeInstruments:
    def get_nifty_index_token(self):
        return 100

    def get_nifty_option_pair(self, spot_price):
        return {
            "CE": {"instrument_token": 101, "tradingsymbol": "CE", "lot_size": 65},
            "PE": {"instrument_token": 102, "tradingsymbol": "PE", "lot_size": 65},
        }


class FakeTicker:
    MODE_FULL = "full"
    latest = None

    def __init__(self, api_key, access_token):
        FakeTicker.latest = self
        self.on_connect = self.on_ticks = self.on_close = self.on_error = None

    def subscribe(self, tokens):
        pass

    def set_mode(self, mode, tokens):
        pass

    def connect(self):
        self.on_connect(self, {})


def test_socket_callbacks_drive_health_without_changing_price_or_candle_authorities(
    monkeypatch,
):
    tracker = MarketDataHealthTracker()
    market = MarketData(SimpleNamespace(access_token="fake-token"), FakeInstruments())
    market.live_market_data_health_tracker = tracker
    market._live_market_data_clock = lambda: NOW
    monkeypatch.setattr(market_module, "KiteTicker", FakeTicker)
    market.connect_live("NIFTY 50")
    ticker = FakeTicker.latest
    assert tracker.snapshot(NOW).state is MarketDataHealthState.CONNECTED_NO_DATA
    ticker.on_ticks(ticker, [{
        "instrument_token": 100,
        "last_price": 25000.0,
        "exchange_timestamp": NOW,
    }])
    assert tracker.snapshot(NOW).is_healthy
    assert market.latest_nifty_spot == 25000.0
    assert market.ohlc.current_candle.time == NOW.replace(second=0, microsecond=0)
    ticker.on_close(ticker, 1000, "closed")
    assert tracker.snapshot(NOW).state is MarketDataHealthState.DISCONNECTED
    ticker.on_connect(ticker, {})
    assert tracker.snapshot(NOW).state is MarketDataHealthState.CONNECTED_NO_DATA


def test_malformed_subscribed_tick_marks_invalid_without_cache_update():
    market, _, _, tracker = live_market()
    market.option_tokens = {101: "CE"}
    tracker.mark_connected(NOW)
    before = dict(market.latest_option_premiums)
    assert not market._record_market_data_health_tick({
        "instrument_token": 101, "last_price": float("nan"),
        "exchange_timestamp": NOW,
    })
    assert tracker.snapshot(NOW).state is MarketDataHealthState.INVALID
    assert market.latest_option_premiums == before


@pytest.mark.parametrize(
    "action, side",
    [(SignalAction.BUY_CE, "CE"), (SignalAction.BUY_PE, "PE")],
)
def test_fresh_spot_after_reconnect_cannot_authorize_cached_option_premium(
    action, side
):
    market, _, client, tracker = live_market()
    old_timestamp = NOW - timedelta(seconds=2)
    old_premium = market.latest_option_premiums[side]
    market.latest_option_timestamps[side] = old_timestamp
    tracker.mark_connected(NOW - timedelta(seconds=3))
    tracker.record_valid_tick(old_timestamp, old_timestamp)
    tracker.mark_disconnected(NOW - timedelta(seconds=1))
    tracker.mark_connected(NOW)
    tracker.record_valid_tick(NOW, NOW)
    assert tracker.snapshot(NOW).state is MarketDataHealthState.HEALTHY
    assert market.latest_option_premiums[side] == old_premium
    assert market.latest_option_timestamps[side] is old_timestamp
    with pytest.raises(LiveMarketDataHealthError):
        market._execute_strategy_result(buy_result(action), NOW)
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0


@pytest.mark.parametrize(
    "action, side",
    [(SignalAction.BUY_CE, "CE"), (SignalAction.BUY_PE, "PE")],
)
def test_fresh_post_reconnect_selected_option_tick_permits_existing_path(
    action, side
):
    market, manager, client, tracker = live_market()
    tracker.mark_connected(NOW - timedelta(seconds=1))
    tracker.record_valid_tick(NOW, NOW)
    market.latest_option_timestamps[side] = NOW
    assert market._execute_strategy_result(buy_result(action), NOW) == ("order-1",)
    assert client.positions_calls == 1
    assert client.place_order_calls == client.order_history_calls == 1
    assert manager.active_position is not None


def test_selected_side_health_is_independent_between_ce_and_pe():
    ce_market, _, ce_client, ce_tracker = live_market()
    ce_tracker.mark_connected(NOW - timedelta(seconds=1))
    ce_tracker.record_valid_tick(NOW, NOW)
    ce_market.latest_option_timestamps = {"CE": NOW}
    assert ce_market._execute_strategy_result(
        buy_result(SignalAction.BUY_CE), NOW
    ) == ("order-1",)
    assert ce_client.place_order_calls == 1

    pe_market, _, pe_client, pe_tracker = live_market()
    pe_tracker.mark_connected(NOW - timedelta(seconds=1))
    pe_tracker.record_valid_tick(NOW, NOW)
    pe_market.latest_option_timestamps = {"CE": NOW}
    with pytest.raises(LiveMarketDataHealthError):
        pe_market._execute_strategy_result(buy_result(SignalAction.BUY_PE), NOW)
    assert pe_client.positions_calls == pe_client.place_order_calls == 0


def test_stale_selected_option_blocks_even_when_another_stream_is_healthy():
    market, _, client, tracker = live_market()
    tracker.mark_connected(NOW - timedelta(seconds=20))
    tracker.record_valid_tick(NOW, NOW)
    market.latest_option_timestamps["CE"] = NOW - timedelta(seconds=11)
    assert tracker.snapshot(NOW).state is MarketDataHealthState.HEALTHY
    with pytest.raises(LiveMarketDataHealthError):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0


def test_future_selected_option_timestamp_blocks_before_broker_activity():
    market, _, client, tracker = live_market()
    tracker.mark_connected(NOW - timedelta(seconds=1))
    tracker.record_valid_tick(NOW, NOW)
    market.latest_option_timestamps["CE"] = NOW + timedelta(seconds=1)
    with pytest.raises(LiveMarketDataHealthError):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0


def test_reconnect_epoch_invalidates_timestamp_trust_without_another_cache():
    market, _, _, tracker = live_market()
    market.latest_option_timestamps["CE"] = NOW - timedelta(seconds=2)
    premiums = market.latest_option_premiums
    timestamps = market.latest_option_timestamps
    tracker.mark_connected(NOW - timedelta(seconds=2))
    tracker.mark_disconnected(NOW - timedelta(seconds=1))
    tracker.mark_connected(NOW)
    assert market.latest_option_premiums is premiums
    assert market.latest_option_timestamps is timestamps
    assert timestamps["CE"] < tracker.connected_at
    assert not hasattr(tracker, "latest_option_premiums")
    assert not hasattr(tracker, "latest_option_timestamps")


def test_paper_remains_default_without_health_requirement():
    market = MarketData(None, None)
    assert market.execution_router.mode is ExecutionMode.PAPER
    assert market.live_market_data_health_tracker is None


def test_stale_intent_guard_blocks_before_broker_position_read():
    guard = LiveExecutionGuard(max_intent_age_seconds=3)
    market, _, client, tracker = live_market(execution_guard=guard)
    tracker.mark_connected(NOW - timedelta(seconds=1))
    tracker.record_valid_tick(NOW, NOW)
    stale = StrategyResult(
        "test", SignalAction.BUY_CE, NOW - timedelta(seconds=4),
        (SignalAction.BUY_CE,),
    )
    with pytest.raises(StaleLiveOrderIntentError):
        market._execute_strategy_result(stale, NOW - timedelta(seconds=4))
    assert client.positions_calls == client.place_order_calls == 0


def test_local_rate_limit_blocks_before_broker_position_read():
    guard = LiveExecutionGuard(max_orders=1, per_seconds=10)
    guard.record_attempt(
        LiveOrderIntent(
            "NIFTY26AUG25100PE", "PE", SignalAction.BUY_PE, 65, 31.0, NOW
        ),
        NOW,
    )
    market, _, client, tracker = live_market(execution_guard=guard)
    tracker.mark_connected(NOW - timedelta(seconds=1))
    tracker.record_valid_tick(NOW, NOW)
    with pytest.raises(LiveOrderRateLimitError):
        market._execute_strategy_result(buy_result(), NOW)
    assert client.positions_calls == client.place_order_calls == 0
