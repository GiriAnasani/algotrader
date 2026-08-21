from dataclasses import FrozenInstanceError

import pytest

from trading.broker_position import BrokerPosition
from trading.live_recovery import (
    LiveRecoveryCoordinator,
    LiveRecoveryResult,
    LiveRecoveryState,
)
from trading.zerodha_order_list_reader import ZerodhaOrderListReader
from trading.zerodha_position_reader import ZerodhaPositionReader


def position(**overrides):
    values = {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "quantity": 75,
        "average_price": 27.0,
        "product": "MIS",
    }
    values.update(overrides)
    return values


def order(**overrides):
    values = {
        "order_id": "order-1",
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 75,
        "filled_quantity": 0,
        "pending_quantity": 75,
        "average_price": 0.0,
        "product": "MIS",
        "status": "OPEN",
    }
    values.update(overrides)
    return values


class FakeKiteClient:
    def __init__(self, positions=None, orders=None, position_exception=None, order_exception=None):
        self.position_response = {"net": positions or [], "day": []}
        self.order_response = list(orders or [])
        self.position_exception = position_exception
        self.order_exception = order_exception
        self.position_calls = 0
        self.order_calls = 0

    def positions(self):
        self.position_calls += 1
        if self.position_exception:
            raise self.position_exception
        return self.position_response

    def orders(self):
        self.order_calls += 1
        if self.order_exception:
            raise self.order_exception
        return self.order_response


def recovery(client):
    return LiveRecoveryCoordinator(
        ZerodhaPositionReader(client),
        ZerodhaOrderListReader(client),
    )


def test_recovery_result_is_immutable():
    result = LiveRecoveryResult(
        LiveRecoveryState.SAFE_FLAT, (), (), "flat"
    )

    with pytest.raises(FrozenInstanceError):
        result.state = LiveRecoveryState.UNKNOWN


@pytest.mark.parametrize(
    "values",
    [
        ("SAFE_FLAT", (), (), "flat"),
        (LiveRecoveryState.SAFE_FLAT, [], (), "flat"),
        (LiveRecoveryState.SAFE_FLAT, (), [], "flat"),
        (LiveRecoveryState.SAFE_FLAT, (), (), ""),
    ],
)
def test_recovery_result_rejects_invalid_state_or_diagnostics(values):
    with pytest.raises((TypeError, ValueError)):
        LiveRecoveryResult(*values)


@pytest.mark.parametrize(
    "positions, expected",
    [
        ([], LiveRecoveryState.SAFE_FLAT),
        ([position()], LiveRecoveryState.POSITION_PRESENT),
        ([position(tradingsymbol="NIFTY2682125000PE", quantity=50)], LiveRecoveryState.POSITION_PRESENT),
        ([position(quantity=-75)], LiveRecoveryState.POSITION_PRESENT),
        ([position(), position(tradingsymbol="NIFTY2682125000PE", quantity=50)], LiveRecoveryState.AMBIGUOUS),
        ([position(tradingsymbol="RELIANCE", exchange="NSE", quantity=10)], LiveRecoveryState.SAFE_FLAT),
        ([position(tradingsymbol="NIFTY26AUGFUT", quantity=10)], LiveRecoveryState.SAFE_FLAT),
        ([position(quantity=0)], LiveRecoveryState.SAFE_FLAT),
    ],
)
def test_position_recovery_classifies_relevant_net_exposure(positions, expected):
    client = FakeKiteClient(positions=positions, orders=[])

    result = recovery(client).recover()

    assert result.state is expected
    assert client.position_calls == 1
    assert client.order_calls == 1


@pytest.mark.parametrize(
    "record, expected",
    [
        (order(status="OPEN"), LiveRecoveryState.PENDING_ORDER),
        (order(status="VALIDATION PENDING"), LiveRecoveryState.PENDING_ORDER),
        (order(status="UNRECOGNIZED"), LiveRecoveryState.PENDING_ORDER),
        (order(status="REJECTED", pending_quantity=75), LiveRecoveryState.SAFE_FLAT),
        (order(status="CANCELLED", pending_quantity=0), LiveRecoveryState.SAFE_FLAT),
        (order(status="CANCELLED", filled_quantity=25, pending_quantity=50), LiveRecoveryState.PENDING_ORDER),
        (order(status="COMPLETE", filled_quantity=75, pending_quantity=0, average_price=27.0), LiveRecoveryState.SAFE_FLAT),
        (order(status="COMPLETE", filled_quantity=25, pending_quantity=0, average_price=27.0), LiveRecoveryState.PENDING_ORDER),
        (order(status="COMPLETE", filled_quantity=75, pending_quantity=1, average_price=27.0), LiveRecoveryState.PENDING_ORDER),
    ],
)
def test_order_recovery_classifies_unresolved_states(record, expected):
    client = FakeKiteClient(orders=[record])

    result = recovery(client).recover()

    assert result.state is expected
    assert client.position_calls == 1
    assert client.order_calls == 1


