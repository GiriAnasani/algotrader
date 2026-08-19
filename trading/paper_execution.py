"""Broker-independent execution records for paper option positions."""

from datetime import datetime
from math import isfinite
from numbers import Real

from trading.paper_position import PaperPosition, PositionStatus
from trading.strategy import SignalAction


class PaperExecutionEngine:
    """Converts supported strategy actions into paper-position state changes."""

    def __init__(self):
        self.active_position = None

    def execute(
        self,
        action,
        *,
        contract_symbol=None,
        premium=None,
        quantity=None,
        execution_time=None,
    ):
        """Records one BUY or EXIT strategy action and returns its position."""
        self._validate_action(action)
        self._validate_premium(premium)
        self._validate_execution_time(execution_time)

        if action in (SignalAction.BUY_CE, SignalAction.BUY_PE):
            return self._buy(
                action,
                contract_symbol,
                premium,
                quantity,
                execution_time,
            )

        return self._exit(action)

    def _buy(self, action, contract_symbol, premium, quantity, execution_time):
        if self.active_position is not None:
            raise ValueError("A paper position is already active.")

        self._validate_contract_symbol(contract_symbol)
        self._validate_quantity(quantity)
        side = "CE" if action is SignalAction.BUY_CE else "PE"
        position = PaperPosition(
            contract_symbol=contract_symbol,
            side=side,
            entry_price=premium,
            quantity=quantity,
            entry_time=execution_time,
            status=PositionStatus.OPEN,
        )
        self.active_position = position
        return position

    def _exit(self, action):
        position = self.active_position
        if position is None:
            raise ValueError("No paper position is active.")

        side = "CE" if action is SignalAction.EXIT_CE else "PE"
        if position.side != side:
            raise ValueError("Exit action does not match the active position side.")

        closed_position = PaperPosition(
            contract_symbol=position.contract_symbol,
            side=position.side,
            entry_price=position.entry_price,
            quantity=position.quantity,
            entry_time=position.entry_time,
            status=PositionStatus.CLOSED,
        )
        self.active_position = None
        return closed_position

    @staticmethod
    def _validate_action(action):
        if action not in (
            SignalAction.BUY_CE,
            SignalAction.BUY_PE,
            SignalAction.EXIT_CE,
            SignalAction.EXIT_PE,
        ):
            raise ValueError("Action must be a supported paper execution action.")

    @staticmethod
    def _validate_contract_symbol(contract_symbol):
        if not isinstance(contract_symbol, str) or not contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")

    @staticmethod
    def _validate_premium(premium):
        if (
            isinstance(premium, bool)
            or not isinstance(premium, Real)
            or not isfinite(premium)
            or premium <= 0
        ):
            raise ValueError("Premium must be a positive finite number.")

    @staticmethod
    def _validate_quantity(quantity):
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

    @staticmethod
    def _validate_execution_time(execution_time):
        if not isinstance(execution_time, datetime):
            raise TypeError("Execution time must be a datetime.")

        if execution_time.tzinfo is None or execution_time.utcoffset() is None:
            raise ValueError("Execution time must be timezone-aware.")
