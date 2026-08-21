import pytest

from trading.broker_position import BrokerPosition
from trading.zerodha_position_reader import ZerodhaPositionReader


def record(**overrides):
    values = {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "quantity": 75,
        "average_price": 27.0,
        "product": "MIS",
    }
    values.update(overrides)
    return values


class FakeKiteClient:
    def __init__(self, response=None, exception=None):
        self.response = response if response is not None else {"net": []}
        self.exception = exception
        self.calls = 0

    def positions(self):
        self.calls += 1
        if self.exception is not None:
            raise self.exception
        return self.response


def test_reader_requires_an_injected_client():
    with pytest.raises(ValueError, match="client"):
        ZerodhaPositionReader(None)


def test_reader_reads_net_once_and_ignores_day_collection():
    client = FakeKiteClient(
        {"net": [record()], "day": [record(tradingsymbol="NIFTY2682125000PE")]}
    )

    positions = ZerodhaPositionReader(client).read()

    assert client.calls == 1
    assert isinstance(positions, tuple)
    assert positions == (BrokerPosition(**record()),)


def test_reader_returns_empty_tuple_for_empty_net_positions():
    client = FakeKiteClient({"net": [], "day": [record()]})

    assert ZerodhaPositionReader(client).read() == ()
    assert client.calls == 1


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"net": {}}, {"net": [record(quantity=True)]}],
)
def test_reader_rejects_malformed_responses(response):
    client = FakeKiteClient()
    client.response = response

    with pytest.raises((TypeError, ValueError)):
        ZerodhaPositionReader(client).read()

    assert client.calls == 1


def test_reader_propagates_broker_failure_without_retry_or_submission():
    client = FakeKiteClient(exception=RuntimeError("broker unavailable"))

    with pytest.raises(RuntimeError, match="broker unavailable"):
        ZerodhaPositionReader(client).read()

    assert client.calls == 1
    assert not hasattr(client, "place_order")
