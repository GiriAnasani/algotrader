"""Construction-only startup composition from production configuration."""

from dataclasses import dataclass

from core.production_config import ProductionConfig
from trading.closed_position_history_store import ClosedPositionHistoryStore
from trading.execution_mode import ExecutionMode
from trading.live_market_factory import build_live_market_data
from trading.live_runtime import LiveRuntimeComponents, build_live_runtime
from trading.market import MarketData
from trading.position_store import PositionStore


@dataclass(frozen=True)
class ProductionStartupComponents:
    """Immutable references produced by one startup composition pass."""

    config: ProductionConfig
    runtime: LiveRuntimeComponents | None
    market: MarketData

    def __post_init__(self):
        if not isinstance(self.config, ProductionConfig):
            raise TypeError("Config must be a ProductionConfig.")
        if not isinstance(self.market, MarketData):
            raise TypeError("Market must be a MarketData.")
        if self.config.execution_mode is ExecutionMode.PAPER:
            if self.runtime is not None:
                raise ValueError("PAPER startup must not contain a LIVE runtime.")
        elif not isinstance(self.runtime, LiveRuntimeComponents):
            raise TypeError("LIVE startup requires LiveRuntimeComponents.")
        if self.market.execution_router.mode is not self.config.execution_mode:
            raise ValueError("Market execution mode must match its configuration.")

    @property
    def execution_mode(self):
        return self.config.execution_mode

    @property
    def execution_enabled(self):
        return self.config.execution_enabled

    @property
    def position_store(self):
        return self.runtime.position_store if self.runtime is not None else None

    @property
    def closed_position_history_store(self):
        if self.runtime is None:
            return None
        return self.runtime.closed_position_history_store


class ProductionStartupBuilder:
    """Builds a safe runtime graph without starting runtime activity."""

    def __init__(self, config):
        if not isinstance(config, ProductionConfig):
            raise TypeError("Config must be a ProductionConfig.")
        self._config = config

    @property
    def config(self):
        return self._config

    def build(self, kite_client=None, instruments=None):
        """Constructs PAPER or LIVE components without recovery or broker calls."""
        if self._config.execution_mode is ExecutionMode.PAPER:
            market = MarketData(
                kite_client,
                instruments,
                execution_mode=ExecutionMode.PAPER,
            )
            return ProductionStartupComponents(self._config, None, market)

        if kite_client is None:
            raise ValueError("LIVE startup requires a caller-supplied kite client.")
        if instruments is None:
            raise ValueError("LIVE startup requires instruments.")

        position_store = PositionStore(self._config.position_store_path)
        history_store = ClosedPositionHistoryStore(
            self._config.closed_position_history_store_path
        )
        runtime = build_live_runtime(
            kite_client,
            execution_enabled=self._config.execution_enabled,
            position_store=position_store,
            closed_position_history_store=history_store,
        )
        market = build_live_market_data(kite_client, instruments, runtime)
        return ProductionStartupComponents(self._config, runtime, market)
