from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading.candle import Candle
from trading.market import MarketData
from trading.strategy import (
    IndicatorSnapshot,
    SignalAction,
    StrategyEngine,
)


IST = ZoneInfo("Asia/Kolkata")
START = datetime(2026, 8, 18, 9, 15, tzinfo=IST)


def make_snapshot(
    minute=0,
    close=100.0,
    ema10=100.0,
    include_ema=True
):
    values = {"ema": {10: ema10}} if include_ema else {"ema": {}}

    return IndicatorSnapshot(
        candle=Candle(
            time=START + timedelta(minutes=minute),
            open=close,
            high=close,
            low=close,
            close=close,
        ),
        values=values,
    )


def enter_ce(engine, premium=100.0):
    engine.previous_close = 100.0
    engine.previous_ema10 = 100.0

    result = engine.evaluate(
        make_snapshot(close=101.0),
        {"CE": premium, "PE": 80.0},
    )

    assert result.action is SignalAction.BUY_CE


def enter_pe(engine, premium=100.0):
    engine.previous_close = 100.0
    engine.previous_ema10 = 100.0

    result = engine.evaluate(
        make_snapshot(close=99.0),
        {"CE": 80.0, "PE": premium},
    )

    assert result.action is SignalAction.BUY_PE


def test_bullish_crossover_enters_ce_with_ce_premium():
    engine = StrategyEngine()

    enter_ce(engine, premium=27.0)

    assert engine.active_position == "CE"
    assert engine.entry_premium == 27.0


def test_bearish_crossover_enters_pe_with_pe_premium():
    engine = StrategyEngine()

    enter_pe(engine, premium=31.0)

    assert engine.active_position == "PE"
    assert engine.entry_premium == 31.0


def test_no_crossover_holds():
    engine = StrategyEngine()
    engine.previous_close = 101.0
    engine.previous_ema10 = 100.0

    result = engine.evaluate(
        make_snapshot(close=102.0),
        {"CE": 20.0, "PE": 20.0},
    )

    assert result.action is SignalAction.HOLD
    assert engine.active_position is None


def test_first_evaluation_holds_without_previous_candle():
    engine = StrategyEngine()

    result = engine.evaluate(
        make_snapshot(close=101.0),
        {"CE": 20.0, "PE": 20.0},
    )

    assert result.action is SignalAction.HOLD
    assert result.reason == "Previous EMA 10 candle is not available."


def test_missing_ema10_holds():
    engine = StrategyEngine()

    result = engine.evaluate(
        make_snapshot(close=101.0, include_ema=False),
        {"CE": 20.0, "PE": 20.0},
    )

    assert result.action is SignalAction.HOLD
    assert result.reason == "EMA 10 is not available."


@pytest.mark.parametrize(
    ("close", "premiums", "expected_reason"),
    [
        (101.0, {"PE": 20.0}, "CE premium is not available."),
        (99.0, {"CE": 20.0}, "PE premium is not available."),
    ],
)
def test_entry_requires_matching_option_premium(
    close,
    premiums,
    expected_reason,
):
    engine = StrategyEngine()
    engine.previous_close = 100.0
    engine.previous_ema10 = 100.0

    result = engine.evaluate(make_snapshot(close=close), premiums)

    assert result.action is SignalAction.HOLD
    assert result.reason == expected_reason
    assert engine.active_position is None
    assert engine.entry_premium is None


def test_ce_remains_active_above_ema10_without_duplicate_entry():
    engine = StrategyEngine()
    enter_ce(engine)

    result = engine.evaluate(
        make_snapshot(minute=1, close=102.0),
        {"CE": 101.0, "PE": 80.0},
    )

    assert result.action is SignalAction.HOLD
    assert engine.active_position == "CE"
    assert engine.entry_premium == 100.0


def test_pe_remains_active_below_ema10_without_duplicate_entry():
    engine = StrategyEngine()
    enter_pe(engine)

    result = engine.evaluate(
        make_snapshot(minute=1, close=98.0),
        {"CE": 80.0, "PE": 101.0},
    )

    assert result.action is SignalAction.HOLD
    assert engine.active_position == "PE"
    assert engine.entry_premium == 100.0


def test_ce_reversal_exits_and_enters_pe():
    engine = StrategyEngine()
    enter_ce(engine)

    result = engine.evaluate(
        make_snapshot(minute=1, close=99.0),
        {"CE": 101.0, "PE": 70.0},
    )

    assert result.actions == (SignalAction.EXIT_CE, SignalAction.BUY_PE)
    assert engine.active_position == "PE"
    assert engine.entry_premium == 70.0


