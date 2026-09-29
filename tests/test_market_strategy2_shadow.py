from datetime import datetime
from zoneinfo import ZoneInfo

from trading.candle import Candle
from trading.market import MarketData
from trading.strategy import (
    IndicatorSnapshot,
    SignalAction,
    StrategyResult,
)
from trading.strategy2_pending_entry import Strategy2PendingEntryManager


IST = ZoneInfo("Asia/Kolkata")


class FakeStrategy2Engine:
    def __init__(self, result):
        self.result = result
        self.active_position = None

    def evaluate(self, snapshot, option_premiums=None):
        return self.result


class FakeInstruments:
    def __init__(self):
        self.calls = []

    def get_nifty_strategy2_contract(
        self,
        spot_price,
        option_type,
        as_of=None,
    ):
        self.calls.append(
            (spot_price, option_type, as_of)
        )

        return {
            "instrument_token": (
                202 if option_type == "CE" else 203
            ),
            "tradingsymbol": (
                "TESTCE"
                if option_type == "CE"
                else "TESTPE"
            ),
            "expiry": as_of.date(),
            "atm_strike": 25000.0,
            "otm_strike": (
                25050.0
                if option_type == "CE"
                else 24950.0
            ),
            "strike": (
                25050.0
                if option_type == "CE"
                else 24950.0
            ),
            "lot_size": 65,
            "option_type": option_type,
        }


def make_snapshot():
    candle = Candle(
        datetime(
            2026,
            9,
            29,
            9,
            52,
            tzinfo=IST,
        ),
        25010.0,
        25030.0,
        25000.0,
        25024.0,
    )

    return IndicatorSnapshot(
        candle=candle,
        values={
            "ema": {
                9: 25020.0,
                20: 25010.0,
            }
        },
    )


def make_result(action):
    return StrategyResult(
        strategy_name="EXP-STRAT-02-FROZEN-V1",
        action=action,
        candle_time=datetime(
            2026,
            9,
            29,
            9,
            52,
            tzinfo=IST,
        ),
        reason="test",
    )


def make_market(action):
    market = MarketData.__new__(MarketData)

    market.strategy2_engine = (
        FakeStrategy2Engine(
            make_result(action)
        )
    )

    market.strategy2_pending_entry_manager = (
        Strategy2PendingEntryManager()
    )

    market.latest_strategy2_result = None
    market.latest_option_premiums = {}
    market.instruments = FakeInstruments()

    market.strategy2_option_tokens = {}
    market.strategy2_pending_contract = None
    market.strategy2_theoretical_entry = None
    market.strategy2_theoretical_entry_error = None

    return market


def test_strategy2_shadow_hold_creates_no_pending_entry():
    market = make_market(SignalAction.HOLD)

    result = market._process_strategy2_shadow(
        make_snapshot()
    )

    assert result.action is SignalAction.HOLD
    assert (
        market.strategy2_pending_entry_manager.pending_entry
        is None
    )
    assert market.instruments.calls == []


def test_strategy2_shadow_ce_creates_pending_entry():
    market = make_market(SignalAction.BUY_CE)
    snapshot = make_snapshot()

    result = market._process_strategy2_shadow(
        snapshot
    )

    pending = (
        market.strategy2_pending_entry_manager.pending_entry
    )

    assert result.action is SignalAction.BUY_CE
    assert pending.direction == "CE"
    assert pending.confirmation_time == snapshot.candle.time

    assert pending.expected_entry_time == datetime(
        2026,
        9,
        29,
        9,
        53,
        tzinfo=IST,
    )

    assert pending.contract_symbol == "TESTCE"
    assert pending.instrument_token == 202

    assert market.instruments.calls == [
        (
            25024.0,
            "CE",
            snapshot.candle.time,
        )
    ]


def test_strategy2_shadow_existing_pending_is_not_replaced():
    market = make_market(SignalAction.BUY_CE)
    snapshot = make_snapshot()

    market._process_strategy2_shadow(
        snapshot
    )

    first = (
        market.strategy2_pending_entry_manager.pending_entry
    )

    market.strategy2_engine.result = (
        make_result(SignalAction.BUY_PE)
    )

    market._process_strategy2_shadow(
        snapshot
    )

    assert (
        market.strategy2_pending_entry_manager.pending_entry
        is first
    )

    assert len(market.instruments.calls) == 1
