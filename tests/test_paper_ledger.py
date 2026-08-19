from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.paper_ledger import PaperTradeLedger
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


def test_empty_ledger_behaves_cleanly():
    ledger = PaperTradeLedger()

    assert ledger.count == 0
    assert ledger.get_trades() == ()
    assert ledger.get_latest_trade() is None


def test_ledger_records_trades_in_order_without_exposing_its_list():
    ledger = PaperTradeLedger()
    first = make_trade()
    second = make_trade(
        contract_symbol="NIFTY2681925000PE",
        side="PE",
        exit_action=SignalAction.EXIT_PE,
    )

    assert ledger.record(first) is first
    ledger.record(second)
    trades = ledger.get_trades()

    assert trades == (first, second)
    assert ledger.get_latest_trade() is second
    assert ledger.count == 2
    assert not hasattr(trades, "append")


def test_ledger_rejects_non_trade_objects_and_can_be_cleared():
    ledger = PaperTradeLedger()

    with pytest.raises(TypeError, match="PaperTrade"):
        ledger.record({})

    ledger.record(make_trade())
    ledger.clear()

    assert ledger.count == 0
