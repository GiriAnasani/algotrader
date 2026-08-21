"""Isolated submission boundary for validated Zerodha order requests."""

from trading.zerodha_order import ZerodhaOrderRequest


class ZerodhaOrderSubmitter:
    """Submits one validated request through an injected Kite-compatible client."""

    def __init__(self, kite_client):
        if kite_client is None:
            raise ValueError("A Kite-compatible client is required.")

        self._kite_client = kite_client

    def submit(self, order_request):
        """Submits one request exactly once and returns the broker order ID."""
        if not isinstance(order_request, ZerodhaOrderRequest):
            raise TypeError("Order request must be a ZerodhaOrderRequest.")

        return self._kite_client.place_order(
            variety="regular",
            tradingsymbol=order_request.tradingsymbol,
            exchange=order_request.exchange,
            transaction_type=order_request.transaction_type,
            quantity=order_request.quantity,
            order_type=order_request.order_type,
            product=order_request.product,
            validity=order_request.validity,
        )
