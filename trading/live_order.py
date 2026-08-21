"""Broker-independent live-order intent data."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from numbers import Real

from trading.strategy import SignalAction


@dataclass(frozen=True)
class LiveOrderIntent:
    """Validated order information for future broker submission."""

    contract_symbol: str
    side: str
    action: SignalAction
    quantity: int
    reference_price: float
    created_time: datetime

    def __post_init__(self):
        if not isinstance(self.contract_symbol, str) or not self.contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")

        if self.side not in ("CE", "PE"):
            raise ValueError("Side must be CE or PE.")

        if self.action not in (
            SignalAction.BUY_CE,
            SignalAction.BUY_PE,
            SignalAction.EXIT_CE,
            SignalAction.EXIT_PE,
        ):
            raise ValueError("Action must be a supported live order action.")

        expected_side = (
            "CE"
            if self.action in (SignalAction.BUY_CE, SignalAction.EXIT_CE)
            else "PE"
        )
        if self.side != expected_side:
            raise ValueError("Action must match the order side.")

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        if (
            isinstance(self.reference_price, bool)
            or not isinstance(self.reference_price, Real)
            or not isfinite(self.reference_price)
            or self.reference_price <= 0
        ):
            raise ValueError("Reference price must be a positive finite number.")

        if not isinstance(self.created_time, datetime):
            raise TypeError("Created time must be a datetime.")

        if self.created_time.tzinfo is None or self.created_time.utcoffset() is None:
            raise ValueError("Created time must be timezone-aware.")

        object.__setattr__(self, "contract_symbol", self.contract_symbol.strip())
        object.__setattr__(self, "reference_price", float(self.reference_price))
