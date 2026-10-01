from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from trading.market import MarketData
from trading.strategy import SignalAction, StrategyResult
from trading.strategy2 import SetupState
from trading.strategy2_forward_telemetry import Strategy2ForwardTelemetryStore
from trading.strategy2_live_shadow_runner import (
    RestrictedKiteClient,
    Strategy2LiveObservationLog,
)


IST = ZoneInfo("Asia/Kolkata")


class FakeKite:
    api_key = "test-api-key"
    access_token = "never-log-this-token"
    order_calls = 0

    def place_order(self, *args, **kwargs):
        self.order_calls += 1
        raise AssertionError("Shadow lifecycle must never place an order.")

    def modify_order(self, *args, **kwargs):
        self.order_calls += 1
        raise AssertionError("Shadow lifecycle must never modify an order.")

    def cancel_order(self, *args, **kwargs):
        self.order_calls += 1
        raise AssertionError("Shadow lifecycle must never cancel an order.")


class FakeTicker:
    MODE_FULL = "full"
    instance = None

    def __init__(self, api_key, access_token):
        self.api_key = api_key
        self.access_token = access_token
        self.subscriptions = []
        self.modes = []
        self.closed = False
        FakeTicker.instance = self

    def subscribe(self, tokens):
        self.subscriptions.append(list(tokens))

    def set_mode(self, mode, tokens):
        self.modes.append((mode, list(tokens)))

    def connect(self):
        self.on_connect(self, {})

    def emit(self, *ticks):
        self.on_ticks(self, list(ticks))

    def close(self):
        self.closed = True


class FakeInstruments:
    def get_nifty_index_token(self):
        return 101

    def get_nifty_strategy2_contract(self, spot_price, option_type, as_of=None):
        return {
            "instrument_token": 202,
            "tradingsymbol": "NIFTY26SEP25050CE",
            "expiry": as_of.date(),
            "atm_strike": 25000.0,
            "otm_strike": 25050.0,
            "strike": 25050.0,
            "lot_size": 65,
            "option_type": option_type,
        }

    def get_nifty_option_pair(self, spot_price):
        raise AssertionError("Strategy 1 ATM selection must not run.")


class ConfirmingEngine:
    state = SetupState.IDLE
    qualified_at = None
    pullback_at = None
    last_signal_qualification_time = datetime(2026, 9, 30, 9, 50, tzinfo=IST)
    last_signal_pullback_time = datetime(2026, 9, 30, 9, 51, tzinfo=IST)

    def __init__(self):
        self.active_position = None
        self.close_calls = 0

    def evaluate(self, snapshot, option_premiums=None):
        return StrategyResult(
            strategy_name="EXP-STRAT-02-FROZEN-V1",
            action=SignalAction.BUY_CE,
            candle_time=snapshot.candle.time,
            reason="confirmation",
        )

    def set_position_active(self, side):
        self.active_position = side

    def set_position_closed(self):
        self.active_position = None
        self.close_calls += 1


def make_market(tmp_path):
    FakeKite.order_calls = 0
    clock = lambda: datetime(2026, 9, 30, 9, 53, 2, tzinfo=IST)
    observations = Strategy2LiveObservationLog(
        root=tmp_path / "forward", clock=clock
    )
    telemetry = Strategy2ForwardTelemetryStore(
        root=tmp_path / "forward", clock=clock
    )
    market = MarketData.create_strategy2_live_shadow(
        RestrictedKiteClient(FakeKite.api_key, FakeKite.access_token),
        FakeInstruments(),
        strategy2_telemetry_store=telemetry,
        observation_log=observations,
        ticker_factory=FakeTicker,
        clock=clock,
    )
    market.strategy2_engine = ConfirmingEngine()
    return market, observations


def events(log):
    return [
        json.loads(line)
        for line in log.path.read_text(encoding="utf-8").splitlines()
    ]


