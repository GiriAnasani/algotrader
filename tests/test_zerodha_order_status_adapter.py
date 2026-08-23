from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
        "quantity": 75,
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


def test_complete_full_fill_uses_exchange_update_timestamp_as_fill_truth():
    status = ZerodhaOrderStatusAdapter().to_broker_order_status(
        make_record(exchange_update_timestamp="2026-08-23 09:30:01")
    )

    assert status.fill_timestamp == datetime(
        2026, 8, 23, 9, 30, 1, tzinfo=ZoneInfo("Asia/Kolkata")
    )
    assert status.fill_timestamp.utcoffset() == timedelta(hours=5, minutes=30)
    assert status.average_price == 27.5
    assert status.filled_quantity == 75
    assert status.is_filled is True


def test_timezone_aware_exchange_update_timestamp_is_preserved_exactly():
    timestamp = datetime(2026, 8, 23, 4, 0, tzinfo=timezone.utc)
    status = ZerodhaOrderStatusAdapter().to_broker_order_status(
        make_record(exchange_update_timestamp=timestamp)
    )
    assert status.fill_timestamp is timestamp


def test_naive_exchange_local_datetime_is_normalized_deterministically():
    status = ZerodhaOrderStatusAdapter().to_broker_order_status(
        make_record(exchange_update_timestamp=datetime(2026, 8, 23, 9, 30))
    )
    assert status.fill_timestamp == datetime(
        2026, 8, 23, 9, 30, tzinfo=ZoneInfo("Asia/Kolkata")
    )


@pytest.mark.parametrize(
    "record",
    [
        make_record(status="OPEN", filled_quantity=0, pending_quantity=75),
        make_record(status="VALIDATION PENDING", filled_quantity=0, pending_quantity=75),
        make_record(status="SOME FUTURE STATUS", filled_quantity=0, pending_quantity=75),
        make_record(status="REJECTED", filled_quantity=0, pending_quantity=75),
        make_record(status="CANCELLED", filled_quantity=0, pending_quantity=0),
        make_record(status="CANCELLED", filled_quantity=25, pending_quantity=50),
        make_record(status="COMPLETE", filled_quantity=25, pending_quantity=0),
        make_record(status="COMPLETE", filled_quantity=75, pending_quantity=1),
    ],
)
def test_non_full_fill_states_never_expose_fill_timestamp(record):
    record["exchange_update_timestamp"] = "2026-08-23 09:30:01"
    assert ZerodhaOrderStatusAdapter().to_broker_order_status(record).fill_timestamp is None


@pytest.mark.parametrize("timestamp", ["invalid", 123, True, object()])
def test_invalid_full_fill_exchange_update_timestamp_is_rejected(timestamp):
    with pytest.raises((TypeError, ValueError)):
        ZerodhaOrderStatusAdapter().to_broker_order_status(
            make_record(exchange_update_timestamp=timestamp)
        )
