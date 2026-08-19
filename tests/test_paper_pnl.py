from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.paper_pnl import PaperTradePnL, calculate_trade_pnl
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
    }
    values.update(overrides)
    return PaperTrade(**values)


@pytest.mark.parametrize(
    ("side", "exit_action", "entry_price", "exit_price", "expected_points"),
    [
        ("CE", SignalAction.EXIT_CE, 27.0, 32.0, 5.0),
        ("CE", SignalAction.EXIT_CE, 20.0, 17.0, -3.0),
        ("CE", SignalAction.EXIT_CE, 20.0, 20.0, 0.0),
        ("PE", SignalAction.EXIT_PE, 27.0, 32.0, 5.0),
        ("PE", SignalAction.EXIT_PE, 20.0, 17.0, -3.0),
        ("PE", SignalAction.EXIT_PE, 20.0, 20.0, 0.0),
    ],
)
def test_calculates_realized_pnl_for_long_option_trades(
    side,
    exit_action,
    entry_price,
    exit_price,
    expected_points,
):
    pnl = calculate_trade_pnl(
        make_trade(
            side=side,
            exit_action=exit_action,
            entry_price=entry_price,
            exit_price=exit_price,
        )
    )

    assert pnl.points_pnl == expected_points
    assert pnl.gross_pnl == expected_points * 75
    assert isinstance(pnl.points_pnl, float)
    assert isinstance(pnl.gross_pnl, float)


def test_pnl_respects_quantity_and_decimal_premiums():
    pnl = calculate_trade_pnl(
        make_trade(entry_price=27.25, exit_price=32.75, quantity=40)
    )

    assert pnl.points_pnl == 5.5
    assert pnl.gross_pnl == 220.0


def test_pnl_rejects_non_paper_trade_input():
    with pytest.raises(TypeError, match="PaperTrade"):
        calculate_trade_pnl({})


def test_pnl_calculation_does_not_mutate_trade():
    trade = make_trade()

    calculate_trade_pnl(trade)

    assert trade.entry_price == 27.0
    assert trade.exit_price == 32.0


def test_pnl_result_is_immutable():
    pnl = calculate_trade_pnl(make_trade())

    with pytest.raises(FrozenInstanceError):
        pnl.gross_pnl = 0.0

    assert isinstance(pnl, PaperTradePnL)
