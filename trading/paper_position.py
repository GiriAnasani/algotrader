from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from numbers import Real


class PositionStatus(Enum):
    """
    Supported paper-position lifecycle states.
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class PaperPosition:
    """
    Immutable record of one virtual option position.
    """

    contract_symbol: str
    side: str
    entry_price: float
    quantity: int
    entry_time: datetime
    status: PositionStatus

    def __post_init__(
        self
    ):

        if (
            not isinstance(self.contract_symbol, str)
            or not self.contract_symbol.strip()
        ):
            raise ValueError(
                "Contract symbol must be a non-empty string."
            )

        if self.side not in ("CE", "PE"):
            raise ValueError("Side must be CE or PE.")

        if (
            isinstance(self.entry_price, bool)
            or not isinstance(self.entry_price, Real)
            or not isfinite(self.entry_price)
            or self.entry_price <= 0
        ):
            raise ValueError(
                "Entry price must be a positive finite number."
            )

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        if not isinstance(self.entry_time, datetime):
            raise TypeError("Entry time must be a datetime.")

        if (
            self.entry_time.tzinfo is None
            or self.entry_time.utcoffset() is None
        ):
            raise ValueError("Entry time must be timezone-aware.")

        if not isinstance(self.status, PositionStatus):
            raise TypeError("Status must be a PositionStatus.")

        object.__setattr__(
            self,
            "contract_symbol",
            self.contract_symbol.strip(),
        )
        object.__setattr__(
            self,
            "entry_price",
            float(self.entry_price),
        )