def test_pe_reversal_exits_and_enters_ce():
    engine = StrategyEngine()
    enter_pe(engine)

    result = engine.evaluate(
        make_snapshot(minute=1, close=101.0),
        {"CE": 70.0, "PE": 101.0},
    )

    assert result.actions == (SignalAction.EXIT_PE, SignalAction.BUY_CE)
    assert engine.active_position == "CE"
    assert engine.entry_premium == 70.0


@pytest.mark.parametrize(
    ("side", "entry_function", "premiums", "exit_action"),
    [
        ("CE", enter_ce, {"CE": 105.0, "PE": 500.0}, SignalAction.EXIT_CE),
        ("PE", enter_pe, {"CE": 500.0, "PE": 105.0}, SignalAction.EXIT_PE),
    ],
)
def test_live_target_exit_clears_active_position_and_entry(
    side,
    entry_function,
    premiums,
    exit_action,
):
    engine = StrategyEngine()
    entry_function(engine)

    result = engine.evaluate_live_option_target(
        make_snapshot(include_ema=False),
        premiums,
    )

    assert result.action is exit_action
    assert engine.active_position is None
    assert engine.entry_premium is None


def test_target_check_only_reads_active_option_and_never_enters():
    engine = StrategyEngine()
    enter_ce(engine)
    previous_state = (
        engine.previous_close,
        engine.previous_ema10,
    )

    result = engine.evaluate_live_option_target(
        make_snapshot(close=99.0, include_ema=False),
        {"CE": 104.0, "PE": 999.0},
    )

    assert result is None
    assert engine.active_position == "CE"
    assert engine.entry_premium == 100.0
    assert (engine.previous_close, engine.previous_ema10) == previous_state


def test_live_target_exit_never_reverses_or_creates_position():
    engine = StrategyEngine()
    enter_ce(engine)

    result = engine.evaluate_live_option_target(
        make_snapshot(close=99.0, include_ema=False),
        {"CE": 105.0, "PE": 999.0},
    )

    assert result.actions == (SignalAction.EXIT_CE,)
    assert engine.active_position is None
    assert engine.entry_premium is None


def test_after_target_exit_no_fresh_crossover_holds():
    engine = StrategyEngine()
    enter_ce(engine)
    engine.evaluate_live_option_target(
        make_snapshot(),
        {"CE": 105.0, "PE": 80.0},
    )

    result = engine.evaluate(
        make_snapshot(minute=1, close=102.0),
        {"CE": 70.0, "PE": 70.0},
    )

    assert result.action is SignalAction.HOLD
    assert engine.active_position is None


def test_after_target_exit_fresh_bullish_crossover_enters_ce():
    engine = StrategyEngine()
    enter_ce(engine)
    engine.evaluate_live_option_target(
        make_snapshot(),
        {"CE": 105.0, "PE": 80.0},
    )

    neutral = engine.evaluate(
        make_snapshot(minute=1, close=100.0),
        {"CE": 70.0, "PE": 70.0},
    )
    result = engine.evaluate(
        make_snapshot(minute=2, close=101.0),
        {"CE": 71.0, "PE": 70.0},
    )

    assert neutral.action is SignalAction.HOLD
    assert result.action is SignalAction.BUY_CE
    assert engine.active_position == "CE"
    assert engine.entry_premium == 71.0


def test_after_target_exit_fresh_bearish_crossover_enters_pe():
    engine = StrategyEngine()
    enter_ce(engine)
    engine.evaluate_live_option_target(
        make_snapshot(),
        {"CE": 105.0, "PE": 80.0},
    )

    result = engine.evaluate(
        make_snapshot(minute=1, close=99.0),
        {"CE": 70.0, "PE": 72.0},
    )

    assert result.action is SignalAction.BUY_PE
    assert engine.active_position == "PE"
    assert engine.entry_premium == 72.0


def test_active_position_is_always_one_valid_side():
    engine = StrategyEngine()
    enter_ce(engine)

    assert engine.active_position in ("CE", "PE", None)

    engine.evaluate(
        make_snapshot(minute=1, close=99.0),
        {"CE": 101.0, "PE": 70.0},
    )

    assert engine.active_position == "PE"
    assert engine.active_position != "CE"


def test_historical_warmup_does_not_evaluate_strategy():
    class FakeKite:
        pass

    class FakeInstruments:
        pass

    class RecordingStrategyEngine(StrategyEngine):
        def __init__(self):
            super().__init__()
            self.evaluation_count = 0

        def evaluate(self, *args, **kwargs):
            self.evaluation_count += 1
            return super().evaluate(*args, **kwargs)

    market = MarketData(FakeKite(), FakeInstruments())
    market.strategy_engine = RecordingStrategyEngine()
    history = pd.DataFrame(
        [
            {
                "date": START + timedelta(minutes=minute),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + minute,
                "volume": 0,
            }
            for minute in range(10)
        ]
    )

    market.warm_indicators_from_dataframe(history)

    assert market.strategy_engine.evaluation_count == 0
