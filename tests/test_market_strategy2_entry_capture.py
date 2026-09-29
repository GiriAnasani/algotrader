from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.candle import Candle
from trading.market import MarketData
from trading.strategy import IndicatorSnapshot, SignalAction, StrategyResult
from trading.strategy2_pending_entry import Strategy2PendingEntryManager


IST = ZoneInfo("Asia/Kolkata")


class FakeTicker:
    MODE_FULL = "full"

    def __init__(self):
        self.subscribed = []
        self.modes = []

    def subscribe(self, tokens):
        self.subscribed.append(list(tokens))

    def set_mode(self, mode, tokens):
        self.modes.append((mode, list(tokens)))


class FakeStrategy2Engine:
    def __init__(self, action=SignalAction.BUY_CE):
        self.action = action
        self.active_position = None

    def evaluate(self, snapshot, option_premiums=None):
        return StrategyResult(
            strategy_name="EXP-STRAT-02-FROZEN-V1",
            action=self.action,
            candle_time=snapshot.candle.time,
            reason="test",
        )

    def set_position_active(self, side):
        self.active_position = side


class FakeInstruments:
    def get_nifty_strategy2_contract(self, spot_price, option_type, as_of=None):
        return {
            "instrument_token": 202,
            "tradingsymbol": "TESTCE",
            "expiry": as_of.date(),
            "atm_strike": 25000.0,
            "otm_strike": 25050.0,
            "strike": 25050.0,
            "lot_size": 65,
            "option_type": option_type,
        }


def snapshot():
    candle = Candle(
        datetime(2026, 9, 29, 9, 52, tzinfo=IST),
        25010.0,
        25030.0,
        25000.0,
        25024.0,
    )
    return IndicatorSnapshot(
        candle=candle,
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )


def market():
    m = MarketData.__new__(MarketData)
    m.strategy2_engine = FakeStrategy2Engine()
    m.strategy2_pending_entry_manager = Strategy2PendingEntryManager()
    m.latest_strategy2_result = None
    m.latest_option_premiums = {}
    m.instruments = FakeInstruments()
    m._live_ticker = FakeTicker()
    m.strategy2_option_tokens = {}
    m.strategy2_pending_contract = None
    m.strategy2_theoretical_entry = None
    m.strategy2_theoretical_entry_error = None
    return m


def test_strategy2_confirmation_subscribes_selected_otm_token():
    m = market()
    m._process_strategy2_shadow(snapshot())

    assert m._live_ticker.subscribed == [[202]]
    assert m._live_ticker.modes == [("full", [202])]
    assert m.strategy2_option_tokens == {202: "CE"}
    assert m.strategy2_pending_contract["tradingsymbol"] == "TESTCE"


def test_strategy2_tick_before_expected_minute_is_ignored():
    m = market()
    m._process_strategy2_shadow(snapshot())

    handled = m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 100.0,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 52, 59, tzinfo=IST
            ),
        }
    )

    assert handled is True
    assert m.strategy2_theoretical_entry is None
    assert m.strategy2_pending_entry_manager.pending_entry is not None
    assert m.strategy2_engine.active_position is None


def test_strategy2_first_valid_tick_in_expected_minute_is_captured_once():
    m = market()
    m._process_strategy2_shadow(snapshot())

    first_time = datetime(2026, 9, 29, 9, 53, 1, tzinfo=IST)

    m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 101.5,
            "exchange_timestamp": first_time,
        }
    )

    entry = m.strategy2_theoretical_entry

    assert entry["direction"] == "CE"
    assert entry["contract_symbol"] == "TESTCE"
    assert entry["instrument_token"] == 202
    assert entry["theoretical_entry_time"] == first_time
    assert entry["theoretical_entry_price"] == 101.5
    assert m.strategy2_pending_entry_manager.pending_entry is None
    assert m.strategy2_engine.active_position == "CE"

    m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 105.0,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 53, 20, tzinfo=IST
            ),
        }
    )

    assert m.strategy2_theoretical_entry is entry
    assert m.strategy2_theoretical_entry["theoretical_entry_price"] == 101.5


def test_strategy2_wrong_token_is_ignored():
    m = market()
    m._process_strategy2_shadow(snapshot())

    handled = m._handle_strategy2_option_tick(
        {
            "instrument_token": 999,
            "last_price": 101.5,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 53, 1, tzinfo=IST
            ),
        }
    )

    assert handled is False
    assert m.strategy2_theoretical_entry is None


def test_strategy2_registered_token_without_pending_entry_is_not_consumed():
    m = market()
    m.strategy2_option_tokens[202] = "CE"

    handled = m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 101.5,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 53, 1, tzinfo=IST
            ),
        }
    )

    assert handled is False


def test_strategy2_captured_token_does_not_intercept_later_ticks():
    m = market()
    m._process_strategy2_shadow(snapshot())

    tick = {
        "instrument_token": 202,
        "last_price": 101.5,
        "exchange_timestamp": datetime(
            2026, 9, 29, 9, 53, 1, tzinfo=IST
        ),
    }

    assert m._handle_strategy2_option_tick(tick) is True
    assert 202 not in m.strategy2_option_tokens
    assert m._handle_strategy2_option_tick(tick) is False


@pytest.mark.parametrize(
    "premium",
    [None, True, False, "100", float("nan"), float("inf"), float("-inf"), 0, -1],
)
def test_strategy2_invalid_premium_does_not_create_entry(premium):
    m = market()
    m._process_strategy2_shadow(snapshot())

    handled = m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": premium,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 53, 1, tzinfo=IST
            ),
        }
    )

    assert handled is True
    assert m.strategy2_theoretical_entry is None
    assert m.strategy2_pending_entry_manager.pending_entry is not None
    assert m.strategy2_engine.active_position is None


def test_strategy2_late_tick_does_not_become_entry():
    m = market()
    m._process_strategy2_shadow(snapshot())

    handled = m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 110.0,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 54, 0, tzinfo=IST
            ),
        }
    )

    assert handled is True
    assert m.strategy2_theoretical_entry is None
    assert (
        m.strategy2_theoretical_entry_error
        == "MISSED_EXPECTED_ENTRY_MINUTE"
    )
    assert m.strategy2_pending_entry_manager.pending_entry is None
    assert m.strategy2_engine.active_position is None
    assert 202 not in m.strategy2_option_tokens

    assert m._handle_strategy2_option_tick(
        {
            "instrument_token": 202,
            "last_price": 111.0,
            "exchange_timestamp": datetime(
                2026, 9, 29, 9, 54, 1, tzinfo=IST
            ),
        }
    ) is False
