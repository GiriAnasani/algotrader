from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading.candle import Candle
from trading.market import MarketData
from trading.paper_position import PositionStatus
from trading.strategy import IndicatorSnapshot, SignalAction, StrategyResult


IST = ZoneInfo("Asia/Kolkata")
TIME = datetime(2026, 8, 19, 9, 15, tzinfo=IST)


def make_market():
    market = MarketData(kite=None, instruments=None)
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY2681925000CE", "lot_size": 75},
        "PE": {"tradingsymbol": "NIFTY2681925000PE", "lot_size": 75},
    }
    market.latest_option_premiums = {"CE": 27.0, "PE": 31.0}
    return market


def result(action, actions=None):
    return StrategyResult(
        strategy_name="test",
        action=action,
        candle_time=TIME,
        actions=actions or (action,),
    )


def set_strategy_position(market, side, premium):
    market.strategy_engine.active_position = side
    market.strategy_engine.entry_premium = premium


@pytest.mark.parametrize(
    ("action", "side", "symbol", "premium"),
    [
        (SignalAction.BUY_CE, "CE", "NIFTY2681925000CE", 27.0),
        (SignalAction.BUY_PE, "PE", "NIFTY2681925000PE", 31.0),
    ],
)
def test_market_routes_buy_to_one_long_lived_paper_engine(
    action,
    side,
    symbol,
    premium,
):
    market = make_market()
    paper_engine = market.paper_execution_engine
    set_strategy_position(market, side, premium)

    positions = market._execute_strategy_result(result(action), TIME)

    assert market.paper_execution_engine is paper_engine
    assert len(positions) == 1
    assert paper_engine.active_position.contract_symbol == symbol
    assert paper_engine.active_position.side == side
    assert paper_engine.active_position.quantity == 75
    assert paper_engine.active_position.entry_price == premium
    assert paper_engine.active_position.entry_time.tzinfo is not None
    assert paper_engine.active_position.status is PositionStatus.OPEN


def test_hold_causes_zero_paper_executions():
    market = make_market()

    assert market._execute_strategy_result(result(SignalAction.HOLD), TIME) == ()
    assert market.paper_execution_engine.active_position is None


@pytest.mark.parametrize(
    ("buy_action", "exit_action", "side", "premium"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_CE, "CE", 27.0),
        (SignalAction.BUY_PE, SignalAction.EXIT_PE, "PE", 31.0),
    ],
)
def test_market_routes_exit_to_active_paper_position(
    buy_action,
    exit_action,
    side,
    premium,
):
    market = make_market()
    set_strategy_position(market, side, premium)
    market._execute_strategy_result(result(buy_action), TIME)
    market.latest_option_premiums[side] = premium + 5
    set_strategy_position(market, None, None)

    closed = market._execute_strategy_result(
        result(exit_action),
        TIME + timedelta(minutes=1),
    )[0]

    assert closed.status is PositionStatus.CLOSED
    assert market.paper_execution_engine.active_position is None


@pytest.mark.parametrize(
    ("entry_action", "exit_action", "entry_side", "next_action", "next_side"),
    [
        (
            SignalAction.BUY_CE,
            SignalAction.EXIT_CE,
            "CE",
            SignalAction.BUY_PE,
            "PE",
        ),
        (
            SignalAction.BUY_PE,
            SignalAction.EXIT_PE,
            "PE",
            SignalAction.BUY_CE,
            "CE",
        ),
    ],
)
def test_reversal_executes_exit_then_buy_in_order(
    entry_action,
    exit_action,
    entry_side,
    next_action,
    next_side,
):
    market = make_market()
    set_strategy_position(market, entry_side, market.latest_option_premiums[entry_side])
    market._execute_strategy_result(result(entry_action), TIME)
    set_strategy_position(market, next_side, market.latest_option_premiums[next_side])

    positions = market._execute_strategy_result(
        result(exit_action, (exit_action, next_action)),
        TIME + timedelta(minutes=1),
    )

    assert [position.status for position in positions] == [
        PositionStatus.CLOSED,
        PositionStatus.OPEN,
    ]
    assert market.paper_execution_engine.active_position.side == next_side
    assert market.strategy_engine.active_position == next_side


def test_target_generated_exit_closes_paper_position():
    market = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    market.latest_completed_snapshot = IndicatorSnapshot(
        candle=Candle(TIME, 100, 100, 100, 100),
        values={"ema": {}},
    )
    market.latest_option_premiums["CE"] = 32.0

    strategy_result = market._monitor_live_option_target(
        TIME + timedelta(minutes=1)
    )

    assert strategy_result.action is SignalAction.EXIT_CE
    assert market.strategy_engine.active_position is None
    assert market.paper_execution_engine.active_position is None


def test_target_exit_uses_fallback_when_option_tick_has_no_timestamp():
    market = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    market.latest_completed_snapshot = IndicatorSnapshot(
        candle=Candle(TIME, 100, 100, 100, 100),
        values={"ema": {}},
    )
    market.option_tokens = {101: "CE"}
    market.latest_option_premiums["CE"] = 32.0

    market._handle_option_tick({"instrument_token": 101, "last_price": 32.0})

    assert market.strategy_engine.active_position is None
    assert market.paper_execution_engine.active_position is None


def test_missing_buy_premium_cannot_create_paper_position():
    market = make_market()
    set_strategy_position(market, "CE", 27.0)
    del market.latest_option_premiums["CE"]

    with pytest.raises(ValueError, match="Premium"):
        market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert market.paper_execution_engine.active_position is None


def test_inconsistent_strategy_and_paper_state_raises_clearly():
    market = make_market()
    set_strategy_position(market, "CE", 27.0)
    market.paper_execution_engine.execute(
        SignalAction.BUY_PE,
        contract_symbol="NIFTY2681925000PE",
        premium=31.0,
        quantity=75,
        execution_time=TIME,
    )

    with pytest.raises(RuntimeError, match="inconsistent"):
        market._validate_paper_state_consistency()


def test_historical_warmup_causes_zero_paper_executions():
    market = make_market()
    history = pd.DataFrame(
        [
            {
                "date": TIME + timedelta(minutes=minute),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + minute,
                "volume": 0,
            }
            for minute in range(3)
        ]
    )

    market.warm_indicators_from_dataframe(history)

    assert market.paper_execution_engine.active_position is None


def test_duplicate_completed_candle_is_not_processed_twice():
    market = make_market()
    candle = Candle(TIME, 100, 100, 100, 100)

    assert market._process_completed_candle(candle)["processed"] is True
    assert market._process_completed_candle(candle)["processed"] is False
    assert market.paper_execution_engine.active_position is None
