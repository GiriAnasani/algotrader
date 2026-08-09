from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    """
    Represents one OHLC candle.
    """

    time: datetime

    open: float

    high: float

    low: float

    close: float

    volume: int = 0