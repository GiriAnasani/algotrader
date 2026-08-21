"""Routes strategy actions to paper execution or live-order intents."""

from trading.execution_mode import ExecutionMode
from trading.live_order import LiveOrderIntent
from trading.paper_execution import PaperExecutionEngine
from trading.strategy import SignalAction


class ExecutionRouter:
    """Selects the execution representation without applying strategy rules."""

    def __init__(self, mode=ExecutionMode.PAPER, paper_execution_engine=None):
        if not isinstance(mode, ExecutionMode):
            raise TypeError("Mode must be an ExecutionMode.")

        if (
            paper_execution_engine is not None
            and not isinstance(paper_execution_engine, PaperExecutionEngine)
        ):
            raise TypeError("Paper execution engine must be a PaperExecutionEngine.")

        self.mode = mode
        self.paper_execution_engine = (
            paper_execution_engine or PaperExecutionEngine()
        )

    def route(
        self,
        action,
        *,
        contract_symbol=None,
        side=None,
        quantity=None,
        reference_price=None,
        created_time=None,
    ):
        """Returns a paper position, live intent, or None for HOLD."""
        if action is SignalAction.HOLD:
            return None

        self._validate_action_side(action, side)

        if self.mode is ExecutionMode.PAPER:
            return self.paper_execution_engine.execute(
                action,
                contract_symbol=contract_symbol,
                premium=reference_price,
                quantity=quantity,
                execution_time=created_time,
            )

        return LiveOrderIntent(
            contract_symbol=contract_symbol,
            side=side,
            action=action,
            quantity=quantity,
            reference_price=reference_price,
            created_time=created_time,
        )

    @staticmethod
    def _validate_action_side(action, side):
        if action not in (
            SignalAction.BUY_CE,
            SignalAction.BUY_PE,
            SignalAction.EXIT_CE,
            SignalAction.EXIT_PE,
        ):
            raise ValueError("Action must be a supported execution action.")

        expected_side = (
            "CE"
            if action in (SignalAction.BUY_CE, SignalAction.EXIT_CE)
            else "PE"
        )
        if side != expected_side:
            raise ValueError("Action must match the execution side.")
