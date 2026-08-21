from dataclasses import dataclass

import pytest

from trading.broker_position import BrokerPosition
from trading.broker_position_reconciler import (
    BrokerPositionReconciler,
    PositionReconciliationState,
)
from trading.market import LiveExecutionContext


@dataclass(frozen=True)
class ExpectedContext:
    side: str = "CE"
    contract_symbol: str = "NIFTY2682125000CE"
    quantity: int = 75


def position(**overrides):
    values = {
        "tradingsymbol": "NIFTY2682125000CE",
        "exchange": "NFO",
        "quantity": 75,
        "average_price": 27.0,
        "product": "MIS",
    }
    values.update(overrides)
    return BrokerPosition(**values)


def reconcile(context=ExpectedContext(), positions=()):
    return BrokerPositionReconciler().reconcile(context, positions)


def test_exact_positive_nifty_option_position_matches_expected_context():
    result = reconcile(positions=(position(),))

    assert result.state is PositionReconciliationState.MATCH
    assert result.expected_contract_symbol == "NIFTY2682125000CE"
    assert result.expected_quantity == 75


def test_reconciler_accepts_existing_live_execution_context_without_mutating_it():
    context = LiveExecutionContext(
        side="CE",
        contract_symbol="NIFTY2682125000CE",
        quantity=75,
    )

    result = BrokerPositionReconciler().reconcile(context, (position(),))

    assert result.state is PositionReconciliationState.MATCH
    assert context.quantity == 75


@pytest.mark.parametrize("quantity", [25, 100])
def test_exact_symbol_with_nonmatching_quantity_is_not_a_match(quantity):
    result = reconcile(positions=(position(quantity=quantity),))

    assert result.state is PositionReconciliationState.QUANTITY_MISMATCH


def test_flat_exact_symbol_is_not_an_active_match():
    result = reconcile(positions=(position(quantity=0),))

    assert result.state is PositionReconciliationState.NO_POSITION


def test_short_expected_symbol_is_a_side_mismatch():
    result = reconcile(positions=(position(quantity=-75),))

    assert result.state is PositionReconciliationState.SIDE_MISMATCH


@pytest.mark.parametrize(
    ("context", "actual_symbol", "state"),
    [
        (ExpectedContext(), "NIFTY2682125000PE", PositionReconciliationState.SIDE_MISMATCH),
        (ExpectedContext(side="PE", contract_symbol="NIFTY2682125000PE"), "NIFTY2682125000CE", PositionReconciliationState.SIDE_MISMATCH),
        (ExpectedContext(), "NIFTY2682125100CE", PositionReconciliationState.SYMBOL_MISMATCH),
    ],
)
def test_other_relevant_option_exposure_is_a_clear_mismatch(context, actual_symbol, state):
    result = reconcile(context, (position(tradingsymbol=actual_symbol),))

    assert result.state is state


def test_missing_expected_position_is_no_position():
    assert reconcile(positions=()).state is PositionReconciliationState.NO_POSITION


def test_multiple_relevant_positions_are_ambiguous():
    result = reconcile(positions=(position(), position(tradingsymbol="NIFTY2682125000PE")))

    assert result.state is PositionReconciliationState.MULTIPLE_MATCHES


def test_product_mismatch_does_not_match():
    assert reconcile(positions=(position(product="NRML"),)).state is PositionReconciliationState.PRODUCT_MISMATCH


def test_no_internal_context_reports_unexpected_relevant_exposure():
    result = reconcile(None, (position(),))

    assert result.state is PositionReconciliationState.UNEXPECTED_POSITION


@pytest.mark.parametrize(
    "unrelated",
    [
        position(tradingsymbol="RELIANCE", exchange="NSE", quantity=10),
        position(tradingsymbol="NIFTY26AUGFUT", exchange="NFO", quantity=10),
        position(quantity=0),
    ],
)
def test_no_internal_context_ignores_unrelated_or_flat_positions(unrelated):
    result = reconcile(None, (unrelated,))

    assert result.state is PositionReconciliationState.NO_POSITION


def test_explicit_allowed_contract_filter_is_deterministic():
    result = BrokerPositionReconciler().reconcile(
        None,
        (position(),),
        allowed_contract_symbols=("NIFTY2682125000PE",),
    )

    assert result.state is PositionReconciliationState.NO_POSITION


def test_reconciler_is_pure_and_rejects_invalid_inputs():
    reconciler = BrokerPositionReconciler()
    positions = (position(),)

    with pytest.raises(TypeError):
        reconciler.reconcile(ExpectedContext(), (object(),))
    with pytest.raises(TypeError):
        reconciler.reconcile(object(), positions)

    assert positions == (position(),)
