import pandas as pd
from datetime import datetime, timedelta

from kiteconnect import KiteTicker

from core.config import (
    KITE_API_KEY
)

from trading.ohlc import OHLCBuilder
from trading.indicator_engine import IndicatorEngine


class MarketData:
    """
    Handles downloading historical
    and live market data.
    """

    def __init__(
        self,
        kite,
        instruments
    ):

        self.kite = kite

        self.instruments = instruments

        # OHLC Builder
        self.ohlc = OHLCBuilder()

        # Indicators
        self.indicator_engine = IndicatorEngine()

        # Completed candle counter
        self.completed_candle_count = 0

    # ==================================================
    # Historical Data
    # ==================================================

    def get_history(
        self,
        symbol,
        interval="day",
        days=30
    ):

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "Trading symbol cannot be empty."
            )

        if days <= 0:
            raise ValueError(
                "Days must be greater than zero."
            )

        valid_intervals = [
            "minute",
            "3minute",
            "5minute",
            "10minute",
            "15minute",
            "30minute",
            "60minute",
            "day"
        ]

        interval = interval.lower()

        if interval not in valid_intervals:
            raise ValueError(
                f"Unsupported interval: {interval}"
            )
        instrument_token = (
            self.instruments.get_instrument_token(
                symbol
            )
        )

        to_date = datetime.now()

        from_date = to_date - timedelta(
            days=days
        )

        data = self.kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval
        )

        return pd.DataFrame(data)

    # ==================================================
    # Live Market Data
    # ==================================================

    def connect_live(
        self,
        symbol
    ):

        instrument_token = (
            self.instruments.get_instrument_token(
                symbol
            )
        )

        kws = KiteTicker(
            KITE_API_KEY,
            self.kite.access_token
        )

        def on_connect(
            ws,
            response
        ):

            print()
            print("=" * 60)
            print(
                "Connected to Zerodha WebSocket"
            )
            print("=" * 60)

            ws.subscribe(
                [instrument_token]
            )

            ws.set_mode(
                ws.MODE_LTP,
                [instrument_token]
            )

            print()
            print(
                f"Subscribed : {symbol}"
            )
            print()

        def on_ticks(
            ws,
            ticks
        ):

            for tick in ticks:

                result = (
                    self.ohlc.process_tick(
                        tick
                    )
                )

                completed_candle = (
                    result[
                        "completed_candle"
                    ]
                )

                # -------------------------
                # Completed Candle
                # -------------------------

                if completed_candle is not None:

                    self.completed_candle_count += 1

                    print()
                    print("=" * 60)
                    print(
                        f"COMPLETED 1-MINUTE CANDLE "
                        f"#{self.completed_candle_count}"
                    )
                    print("=" * 60)

                    print(
                        f"Symbol : {symbol}"
                    )

                    print(
                        f"Time   : "
                        f"{completed_candle.time}"
                    )

                    print(
                        f"Open   : "
                        f"{completed_candle.open}"
                    )

                    print(
                        f"High   : "
                        f"{completed_candle.high}"
                    )

                    print(
                        f"Low    : "
                        f"{completed_candle.low}"
                    )

                    print(
                        f"Close  : "
                        f"{completed_candle.close}"
                    )

                    print()

                    # -------------------------
                    # Update Indicators
                    # -------------------------

                    ema_values = (
                        self.indicator_engine.update(
                            completed_candle
                        )
                    )

                    if ema_values:

                        print("=" * 60)
                        print("EMA INDICATORS")
                        print("=" * 60)

                        for period, ema_value in ema_values.items():

                            print(
                                f"EMA {period} : "
                                f"{ema_value:.2f}"
                            )

                        print()

                # -------------------------
                # Current Candle
                # -------------------------

                current_candle = (
                    result[
                        "current_candle"
                    ]
                )

                # -------------------------
                # WebSocket Tick Received
                # -------------------------

                # Current candle is intentionally
                # not printed for every tick.
                # This keeps terminal output clean
                # and makes candle completion easier
                # to verify.

        def on_close(
            ws,
            code,
            reason
        ):

            print()
            print(
                "WebSocket Closed"
            )

        def on_error(
            ws,
            code,
            reason
        ):

            print()
            print(
                f"Connection Error : {reason}"
            )

        kws.on_connect = on_connect
        kws.on_ticks = on_ticks
        kws.on_close = on_close
        kws.on_error = on_error

        kws.connect()
