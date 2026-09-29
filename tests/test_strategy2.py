from datetime import datetime
from zoneinfo import ZoneInfo

from trading.candle import Candle
from trading.strategy import IndicatorSnapshot, SignalAction
from trading.strategy2 import (
    ENTRY_END,
    ENTRY_START,
    FrozenStrategy2Engine,
    STRATEGY_ID,
)

IST = ZoneInfo("Asia/Kolkata")


def snapshot(minute, o, h, l, c, e9, e20):
    return IndicatorSnapshot(
        Candle(datetime(2026, 9, 29, 9, minute, tzinfo=IST), o, h, l, c),
        {"ema": {9: e9, 20: e20}},
    )


def test_frozen_identity_and_window():
    assert STRATEGY_ID == "EXP-STRAT-02-FROZEN-V1"
    assert ENTRY_START.hour == 9 and ENTRY_START.minute == 45
    assert ENTRY_END.hour == 9 and ENTRY_END.minute == 59


def test_active_position_blocks_new_signal():
    engine = FrozenStrategy2Engine()
    engine.set_position_active("CE")
    result = engine.evaluate(snapshot(50, 100, 102, 99, 101, 100, 99))
    assert result.action is SignalAction.HOLD
    assert "already active" in result.reason


def test_position_side_validation():
    engine = FrozenStrategy2Engine()
    try:
        engine.set_position_active("ATM")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid Strategy 2 side must be rejected")


def test_missing_ema_holds():
    engine = FrozenStrategy2Engine()
    s = IndicatorSnapshot(
        Candle(datetime(2026, 9, 29, 9, 45, tzinfo=IST), 100, 101, 99, 100),
        {"ema": {20: 99}},
    )
    result = engine.evaluate(s)
    assert result.action is SignalAction.HOLD
