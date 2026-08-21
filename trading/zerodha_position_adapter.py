"""Pure normalization of Zerodha position rows."""

from collections.abc import Mapping

from trading.broker_position import BrokerPosition


class ZerodhaPositionAdapter:
    """Converts one Zerodha position row to a BrokerPosition."""

    def to_broker_position(self, row):
        if not isinstance(row, Mapping):
            raise TypeError("Zerodha position row must be a mapping.")

        required_fields = (
            "tradingsymbol",
            "exchange",
            "quantity",
            "average_price",
            "product",
        )
        missing_fields = [field for field in required_fields if field not in row]
        if missing_fields:
            raise ValueError(
                "Zerodha position row is missing required fields: "
                + ", ".join(missing_fields)
            )

        return BrokerPosition(
            tradingsymbol=row["tradingsymbol"],
            exchange=row["exchange"],
            quantity=row["quantity"],
            average_price=row["average_price"],
            product=row["product"],
        )
