from dataclasses import FrozenInstanceError

import pytest

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus


def make_status(**overrides):
    values = {
        "order_id": "240821000001",
        "state": BrokerOrderState.COMPLETE,
        "filled_quantity": 75,
        "pending_quantity": 0,
        "average_price": 27.5,
        "broker_status": "COMPLETE",
        "status_message": None,
    }
    values.update(overrides)
    return BrokerOrderStatus(**values)


def test_valid_broker_order_status_is_immutable():
    status = make_status(status_message="Filled")

    assert status.status_message == "Filled"
    assert status.is_terminal is True
    assert status.is_filled is True

    with pytest.raises(FrozenInstanceError):
        status.filled_quantity = 0


@pytest.mark.parametrize(
    ("filled_quantity", "pending_quantity", "expected_filled"),
    [(75, 0, True), (75, 1, False), (0, 0, False)],
)
def test_complete_status_is_filled_only_when_no_quantity_is_pending(
    filled_quantity,
    pending_quantity,
    expected_filled,
):
    status = make_status(
        filled_quantity=filled_quantity,
        pending_quantity=pending_quantity,
    )

    assert status.is_filled is expected_filled


@pytest.mark.parametrize("order_id", ["", "   ", None, 123])
def test_invalid_order_id_is_rejected(order_id):
    with pytest.raises(ValueError):
        make_status(order_id=order_id)


@pytest.mark.parametrize("state", ["COMPLETE", None])
def test_invalid_state_is_rejected(state):
    with pytest.raises(TypeError):
        make_status(state=state)


@pytest.mark.parametrize("field", ["filled_quantity", "pending_quantity"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_quantities_must_be_non_negative_integers(field, value):
    with pytest.raises(ValueError):
        make_status(**{field: value})


@pytest.mark.parametrize("average_price", [-1, True, float("inf"), float("nan"), "27"])
def test_average_price_must_be_finite_and_non_negative(average_price):
    with pytest.raises(ValueError):
        make_status(average_price=average_price)


def test_terminal_and_fill_semantics_are_conservative():
    rejected = make_status(
        state=BrokerOrderState.REJECTED,
        filled_quantity=0,
        broker_status="REJECTED",
    )
    cancelled = make_status(
        state=BrokerOrderState.CANCELLED,
        filled_quantity=25,
        pending_quantity=50,
        broker_status="CANCELLED",
    )
    open_status = make_status(
        state=BrokerOrderState.OPEN,
        filled_quantity=0,
        pending_quantity=75,
        broker_status="OPEN",
    )
    submitted = make_status(
        state=BrokerOrderState.SUBMITTED,
        filled_quantity=0,
        pending_quantity=75,
        broker_status="VALIDATION PENDING",
    )
    unknown = make_status(
        state=BrokerOrderState.UNKNOWN,
        filled_quantity=75,
        pending_quantity=0,
        broker_status="UNRECOGNIZED",
    )

    assert rejected.is_terminal is True
    assert rejected.is_rejected is True
    assert rejected.is_filled is False
    assert cancelled.is_terminal is True
    assert cancelled.filled_quantity == 25
    assert cancelled.is_filled is False
    assert open_status.is_terminal is False
    assert submitted.is_terminal is False
    assert unknown.is_terminal is False
    assert unknown.is_filled is False
