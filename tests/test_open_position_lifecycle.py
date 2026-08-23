from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.open_position_lifecycle import ConfirmedPositionEntry, OpenPositionLifecycle
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import ActivePositionExistsError, PositionManager


FILL_TIME = datetime(2026, 8, 23, 9, 20, tzinfo=timezone.utc)


def make_entry(side=PositionSide.CE, **overrides):
    symbol_side = side.value if isinstance(side, PositionSide) else "CE"
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{symbol_side}",
        "quantity": 65,
        "fill_price": 101.5,
        "fill_time": FILL_TIME,
    }
    values.update(overrides)
    return ConfirmedPositionEntry(**values)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_valid_confirmed_entry_is_accepted(side):
    entry = make_entry(side)
    assert entry.side is side


def test_confirmed_entry_normalizes_symbol_and_price_and_preserves_time():
    entry = make_entry(contract_symbol="  NIFTY26AUG25000CE  ", fill_price=101)
    assert entry.contract_symbol == "NIFTY26AUG25000CE"
    assert entry.fill_price == 101.0
    assert type(entry.fill_price) is float
    assert entry.fill_time is FILL_TIME


def test_confirmed_entry_is_immutable():
    entry = make_entry()
    with pytest.raises(FrozenInstanceError):
        entry.quantity = 130


@pytest.mark.parametrize("side", ["CE", "PE", None, 1, True])
def test_confirmed_entry_rejects_non_position_side(side):
    with pytest.raises(ValueError):
        make_entry(side=side)


@pytest.mark.parametrize(
    ("side", "symbol"),
    [(PositionSide.CE, "NIFTY26AUG25000PE"), (PositionSide.PE, "NIFTY26AUG25000CE")],
)
def test_confirmed_entry_rejects_side_symbol_mismatch(side, symbol):
    with pytest.raises(ValueError):
        make_entry(side, contract_symbol=symbol)


def test_confirmed_entry_symbol_suffix_is_case_insensitive():
    assert make_entry(contract_symbol="nifty26aug25000ce").contract_symbol.endswith("ce")


@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_confirmed_entry_rejects_invalid_symbol(symbol):
    with pytest.raises(ValueError):
        make_entry(contract_symbol=symbol)


@pytest.mark.parametrize("quantity", [1, 65])
def test_confirmed_entry_accepts_positive_integer_quantity(quantity):
    assert make_entry(quantity=quantity).quantity == quantity


@pytest.mark.parametrize("quantity", [0, -1, 1.0, True, "65"])
def test_confirmed_entry_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        make_entry(quantity=quantity)


@pytest.mark.parametrize("fill_price", [0, 1, 0.0, 101.5])
def test_confirmed_entry_accepts_non_negative_real_fill_price(fill_price):
    entry = make_entry(fill_price=fill_price)
    assert entry.fill_price == float(fill_price)
    assert type(entry.fill_price) is float


@pytest.mark.parametrize(
    "fill_price",
    [-0.01, float("nan"), float("inf"), float("-inf"), True, "101.5", None],
)
def test_confirmed_entry_rejects_invalid_fill_price(fill_price):
    with pytest.raises(ValueError):
        make_entry(fill_price=fill_price)


def test_confirmed_entry_accepts_timezone_aware_fill_time():
    assert make_entry(fill_time=FILL_TIME).fill_time is FILL_TIME


@pytest.mark.parametrize("fill_time", [datetime(2026, 8, 23, 9, 20), "now", None])
def test_confirmed_entry_rejects_invalid_fill_time(fill_time):
    with pytest.raises(ValueError):
        make_entry(fill_time=fill_time)


@pytest.mark.parametrize("manager", [None, object(), "manager", True])
def test_lifecycle_requires_position_manager(manager):
    with pytest.raises(TypeError):
        OpenPositionLifecycle(manager)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_open_maps_entry_to_one_registered_open_position(side):
    manager = PositionManager()
    lifecycle = OpenPositionLifecycle(manager)
    entry = make_entry(side)
    original_entry_values = tuple(entry.__dict__.values())

    position = lifecycle.open(entry)

    assert isinstance(position, ManagedPosition)
    assert position.side is entry.side
    assert position.contract_symbol == entry.contract_symbol
    assert position.quantity == entry.quantity
    assert position.entry_price == entry.fill_price
    assert position.entry_time is entry.fill_time
    assert position.state is PositionState.OPEN
    assert position is manager.active_position
    assert manager.has_active_position is True
    assert tuple(entry.__dict__.values()) == original_entry_values


def test_open_conflict_propagates_and_preserves_existing_position_exactly():
    manager = PositionManager()
    lifecycle = OpenPositionLifecycle(manager)
    existing = lifecycle.open(make_entry(quantity=65))

    with pytest.raises(ActivePositionExistsError):
        lifecycle.open(make_entry(PositionSide.PE, quantity=130))

    assert manager.active_position is existing
    assert manager.active_position.side is PositionSide.CE
    assert manager.active_position.quantity == 65


@pytest.mark.parametrize("confirmed_entry", [None, object(), "entry", True])
def test_open_rejects_unconfirmed_input_without_modifying_empty_manager(confirmed_entry):
    manager = PositionManager()
    lifecycle = OpenPositionLifecycle(manager)
    with pytest.raises(TypeError):
        lifecycle.open(confirmed_entry)
    assert manager.active_position is None


def test_invalid_open_input_preserves_existing_manager_state():
    manager = PositionManager()
    lifecycle = OpenPositionLifecycle(manager)
    existing = lifecycle.open(make_entry())
    with pytest.raises(TypeError):
        lifecycle.open(None)
    assert manager.active_position is existing


def test_confirmed_entry_has_only_confirmed_fill_fields():
    assert [field.name for field in fields(ConfirmedPositionEntry)] == [
        "side",
        "contract_symbol",
        "quantity",
        "fill_price",
        "fill_time",
    ]


def test_open_lifecycle_has_no_external_system_or_out_of_scope_behavior():
    source = Path("trading/open_position_lifecycle.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect",
        "zerodha",
        "brokerposition",
        "brokerorderstatus",
        "liveorder",
        "pendingliveorder",
        "liveexecutioncoordinator",
        "liverecoverycoordinator",
        "marketdata",
        "executionrouter",
        "livereadinessgate",
        "place_order",
        "partial",
        "pnl",
        "portfolio",
        "synchroniz",
        "restart",
        "reversal",
    )
    assert all(term not in source for term in forbidden)
    assert not hasattr(OpenPositionLifecycle, "close")
