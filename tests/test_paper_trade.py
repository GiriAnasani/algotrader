from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.paper_trade import PaperTrade
from trading.strategy import SignalAction


TIME = datetime(2026, 8, 19, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_trade(**overrides):
    values = {
        "contract_symbol": "NIFTY2681925000CE",
        "side": "CE",
        "quantity": 75,
        "entry_price": 27.0,
        "entry_time": TIME,
        "exit_price": 32.0,
        "exit_time": TIME + timedelta(minutes=1),
        "exit_action": SignalAction.EXIT_CE,
        "strategy_name": "nifty_ema10_crossover",
        "exit_reason": "CE target reached.",
    }
    values.update(overrides)
    return PaperTrade(**values)


@pytest.mark.parametrize(
    ("side", "exit_action"),
    [("CE", SignalAction.EXIT_CE), ("PE", SignalAction.EXIT_PE)],
)
def test_valid_paper_trade_for_each_option_side(side, exit_action):
    trade = make_trade(side=side, exit_action=exit_action)

    assert trade.side == side
    assert trade.exit_action is exit_action


def test_paper_trade_is_immutable():
    trade = make_trade()

    with pytest.raises(FrozenInstanceError):
        trade.exit_price = 33.0


@pytest.mark.parametrize("contract_symbol", ["", "   ", None, 123])
def test_contract_symbol_must_be_non_empty(contract_symbol):
    with pytest.raises(ValueError):
        make_trade(contract_symbol=contract_symbol)


@pytest.mark.parametrize("side", ["CALL", "ce", "", None])
def test_side_must_be_ce_or_pe(side):
    with pytest.raises(ValueError):
        make_trade(side=side)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "75"])
def test_quantity_must_be_a_positive_integer(quantity):
    with pytest.raises(ValueError):
        make_trade(quantity=quantity)


@pytest.mark.parametrize("field", ["entry_price", "exit_price"])
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "27"])
def test_prices_must_be_positive_finite_numbers(field, value):
    with pytest.raises(ValueError):
        make_trade(**{field: value})


@pytest.mark.parametrize("field", ["entry_time", "exit_time"])
def test_times_must_be_timezone_aware(field):
    with pytest.raises(ValueError):
        make_trade(**{field: datetime(2026, 8, 19, 9, 15)})


def test_exit_time_cannot_precede_entry_time():
    with pytest.raises(ValueError, match="earlier"):
        make_trade(exit_time=TIME - timedelta(minutes=1))


@pytest.mark.parametrize(
    ("side", "exit_action"),
    [("CE", SignalAction.EXIT_PE), ("PE", SignalAction.EXIT_CE)],
)
def test_exit_action_must_match_trade_side(side, exit_action):
    with pytest.raises(ValueError, match="match"):
        make_trade(side=side, exit_action=exit_action)


@pytest.mark.parametrize("exit_action", ["EXIT_CE", None, SignalAction.HOLD])
def test_exit_action_must_be_matching_explicit_exit_signal(exit_action):
    with pytest.raises((TypeError, ValueError)):
        make_trade(exit_action=exit_action)


@pytest.mark.parametrize("strategy_name", ["", "   ", None, 123])
def test_strategy_name_must_be_non_empty(strategy_name):
    with pytest.raises(ValueError):
        make_trade(strategy_name=strategy_name)