@pytest.mark.parametrize(
    "record",
    [
        order(tradingsymbol="RELIANCE", exchange="NSE"),
        order(tradingsymbol="NIFTY26AUGFUT"),
        order(product="NRML"),
    ],
)
def test_unrelated_orders_do_not_block_safe_flat_recovery(record):
    assert recovery(FakeKiteClient(orders=[record])).recover().state is LiveRecoveryState.SAFE_FLAT


def test_position_and_unresolved_order_are_ambiguous():
    result = recovery(FakeKiteClient(positions=[position()], orders=[order()])).recover()

    assert result.state is LiveRecoveryState.AMBIGUOUS


def test_multiple_unresolved_orders_are_ambiguous():
    result = recovery(FakeKiteClient(orders=[order(), order(order_id="order-2")])).recover()

    assert result.state is LiveRecoveryState.AMBIGUOUS


def test_allowed_symbols_cannot_hide_existing_relevant_exposure():
    result = recovery(
        FakeKiteClient(positions=[position()], orders=[order()])
    ).recover(allowed_contract_symbols=("NIFTY2682125000PE",))

    assert result.state is LiveRecoveryState.AMBIGUOUS


CURRENT_PAIR = ("NIFTY2682125000CE", "NIFTY2682125000PE")


def test_stale_option_position_outside_allowed_symbols_is_not_safe_flat():
    result = recovery(
        FakeKiteClient(positions=[position(tradingsymbol="NIFTY2682124900CE")])
    ).recover(allowed_contract_symbols=CURRENT_PAIR)

    assert result.state is LiveRecoveryState.POSITION_PRESENT


def test_unresolved_stale_option_order_outside_allowed_symbols_is_not_safe_flat():
    result = recovery(
        FakeKiteClient(orders=[order(tradingsymbol="NIFTY2682124900CE")])
    ).recover(allowed_contract_symbols=CURRENT_PAIR)

    assert result.state is LiveRecoveryState.PENDING_ORDER


@pytest.mark.parametrize(
    "positions",
    [
        [position(tradingsymbol="RELIANCE", exchange="NSE", quantity=10)],
        [position(tradingsymbol="NIFTY26AUGFUT", quantity=10)],
        [position(quantity=0)],
    ],
)
def test_allowed_pair_does_not_block_safe_flat_for_unrelated_or_flat_rows(positions):
    result = recovery(FakeKiteClient(positions=positions)).recover(
        allowed_contract_symbols=CURRENT_PAIR
    )

    assert result.state is LiveRecoveryState.SAFE_FLAT


def test_completed_stale_option_order_without_position_does_not_block_safe_flat():
    result = recovery(
        FakeKiteClient(
            orders=[
                order(
                    tradingsymbol="NIFTY2682124900CE",
                    status="COMPLETE",
                    filled_quantity=75,
                    pending_quantity=0,
                    average_price=27.0,
                )
            ]
        )
    ).recover(allowed_contract_symbols=CURRENT_PAIR)

    assert result.state is LiveRecoveryState.SAFE_FLAT


@pytest.mark.parametrize(
    "response",
    [{}, {"net": {}}, {"net": [position(quantity=True)]}],
)
def test_malformed_position_response_fails_closed(response):
    client = FakeKiteClient()
    client.position_response = response

    with pytest.raises((TypeError, ValueError)):
        recovery(client).recover()

    assert client.position_calls == 1
    assert client.order_calls == 0


def test_malformed_order_row_fails_closed_after_one_order_read():
    client = FakeKiteClient(orders=[{"order_id": "order-1"}])

    with pytest.raises(ValueError, match="missing"):
        recovery(client).recover()

    assert client.position_calls == 1
    assert client.order_calls == 1


def test_malformed_listed_order_status_fails_clearly_without_silent_filtering():
    client = FakeKiteClient(orders=[order(status=None)])

    with pytest.raises(ValueError, match="Broker status"):
        recovery(client).recover()

    assert client.position_calls == 1
    assert client.order_calls == 1


@pytest.mark.parametrize(
    "client",
    [
        FakeKiteClient(position_exception=RuntimeError("positions unavailable")),
        FakeKiteClient(order_exception=RuntimeError("orders unavailable")),
    ],
)
def test_broker_read_failures_propagate_without_retry(client):
    with pytest.raises(RuntimeError):
        recovery(client).recover()

    assert client.position_calls == 1
    assert client.order_calls <= 1
    assert not hasattr(client, "place_order")


def test_order_list_reader_requires_client_and_returns_immutable_summaries():
    with pytest.raises(ValueError):
        ZerodhaOrderListReader(None)

    client = FakeKiteClient(orders=[order()])
    summaries = ZerodhaOrderListReader(client).read()

    assert client.order_calls == 1
    assert isinstance(summaries, tuple)
    assert summaries[0].order_id == "order-1"
