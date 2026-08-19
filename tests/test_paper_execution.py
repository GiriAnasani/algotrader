from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.paper_execution import PaperExecutionEngine
from trading.paper_position import PositionStatus
from trading.strategy import SignalAction


EXECUTION_TIME = datetime(2026, 8, 19, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
EXIT_TIME = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def buy(engine, action=SignalAction.BUY_CE, **overrides):
    values = {
        "contract_symbol": "NIFTY2681925000CE",
        "premium": 125.5,
        "quantity": 75,
        "execution_time": EXECUTION_TIME,
    }
    values.update(overrides)
    return engine.execute(action, **values)


def exit(engine, action=SignalAction.EXIT_CE, **overrides):
    values = {"premium": 130.5, "execution_time": EXIT_TIME}
    values.update(overrides)
    return engine.execute(action, **values)


def test_buy_ce_creates_open_ce_position_with_entry_details():
    position = buy(PaperExecutionEngine())

    assert position.side == "CE"
    assert position.status is PositionStatus.OPEN
    assert position.entry_price == 125.5
    assert position.entry_time == EXECUTION_TIME


def test_buy_pe_creates_open_pe_position():
    position = buy(
        PaperExecutionEngine(),
        SignalAction.BUY_PE,
        contract_symbol="NIFTY2681925000PE",
    )

    assert position.side == "PE"
    assert position.status is PositionStatus.OPEN


def test_duplicate_buy_is_rejected():
    engine = PaperExecutionEngine()
    active_position = buy(engine)

    with pytest.raises(ValueError, match="already active"):
        buy(engine, SignalAction.BUY_PE)

    assert engine.active_position is active_position
    assert active_position.contract_symbol == "NIFTY2681925000CE"
    assert active_position.side == "CE"
    assert active_position.entry_price == 125.5
    assert active_position.quantity == 75
    assert active_position.entry_time == EXECUTION_TIME
    assert active_position.status is PositionStatus.OPEN


def test_exit_ce_closes_active_ce_and_retains_entry_information():
    engine = PaperExecutionEngine()
    opened = buy(engine)
    closed = exit(engine)

    assert closed is not opened
    assert closed.status is PositionStatus.CLOSED
    assert closed.contract_symbol == opened.contract_symbol
    assert closed.side == opened.side
    assert closed.entry_price == opened.entry_price
    assert closed.quantity == opened.quantity
    assert closed.entry_time == opened.entry_time
    assert engine.active_position is None


def test_exit_pe_closes_active_pe_position():
    engine = PaperExecutionEngine()
    buy(engine, SignalAction.BUY_PE, contract_symbol="NIFTY2681925000PE")

    closed = exit(engine, SignalAction.EXIT_PE)

    assert closed.side == "PE"
    assert closed.status is PositionStatus.CLOSED


@pytest.mark.parametrize(
    ("buy_action", "exit_action"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_PE),
        (SignalAction.BUY_PE, SignalAction.EXIT_CE),
    ],
)
def test_wrong_side_exit_is_rejected(buy_action, exit_action):
    engine = PaperExecutionEngine()
    active_position = buy(engine, buy_action)

    with pytest.raises(ValueError, match="does not match"):
        exit(engine, exit_action)

    assert engine.active_position is active_position


def test_exit_with_no_active_position_is_rejected():
    with pytest.raises(ValueError, match="No paper position"):
        exit(PaperExecutionEngine())


@pytest.mark.parametrize("premium", [0, -1, True, float("inf"), float("nan"), "125.5"])
def test_invalid_premium_is_rejected(premium):
    with pytest.raises(ValueError, match="Premium"):
        buy(PaperExecutionEngine(), premium=premium)


def test_exit_invalid_premium_is_rejected():
    engine = PaperExecutionEngine()
    active_position = buy(engine)

    with pytest.raises(ValueError, match="Premium"):
        exit(engine, premium=0)

    assert engine.active_position is active_position


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "75"])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ValueError, match="Quantity"):
        buy(PaperExecutionEngine(), quantity=quantity)


@pytest.mark.parametrize("contract_symbol", ["", "   ", None, 123])
def test_invalid_contract_symbol_is_rejected(contract_symbol):
    with pytest.raises(ValueError, match="Contract symbol"):
        buy(PaperExecutionEngine(), contract_symbol=contract_symbol)


def test_naive_execution_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        buy(PaperExecutionEngine(), execution_time=datetime(2026, 8, 19, 9, 15))


@pytest.mark.parametrize(
    ("buy_action", "exit_action"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_CE),
        (SignalAction.BUY_PE, SignalAction.EXIT_PE),
    ],
)
def test_naive_exit_time_is_rejected_without_changing_active_position(
    buy_action,
    exit_action,
):
    engine = PaperExecutionEngine()
    active_position = buy(engine, buy_action)

    with pytest.raises(ValueError, match="timezone-aware"):
        exit(
            engine,
            exit_action,
            execution_time=datetime(2026, 8, 19, 9, 30),
        )

    assert engine.active_position is active_position


@pytest.mark.parametrize("action", [SignalAction.HOLD, "BUY_CE", None])
def test_invalid_action_is_rejected(action):
    with pytest.raises(ValueError, match="supported"):
        PaperExecutionEngine().execute(
            action,
            premium=125.5,
            execution_time=EXECUTION_TIME,
        )
