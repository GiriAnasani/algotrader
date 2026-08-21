"""Pure normalization of Zerodha order-history records."""

from collections.abc import Mapping

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus


class ZerodhaOrderStatusAdapter:
    """Converts one Zerodha order-history record into a normalized status."""

    _STATE_BY_STATUS = {
        "COMPLETE": BrokerOrderState.COMPLETE,
        "OPEN": BrokerOrderState.OPEN,
        "TRIGGER PENDING": BrokerOrderState.OPEN,
        "VALIDATION PENDING": BrokerOrderState.SUBMITTED,
        "PUT ORDER REQ RECEIVED": BrokerOrderState.SUBMITTED,
        "OPEN PENDING": BrokerOrderState.SUBMITTED,
        "MODIFY VALIDATION PENDING": BrokerOrderState.SUBMITTED,
        "MODIFY PENDING": BrokerOrderState.SUBMITTED,
        "CANCEL PENDING": BrokerOrderState.SUBMITTED,
        "CANCELLED": BrokerOrderState.CANCELLED,
        "REJECTED": BrokerOrderState.REJECTED,
    }

    def to_broker_order_status(self, record):
        """Normalizes one broker history record without broker access."""
        if not isinstance(record, Mapping):
            raise TypeError("Order history record must be a mapping.")

        broker_status = record["status"]
        if not isinstance(broker_status, str) or not broker_status.strip():
            raise ValueError("Broker status must be a non-empty string.")

        normalized_status = broker_status.strip().upper()
        return BrokerOrderStatus(
            order_id=record["order_id"],
            state=self._STATE_BY_STATUS.get(
                normalized_status,
                BrokerOrderState.UNKNOWN,
            ),
            filled_quantity=record["filled_quantity"],
            pending_quantity=record["pending_quantity"],
            average_price=record["average_price"],
            broker_status=broker_status,
            status_message=record.get("status_message"),
        )
