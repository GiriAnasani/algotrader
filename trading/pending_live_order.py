"""Immutable continuity data for a submitted but unresolved LIVE order."""

from dataclasses import dataclass
from datetime import datetime

from trading.strategy import SignalAction


@dataclass(frozen=True)
class PendingLiveOrder:
    """Information needed for one explicit broker-status reconciliation."""

    order_id: str
    action: SignalAction
    side: str
    contract_symbol: str
    quantity: int
    created_time: datetime

    def __post_init__(self):
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("Order ID must be a non-empty string.")

        if self.action not in (
            SignalAction.BUY_CE,
            SignalAction.BUY_PE,
            SignalAction.EXIT_CE,
            SignalAction.EXIT_PE,
        ):
            raise ValueError("Action must be a supported pending LIVE order action.")

        if self.side not in ("CE", "PE"):
            raise ValueError("Side must be CE or PE.")

        expected_side = (
            "CE"
            if self.action in (SignalAction.BUY_CE, SignalAction.EXIT_CE)
            else "PE"
        )
        if self.side != expected_side:
            raise ValueError("Action must match the pending LIVE order side.")

        if not isinstance(self.contract_symbol, str) or not self.contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        if not isinstance(self.created_time, datetime):
            raise TypeError("Created time must be a datetime.")

        if self.created_time.tzinfo is None or self.created_time.utcoffset() is None:
            raise ValueError("Created time must be timezone-aware.")

        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "contract_symbol", self.contract_symbol.strip())
