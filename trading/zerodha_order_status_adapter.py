"""Pure normalization of Zerodha order-history records."""

from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

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
    _EXCHANGE_TIMEZONE = ZoneInfo("Asia/Kolkata")
    _BROKER_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

    def to_broker_order_status(self, record):
        """Normalizes one broker history record without broker access."""
        if not isinstance(record, Mapping):
            raise TypeError("Order history record must be a mapping.")

        broker_status = record["status"]
        if not isinstance(broker_status, str) or not broker_status.strip():
            raise ValueError("Broker status must be a non-empty string.")

        normalized_status = broker_status.strip().upper()
        state = self._STATE_BY_STATUS.get(
            normalized_status,
            BrokerOrderState.UNKNOWN,
        )
        return BrokerOrderStatus(
            order_id=record["order_id"],
            state=state,
            filled_quantity=record["filled_quantity"],
            pending_quantity=record["pending_quantity"],
            average_price=record["average_price"],
            broker_status=broker_status,
            status_message=record.get("status_message"),
            fill_timestamp=self._full_fill_timestamp(record, state),
        )

    @classmethod
    def _full_fill_timestamp(cls, record, state):
        filled_quantity = record["filled_quantity"]
        is_confirmed_full_fill = (
            state is BrokerOrderState.COMPLETE
            and isinstance(filled_quantity, int)
            and not isinstance(filled_quantity, bool)
            and filled_quantity > 0
            and record["pending_quantity"] == 0
            and record.get("quantity") == filled_quantity
        )
        if not is_confirmed_full_fill:
            return None

        timestamp = record.get("exchange_update_timestamp")
        if timestamp is None:
            return None
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                return timestamp.replace(tzinfo=cls._EXCHANGE_TIMEZONE)
            return timestamp
        if isinstance(timestamp, str):
            parsed = datetime.strptime(timestamp, cls._BROKER_TIMESTAMP_FORMAT)
            return parsed.replace(tzinfo=cls._EXCHANGE_TIMEZONE)
        raise TypeError(
            "Exchange update timestamp must be a datetime, string, or None."
        )
