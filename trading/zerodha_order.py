"""Immutable Zerodha order-request data without broker submission."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ZerodhaOrderRequest:
    """Validated data required for a future Zerodha order submission."""

    tradingsymbol: str
    exchange: str
    transaction_type: str
    quantity: int
    order_type: str
    product: str
    validity: str

    def __post_init__(self):
        self._validate_non_empty_string(self.tradingsymbol, "Trading symbol")
        self._validate_non_empty_string(self.exchange, "Exchange")

        if self.transaction_type not in ("BUY", "SELL"):
            raise ValueError("Transaction type must be BUY or SELL.")

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        self._validate_non_empty_string(self.order_type, "Order type")
        self._validate_non_empty_string(self.product, "Product")
        self._validate_non_empty_string(self.validity, "Validity")

        object.__setattr__(self, "tradingsymbol", self.tradingsymbol.strip())
        object.__setattr__(self, "exchange", self.exchange.strip())
        object.__setattr__(self, "order_type", self.order_type.strip())
        object.__setattr__(self, "product", self.product.strip())
        object.__setattr__(self, "validity", self.validity.strip())

    @staticmethod
    def _validate_non_empty_string(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