def test_isolated_feed_subscribes_only_spot_then_exact_strategy2_token(tmp_path):
    market, observations = make_market(tmp_path)
    market.connect_strategy2_live_shadow()
    ticker = FakeTicker.instance

    assert ticker.subscriptions == [[101]]
    assert not hasattr(market, "strategy_engine")
    assert not hasattr(market, "execution_router")

    ticker.emit(
        {"instrument_token": 999, "last_price": 1.0},
        {
            "instrument_token": 101,
            "last_price": 25000.0,
            "exchange_timestamp": datetime(2026, 9, 30, 9, 52, 10, tzinfo=IST),
        },
        {
            "instrument_token": 101,
            "last_price": 25010.0,
            "exchange_timestamp": datetime(2026, 9, 30, 9, 53, 1, tzinfo=IST),
        },
    )

    assert ticker.subscriptions == [[101], [202]]
    assert market.strategy2_pending_contract["otm_strike"] == 25050.0
    assert market.strategy2_pending_entry_manager.pending_entry.expected_entry_time == datetime(
        2026, 9, 30, 9, 53, tzinfo=IST
    )
    names = [event["event_type"] for event in events(observations)]
    assert "RAW_NIFTY_TICK" in names
    assert "COMPLETED_CANDLE" in names
    assert "CONFIRMED" in names
    assert "CONTRACT_SELECTED" in names
    assert "OPTION_SUBSCRIBED" in names


def test_shadow_entry_target_and_telemetry_complete_without_orders(tmp_path):
    market, observations = make_market(tmp_path)
    market.connect_strategy2_live_shadow()
    ticker = FakeTicker.instance
    ticker.emit(
        {
            "instrument_token": 101,
            "last_price": 25000.0,
            "exchange_timestamp": datetime(2026, 9, 30, 9, 52, 10, tzinfo=IST),
        },
        {
            "instrument_token": 101,
            "last_price": 25010.0,
            "exchange_timestamp": datetime(2026, 9, 30, 9, 53, 1, tzinfo=IST),
        },
    )
    ticker.emit({
        "instrument_token": 202,
        "last_price": 100.0,
        "exchange_timestamp": datetime(2026, 9, 30, 9, 53, 2, tzinfo=IST),
    })
    ticker.emit({
        "instrument_token": 202,
        "last_price": 135.0,
        "exchange_timestamp": datetime(2026, 9, 30, 9, 54, tzinfo=IST),
    })

    assert market.strategy2_theoretical_entry["theoretical_entry_price"] == 100.0
    assert market.strategy2_shadow_exit["exit_reason"] == "TARGET"
    assert market.strategy2_engine.close_calls == 1
    assert FakeKite.order_calls == 0
    record = market.strategy2_telemetry_store.records[0]
    assert record["entry_status"] == "CLOSED"
    assert record["exit_reason"] == "TARGET"
    names = [event["event_type"] for event in events(observations)]
    assert "THEORETICAL_ENTRY" in names
    assert "TARGET" in names
    assert "never-log-this-token" not in observations.path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("raw", "is_naive", "expected_hour"),
    [
        (datetime(2026, 9, 30, 9, 52, tzinfo=IST), False, 9),
        (datetime(2026, 9, 30, 4, 22, tzinfo=timezone.utc), False, 9),
        (datetime(2026, 9, 30, 9, 52), True, None),
    ],
)
def test_timestamp_diagnostics_and_completed_candle_alignment(
    tmp_path, raw, is_naive, expected_hour
):
    market, observations = make_market(tmp_path)
    market.connect_strategy2_live_shadow()
    ticker = FakeTicker.instance
    ticker.emit({"instrument_token": 101, "last_price": 25000.0,
                 "exchange_timestamp": raw})

    raw_event = next(
        event for event in events(observations)
        if event["event_type"] == "RAW_NIFTY_TICK"
    )
    assert raw_event["raw_timestamp_is_naive"] is is_naive
    if expected_hour is not None:
        normalized = datetime.fromisoformat(raw_event["normalized_ist_timestamp"])
        assert normalized.hour == expected_hour
        assert normalized.utcoffset() == timedelta(hours=5, minutes=30)
    if is_naive:
        assert raw_event["data_quality_flags"] == [
            "NAIVE_EXCHANGE_TIMESTAMP_HOST_LOCAL_INTERPRETATION"
        ]

    second = raw + timedelta(minutes=1)
    ticker.emit({"instrument_token": 101, "last_price": 25001.0,
                 "exchange_timestamp": second})
    candle_event = next(
        event for event in events(observations)
        if event["event_type"] == "COMPLETED_CANDLE"
    )
    candle_time = datetime.fromisoformat(
        candle_event["completed_candle_timestamp"]
    )
    assert candle_time.second == 0
    assert candle_time.microsecond == 0


def test_disconnect_records_close_on_ticker(tmp_path):
    market, _ = make_market(tmp_path)
    market.connect_strategy2_live_shadow()
    market.disconnect_strategy2_live_shadow()
    assert FakeTicker.instance.closed is True
