import inspect

import pytest

from trading.zerodha_order import ZerodhaOrderRequest
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


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


class FakeKiteClient:
    def __init__(self, order_id="240821000001", exception=None):
        self.order_id = order_id
        self.exception = exception
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        if self.exception is not None:
            raise self.exception
        return self.order_id


def test_submitter_requires_an_injected_client():
    with pytest.raises(ValueError, match="client"):
        ZerodhaOrderSubmitter(None)


@pytest.mark.parametrize("transaction_type", ["BUY", "SELL"])
def test_submitter_maps_request_once_and_returns_exact_broker_order_id(
    transaction_type,
):
    client = FakeKiteClient(order_id="broker-order-123")
    request = make_request(transaction_type=transaction_type, quantity=50)

    order_id = ZerodhaOrderSubmitter(client).submit(request)

    assert order_id == "broker-order-123"
    assert len(client.calls) == 1
    assert client.calls[0] == {
        "variety": "regular",
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "transaction_type": transaction_type,
        "quantity": 50,
        "order_type": "MARKET",
        "product": "MIS",
        "validity": "DAY",
    }
    assert "price" not in client.calls[0]


@pytest.mark.parametrize("value", [{}, None, "request"])
def test_submitter_accepts_only_zerodha_order_requests(value):
    with pytest.raises(TypeError, match="ZerodhaOrderRequest"):
        ZerodhaOrderSubmitter(FakeKiteClient()).submit(value)


def test_broker_exception_propagates_without_retry():
    client = FakeKiteClient(exception=RuntimeError("broker unavailable"))

    with pytest.raises(RuntimeError, match="broker unavailable"):
        ZerodhaOrderSubmitter(client).submit(make_request())

    assert len(client.calls) == 1


def test_submitter_has_no_client_construction_or_automatic_integration():
    source = inspect.getsource(ZerodhaOrderSubmitter)

    assert "KiteConnect" not in source
    assert "MarketData" not in source
    assert "ExecutionRouter" not in source
