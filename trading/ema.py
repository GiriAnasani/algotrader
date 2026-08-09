from trading.candle import Candle


class EMA:
    """
    Exponential Moving Average.
    """

    def __init__(
        self,
        period,
        max_history=500
    ):

        if period <= 0:
            raise ValueError(
                "Period must be greater than zero."
            )

        self.period = period

        # Maximum candles to retain
        self.max_history = max_history

        self.multiplier = (
            2 / (period + 1)
        )

        # Historical candles
        self.candles = []

        # Latest EMA value
        self.current_ema = None

    def _validate_candle(
        self,
        candle
    ):
        """
        Validates candle object.
        """

        if not isinstance(
            candle,
            Candle
        ):
            raise TypeError(
                "Expected Candle object."
            )

    def _calculate_sma(
        self
    ):
        """
        Calculates the initial
        Simple Moving Average.
        """

        closes = []

        for candle in self.candles:
            closes.append(
                candle.close
            )

        return (
            sum(closes)
            / self.period
        )

    def update(
        self,
        candle
    ):
        """
        Updates EMA using
        the latest candle.
        """

        self._validate_candle(
            candle
        )

        self.candles.append(
            candle
        )

        # Wait until enough candles
        if len(self.candles) < self.period:

            return None

        # Initial EMA = SMA
        if self.current_ema is None:

            self.current_ema = (
                self._calculate_sma()
            )

            return self.current_ema

        # Keep only required candles
        if len(self.candles) > self.max_history:

            self.candles.pop(0)

        close = candle.close

        self.current_ema = (
            (
                close -
                self.current_ema
            ) * self.multiplier
        ) + self.current_ema

        return self.current_ema

    def value(
        self
    ):
        """
        Returns the latest EMA value.
        """

        return self.current_ema

    def reset(
        self
    ):
        """
        Resets the EMA indicator.
        """

        self.candles.clear()

        self.current_ema = None