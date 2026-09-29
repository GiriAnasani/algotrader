"""Frozen NIFTY expiry-day Strategy 2 signal engine.

Implements EXP-STRAT-02-FROZEN-V1 signal detection only. Execution, contract
selection, next-minute option entry capture, and order telemetry remain the
responsibility of the market/execution integration layer.
"""

from collections import deque
from dataclasses import dataclass
from datetime import timedelta, time
from enum import Enum
from statistics import median

from trading.strategy import IndicatorSnapshot, SignalAction, StrategyResult


STRATEGY_ID = "EXP-STRAT-02-FROZEN-V1"
ENTRY_START = time(9, 45)
ENTRY_END = time(9, 59)
EMA_SEPARATION = 0.0003
PULLBACK_EMA9_TOLERANCE = 0.0003
PULLBACK_EMA20_BREACH = 0.0008
PULLBACK_WINDOW_MINUTES = 10


class SetupState(Enum):
    IDLE = "IDLE"
    TREND_QUALIFIED = "TREND_QUALIFIED"
    PULLBACK_DETECTED = "PULLBACK_DETECTED"


@dataclass(frozen=True)
class Strategy2Signal:
    direction: str
    confirmation_time: object


class FrozenStrategy2Engine:
    """Causal completed-candle engine for the frozen Atlas Strategy 2 rules."""

    def __init__(self):
        self.state = SetupState.IDLE
        self.direction = None
        self.qualified_at = None
        self.pullback_at = None
        self._candles = deque(maxlen=7)
        self._previous_ema9 = None
        self._previous_ema20 = None
        self.active_position = None
        self.last_consumed_confirmation = None

    def set_position_active(self, side):
        if side not in ("CE", "PE"):
            raise ValueError("Strategy 2 active side must be CE or PE.")
        self.active_position = side
        self._reset_setup()

    def set_position_closed(self):
        self.active_position = None
        self._reset_setup()

    def evaluate(self, snapshot: IndicatorSnapshot) -> StrategyResult:
        if not isinstance(snapshot, IndicatorSnapshot):
            raise TypeError("Expected IndicatorSnapshot object.")

        ema = snapshot.values.get("ema", {})
        ema9 = ema.get(9)
        ema20 = ema.get(20)
        candle = snapshot.candle

        if ema9 is None or ema20 is None:
            self._remember(candle, ema9, ema20)
            return self._hold(snapshot, "EMA 9/20 is not available.")

        previous_candles = list(self._candles)
        previous_ema9 = self._previous_ema9
        previous_ema20 = self._previous_ema20

        if self.active_position is not None:
            self._remember(candle, ema9, ema20)
            return self._hold(snapshot, "Strategy 2 position is already active.")

        if self.state is not SetupState.IDLE and self.qualified_at is not None:
            if candle.time > self.qualified_at + timedelta(minutes=PULLBACK_WINDOW_MINUTES):
                self._reset_setup()

        if self.state is SetupState.IDLE and len(previous_candles) >= 6:
            if self._bullish_qualified(candle, ema9, ema20, previous_ema9, previous_ema20, previous_candles):
                self.state = SetupState.TREND_QUALIFIED
                self.direction = "CE"
                self.qualified_at = candle.time
            elif self._bearish_qualified(candle, ema9, ema20, previous_ema9, previous_ema20, previous_candles):
                self.state = SetupState.TREND_QUALIFIED
                self.direction = "PE"
                self.qualified_at = candle.time

        if self.state is SetupState.TREND_QUALIFIED:
            if self.direction == "CE" and self._bullish_pullback(candle, ema9, ema20):
                self.state = SetupState.PULLBACK_DETECTED
                self.pullback_at = candle.time
            elif self.direction == "PE" and self._bearish_pullback(candle, ema9, ema20):
                self.state = SetupState.PULLBACK_DETECTED
                self.pullback_at = candle.time

        result = None
        if self.state is SetupState.PULLBACK_DETECTED and previous_candles:
            local_time = candle.time.timetz().replace(tzinfo=None)
            in_entry_window = ENTRY_START <= local_time <= ENTRY_END
            if in_entry_window and candle.time != self.pullback_at:
                if self.direction == "CE" and self._bullish_confirmation(candle, ema9, ema20, previous_candles):
                    result = self._signal(snapshot, SignalAction.BUY_CE, "Bullish Strategy 2 continuation confirmed.")
                elif self.direction == "PE" and self._bearish_confirmation(candle, ema9, ema20, previous_candles):
                    result = self._signal(snapshot, SignalAction.BUY_PE, "Bearish Strategy 2 continuation confirmed.")

        self._remember(candle, ema9, ema20)

        if result is not None:
            self.last_consumed_confirmation = candle.time
            self._reset_setup()
            return result

        return self._hold(snapshot, f"Strategy 2 state: {self.state.value}.")

    def _bullish_qualified(self, c, e9, e20, p9, p20, prev):
        if p9 is None or p20 is None:
            return False
        last5 = prev[-5:]
        changes = [prev[i].close - prev[i - 1].close for i in range(len(prev) - 4, len(prev))]
        return (
            e9 > e20
            and e9 > p9
            and e20 >= p20
            and c.close > e9
            and (e9 - e20) / c.close >= EMA_SEPARATION
            and c.close > min(x.close for x in last5)
            and sum(x > 0 for x in changes) >= 3
        )

    def _bearish_qualified(self, c, e9, e20, p9, p20, prev):
        if p9 is None or p20 is None:
            return False
        last5 = prev[-5:]
        changes = [prev[i].close - prev[i - 1].close for i in range(len(prev) - 4, len(prev))]
        return (
            e9 < e20
            and e9 < p9
            and e20 <= p20
            and c.close < e9
            and (e20 - e9) / c.close >= EMA_SEPARATION
            and c.close < max(x.close for x in last5)
            and sum(x < 0 for x in changes) >= 3
        )

    @staticmethod
    def _bullish_pullback(c, e9, e20):
        touches_e9 = c.low <= e9 * (1 + PULLBACK_EMA9_TOLERANCE)
        not_too_deep = c.low >= e20 * (1 - PULLBACK_EMA20_BREACH)
        return touches_e9 and c.close > e20 and not_too_deep and e9 > e20

    @staticmethod
    def _bearish_pullback(c, e9, e20):
        touches_e9 = c.high >= e9 * (1 - PULLBACK_EMA9_TOLERANCE)
        not_too_deep = c.high <= e20 * (1 + PULLBACK_EMA20_BREACH)
        return touches_e9 and c.close < e20 and not_too_deep and e9 < e20

    @staticmethod
    def _bullish_confirmation(c, e9, e20, prev):
        if c.high == c.low:
            return False
        bodies = [abs(x.close - x.open) for x in prev[-5:]]
        return (
            c.close > prev[-1].high
            and c.close > c.open
            and c.close > e9
            and e9 > e20
            and abs(c.close - c.open) >= median(bodies)
            and (c.close - c.low) / (c.high - c.low) >= 0.60
        )

    @staticmethod
    def _bearish_confirmation(c, e9, e20, prev):
        if c.high == c.low:
            return False
        bodies = [abs(x.close - x.open) for x in prev[-5:]]
        return (
            c.close < prev[-1].low
            and c.close < c.open
            and c.close < e9
            and e9 < e20
            and abs(c.close - c.open) >= median(bodies)
            and (c.close - c.low) / (c.high - c.low) <= 0.40
        )

    def _remember(self, candle, ema9, ema20):
        self._candles.append(candle)
        if ema9 is not None:
            self._previous_ema9 = ema9
        if ema20 is not None:
            self._previous_ema20 = ema20

    def _reset_setup(self):
        self.state = SetupState.IDLE
        self.direction = None
        self.qualified_at = None
        self.pullback_at = None

    @staticmethod
    def _hold(snapshot, reason):
        return StrategyResult(
            strategy_name=STRATEGY_ID,
            action=SignalAction.HOLD,
            candle_time=snapshot.candle.time,
            reason=reason,
        )

    @staticmethod
    def _signal(snapshot, action, reason):
        return StrategyResult(
            strategy_name=STRATEGY_ID,
            action=action,
            candle_time=snapshot.candle.time,
            reason=reason,
            metadata={"next_minute_entry_required": True},
        )
