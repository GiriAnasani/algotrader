from datetime import datetime

from trading.candle import Candle


class OHLCBuilder:
    """
    Builds a single OHLC candle
    from incoming live ticks.
    """

    def __init__(self):

        self.current_candle = None

    def process_tick(
        self,
        tick
    ):
        """
        Processes one incoming market tick.

        Parameters
        ----------
        tick : dict
            Zerodha tick dictionary.
        """

        if not isinstance(tick, dict):
            raise TypeError(
                "Tick must be a dictionary."
            )

        if "last_price" not in tick:
            raise KeyError(
                "Tick does not contain 'last_price'."
            )

        price = tick["last_price"]

        timestamp = tick.get(
            "exchange_timestamp"
        )

        if timestamp is None:
            timestamp = tick.get(
                "timestamp"
            )

        if timestamp is None:
            timestamp = datetime.now()

        # -----------------------------
        # Create First Candle
        # -----------------------------

        if self.current_candle is None:

            self.current_candle = Candle(
                time=timestamp,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0
            )

            return self.current_candle

        # -----------------------------
        # Update High
        # -----------------------------

        if price > self.current_candle.high:

            self.current_candle.high = price

        # -----------------------------
        # Update Low
        # -----------------------------

        if price < self.current_candle.low:

            self.current_candle.low = price

        # -----------------------------
        # Update Close
        # -----------------------------

        self.current_candle.close = price

        return self.current_candle

    def get_current_candle(self):
        """
        Returns the latest candle.
        """

        return self.current_candle

    def reset(self):
        """
        Clears the current candle.
        """

        self.current_candle = None