"""Broker-independent immutable summary of one listed broker order."""

from dataclasses import dataclass

from trading.broker_order_status import BrokerOrderStatus


@dataclass(frozen=True)
class BrokerOrderSummary:
    """Listed-order identity and normalized status, without execution behavior."""

    order_id: str
    tradingsymbol: str
    exchange: str
    transaction_type: str
    quantity: int
    product: str
    status: BrokerOrderStatus

    def __post_init__(self):
        for value, label in (
            (self.order_id, "Order ID"),
            (self.tradingsymbol, "Trading symbol"),
            (self.exchange, "Exchange"),
            (self.product, "Product"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string.")
        if self.transaction_type not in ("BUY", "SELL"):
            raise ValueError("Transaction type must be BUY or SELL.")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError("Quantity must be a positive integer.")
        if not isinstance(self.status, BrokerOrderStatus):
            raise TypeError("Status must be a BrokerOrderStatus.")
        if self.status.order_id != self.order_id.strip():
            raise ValueError("Order summary ID must match status order ID.")

        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "tradingsymbol", self.tradingsymbol.strip())
        object.__setattr__(self, "exchange", self.exchange.strip())
        object.__setattr__(self, "product", self.product.strip())
