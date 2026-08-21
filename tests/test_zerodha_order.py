from dataclasses import FrozenInstanceError

import pytest

from trading.zerodha_order import ZerodhaOrderRequest


def make_request(**overrides):
    values = {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 75,
        "order_type": "MARKET",
        "product": "MIS",
        "validity": "DAY",
    }
    values.update(overrides)
    return ZerodhaOrderRequest(**values)


def test_valid_zerodha_order_request_is_immutable():
    request = make_request(tradingsymbol=" NIFTY2682125000CE ")

    assert request.tradingsymbol == "NIFTY2682125000CE"
    assert request.transaction_type == "BUY"

    with pytest.raises(FrozenInstanceError):
        request.quantity = 50


@pytest.mark.parametrize("tradingsymbol", ["", "   ", None, 123])
def test_invalid_tradingsymbol_is_rejected(tradingsymbol):
    with pytest.raises(ValueError):
        make_request(tradingsymbol=tradingsymbol)


@pytest.mark.parametrize("transaction_type", ["", "buy", "HOLD", None])
def test_invalid_transaction_type_is_rejected(transaction_type):
    with pytest.raises(ValueError):
        make_request(transaction_type=transaction_type)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "75"])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ValueError):
        make_request(quantity=quantity)


@pytest.mark.parametrize(
    "field",
    ["exchange", "order_type", "product", "validity"],
)
@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_required_string_fields_are_non_empty(field, value):
    with pytest.raises(ValueError):
        make_request(**{field: value})
