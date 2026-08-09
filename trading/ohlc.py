from datetime import datetime

from trading.candle import Candle


class OHLCBuilder:
    """
    Builds OHLC candles
    from incoming live ticks.
    """

    def __init__(
        self,
        interval_seconds=60,
        max_candles=500
    ):

        # Active candle
        self.current_candle = None

        # Latest completed candle
        self.completed_candle = None

        # Candle history
        self.completed_candles = []

        # Configuration
        self.interval_seconds = interval_seconds
        self.max_candles = max_candles

        # Current candle start time
        self.candle_start_time = None

    # ==================================================
    # Private Methods
    # ==================================================

    def _validate_tick(
        self,
        tick
    ):
        """
        Validates Zerodha tick.
        """

        if not isinstance(
            tick,
            dict
        ):
            raise TypeError(
                "Tick must be a dictionary."
            )

        if "last_price" not in tick:
            raise KeyError(
                "Tick does not contain "
                "'last_price'."
            )

    def _extract_tick_data(
        self,
        tick
    ):
        """
        Extracts price and timestamp.
        """

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

        return (
            price,
            timestamp
        )

    def _create_new_candle(
        self,
        price,
        timestamp
    ):
        """
        Creates a new candle.
        """

        self.candle_start_time = timestamp

        self.current_candle = Candle(
            time=timestamp,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=0
        )

    def _update_current_candle(
        self,
        price
    ):
        """
        Updates the active candle.
        """

        if price > self.current_candle.high:
            self.current_candle.high = price

        if price < self.current_candle.low:
            self.current_candle.low = price

        self.current_candle.close = price

    def _is_candle_complete(
        self,
        timestamp
    ):
        """
        Checks whether the current candle
        has completed.
        """

        if self.candle_start_time is None:
            return False

        elapsed = (
            timestamp -
            self.candle_start_time
        ).total_seconds()

        return (
            elapsed >=
            self.interval_seconds
        )

    def _finalize_candle(
        self
    ):
        """
        Stores completed candle.
        """

        if self.current_candle is None:
            return

        self.completed_candle = (
            self.current_candle
        )

        self.completed_candles.append(
            self.current_candle
        )

        if (
            len(self.completed_candles)
            > self.max_candles
        ):
            self.completed_candles.pop(0)

    # ==================================================
    # Public Methods
    # ==================================================

    def process_tick(
        self,
        tick
    ):
        """
        Processes one incoming
        Zerodha market tick.
        """

        # Reset latest completed candle
        self.completed_candle = None

        self._validate_tick(
            tick
        )

        price, timestamp = (
            self._extract_tick_data(
                tick
            )
        )

        # ---------------------------------
        # Create First Candle
        # ---------------------------------

        if self.current_candle is None:

            self._create_new_candle(
                price,
                timestamp
            )

            return {
                "current_candle":
                    self.current_candle,
                "completed_candle":
                    self.completed_candle
            }

        # ---------------------------------
        # Check Candle Completion
        # ---------------------------------

        if self._is_candle_complete(
            timestamp
        ):

            self._finalize_candle()

            self._create_new_candle(
                price,
                timestamp
            )

            return {
                "current_candle":
                    self.current_candle,
                "completed_candle":
                    self.completed_candle
            }

        # ---------------------------------
        # Update Current Candle
        # ---------------------------------

        self._update_current_candle(
            price
        )

        return {
            "current_candle":
                self.current_candle,
            "completed_candle":
                self.completed_candle
        }

    def get_current_candle(
        self
    ):
        """
        Returns the active candle.
        """

        return self.current_candle

    def get_completed_candle(
        self
    ):
        """
        Returns the latest completed candle.
        """

        return self.completed_candle

    def get_completed_candles(
        self
    ):
        """
        Returns all completed candles.
        """

        return self.completed_candles

    def get_latest_completed_candle(
        self
    ):
        """
        Returns the latest completed candle
        from history.
        """

        if not self.completed_candles:
            return None

        return self.completed_candles[-1]

    def clear_history(
        self
    ):
        """
        Clears completed candle history.
        """

        self.completed_candles.clear()

        self.completed_candle = None

    def reset(
        self
    ):
        """
        Resets the OHLC builder.
        """

        self.current_candle = None

        self.completed_candle = None

        self.completed_candles.clear()

        self.candle_start_time = None

        return None