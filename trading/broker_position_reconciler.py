"""Pure comparison of confirmed LIVE context against broker net positions."""

from dataclasses import dataclass
from enum import Enum
import re

from trading.broker_position import BrokerPosition


class PositionReconciliationState(Enum):
    """Conservative outcomes of broker-position comparison."""

    MATCH = "MATCH"
    NO_POSITION = "NO_POSITION"
    UNEXPECTED_POSITION = "UNEXPECTED_POSITION"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    PRODUCT_MISMATCH = "PRODUCT_MISMATCH"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrokerPositionReconciliation:
    """Immutable diagnostic result; it never changes execution state."""

    state: PositionReconciliationState
    expected_side: str | None
    expected_contract_symbol: str | None
    expected_quantity: int | None
    broker_positions: tuple[BrokerPosition, ...]
    message: str


class BrokerPositionReconciler:
    """Reconciles one expected long NIFTY option position without side effects."""

    _NIFTY_OPTION_SYMBOL = re.compile(r"^NIFTY\d.*(?:CE|PE)$")

    def __init__(self, expected_product="MIS"):
        if not isinstance(expected_product, str) or not expected_product.strip():
            raise ValueError("Expected product must be a non-empty string.")
        self.expected_product = expected_product.strip()

    def reconcile(self, expected_context, broker_positions, allowed_contract_symbols=None):
        """Compares expected context with normalized, relevant non-zero rows."""
        expected = self._normalize_expected_context(expected_context)
        positions = self._validate_positions(broker_positions)
        allowed_symbols = self._normalize_allowed_symbols(allowed_contract_symbols)
        relevant = tuple(
            position
            for position in positions
            if self._is_relevant(position, allowed_symbols)
        )

        if expected is None:
            if relevant:
                return self._result(
                    PositionReconciliationState.UNEXPECTED_POSITION,
                    None,
                    relevant,
                    "Broker reports relevant non-zero NIFTY option exposure.",
                )
            return self._result(
                PositionReconciliationState.NO_POSITION,
                None,
                (),
                "No relevant broker position is open.",
            )

        side, symbol, quantity = expected
        if len(relevant) > 1:
            return self._result(
                PositionReconciliationState.MULTIPLE_MATCHES,
                expected,
                relevant,
                "Multiple relevant broker positions make exposure ambiguous.",
            )

        exact = tuple(
            position
            for position in relevant
            if position.tradingsymbol.upper() == symbol.upper()
        )
        if not exact:
            if not relevant:
                return self._result(
                    PositionReconciliationState.NO_POSITION,
                    expected,
                    (),
                    "Expected broker position is not open.",
                )

            actual = relevant[0]
            state = (
                PositionReconciliationState.SIDE_MISMATCH
                if self._option_side(actual.tradingsymbol) != side
                else PositionReconciliationState.SYMBOL_MISMATCH
            )
            return self._result(
                state,
                expected,
                relevant,
                "Broker position does not match the expected contract side or symbol.",
            )

        position = exact[0]
        if position.product != self.expected_product:
            return self._result(
                PositionReconciliationState.PRODUCT_MISMATCH,
                expected,
                exact,
                "Broker position product does not match the expected product.",
            )
        if position.quantity < 0:
            return self._result(
                PositionReconciliationState.SIDE_MISMATCH,
                expected,
                exact,
                "Broker position is short while the strategy expects a long option.",
            )
        if position.quantity != quantity:
            return self._result(
                PositionReconciliationState.QUANTITY_MISMATCH,
                expected,
                exact,
                "Broker position quantity does not match the confirmed context.",
            )
        return self._result(
            PositionReconciliationState.MATCH,
            expected,
            exact,
            "Broker position matches the confirmed LIVE context.",
        )

    @staticmethod
    def _validate_positions(broker_positions):
        if not isinstance(broker_positions, (tuple, list)):
            raise TypeError("Broker positions must be a tuple or list.")
        if not all(isinstance(position, BrokerPosition) for position in broker_positions):
            raise TypeError("Broker positions must contain BrokerPosition values.")
        return tuple(broker_positions)

    @staticmethod
    def _normalize_expected_context(context):
        if context is None:
            return None
        try:
            side = context.side
            symbol = context.contract_symbol
            quantity = context.quantity
        except AttributeError as error:
            raise TypeError(
                "Expected context must provide side, contract_symbol, and quantity."
            ) from error

        if side not in ("CE", "PE"):
            raise ValueError("Expected context side must be CE or PE.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Expected context contract symbol must be non-empty.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("Expected context quantity must be a positive integer.")
        return side, symbol.strip(), quantity

    @staticmethod
    def _normalize_allowed_symbols(allowed_contract_symbols):
        if allowed_contract_symbols is None:
            return None
        if not isinstance(allowed_contract_symbols, (tuple, list, set, frozenset)):
            raise TypeError("Allowed contract symbols must be a collection of strings.")
        if not all(isinstance(symbol, str) and symbol.strip() for symbol in allowed_contract_symbols):
            raise ValueError("Allowed contract symbols must be non-empty strings.")
        return {symbol.strip().upper() for symbol in allowed_contract_symbols}

    def _is_relevant(self, position, allowed_symbols):
        symbol = position.tradingsymbol.upper()
        return (
            position.exchange.upper() == "NFO"
            and position.quantity != 0
            and self._NIFTY_OPTION_SYMBOL.fullmatch(symbol) is not None
            and (allowed_symbols is None or symbol in allowed_symbols)
        )

    @staticmethod
    def _option_side(symbol):
        return "CE" if symbol.upper().endswith("CE") else "PE"

    @staticmethod
    def _result(state, expected, broker_positions, message):
        if expected is None:
            side = symbol = quantity = None
        else:
            side, symbol, quantity = expected
        return BrokerPositionReconciliation(
            state=state,
            expected_side=side,
            expected_contract_symbol=symbol,
            expected_quantity=quantity,
            broker_positions=tuple(broker_positions),
            message=message,
        )
