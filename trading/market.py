import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

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
from trading.execution_mode import ExecutionMode
from trading.execution_router import ExecutionRouter
from trading.live_execution import LiveExecutionCoordinator
from trading.live_readiness import LiveReadinessGate
from trading.live_order import LiveOrderIntent
from trading.broker_order_status import BrokerOrderState
from trading.broker_position_reconciler import (
    BrokerPositionReconciler,
    PositionReconciliationState,
)
from trading.pending_live_order import PendingLiveOrder
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_position_reader import ZerodhaPositionReader
from trading.strategy import (
    IndicatorSnapshot,
    StrategyEngine,
)
from trading.paper_execution import PaperExecutionEngine
from trading.paper_ledger import PaperTradeLedger
from trading.paper_pnl import calculate_trade_pnl
from trading.paper_session import calculate_session_summary
from trading.paper_position import PaperPosition
from trading.paper_trade import PaperTrade
from trading.strategy import SignalAction


@dataclass(frozen=True)
class LiveExecutionContext:
    """Broker-confirmed full-fill continuity only; it is not broker fill state."""

    side: str
    contract_symbol: str
    quantity: int


class PendingLiveOrderError(RuntimeError):
    """Raised when a LIVE action would conflict with an unresolved order."""


class LiveReadinessError(RuntimeError):
    """Raised when LIVE execution has not been explicitly authorized."""


class LivePositionReconciliationError(RuntimeError):
    """Raised when broker net exposure cannot safely permit a LIVE action."""


