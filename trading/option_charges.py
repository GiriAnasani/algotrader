"""Deterministic charges and net P&L for normally closed long options."""

from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.position_pnl import PositionPnLCalculator


def _finite_number(value, label, *, non_negative=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not isfinite(value)
        or (non_negative and value < 0)
    ):
        qualifier = "finite non-negative" if non_negative else "finite"
        raise ValueError(f"{label} must be a {qualifier} number.")
    return float(value)


@dataclass(frozen=True)
class OptionChargeSchedule:
    """Immutable, explicitly versioned rates for an option round trip."""

    brokerage_per_executed_order: float
    sell_stt_rate: float
    exchange_transaction_rate: float
    sebi_rate: float
    stamp_duty_buy_rate: float
    gst_rate: float

    def __post_init__(self):
        for name in (
            "brokerage_per_executed_order",
            "sell_stt_rate",
            "exchange_transaction_rate",
            "sebi_rate",
            "stamp_duty_buy_rate",
            "gst_rate",
        ):
            value = _finite_number(
                getattr(self, name), name.replace("_", " ").capitalize(),
                non_negative=True,
            )
            if name != "brokerage_per_executed_order" and value >= 1:
                raise ValueError(f"{name.replace('_', ' ').capitalize()} must be less than 1.")
            object.__setattr__(self, name, value)

    @classmethod
    def zerodha_nse_options_2026(cls):
        """Return the reference Zerodha/NSE option schedule at 2026-08-24."""
        return cls(20.0, 0.0015, 0.0003553, 0.000001, 0.00003, 0.18)


ZERODHA_NSE_OPTIONS_2026 = OptionChargeSchedule.zerodha_nse_options_2026()


@dataclass(frozen=True)
class OptionTradeChargesResult:
    """Immutable full-precision charge components for one closed trade."""

    buy_turnover: float
    sell_turnover: float
    total_turnover: float
    brokerage: float
    stt: float
    exchange_transaction_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(
                self, name,
                _finite_number(
                    getattr(self, name), name.replace("_", " ").capitalize(),
                    non_negative=True,
                ),
            )
        if self.total_turnover != fsum((self.buy_turnover, self.sell_turnover)):
            raise ValueError("Total turnover must equal buy plus sell turnover.")
        components = (
            self.brokerage,
            self.stt,
            self.exchange_transaction_charges,
            self.sebi_charges,
            self.stamp_duty,
            self.gst,
        )
        if self.total_charges != fsum(components):
            raise ValueError("Total charges must equal the sum of charge components.")


def _positive_order_count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return value


class OptionTradeChargesCalculator:
    """Calculate normal buy-premium/sell-premium option charges."""

    def __init__(self, schedule):
        if not isinstance(schedule, OptionChargeSchedule):
            raise TypeError("Schedule must be an OptionChargeSchedule.")
        self._schedule = schedule

    def calculate(self, closed_position, buy_order_count=1, sell_order_count=1):
        if not isinstance(closed_position, ClosedPosition):
            raise TypeError("Position must be a ClosedPosition.")
        buy_order_count = _positive_order_count(buy_order_count, "Buy order count")
        sell_order_count = _positive_order_count(sell_order_count, "Sell order count")

        buy_turnover = closed_position.entry_price * closed_position.quantity
        sell_turnover = closed_position.exit_price * closed_position.quantity
        total_turnover = fsum((buy_turnover, sell_turnover))
        brokerage = self._schedule.brokerage_per_executed_order * (
            buy_order_count + sell_order_count
        )
        stt = sell_turnover * self._schedule.sell_stt_rate
        transaction = total_turnover * self._schedule.exchange_transaction_rate
        sebi = total_turnover * self._schedule.sebi_rate
        stamp = buy_turnover * self._schedule.stamp_duty_buy_rate
        gst = fsum((brokerage, transaction, sebi)) * self._schedule.gst_rate
        total_charges = fsum((brokerage, stt, transaction, sebi, stamp, gst))
        return OptionTradeChargesResult(
            buy_turnover, sell_turnover, total_turnover, brokerage, stt,
            transaction, sebi, stamp, gst, total_charges,
        )


@dataclass(frozen=True)
class NetPnLResult:
    """Immutable gross-to-net P&L result for one closed trade."""

    gross_pnl: float
    total_charges: float
    net_pnl: float

    def __post_init__(self):
        gross_pnl = _finite_number(self.gross_pnl, "Gross P&L")
        total_charges = _finite_number(
            self.total_charges, "Total charges", non_negative=True
        )
        net_pnl = _finite_number(self.net_pnl, "Net P&L")
        if net_pnl != fsum((gross_pnl, -total_charges)):
            raise ValueError("Net P&L must equal gross P&L minus total charges.")
        object.__setattr__(self, "gross_pnl", gross_pnl)
        object.__setattr__(self, "total_charges", total_charges)
        object.__setattr__(self, "net_pnl", net_pnl)


class NetPnLCalculator:
    """Compose existing gross P&L and charge calculators."""

    def __init__(self, charge_calculator):
        if not isinstance(charge_calculator, OptionTradeChargesCalculator):
            raise TypeError("Charge calculator must be an OptionTradeChargesCalculator.")
        self._charge_calculator = charge_calculator
        self._position_pnl_calculator = PositionPnLCalculator()

    def calculate(self, closed_position, buy_order_count=1, sell_order_count=1):
        gross_result = self._position_pnl_calculator.calculate_closed(closed_position)
        charges = self._charge_calculator.calculate(
            closed_position, buy_order_count, sell_order_count
        )
        return NetPnLResult(
            gross_result.gross_pnl,
            charges.total_charges,
            fsum((gross_result.gross_pnl, -charges.total_charges)),
        )
