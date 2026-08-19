from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.paper_ledger import PaperTradeLedger
from trading.paper_session import PaperSessionSummary, calculate_session_summary
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


def test_empty_session_returns_zero_summary():
    summary = calculate_session_summary(())

    assert summary == PaperSessionSummary(0, 0, 0, 0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("side", "exit_action", "exit_price", "expected_counts"),
    [
        ("CE", SignalAction.EXIT_CE, 32.0, (1, 0, 0)),
        ("CE", SignalAction.EXIT_CE, 22.0, (0, 1, 0)),
        ("CE", SignalAction.EXIT_CE, 27.0, (0, 0, 1)),
        ("PE", SignalAction.EXIT_PE, 32.0, (1, 0, 0)),
        ("PE", SignalAction.EXIT_PE, 22.0, (0, 1, 0)),
        ("PE", SignalAction.EXIT_PE, 27.0, (0, 0, 1)),
    ],
)
def test_single_trade_summary_classifies_ce_and_pe(
    side,
    exit_action,
    exit_price,
    expected_counts,
):
    summary = calculate_session_summary(
        [make_trade(side=side, exit_action=exit_action, exit_price=exit_price)]
    )

    assert summary.completed_trades == 1
    assert (
        summary.winning_trades,
        summary.losing_trades,
        summary.flat_trades,
    ) == expected_counts


def test_mixed_session_aggregates_pnl_with_quantities_and_decimals():
    trades = (
        make_trade(entry_price=27.25, exit_price=32.75, quantity=40),
        make_trade(entry_price=20.0, exit_price=17.0, quantity=75),
        make_trade(
            contract_symbol="NIFTY2681925000PE",
            side="PE",
            exit_action=SignalAction.EXIT_PE,
            entry_price=31.0,
            exit_price=31.0,
            quantity=50,
        ),
    )

    summary = calculate_session_summary(trades)

    assert summary.completed_trades == 3
    assert summary.winning_trades == 1
    assert summary.losing_trades == 1
    assert summary.flat_trades == 1
    assert summary.total_points_pnl == 2.5
    assert summary.total_gross_pnl == -5.0


def test_summary_does_not_mutate_trades_or_ledger():
    ledger = PaperTradeLedger()
    trade = make_trade()
    ledger.record(trade)
    before = ledger.get_trades()

    summary = calculate_session_summary(before)

    assert ledger.get_trades() == before
    assert trade.entry_price == 27.0
    assert trade.exit_price == 32.0
    with pytest.raises(FrozenInstanceError):
        summary.total_gross_pnl = 0.0
