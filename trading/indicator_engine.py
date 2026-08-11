from trading.candle import Candle
from trading.ema import EMA


class IndicatorEngine:
    """
    Updates configured indicators from completed candles.
    """

    DEFAULT_EMA_PERIODS = [10, 20, 50, 100, 200]

    def __init__(
        self,
        ema_periods=None
    ):

        if ema_periods is None:
            ema_periods = self.DEFAULT_EMA_PERIODS

        periods = list(ema_periods)

        if len(periods) != len(set(periods)):
            raise ValueError(
                "EMA periods must be unique."
            )

        self.emas = {
            period: EMA(
                period=period
            )
            for period in periods
        }

    def update(
        self,
        candle
    ):
        """
        Updates all indicators with one completed candle.
        """

        if not isinstance(
            candle,
            Candle
        ):
            raise TypeError(
                "Expected Candle object."
            )

        values = {}

        for period, ema in self.emas.items():

            value = ema.update(
                candle
            )

            if value is not None:
                values[period] = value

        return values
