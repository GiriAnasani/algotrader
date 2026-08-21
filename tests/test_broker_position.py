from dataclasses import FrozenInstanceError

import pytest

from trading.broker_position import BrokerPosition


def make_position(**overrides):
    values = {
        "tradingsymbol": " NIFTY2682125000CE ",
        "exchange": " NFO ",
        "quantity": 75,
        "average_price": 27.0,
        "product": " MIS ",
    }
    values.update(overrides)
    return BrokerPosition(**values)


def test_broker_position_is_immutable_and_normalizes_strings():
    position = make_position()

    assert position.tradingsymbol == "NIFTY2682125000CE"
    assert position.exchange == "NFO"
    assert position.product == "MIS"
    with pytest.raises(FrozenInstanceError):
        position.quantity = 50


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tradingsymbol", " "),
        ("exchange", ""),
        ("quantity", True),
        ("average_price", -1),
        ("average_price", float("inf")),
        ("product", None),
    ],
)
def test_broker_position_rejects_invalid_fields(field, value):
    with pytest.raises(ValueError):
        make_position(**{field: value})


@pytest.mark.parametrize("quantity", [-75, 0])
def test_broker_position_allows_signed_and_flat_quantities(quantity):
    assert make_position(quantity=quantity).quantity == quantity
