"""Single-read access to Zerodha's listed broker orders."""

from collections.abc import Mapping

from trading.broker_order_summary import BrokerOrderSummary
from trading.zerodha_order_status_adapter import ZerodhaOrderStatusAdapter


class ZerodhaOrderListReader:
    """Reads and normalizes one ``kite.orders()`` response through injection."""

    def __init__(self, kite_client, status_adapter=None):
        if kite_client is None:
            raise ValueError("A Kite-compatible client is required.")
        if status_adapter is None:
            status_adapter = ZerodhaOrderStatusAdapter()
        if not isinstance(status_adapter, ZerodhaOrderStatusAdapter):
            raise TypeError("Status adapter must be a ZerodhaOrderStatusAdapter.")
        self._kite_client = kite_client
        self._status_adapter = status_adapter

    def read(self):
        """Calls ``orders()`` once and returns immutable normalized summaries."""
        records = self._kite_client.orders()
        if not isinstance(records, list):
            raise TypeError("Broker orders response must be a list.")
        return tuple(self._to_summary(record) for record in records)

    def _to_summary(self, record):
        if not isinstance(record, Mapping):
            raise TypeError("Broker order record must be a mapping.")
        required_fields = (
            "order_id", "tradingsymbol", "exchange", "transaction_type",
            "quantity", "product",
        )
        missing = [field for field in required_fields if field not in record]
        if missing:
            raise ValueError("Broker order record is missing required fields: " + ", ".join(missing))
        return BrokerOrderSummary(
            order_id=record["order_id"],
            tradingsymbol=record["tradingsymbol"],
            exchange=record["exchange"],
            transaction_type=record["transaction_type"],
            quantity=record["quantity"],
            product=record["product"],
            status=self._status_adapter.to_broker_order_status(record),
        )
