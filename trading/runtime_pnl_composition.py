"""Explicit composition of the existing LIVE runtime P&L stack."""

from dataclasses import dataclass

from trading.market import MarketData
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.runtime_pnl import RuntimePnLSnapshotBuilder
from trading.runtime_pnl_report import RuntimePnLReporter
from trading.runtime_pnl_state import RuntimePnLService, RuntimePnLStateAdapter
from trading.session_net_pnl import SessionNetPnLAggregator


@dataclass(frozen=True)
class RuntimePnLComposition:
    """Small immutable bundle for explicit LIVE P&L observation."""

    state_adapter: RuntimePnLStateAdapter
    snapshot_builder: RuntimePnLSnapshotBuilder
    service: RuntimePnLService
    reporter: RuntimePnLReporter

    def __post_init__(self):
        if not isinstance(self.state_adapter, RuntimePnLStateAdapter):
            raise TypeError("State adapter must be a RuntimePnLStateAdapter.")
        if not isinstance(self.snapshot_builder, RuntimePnLSnapshotBuilder):
            raise TypeError("Snapshot builder must be a RuntimePnLSnapshotBuilder.")
        if not isinstance(self.service, RuntimePnLService):
            raise TypeError("Service must be a RuntimePnLService.")
        if not isinstance(self.reporter, RuntimePnLReporter):
            raise TypeError("Reporter must be a RuntimePnLReporter.")

    def snapshot(
        self,
        trading_date,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        return self.service.snapshot(
            trading_date,
            reference_price,
            buy_order_count,
            sell_order_count,
        )

    def report(
        self,
        trading_date,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        return self.reporter.build(
            self.snapshot(
                trading_date,
                reference_price,
                buy_order_count,
                sell_order_count,
            )
        )

    @property
    def session_net_pnl_aggregator(self):
        return self.snapshot_builder.session_net_pnl_aggregator


def build_runtime_pnl_composition(market, charge_schedule):
    """Wire existing P&L components around one exact LIVE market authority."""
    if not isinstance(market, MarketData):
        raise TypeError("Market must be a MarketData.")
    if not isinstance(charge_schedule, OptionChargeSchedule):
        raise TypeError("Charge schedule must be an OptionChargeSchedule.")

    realized = RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(charge_schedule))
    )
    return build_runtime_pnl_composition_from_session_aggregator(
        market, SessionNetPnLAggregator(realized)
    )


def build_runtime_pnl_composition_from_session_aggregator(
    market, session_net_pnl_aggregator
):
    """Compose reporting from one caller-owned canonical economic authority."""
    if not isinstance(market, MarketData):
        raise TypeError("Market must be a MarketData.")
    if not isinstance(session_net_pnl_aggregator, SessionNetPnLAggregator):
        raise TypeError(
            "Session net P&L aggregator must be a SessionNetPnLAggregator."
        )
    realized = session_net_pnl_aggregator.realized_net_pnl_aggregator
    snapshot_builder = RuntimePnLSnapshotBuilder(
        PortfolioNetPnLAggregator(realized),
        session_net_pnl_aggregator,
    )
    state_adapter = RuntimePnLStateAdapter(market)
    service = RuntimePnLService(state_adapter, snapshot_builder)
    return RuntimePnLComposition(
        state_adapter=state_adapter,
        snapshot_builder=snapshot_builder,
        service=service,
        reporter=RuntimePnLReporter(),
    )
