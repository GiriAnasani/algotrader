from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.position import ManagedPosition, PositionSide, PositionState


ENTRY_TIME = datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc)


def make_position(**overrides):
    values = {
        "side": PositionSide.CE,
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": ENTRY_TIME,
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def test_position_enums_have_only_required_values():
    assert {member.value for member in PositionSide} == {"CE", "PE"}
    assert {member.value for member in PositionState} == {"OPEN", "CLOSED"}


@pytest.mark.parametrize(
    ("side", "symbol"),
    [(PositionSide.CE, "NIFTY26AUG25000CE"), (PositionSide.PE, "NIFTY26AUG25000PE")],
)
def test_valid_position_sides_are_accepted(side, symbol):
    assert make_position(side=side, contract_symbol=symbol).side is side


def test_position_normalizes_symbol_and_price_and_preserves_time():
    position = make_position(contract_symbol="  NIFTY26AUG25000CE  ", entry_price=100)
    assert position.contract_symbol == "NIFTY26AUG25000CE"
    assert position.entry_price == 100.0
    assert type(position.entry_price) is float
    assert position.entry_time is ENTRY_TIME


def test_position_is_frozen():
    position = make_position()
    with pytest.raises(FrozenInstanceError):
        position.quantity = 130


@pytest.mark.parametrize("side", ["CE", "PE", None, 1])
def test_non_position_side_is_rejected(side):
    with pytest.raises(ValueError):
        make_position(side=side)


@pytest.mark.parametrize(
    ("side", "symbol"),
    [(PositionSide.CE, "NIFTY26AUG25000PE"), (PositionSide.PE, "NIFTY26AUG25000CE")],
)
def test_side_symbol_mismatch_is_rejected(side, symbol):
    with pytest.raises(ValueError):
        make_position(side=side, contract_symbol=symbol)


def test_symbol_suffix_check_is_case_insensitive():
    assert make_position(contract_symbol="nifty26aug25000ce").contract_symbol.endswith("ce")


@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_invalid_symbol_is_rejected(symbol):
    with pytest.raises(ValueError):
        make_position(contract_symbol=symbol)


@pytest.mark.parametrize("quantity", [1, 65])
def test_positive_integer_quantity_is_accepted(quantity):
    assert make_position(quantity=quantity).quantity == quantity


@pytest.mark.parametrize("quantity", [0, -1, 1.0, True, "65"])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ValueError):
        make_position(quantity=quantity)


@pytest.mark.parametrize("entry_price", [0, 1, 0.0, 99.5])
def test_non_negative_real_entry_price_is_accepted(entry_price):
    assert make_position(entry_price=entry_price).entry_price == float(entry_price)


@pytest.mark.parametrize("entry_price", [-0.01, float("nan"), float("inf"), float("-inf"), True, "10", None])
def test_invalid_entry_price_is_rejected(entry_price):
    with pytest.raises(ValueError):
        make_position(entry_price=entry_price)


def test_timezone_aware_entry_time_is_accepted():
    assert make_position(entry_time=ENTRY_TIME).entry_time is ENTRY_TIME


@pytest.mark.parametrize("entry_time", [datetime(2026, 8, 23, 9, 15), "2026-08-23", None])
def test_invalid_entry_time_is_rejected(entry_time):
    with pytest.raises(ValueError):
        make_position(entry_time=entry_time)


@pytest.mark.parametrize("state", [PositionState.OPEN, PositionState.CLOSED])
def test_position_states_are_accepted(state):
    assert make_position(state=state).state is state


@pytest.mark.parametrize("state", ["OPEN", "CLOSED", None, 1])
def test_non_position_state_is_rejected(state):
    with pytest.raises(ValueError):
        make_position(state=state)


def test_model_has_only_position_domain_fields_and_no_lifecycle_or_pnl_behavior():
    assert [field.name for field in fields(ManagedPosition)] == [
        "side",
        "contract_symbol",
        "quantity",
        "entry_price",
        "entry_time",
        "state",
    ]
    for name in ("open", "close", "pnl", "profit", "loss"):
        assert not hasattr(ManagedPosition, name)


def test_position_module_has_no_external_system_dependencies_or_broker_calls():
    source = Path("trading/position.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect",
        "brokerposition",
        "brokerorderstatus",
        "zerodha",
        "marketdata",
        "liveexecutioncoordinator",
        "livereadinessgate",
        "place_order",
    )
    assert all(term not in source for term in forbidden)
