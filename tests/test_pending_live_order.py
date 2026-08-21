from dataclasses import FrozenInstanceError
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.pending_live_order import PendingLiveOrder
from trading.strategy import SignalAction


TIME = datetime(2026, 8, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_pending(**overrides):
    values = {
        "order_id": " order-123 ",
        "action": SignalAction.BUY_CE,
        "side": "CE",
        "contract_symbol": " NIFTY2682125000CE ",
        "quantity": 75,
        "created_time": TIME,
    }
    values.update(overrides)
    return PendingLiveOrder(**values)


def test_pending_live_order_is_immutable_and_normalized():
    pending = make_pending()

    assert pending.order_id == "order-123"
    assert pending.contract_symbol == "NIFTY2682125000CE"
    with pytest.raises(FrozenInstanceError):
        pending.quantity = 50


@pytest.mark.parametrize("order_id", [None, "", " "])
def test_pending_live_order_rejects_invalid_order_id(order_id):
    with pytest.raises(ValueError, match="Order ID"):
        make_pending(order_id=order_id)


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("action", SignalAction.HOLD, ValueError),
        ("side", "XX", ValueError),
        ("side", "PE", ValueError),
        ("contract_symbol", " ", ValueError),
        ("quantity", 0, ValueError),
        ("quantity", True, ValueError),
        ("created_time", datetime(2026, 8, 21, 9, 15), ValueError),
    ],
)
def test_pending_live_order_rejects_invalid_state(field, value, exception):
    with pytest.raises(exception):
        make_pending(**{field: value})
