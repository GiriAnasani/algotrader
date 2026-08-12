from datetime import datetime
from math import isfinite
from numbers import Real

from trading.candle import Candle


REQUIRED_CANDLE_FIELDS = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def historical_row_to_candle(
    row
):
    """
    Converts one Zerodha historical candle row to a Candle.
    """

    for field in REQUIRED_CANDLE_FIELDS:

        try:
            value = row[field]
        except (KeyError, TypeError):
            raise KeyError(
                f"Historical candle row is missing '{field}'."
            ) from None

        if value is None:
            raise ValueError(
                f"Historical candle field '{field}' cannot be None."
            )

    timestamp = row["date"]

    if not isinstance(timestamp, datetime):
        raise TypeError(
            "Historical candle field 'date' must be a datetime."
        )

    prices = {
        field: row[field]
        for field in ("open", "high", "low", "close")
    }

    for field, value in prices.items():

        if not isinstance(value, Real) or not isfinite(value):
            raise TypeError(
                f"Historical candle field '{field}' must be a finite number."
            )

    volume = row["volume"]

    if not isinstance(volume, Real) or not isfinite(volume):
        raise TypeError(
            "Historical candle field 'volume' must be a finite number."
        )

    return Candle(
        time=timestamp,
        open=prices["open"],
        high=prices["high"],
        low=prices["low"],
        close=prices["close"],
        volume=volume
    )
