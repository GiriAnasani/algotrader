"""Deterministic LIVE entry-risk policy over runtime-owned truth."""

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from numbers import Real

from trading.closed_position_history import ClosedPositionHistory
from trading.position_manager import PositionManager
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.strategy import SignalAction


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return value


@dataclass(frozen=True)
class LiveRiskLimits:
    """Explicit limits applied only to new LIVE exposure."""

    max_open_positions: int
    max_order_quantity: int
    max_completed_trades_per_day: int
    max_realized_net_loss: float

    def __post_init__(self):
        for name in (
            "max_open_positions",
            "max_order_quantity",
            "max_completed_trades_per_day",
        ):
            _positive_integer(getattr(self, name), name.replace("_", " ").capitalize())
        loss = self.max_realized_net_loss
        if (
            isinstance(loss, bool)
            or not isinstance(loss, Real)
            or not isfinite(loss)
            or loss <= 0
        ):
            raise ValueError("Max realized net loss must be a positive finite number.")
        object.__setattr__(self, "max_realized_net_loss", float(loss))


@dataclass(frozen=True)
class LiveRiskDecision:
    """Read-only diagnostic result of one proposed action evaluation."""

    allowed: bool
    reason: str
    open_position_count: int
    order_quantity: int
    completed_trade_count: int
    realized_net_pnl: float


class LiveRiskViolationError(RuntimeError):
    """Raised when deterministic LIVE risk policy rejects an entry."""


class LiveRiskEvaluator:
    """Evaluates actions from exact position, history, and Phase 9 P&L owners."""

    _ENTRY_ACTIONS = (SignalAction.BUY_CE, SignalAction.BUY_PE)
    _EXIT_ACTIONS = (SignalAction.EXIT_CE, SignalAction.EXIT_PE)

    def __init__(self, limits, position_manager, closed_position_history,
                 session_net_pnl_aggregator):
        if not isinstance(limits, LiveRiskLimits):
            raise TypeError("Limits must be LiveRiskLimits.")
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        if not isinstance(closed_position_history, ClosedPositionHistory):
            raise TypeError("Closed-position history must be a ClosedPositionHistory.")
        if not isinstance(session_net_pnl_aggregator, SessionNetPnLAggregator):
            raise TypeError("Session net P&L aggregator must be a SessionNetPnLAggregator.")
        self._limits = limits
        self._position_manager = position_manager
        self._closed_position_history = closed_position_history
        self._session_net_pnl_aggregator = session_net_pnl_aggregator

    @property
    def limits(self):
        return self._limits

    @property
    def position_manager(self):
        return self._position_manager

    @property
    def closed_position_history(self):
        return self._closed_position_history

    @property
    def session_net_pnl_aggregator(self):
        return self._session_net_pnl_aggregator

    def evaluate(self, action, quantity, trading_date):
        if not isinstance(action, SignalAction) or action not in (
            self._ENTRY_ACTIONS + self._EXIT_ACTIONS
        ):
            raise TypeError("Action must be a supported SignalAction.")
        quantity = _positive_integer(quantity, "Order quantity")
        if isinstance(trading_date, datetime) or not isinstance(trading_date, date):
            raise TypeError("Trading date must be a date, not a datetime.")

        active = self._position_manager.active_position
        session = self._session_net_pnl_aggregator.aggregate(
            trading_date,
            self._closed_position_history.positions,
        )
        open_count = int(active is not None)
        allowed = True
        reason = "allowed"
        if action in self._ENTRY_ACTIONS:
            if open_count >= self._limits.max_open_positions:
                allowed, reason = False, "max_open_positions"
            elif quantity > self._limits.max_order_quantity:
                allowed, reason = False, "max_order_quantity"
            elif session.closed_trade_count >= self._limits.max_completed_trades_per_day:
                allowed, reason = False, "max_completed_trades_per_day"
            elif session.realized_net_pnl <= -self._limits.max_realized_net_loss:
                allowed, reason = False, "max_realized_net_loss"

        return LiveRiskDecision(
            allowed=allowed,
            reason=reason,
            open_position_count=open_count,
            order_quantity=quantity,
            completed_trade_count=session.closed_trade_count,
            realized_net_pnl=session.realized_net_pnl,
        )


class LiveRiskGuard:
    """Enforces an already calculated risk decision."""

    def validate(self, decision):
        if not isinstance(decision, LiveRiskDecision):
            raise TypeError("Decision must be a LiveRiskDecision.")
        if not decision.allowed:
            raise LiveRiskViolationError(f"LIVE risk rejected: {decision.reason}.")
        return decision
