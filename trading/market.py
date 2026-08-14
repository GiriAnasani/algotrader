import pandas as pd
from datetime import datetime, timedelta

from kiteconnect import KiteTicker

from core.config import (
    KITE_API_KEY
)

from trading.ohlc import (
    OHLCBuilder,
    EXCHANGE_TIMEZONE,
)
from trading.indicator_engine import IndicatorEngine
from trading.historical import historical_row_to_candle
from trading.candle import Candle
from trading.strategy import (
    IndicatorSnapshot,
    StrategyEngine,
)


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

        # Strategy
        self.strategy_engine = StrategyEngine()
        self.latest_strategy_result = None

        # Processed completed candle identities
        self.processed_candle_times = set()

        # Completed candle counter
        self.completed_candle_count = 0

        # C7 NIFTY option subscription and premium state
        self.nifty_index_token = None
        self.nifty_option_pair = None
        self.option_tokens = {}
        self.latest_option_premiums = {}

    def _select_nifty_option_pair(
        self,
        ws,
        spot_price
    ):
        """
        Selects and subscribes the session's ATM NIFTY option pair.
        """

        if self.nifty_option_pair is not None:
            return self.nifty_option_pair

        option_pair = self.instruments.get_nifty_option_pair(
            spot_price
        )

        tokens = {
            "CE": option_pair["CE"]["instrument_token"],
            "PE": option_pair["PE"]["instrument_token"]
        }

        ws.subscribe(list(tokens.values()))
        ws.set_mode(ws.MODE_FULL, list(tokens.values()))

        self.nifty_option_pair = option_pair
        self.option_tokens = {
            token: option_type
            for option_type, token in tokens.items()
        }

        print(
            "Selected NIFTY ATM option pair: "
            f"{option_pair['CE']['tradingsymbol']} / "
            f"{option_pair['PE']['tradingsymbol']}"
        )

        return option_pair

    def _cache_option_premium(
        self,
        tick
    ):
        """
        Caches a subscribed option's latest valid premium.
        """

        option_type = self.option_tokens.get(
            tick.get("instrument_token")
        )

        if option_type is None:
            return False

        premium = tick.get("last_price")

        if not isinstance(premium, (int, float)) or premium <= 0:
            return False

        self.latest_option_premiums[option_type] = float(premium)

        return True

    def _get_candle_identity(
        self,
        candle
    ):
        """
        Returns the canonical candle identity.
        """

        if not isinstance(
            candle,
            Candle
        ):
            raise TypeError(
                "Expected Candle object."
            )

        if not isinstance(
            candle.time,
            datetime
        ):
            raise TypeError(
                "Candle time must be a datetime."
            )

        if candle.time.tzinfo is None:
            raise ValueError(
                "Candle time must be timezone-aware."
            )

        identity = candle.time.astimezone(
            EXCHANGE_TIMEZONE
        )

        if identity.second != 0 or identity.microsecond != 0:
            raise ValueError(
                "Candle time must be exchange-minute aligned."
            )

        return identity

    def _process_completed_candle(
        self,
        candle
    ):
        """
        Processes one completed candle at most once.
        """

        candle_time = self._get_candle_identity(
            candle
        )

        if candle_time in self.processed_candle_times:
            return {
                "processed": False,
                "indicator_values": {}
            }

        indicator_values = self.indicator_engine.update(
            candle
        )

        self.processed_candle_times.add(
            candle_time
        )

        return {
            "processed": True,
            "indicator_values": indicator_values
        }

    # ==================================================
    # Historical Data
    # ==================================================

    def warm_indicators_from_history(
        self,
        symbol,
        days=30
    ):
        """
        Warms indicators with historical 1-minute candles.
        """

        historical_data = self.get_history(
            symbol,
            interval="minute",
            days=days
        )

        return self.warm_indicators_from_dataframe(
            historical_data
        )

    def warm_indicators_from_dataframe(
        self,
        historical_data
    ):
        """
        Warms indicators from historical candle data.
        """

        if not isinstance(
            historical_data,
            pd.DataFrame
        ):
            raise TypeError(
                "Historical data must be a pandas DataFrame."
            )

        if historical_data.empty:
            raise ValueError(
                "Historical data is empty."
            )

        if "date" not in historical_data.columns:
            raise KeyError(
                "Historical data is missing 'date'."
            )

        ordered_data = historical_data.sort_values(
            "date",
            kind="stable"
        )

        indicator_values = {}
        processed_candles = 0
        skipped_current_candles = 0
        skipped_duplicate_candles = 0

        current_minute = datetime.now(
            EXCHANGE_TIMEZONE
        ).replace(
            second=0,
            microsecond=0
        )

        for row in ordered_data.to_dict("records"):

            candle = historical_row_to_candle(
                row
            )

            candle_time = self._get_candle_identity(
                candle
            )

            if candle_time == current_minute:
                skipped_current_candles += 1
                continue

            result = self._process_completed_candle(
                candle
            )

            if result["processed"]:
                indicator_values = result["indicator_values"]
                processed_candles += 1
            else:
                skipped_duplicate_candles += 1

        return {
            "processed_candles": processed_candles,
            "indicator_values": indicator_values,
            "skipped_current_candles": skipped_current_candles,
            "skipped_duplicate_candles": skipped_duplicate_candles
        }

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

        symbol = symbol.strip().upper()

        if symbol != "NIFTY 50":
            raise ValueError("C7 live data is configured for NIFTY 50 only.")

        instrument_token = self.instruments.get_nifty_index_token()
        self.nifty_index_token = instrument_token

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
                ws.MODE_FULL,
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

                if self._cache_option_premium(tick):
                    continue

                if tick.get("instrument_token") != instrument_token:
                    continue

                self._select_nifty_option_pair(
                    ws,
                    tick.get("last_price")
                )

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

                    processing_result = (
                        self._process_completed_candle(
                            completed_candle
                        )
                    )

                    if processing_result["processed"]:

                        ema_values = (
                            processing_result[
                                "indicator_values"
                            ]
                        )

                        snapshot = IndicatorSnapshot(
                            candle=completed_candle,
                            values={
                                "ema": ema_values
                            }
                        )

                        strategy_result = (
                            self.strategy_engine.evaluate(
                                snapshot,
                                option_premiums=dict(
                                    self.latest_option_premiums
                                )
                            )
                        )

                        self.latest_strategy_result = (
                            strategy_result
                        )

                    else:

                        ema_values = {}

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

                    if processing_result["processed"]:

                        print("=" * 60)
                        print("STRATEGY RESULT")
                        print("=" * 60)

                        print(
                            f"Strategy : "
                            f"{strategy_result.strategy_name}"
                        )

                        print(
                            f"Action   : "
                            f"{strategy_result.action.value}"
                        )

                        print(
                            f"Candle   : "
                            f"{strategy_result.candle_time}"
                        )

                        print(
                            f"Reason   : "
                            f"{strategy_result.reason}"
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
