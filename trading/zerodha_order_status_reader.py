"""Isolated single-read access to Zerodha order-history status."""

from trading.zerodha_order_status_adapter import ZerodhaOrderStatusAdapter


class ZerodhaOrderStatusReader:
    """Reads one order history through an injected Kite-compatible client."""

    def __init__(self, kite_client, adapter=None):
        if kite_client is None:
            raise ValueError("A Kite-compatible client is required.")

        if adapter is None:
            adapter = ZerodhaOrderStatusAdapter()

        if not isinstance(adapter, ZerodhaOrderStatusAdapter):
            raise TypeError("Adapter must be a ZerodhaOrderStatusAdapter.")

        self._kite_client = kite_client
        self._adapter = adapter

    def read(self, order_id):
        """Reads exactly one broker order history and normalizes its latest record."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("Order ID must be a non-empty string.")

        history = self._kite_client.order_history(order_id.strip())
        if not isinstance(history, list):
            raise TypeError("Order history must be a list.")

        if not history:
            raise ValueError("Order history is empty.")

        status = self._adapter.to_broker_order_status(history[-1])
        if status.order_id != order_id.strip():
            raise ValueError("Broker response order ID does not match the request.")

        return status
