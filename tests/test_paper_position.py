from dataclasses import FrozenInstanceError
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.paper_position import PaperPosition, PositionStatus


ENTRY_TIME = datetime(
    2026,
    8,
    19,
    9,
    15,
    tzinfo=ZoneInfo("Asia/Kolkata"),
)


def make_position(**overrides):
    values = {
        "contract_symbol": "NIFTY2681925000CE",
        "side": "CE",
        "entry_price": 125.5,
        "quantity": 75,
        "entry_time": ENTRY_TIME,
        "status": PositionStatus.OPEN,
    }
    values.update(overrides)

    return PaperPosition(**values)


def test_open_position_stores_required_fields_immutably():
    position = make_position(contract_symbol=" NIFTY2681925000CE ")

    assert position.contract_symbol == "NIFTY2681925000CE"
    assert position.side == "CE"
    assert position.entry_price == 125.5
    assert position.quantity == 75
    assert position.entry_time == ENTRY_TIME
    assert position.status is PositionStatus.OPEN

    with pytest.raises(FrozenInstanceError):
        position.status = PositionStatus.CLOSED


def test_closed_status_is_supported():
    position = make_position(side="PE", status=PositionStatus.CLOSED)

    assert position.side == "PE"
    assert position.status is PositionStatus.CLOSED


@pytest.mark.parametrize(
    "contract_symbol",
    ["", "   ", None, 123],
)
def test_contract_symbol_must_be_a_non_empty_string(contract_symbol):
    with pytest.raises(ValueError):
        make_position(contract_symbol=contract_symbol)


@pytest.mark.parametrize("side", ["CALL", "ce", "", None])
def test_side_must_be_ce_or_pe(side):
    with pytest.raises(ValueError):
        make_position(side=side)


@pytest.mark.parametrize(
    "entry_price",
    [0, -1, True, float("inf"), float("nan"), "125.5"],
)
def test_entry_price_must_be_a_positive_finite_number(entry_price):
    with pytest.raises(ValueError):
        make_position(entry_price=entry_price)


@pytest.mark.parametrize(
    "quantity",
    [0, -1, 1.5, True, "75"],
)
def test_quantity_must_be_a_positive_integer(quantity):
    with pytest.raises(ValueError):
        make_position(quantity=quantity)


def test_entry_time_must_be_a_timezone_aware_datetime():
    with pytest.raises(TypeError):
        make_position(entry_time="2026-08-19T09:15:00+05:30")

    with pytest.raises(ValueError):
        make_position(entry_time=datetime(2026, 8, 19, 9, 15))


@pytest.mark.parametrize(
    "status",
    ["OPEN", "CLOSED", None],
)
def test_status_must_be_an_explicit_position_status(status):
    with pytest.raises(TypeError):
        make_position(status=status)
