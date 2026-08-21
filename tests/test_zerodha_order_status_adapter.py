import pytest

from trading.broker_order_status import BrokerOrderState
from trading.zerodha_order_status_adapter import ZerodhaOrderStatusAdapter


def make_record(**overrides):
    values = {
        "order_id": "240821000001",
        "status": "COMPLETE",
        "filled_quantity": 75,
        "pending_quantity": 0,
        "average_price": 27.5,
        "status_message": "Filled",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("broker_status", "expected_state"),
    [
        ("COMPLETE", BrokerOrderState.COMPLETE),
        ("OPEN", BrokerOrderState.OPEN),
        ("TRIGGER PENDING", BrokerOrderState.OPEN),
        ("VALIDATION PENDING", BrokerOrderState.SUBMITTED),
        ("PUT ORDER REQ RECEIVED", BrokerOrderState.SUBMITTED),
        ("OPEN PENDING", BrokerOrderState.SUBMITTED),
        ("MODIFY VALIDATION PENDING", BrokerOrderState.SUBMITTED),
        ("MODIFY PENDING", BrokerOrderState.SUBMITTED),
        ("CANCEL PENDING", BrokerOrderState.SUBMITTED),
        ("CANCELLED", BrokerOrderState.CANCELLED),
        ("REJECTED", BrokerOrderState.REJECTED),
        ("SOME FUTURE STATUS", BrokerOrderState.UNKNOWN),
    ],
)
def test_adapter_normalizes_broker_statuses(broker_status, expected_state):
    status = ZerodhaOrderStatusAdapter().to_broker_order_status(
        make_record(status=broker_status, filled_quantity=25, pending_quantity=50)
    )

    assert status.state is expected_state
    assert status.broker_status == broker_status
    assert status.filled_quantity == 25
    assert status.pending_quantity == 50
    assert status.average_price == 27.5


@pytest.mark.parametrize("record", [{}, None, "record"])
def test_adapter_rejects_malformed_records(record):
    with pytest.raises((TypeError, KeyError, ValueError)):
        ZerodhaOrderStatusAdapter().to_broker_order_status(record)
