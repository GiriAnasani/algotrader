"""Immutable records of completed paper option trades."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from numbers import Real

from trading.strategy import SignalAction


@dataclass(frozen=True)
class PaperTrade:
    """One completed virtual option trade."""

    contract_symbol: str
    side: str
    quantity: int
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    exit_action: SignalAction
    strategy_name: str
    exit_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.contract_symbol, str) or not self.contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")

        if self.side not in ("CE", "PE"):
            raise ValueError("Side must be CE or PE.")

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        self._validate_price(self.entry_price, "Entry price")
        self._validate_price(self.exit_price, "Exit price")
        self._validate_time(self.entry_time, "Entry time")
        self._validate_time(self.exit_time, "Exit time")

        if self.exit_time < self.entry_time:
            raise ValueError("Exit time cannot be earlier than entry time.")

        if not isinstance(self.exit_action, SignalAction):
            raise TypeError("Exit action must be a SignalAction.")

        expected_action = (
            SignalAction.EXIT_CE if self.side == "CE" else SignalAction.EXIT_PE
        )
        if self.exit_action is not expected_action:
            raise ValueError("Exit action must match the trade side.")

        if not isinstance(self.strategy_name, str) or not self.strategy_name.strip():
            raise ValueError("Strategy name must be a non-empty string.")

        if self.exit_reason is not None and not isinstance(self.exit_reason, str):
            raise TypeError("Exit reason must be a string or None.")

        object.__setattr__(self, "contract_symbol", self.contract_symbol.strip())
        object.__setattr__(self, "entry_price", float(self.entry_price))
        object.__setattr__(self, "exit_price", float(self.exit_price))
        object.__setattr__(self, "strategy_name", self.strategy_name.strip())

    @staticmethod
    def _validate_price(price, label):
        if (
            isinstance(price, bool)
            or not isinstance(price, Real)
            or not isfinite(price)
            or price <= 0
        ):
            raise ValueError(f"{label} must be a positive finite number.")

    @staticmethod
    def _validate_time(value, label):
        if not isinstance(value, datetime):
            raise TypeError(f"{label} must be a datetime.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} must be timezone-aware.")
