"""Read-only adaptation of existing runtime-owned state for P&L snapshots."""

from dataclasses import dataclass

from trading.close_position_lifecycle import ClosedPosition
from trading.execution_mode import ExecutionMode
from trading.market import MarketData
from trading.position import ManagedPosition, PositionState
from trading.runtime_pnl import RuntimePnLSnapshotBuilder


class PaperRuntimePnLStateUnsupportedError(RuntimeError):
    """Raised because paper trades are not losslessly ClosedPosition history."""


@dataclass(frozen=True)
class RuntimePnLStateView:
    """Exact references to the P&L-compatible state the runtime currently owns."""

    closed_positions: tuple
    active_position: ManagedPosition | None

    def __post_init__(self):
        if not isinstance(self.closed_positions, tuple):
            raise TypeError("Closed positions must be a tuple.")
        if not all(
            isinstance(position, ClosedPosition)
            for position in self.closed_positions
        ):
            raise TypeError("Closed positions must contain ClosedPosition values.")
        if self.active_position is not None and not isinstance(
            self.active_position, ManagedPosition
        ):
            raise TypeError("Active position must be a ManagedPosition or None.")
        if (
            self.active_position is not None
            and self.active_position.state is not PositionState.OPEN
        ):
            raise ValueError("Active position must be OPEN.")


class RuntimePnLStateAdapter:
    """Read LIVE state without creating ownership or claiming complete history.

    LIVE currently owns only ``latest_closed_position`` rather than a complete
    closed-position ledger, so the returned tuple contains at most that object.
    PAPER is explicit unsupported because its PaperTrade ledger is not the
    ClosedPosition domain required by the Phase 9 calculation stack.
    """

    def __init__(self, market):
        if not isinstance(market, MarketData):
            raise TypeError("Market must be a MarketData.")
        self._market = market

    def view(self):
        if self._market.execution_router.mode is not ExecutionMode.LIVE:
            raise PaperRuntimePnLStateUnsupportedError(
                "PAPER history cannot be represented as ClosedPosition values."
            )
        latest = self._market.latest_closed_position
        closed_positions = () if latest is None else (latest,)
        return RuntimePnLStateView(
            closed_positions=closed_positions,
            active_position=self._market.live_position_manager.active_position,
        )


class RuntimePnLService:
    """Forward an exact runtime state view to the configured snapshot builder."""

    def __init__(self, state_adapter, snapshot_builder):
        if not isinstance(state_adapter, RuntimePnLStateAdapter):
            raise TypeError("State adapter must be a RuntimePnLStateAdapter.")
        if not isinstance(snapshot_builder, RuntimePnLSnapshotBuilder):
            raise TypeError("Snapshot builder must be a RuntimePnLSnapshotBuilder.")
        self._state_adapter = state_adapter
        self._snapshot_builder = snapshot_builder

    def snapshot(
        self,
        trading_date,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        state = self._state_adapter.view()
        return self._snapshot_builder.build(
            trading_date,
            state.closed_positions,
            state.active_position,
            reference_price,
            buy_order_count,
            sell_order_count,
        )
