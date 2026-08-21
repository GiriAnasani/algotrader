from dataclasses import FrozenInstanceError
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.live_order import LiveOrderIntent
from trading.strategy import SignalAction


TIME = datetime(2026, 8, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_intent(**overrides):
    values = {
        "contract_symbol": "NIFTY2682125000CE",
        "side": "CE",
        "action": SignalAction.BUY_CE,
        "quantity": 75,
        "reference_price": 27.0,
        "created_time": TIME,
    }
    values.update(overrides)
    return LiveOrderIntent(**values)


@pytest.mark.parametrize(
    ("side", "action"),
    [
        ("CE", SignalAction.BUY_CE),
        ("PE", SignalAction.BUY_PE),
        ("CE", SignalAction.EXIT_CE),
        ("PE", SignalAction.EXIT_PE),
    ],
)
def test_valid_live_order_intents(side, action):
    intent = make_intent(side=side, action=action)

    assert intent.side == side
    assert intent.action is action


def test_live_order_intent_is_immutable():
    intent = make_intent()

    with pytest.raises(FrozenInstanceError):
        intent.quantity = 50


@pytest.mark.parametrize("contract_symbol", ["", "   ", None, 123])
def test_invalid_contract_symbol_is_rejected(contract_symbol):
    with pytest.raises(ValueError):
        make_intent(contract_symbol=contract_symbol)


@pytest.mark.parametrize("side", ["CALL", "ce", "", None])
def test_invalid_side_is_rejected(side):
    with pytest.raises(ValueError):
        make_intent(side=side)


@pytest.mark.parametrize("action", [SignalAction.HOLD, "BUY_CE", None])
def test_invalid_action_is_rejected(action):
    with pytest.raises(ValueError):
        make_intent(action=action)


@pytest.mark.parametrize(
    ("side", "action"),
    [("CE", SignalAction.EXIT_PE), ("PE", SignalAction.BUY_CE)],
)
def test_action_side_mismatch_is_rejected(side, action):
    with pytest.raises(ValueError, match="match"):
        make_intent(side=side, action=action)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "75"])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ValueError):
        make_intent(quantity=quantity)


@pytest.mark.parametrize("reference_price", [0, -1, True, float("inf"), float("nan"), "27"])
def test_invalid_reference_price_is_rejected(reference_price):
    with pytest.raises(ValueError):
        make_intent(reference_price=reference_price)


def test_naive_created_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_intent(created_time=datetime(2026, 8, 21, 9, 15))
