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


def build_runtime_pnl_composition(market, charge_schedule):
    """Wire existing P&L components around one exact LIVE market authority."""
    if not isinstance(market, MarketData):
        raise TypeError("Market must be a MarketData.")
    if not isinstance(charge_schedule, OptionChargeSchedule):
        raise TypeError("Charge schedule must be an OptionChargeSchedule.")

    charge_calculator = OptionTradeChargesCalculator(charge_schedule)
    net_calculator = NetPnLCalculator(charge_calculator)
    realized = RealizedNetPnLAggregator(net_calculator)
    snapshot_builder = RuntimePnLSnapshotBuilder(
        PortfolioNetPnLAggregator(realized),
        SessionNetPnLAggregator(realized),
    )
    state_adapter = RuntimePnLStateAdapter(market)
    service = RuntimePnLService(state_adapter, snapshot_builder)
    return RuntimePnLComposition(
        state_adapter=state_adapter,
        snapshot_builder=snapshot_builder,
        service=service,
        reporter=RuntimePnLReporter(),
    )
