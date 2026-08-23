import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from trading.broker_order_status import BrokerOrderState
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader


def make_record(status="OPEN", **overrides):
    values = {
        "order_id": "240821000001",
        "status": status,
        "filled_quantity": 0,
        "pending_quantity": 75,
        "average_price": 0.0,
        "quantity": 75,
    }
    values.update(overrides)
    return values


class FakeKiteClient:
    def __init__(self, history=None, exception=None):
        self.history = history if history is not None else [make_record()]
        self.exception = exception
        self.order_history_calls = []

    def order_history(self, order_id):
        self.order_history_calls.append(order_id)
        if self.exception is not None:
            raise self.exception
        return self.history

    def place_order(self, **kwargs):
        raise AssertionError("Status reader must not submit orders.")

    def cancel_order(self, **kwargs):
        raise AssertionError("Status reader must not cancel orders.")


def test_reader_requires_an_injected_client():
    with pytest.raises(ValueError, match="client"):
        ZerodhaOrderStatusReader(None)


@pytest.mark.parametrize("order_id", ["", "   ", None, 123])
def test_invalid_order_id_fails_before_broker_call(order_id):
    client = FakeKiteClient()

    with pytest.raises(ValueError):
        ZerodhaOrderStatusReader(client).read(order_id)

    assert client.order_history_calls == []


def test_reader_reads_once_and_selects_latest_history_record():
    client = FakeKiteClient(
        history=[
            make_record("OPEN"),
            make_record(
                "COMPLETE",
                filled_quantity=75,
                pending_quantity=0,
                average_price=28.0,
                exchange_update_timestamp="2026-08-23 09:30:01",
            ),
        ]
    )

    status = ZerodhaOrderStatusReader(client).read(" 240821000001 ")

    assert client.order_history_calls == ["240821000001"]
    assert status.state is BrokerOrderState.COMPLETE
    assert status.average_price == 28.0
    assert status.fill_timestamp == datetime(
        2026, 8, 23, 9, 30, 1, tzinfo=ZoneInfo("Asia/Kolkata")
    )


def test_reader_rejects_mismatched_broker_response_order_id_without_retry():
    client = FakeKiteClient(history=[make_record(order_id="different-order")])

    with pytest.raises(ValueError, match="does not match"):
        ZerodhaOrderStatusReader(client).read("240821000001")

    assert client.order_history_calls == ["240821000001"]


@pytest.mark.parametrize("history", [[], {}, ["record"]])
def test_reader_rejects_empty_or_malformed_history(history):
    with pytest.raises((TypeError, ValueError)):
        ZerodhaOrderStatusReader(FakeKiteClient(history=history)).read(
            "240821000001"
        )


def test_broker_exception_propagates_without_retry():
    client = FakeKiteClient(exception=RuntimeError("broker unavailable"))

    with pytest.raises(RuntimeError, match="broker unavailable"):
        ZerodhaOrderStatusReader(client).read("240821000001")

    assert client.order_history_calls == ["240821000001"]
