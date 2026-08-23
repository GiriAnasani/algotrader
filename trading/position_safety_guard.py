"""Broker-independent action policy for trusted position safety results."""

from trading.position_safety import PositionSafetyResult, PositionSafetyState
from trading.strategy import SignalAction


class PositionSafetyViolationError(RuntimeError):
    """Raised when a position-changing action violates position safety."""

    def __init__(self, action, safety_result, message):
        if not isinstance(action, SignalAction):
            raise TypeError("Action must be a SignalAction.")
        if not isinstance(safety_result, PositionSafetyResult):
            raise TypeError("Safety result must be a PositionSafetyResult.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message must be a non-empty string.")
        self.action = action
        self.safety_result = safety_result
        self.message = message.strip()
        super().__init__(self.message)


class PositionSafetyGuard:
    """Validate whether one position-changing action is safe to submit."""

    _BUY_ACTIONS = (SignalAction.BUY_CE, SignalAction.BUY_PE)
    _EXIT_ACTIONS = (SignalAction.EXIT_CE, SignalAction.EXIT_PE)

    def validate(self, action, safety_result, contract_symbol, quantity):
        """Return None when permitted; otherwise raise with exact diagnostics."""
        if not isinstance(action, SignalAction) or action not in (
            *self._BUY_ACTIONS,
            *self._EXIT_ACTIONS,
        ):
            raise TypeError("Action must be a supported position SignalAction.")
        if not isinstance(safety_result, PositionSafetyResult):
            raise TypeError("Safety result must be a PositionSafetyResult.")
        if action in self._BUY_ACTIONS:
            if safety_result.state is not PositionSafetyState.SAFE_FLAT:
                self._reject(
                    action, safety_result, "BUY requires safely flat position state."
                )
            self._validate_exposure(contract_symbol, quantity)
            return None

        if safety_result.state is not PositionSafetyState.SAFE_OPEN:
            self._reject(action, safety_result, "EXIT requires safely open position state.")

        self._validate_exposure(contract_symbol, quantity)

        managed = (
            safety_result.recovery_result
            .synchronization_result.managed_position
        )
        expected_side = "CE" if action is SignalAction.EXIT_CE else "PE"
        if (
            managed is None
            or managed.side.value != expected_side
            or managed.contract_symbol != contract_symbol.strip()
            or managed.quantity != quantity
        ):
            self._reject(
                action,
                safety_result,
                "EXIT does not match synchronized managed exposure.",
            )
        return None

    @staticmethod
    def _validate_exposure(contract_symbol, quantity):
        if not isinstance(contract_symbol, str) or not contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("Quantity must be a positive integer.")

    @staticmethod
    def _reject(action, safety_result, message):
        raise PositionSafetyViolationError(action, safety_result, message)
