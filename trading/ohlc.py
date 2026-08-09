from datetime import datetime


class OHLCBuilder:
    """
    Builds a single OHLC candle
    from incoming live ticks.
    """

    def __init__(self):

        self.current_candle = None

    def process_tick(
        self,
        price,
        timestamp=None
    ):
        """
        Processes one incoming tick.
        """

        if timestamp is None:
            timestamp = datetime.now()

        # Create first candle
        if self.current_candle is None:

            self.current_candle = {

                "time": timestamp,

                "open": price,

                "high": price,

                "low": price,

                "close": price

            }

            return self.current_candle

        # Update High

        if price > self.current_candle["high"]:

            self.current_candle["high"] = price

        # Update Low

        if price < self.current_candle["low"]:

            self.current_candle["low"] = price

        # Update Close

        self.current_candle["close"] = price

        return self.current_candle

    def get_current_candle(self):
        """
        Returns the latest candle.
        """

        return self.current_candle

    def reset(self):
        """
        Starts a new candle.
        """

        self.current_candle = None