from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.live_order import LiveOrderIntent
from trading.strategy import SignalAction
from trading.zerodha_order_adapter import ZerodhaOrderAdapter


TIME = datetime(2026, 8, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_intent(action=SignalAction.BUY_CE, **overrides):
    side = "CE" if action in (SignalAction.BUY_CE, SignalAction.EXIT_CE) else "PE"
    values = {
        "contract_symbol": f"NIFTY2682125000{side}",
        "side": side,
        "action": action,
        "quantity": 75,
        "reference_price": 27.0,
        "created_time": TIME,
    }
    values.update(overrides)
    return LiveOrderIntent(**values)


@pytest.mark.parametrize(
    ("action", "transaction_type"),
    [
        (SignalAction.BUY_CE, "BUY"),
        (SignalAction.BUY_PE, "BUY"),
        (SignalAction.EXIT_CE, "SELL"),
        (SignalAction.EXIT_PE, "SELL"),
    ],
)
def test_adapter_translates_live_intents_to_zerodha_requests(
    action,
    transaction_type,
):
    intent = make_intent(action, quantity=50)

    request = ZerodhaOrderAdapter().to_order_request(intent)

    assert request.tradingsymbol == intent.contract_symbol
    assert request.transaction_type == transaction_type
    assert request.quantity == 50
    assert request.exchange == "NFO"
    assert request.order_type == "MARKET"
    assert request.product == "MIS"
    assert request.validity == "DAY"


def test_adapter_does_not_mutate_or_convert_intent_reference_price():
    intent = make_intent(reference_price=27.5)

    request = ZerodhaOrderAdapter().to_order_request(intent)

    assert intent.reference_price == 27.5
    assert not hasattr(request, "reference_price")


@pytest.mark.parametrize("value", [{}, None, "intent"])
def test_adapter_accepts_only_live_order_intents(value):
    with pytest.raises(TypeError, match="LiveOrderIntent"):
        ZerodhaOrderAdapter().to_order_request(value)


def test_adapter_has_no_broker_client_dependency():
    adapter = ZerodhaOrderAdapter()

    assert not hasattr(adapter, "kite")
    assert not hasattr(adapter, "client")
