"""Explicit, diagnostic-only LIVE startup recovery reconciliation."""

from dataclasses import dataclass
from enum import Enum
import re

from trading.broker_order_status import BrokerOrderState
from trading.broker_order_summary import BrokerOrderSummary
from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.zerodha_order_list_reader import ZerodhaOrderListReader
from trading.zerodha_position_reader import ZerodhaPositionReader


class LiveRecoveryState(Enum):
    """Conservative outcomes for one explicit startup/recovery inspection."""

    SAFE_FLAT = "SAFE_FLAT"
    POSITION_PRESENT = "POSITION_PRESENT"
    PENDING_ORDER = "PENDING_ORDER"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveRecoveryResult:
    """Immutable diagnostic result that never adopts or modifies LIVE state."""

    state: LiveRecoveryState
    broker_positions: tuple
    relevant_order_statuses: tuple[BrokerOrderSummary, ...]
    message: str

    def __post_init__(self):
        if not isinstance(self.state, LiveRecoveryState):
            raise TypeError("State must be a LiveRecoveryState.")
        if not isinstance(self.broker_positions, tuple):
            raise TypeError("Broker positions must be a tuple.")
        if not isinstance(self.relevant_order_statuses, tuple):
            raise TypeError("Relevant order statuses must be a tuple.")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("Message must be a non-empty string.")


class LiveRecoveryCoordinator:
    """Reads broker state once for explicit recovery diagnostics; never trades."""

    _NIFTY_OPTION_SYMBOL = re.compile(r"^NIFTY\d.*(?:CE|PE)$")

    def __init__(self, position_reader, order_reader, position_reconciler=None):
        if not isinstance(position_reader, ZerodhaPositionReader):
            raise TypeError("Position reader must be a ZerodhaPositionReader.")
        if not isinstance(order_reader, ZerodhaOrderListReader):
            raise TypeError("Order reader must be a ZerodhaOrderListReader.")
        if position_reconciler is None:
            position_reconciler = BrokerPositionReconciler()
        if not isinstance(position_reconciler, BrokerPositionReconciler):
            raise TypeError("Position reconciler must be a BrokerPositionReconciler.")
        self._position_reader = position_reader
        self._order_reader = order_reader
        self._position_reconciler = position_reconciler

    def recover(self, allowed_contract_symbols=None):
        """Performs one position read and one order-list read with no side effects."""
        positions = self._position_reader.read()
        reconciliation = self._position_reconciler.reconcile(
            None,
            positions,
        )
        relevant_positions = tuple(
            position
            for position in reconciliation.broker_positions
            if position.product.upper() == "MIS"
        )
        orders = self._order_reader.read()
        unresolved_orders = tuple(
            order for order in orders
            if self._is_relevant_order(order)
            and self._is_unresolved(order)
        )

        if len(relevant_positions) > 1 or len(unresolved_orders) > 1:
            return self._result(
                LiveRecoveryState.AMBIGUOUS,
                relevant_positions,
                unresolved_orders,
                "Multiple relevant broker positions or unresolved orders were found.",
            )
        if relevant_positions and unresolved_orders:
            return self._result(
                LiveRecoveryState.AMBIGUOUS,
                relevant_positions,
                unresolved_orders,
                "Both broker position exposure and an unresolved order were found.",
            )
        if relevant_positions:
            return self._result(
                LiveRecoveryState.POSITION_PRESENT,
                relevant_positions,
                (),
                "One relevant broker option position is present.",
            )
        if unresolved_orders:
            return self._result(
                LiveRecoveryState.PENDING_ORDER,
                (),
                unresolved_orders,
                "One relevant broker order remains unresolved.",
            )
        return self._result(
            LiveRecoveryState.SAFE_FLAT,
            (),
            (),
            "No relevant broker position or unresolved order is present.",
        )

    def _is_relevant_order(self, order):
        symbol = order.tradingsymbol.upper()
        return (
            order.exchange.upper() == "NFO"
            and order.product.upper() == "MIS"
            and self._NIFTY_OPTION_SYMBOL.fullmatch(symbol) is not None
        )

    @staticmethod
    def _is_unresolved(order):
        status = order.status
        if status.state in (
            BrokerOrderState.SUBMITTED,
            BrokerOrderState.OPEN,
            BrokerOrderState.UNKNOWN,
        ):
            return True
        if status.state is BrokerOrderState.CANCELLED:
            return status.filled_quantity > 0
        if status.state is BrokerOrderState.COMPLETE:
            return not (
                status.is_filled
                and status.filled_quantity == order.quantity
            )
        return False

    @staticmethod
    def _result(state, positions, orders, message):
        return LiveRecoveryResult(
            state=state,
            broker_positions=tuple(positions),
            relevant_order_statuses=tuple(orders),
            message=message,
        )
