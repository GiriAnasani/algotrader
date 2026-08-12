from datetime import datetime
from zoneinfo import ZoneInfo

from trading.candle import Candle


EXCHANGE_TIMEZONE = ZoneInfo(
    "Asia/Kolkata"
)


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

        # Exchange timestamp timezone
        self.exchange_timezone = EXCHANGE_TIMEZONE

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
        Extracts price and exchange timestamp.
        """

        price = tick["last_price"]

        timestamp = tick.get(
            "exchange_timestamp"
        )

        if timestamp is None:
            raise KeyError(
                "Tick does not contain "
                "'exchange_timestamp'."
            )

        return (
            price,
            self._normalize_timestamp(
                timestamp
            )
        )

    def _normalize_timestamp(
        self,
        timestamp
    ):
        """
        Normalizes timestamps to the exchange timezone.
        """

        if not isinstance(
            timestamp,
            datetime
        ):
            raise TypeError(
                "Exchange timestamp must be a datetime."
            )

        if timestamp.tzinfo is None:
            return timestamp.replace(
                tzinfo=self.exchange_timezone
            )

        return timestamp.astimezone(
            self.exchange_timezone
        )

    def _get_minute_bucket(
        self,
        timestamp
    ):
        """
        Returns the canonical exchange-minute start.
        """

        return timestamp.replace(
            second=0,
            microsecond=0
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
        Checks whether a new exchange-minute
        candle has started.
        """

        if self.candle_start_time is None:
            return False

        return (
            timestamp >
            self.candle_start_time
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

        minute_bucket = self._get_minute_bucket(
            timestamp
        )

        # ---------------------------------
        # Create First Candle
        # ---------------------------------

        if self.current_candle is None:

            self._create_new_candle(
                price,
                minute_bucket
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

        if minute_bucket < self.candle_start_time:
            raise ValueError(
                "Tick belongs to an earlier "
                "exchange-minute bucket."
            )

        if self._is_candle_complete(
            minute_bucket
        ):

            self._finalize_candle()

            self._create_new_candle(
                price,
                minute_bucket
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