class MarketData:
    """
    Handles downloading historical
    and live market data.
    """

    def __init__(
        self,
        kite,
        instruments,
        execution_mode=ExecutionMode.PAPER,
        live_execution_coordinator=None,
        live_order_status_reader=None,
        live_position_reader=None,
        live_position_reconciler=None,
        live_readiness_gate=None,
    ):

        if not isinstance(execution_mode, ExecutionMode):
            raise TypeError("Execution mode must be an ExecutionMode.")

        if (
            execution_mode is ExecutionMode.LIVE
            and not isinstance(
                live_execution_coordinator,
                LiveExecutionCoordinator,
            )
        ):
            raise TypeError(
                "LIVE execution requires a LiveExecutionCoordinator."
            )

        if (
            execution_mode is ExecutionMode.LIVE
            and not isinstance(
                live_order_status_reader,
                ZerodhaOrderStatusReader,
            )
        ):
            raise TypeError(
                "LIVE execution requires a ZerodhaOrderStatusReader."
            )

        if (
            execution_mode is ExecutionMode.LIVE
            and not isinstance(live_position_reader, ZerodhaPositionReader)
        ):
            raise TypeError(
                "LIVE execution requires a ZerodhaPositionReader."
            )

        if (
            execution_mode is ExecutionMode.LIVE
            and not isinstance(live_position_reconciler, BrokerPositionReconciler)
        ):
            raise TypeError(
                "LIVE execution requires a BrokerPositionReconciler."
            )

        if (
            execution_mode is ExecutionMode.LIVE
            and not isinstance(live_readiness_gate, LiveReadinessGate)
        ):
            raise TypeError(
                "LIVE execution requires a LiveReadinessGate."
            )

        self.kite = kite

        self.instruments = instruments

        # OHLC Builder
        self.ohlc = OHLCBuilder()

        # Indicators
        self.indicator_engine = IndicatorEngine()

        # Strategy
        self.strategy_engine = StrategyEngine(target_points=2.0)
        self.latest_strategy_result = None
        self.paper_execution_engine = PaperExecutionEngine()
        self.execution_router = ExecutionRouter(
            mode=execution_mode,
            paper_execution_engine=self.paper_execution_engine
        )
        self.live_execution_coordinator = live_execution_coordinator
        self.live_order_status_reader = live_order_status_reader
        self.live_position_reader = live_position_reader
        self.live_position_reconciler = live_position_reconciler
        self.live_readiness_gate = live_readiness_gate
        self.live_execution_context = None
        self.pending_live_order = None
        self.latest_live_position_reconciliation = None
        self.paper_trade_ledger = PaperTradeLedger()

        # Processed completed candle identities
        self.processed_candle_times = set()

        # Completed candle counter
        self.completed_candle_count = 0

        # C7 NIFTY option subscription and premium state
        self.nifty_index_token = None
        self.nifty_option_pair = None
        self.option_tokens = {}
        self.latest_option_premiums = {}
        self.latest_option_timestamps = {}

        # C8 live monitoring state
        self.latest_nifty_spot = None
        self.latest_indicator_values = {}
        self.latest_completed_snapshot = None
        self.last_position_display_time = None
        self.position_display_interval_seconds = 1.0

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
        timestamp = tick.get("exchange_timestamp")
        if timestamp is not None:
            self.latest_option_timestamps[option_type] = (
                self._normalize_execution_time(timestamp)
            )

        return True

    @staticmethod
    def _normalize_execution_time(timestamp):
        """Normalizes an exchange timestamp for paper execution."""
        if not isinstance(timestamp, datetime):
            raise TypeError("Exchange timestamp must be a datetime.")

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return timestamp.replace(tzinfo=EXCHANGE_TIMEZONE)

        return timestamp.astimezone(EXCHANGE_TIMEZONE)

    def _execute_strategy_result(self, strategy_result, execution_time):
        """Routes ordered strategy actions through the execution router."""
        if strategy_result is None or strategy_result.action is SignalAction.HOLD:
            return ()

        execution_time = self._normalize_execution_time(execution_time)
        execution_results = []

        for action in strategy_result.actions:
            if action is SignalAction.HOLD:
                continue

            if (
                self.execution_router.mode is ExecutionMode.LIVE
                and self.pending_live_order is not None
            ):
                raise PendingLiveOrderError(
                    "A pending LIVE order must be reconciled before execution."
                )

            if self.execution_router.mode is ExecutionMode.LIVE:
                if not self.live_readiness_gate.is_ready:
                    raise LiveReadinessError(
                        "LIVE execution requires a READY LiveReadinessGate."
                    )
                self._validate_live_broker_position_before_action()

            side = "CE" if action in (
                SignalAction.BUY_CE,
                SignalAction.EXIT_CE,
            ) else "PE"
            premium = self.latest_option_premiums.get(side)

            if action in (SignalAction.BUY_CE, SignalAction.BUY_PE):
                if (
                    self.execution_router.mode is ExecutionMode.LIVE
                    and self.live_execution_context is not None
                ):
                    raise ValueError("A live execution context is already active.")

                if self.nifty_option_pair is None:
                    raise ValueError("NIFTY option pair has not been selected.")

                contract = self.nifty_option_pair[side]
                contract_symbol = contract["tradingsymbol"]
                quantity = contract["lot_size"]
            elif action in (SignalAction.EXIT_CE, SignalAction.EXIT_PE):
                if self.execution_router.mode is ExecutionMode.LIVE:
                    active_context = self.live_execution_context
                    if active_context is None:
                        raise ValueError("No live execution context is active.")

                    if active_context.side != side:
                        raise ValueError(
                            "Exit action does not match the live execution context side."
                        )

                    contract_symbol = active_context.contract_symbol
                    quantity = active_context.quantity
                else:
                    active_position = self.paper_execution_engine.active_position
                    contract_symbol = (
                        active_position.contract_symbol
                        if active_position is not None
                        else None
                    )
                    quantity = (
                        active_position.quantity
                        if active_position is not None
                        else None
                    )
            else:
                raise ValueError("Unsupported strategy action for execution.")

            execution_result = self.execution_router.route(
                action,
                contract_symbol=contract_symbol,
                side=side,
                quantity=quantity,
                reference_price=premium,
                created_time=execution_time,
            )

            if isinstance(execution_result, LiveOrderIntent):
                order_id = self.live_execution_coordinator.execute(
                    execution_result
                )
                broker_status = self.live_order_status_reader.read(order_id)
                is_pending = self._apply_live_order_status(
                    order_id,
                    execution_result,
                    broker_status,
                )

                execution_results.append(order_id)
                print(
                    f"LIVE ORDER {action.value}: "
                    f"{execution_result.contract_symbol} "
                    f"@ Rs {premium:.2f} ID {order_id}"
                )

                if (
                    is_pending
                    or not self._is_confirmed_full_fill(
                        execution_result,
                        broker_status,
                    )
                ):
                    break

                continue

            if (
                action in (SignalAction.EXIT_CE, SignalAction.EXIT_PE)
                and isinstance(execution_result, PaperPosition)
            ):
                trade = self.paper_trade_ledger.record(
                    PaperTrade(
                        contract_symbol=execution_result.contract_symbol,
                        side=execution_result.side,
                        quantity=execution_result.quantity,
                        entry_price=execution_result.entry_price,
                        entry_time=execution_result.entry_time,
                        exit_price=premium,
                        exit_time=execution_time,
                        exit_action=action,
                        strategy_name=strategy_result.strategy_name,
                        exit_reason=strategy_result.reason,
                    )
                )
                pnl = calculate_trade_pnl(trade)
                print(
                    f"PAPER REALIZED P&L: {execution_result.contract_symbol} "
                    f"Points {pnl.points_pnl:+.2f}, "
                    f"Gross Rs {pnl.gross_pnl:+.2f}"
                )
                summary = calculate_session_summary(
                    self.paper_trade_ledger.get_trades()
                )
                print(
                    "PAPER SESSION: "
                    f"Trades {summary.completed_trades}, "
                    f"Wins {summary.winning_trades}, "
                    f"Losses {summary.losing_trades}, "
                    f"Flat {summary.flat_trades}, "
                    f"Points {summary.total_points_pnl:+.2f}, "
                    f"Gross Rs {summary.total_gross_pnl:+.2f}"
                )

            execution_results.append(execution_result)
            execution_label = (
                "PAPER"
                if isinstance(execution_result, PaperPosition)
                else "LIVE INTENT"
            )
            print(
                f"{execution_label} {action.value}: "
                f"{execution_result.contract_symbol} "
                f"@ Rs {premium:.2f}"
            )

        if self.execution_router.mode is ExecutionMode.PAPER:
            self._validate_paper_state_consistency()

        return tuple(execution_results)

    def _validate_live_broker_position_before_action(self):
        """Fails closed unless broker net positions match confirmed LIVE state."""
        allowed_symbols = set()
        if self.nifty_option_pair is not None:
            allowed_symbols.update(
                contract["tradingsymbol"]
                for contract in self.nifty_option_pair.values()
                if isinstance(contract, dict) and "tradingsymbol" in contract
            )
        if self.live_execution_context is not None:
            allowed_symbols.add(self.live_execution_context.contract_symbol)

        broker_positions = self.live_position_reader.read()
        reconciliation = self.live_position_reconciler.reconcile(
            self.live_execution_context,
            broker_positions,
            allowed_contract_symbols=tuple(allowed_symbols),
        )
        self.latest_live_position_reconciliation = reconciliation

        expected_state = (
            PositionReconciliationState.MATCH
            if self.live_execution_context is not None
            else PositionReconciliationState.NO_POSITION
        )
        if reconciliation.state is not expected_state:
            raise LivePositionReconciliationError(
                f"LIVE broker position reconciliation is "
                f"{reconciliation.state.value}: {reconciliation.message}"
            )

    def reconcile_pending_live_order(self):
        """Reads one pending LIVE order status without submitting an order."""
        pending_order = self.pending_live_order
        if pending_order is None:
            raise ValueError("No pending LIVE order exists to reconcile.")

        broker_status = self.live_order_status_reader.read(pending_order.order_id)
        self._apply_live_order_status(
            pending_order.order_id,
            pending_order,
            broker_status,
        )
        return broker_status

    def _apply_live_order_status(self, order_id, order, broker_status):
        """Applies normalized status only to confirmed or pending continuity."""
        if self._is_confirmed_full_fill(order, broker_status):
            if order.action in (SignalAction.BUY_CE, SignalAction.BUY_PE):
                self.live_execution_context = LiveExecutionContext(
                    side=order.side,
                    contract_symbol=order.contract_symbol,
                    quantity=order.quantity,
                )
            else:
                self.live_execution_context = None
            self.pending_live_order = None
            return False

        if self._status_requires_pending_tracking(broker_status):
            self.pending_live_order = PendingLiveOrder(
                order_id=order_id,
                action=order.action,
                side=order.side,
                contract_symbol=order.contract_symbol,
                quantity=order.quantity,
                created_time=order.created_time,
            )
            return True

        self.pending_live_order = None
        return False

    @staticmethod
    def _is_confirmed_full_fill(order, broker_status):
        """Returns whether status confirms the entire submitted quantity filled."""
        return (
            broker_status.is_filled
            and broker_status.filled_quantity == order.quantity
        )

    @staticmethod
    def _status_requires_pending_tracking(broker_status):
        """Returns whether a non-filled status leaves possible broker exposure."""
        if broker_status.state in (
            BrokerOrderState.OPEN,
            BrokerOrderState.SUBMITTED,
            BrokerOrderState.UNKNOWN,
        ):
            return True

        if broker_status.state is BrokerOrderState.CANCELLED:
            return broker_status.filled_quantity > 0

        if broker_status.state is BrokerOrderState.COMPLETE:
            return (
                broker_status.filled_quantity > 0
                or broker_status.pending_quantity > 0
            )

        return False

    def _validate_paper_state_consistency(self):
        """Ensures strategy and paper active-position state agree."""
        strategy_side = self.strategy_engine.active_position
        paper_position = self.paper_execution_engine.active_position
        paper_side = paper_position.side if paper_position is not None else None

        if strategy_side != paper_side:
            raise RuntimeError(
                "Strategy and paper execution position states are inconsistent."
            )

    def _format_price(
        self,
        value,
        signed=False
    ):
        """
        Formats a premium value for the console.
        """

        if value is None:
            return "N/A"

        prefix = "+" if signed and value >= 0 else ""

        return f"Rs {prefix}{value:.2f}"

    def _display_strategy_scan(
        self,
        snapshot,
        indicator_values,
        strategy_result
    ):
        """
        Displays the completed-candle strategy scan panel.
        """

        print()
        print("=" * 60)
        print("STRATEGY SCAN")
        print("=" * 60)
        print(f"NIFTY 50 Close : {snapshot.candle.close:.2f}")

        for period in (10, 20, 50, 100, 200):
            value = indicator_values.get(period)
            formatted_value = (
                f"{value:.2f}"
                if value is not None
                else "N/A"
            )
            print(f"EMA {period:<3}        : {formatted_value}")

        print(f"Strategy       : {strategy_result.strategy_name}")
        print(f"Signal         : {strategy_result.action.value}")
        print(f"Reason         : {strategy_result.reason}")
        print("=" * 60)

    def _display_active_position(
        self,
        force=False
    ):
        """
        Displays the active option position at most once per second.
        """

        side = self.strategy_engine.active_position

        if side is None:
            return

        now = monotonic()

        if (
            not force
            and self.last_position_display_time is not None
            and now - self.last_position_display_time
            < self.position_display_interval_seconds
        ):
            return

        entry_price = self.strategy_engine.entry_premium
        current_price = self.latest_option_premiums.get(side)
        target_price = entry_price + self.strategy_engine.target_points
        pnl = (
            current_price - entry_price
            if current_price is not None
            else None
        )
        contract = self.nifty_option_pair[side]["tradingsymbol"]
        ema10 = self.latest_indicator_values.get(10)
        spot = self.latest_nifty_spot

        print()
        print("=" * 60)
        print("ACTIVE POSITION")
        print("=" * 60)
        print(f"Option         : {contract}")
        print(f"Side           : {side}")
        print(f"Entry Price    : {self._format_price(entry_price)}")
        print(f"Target Price   : {self._format_price(target_price)}")
        print(f"Current Price  : {self._format_price(current_price)}")
        print(f"P&L            : {self._format_price(pnl, signed=True)}")
        print(
            "NIFTY Spot     : "
            f"{spot:.2f}" if spot is not None else "NIFTY Spot     : N/A"
        )
        print(
            "EMA 10         : "
            f"{ema10:.2f}" if ema10 is not None else "EMA 10         : N/A"
        )
        print("Status         : ACTIVE")
        print(
            "Last Update    : "
            f"{datetime.now(EXCHANGE_TIMEZONE):%H:%M:%S}"
        )
        print("=" * 60)

        self.last_position_display_time = now

    def _monitor_live_option_target(
        self,
        execution_time=None,
    ):
        """
        Checks the active option target from its live premium stream.
        """

        if (
            self.strategy_engine.active_position is None
            or self.latest_completed_snapshot is None
        ):
            return None

        active_side = self.strategy_engine.active_position

        strategy_result = self.strategy_engine.evaluate_live_option_target(
            self.latest_completed_snapshot,
            option_premiums=dict(self.latest_option_premiums)
        )

        if strategy_result is not None:
            self.latest_strategy_result = strategy_result
            if execution_time is None:
                execution_time = self.latest_option_timestamps.get(
                    active_side
                )

            if execution_time is None:
                execution_time = self.latest_completed_snapshot.candle.time

            self._execute_strategy_result(
                strategy_result,
                execution_time,
            )
            self.last_position_display_time = None
            print(
                f"Position closed: {strategy_result.reason}"
            )

        return strategy_result

    def _handle_option_tick(
        self,
        tick
    ):
        """
        Monitors only the active option from live option ticks.
        """

        option_type = self.option_tokens.get(
            tick.get("instrument_token")
        )

        if option_type != self.strategy_engine.active_position:
            return

        timestamp = tick.get("exchange_timestamp")
        execution_time = (
            self._normalize_execution_time(timestamp)
            if timestamp is not None
            else None
        )

        if self._monitor_live_option_target(execution_time) is None:
            self._display_active_position()

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
                    self._handle_option_tick(tick)
                    continue

                if tick.get("instrument_token") != instrument_token:
                    continue

                self.latest_nifty_spot = tick.get("last_price")

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

                        self.latest_completed_snapshot = snapshot
                        self.latest_indicator_values = ema_values

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

                        self._execute_strategy_result(
                            strategy_result,
                            self._normalize_execution_time(
                                tick.get("exchange_timestamp")
                            ),
                        )

                        if self.strategy_engine.active_position is None:
                            self._display_strategy_scan(
                                snapshot,
                                ema_values,
                                strategy_result
                            )
                        else:
                            self._display_active_position(force=True)

                    else:

                        ema_values = {}

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
