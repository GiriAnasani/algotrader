"""Broker-independent immutable representation of one broker position row."""

from dataclasses import dataclass
from math import isfinite
from numbers import Real


@dataclass(frozen=True)
class BrokerPosition:
    """Validated broker-reported position without account or P&L data."""

    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: float
    product: str

    def __post_init__(self):
        self._validate_non_empty_string(self.tradingsymbol, "Trading symbol")
        self._validate_non_empty_string(self.exchange, "Exchange")
        self._validate_non_empty_string(self.product, "Product")

        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise ValueError("Quantity must be an integer.")

        if (
            isinstance(self.average_price, bool)
            or not isinstance(self.average_price, Real)
            or not isfinite(self.average_price)
            or self.average_price < 0
        ):
            raise ValueError("Average price must be a finite non-negative number.")

        object.__setattr__(self, "tradingsymbol", self.tradingsymbol.strip())
        object.__setattr__(self, "exchange", self.exchange.strip())
        object.__setattr__(self, "product", self.product.strip())
        object.__setattr__(self, "average_price", float(self.average_price))

    @staticmethod
    def _validate_non_empty_string(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
