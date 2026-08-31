"""Thin deterministic lifecycle orchestration for the production process."""

from enum import Enum

from core.production_auth import (
    ProductionAuthenticationResult,
    ProductionAuthenticationState,
    ProductionAuthenticator,
)
from core.production_startup import ProductionStartupBuilder
from trading.execution_mode import ExecutionMode
from trading.live_restart_orchestration import (
    LiveRestartOrchestrationState,
    LiveRestartOrchestrator,
)
from trading.live_run_authorization import LiveRunAuthorizer
from trading.live_startup import LiveStartupResult


class ProductionApplicationState(Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ProductionApplicationError(RuntimeError):
    """Raised when the production lifecycle cannot proceed safely."""


class ProductionApplication:
    """Coordinates existing production authorities without replacing them."""

    def __init__(self, startup_builder, authenticator=None, instruments=None):
        if not isinstance(startup_builder, ProductionStartupBuilder):
            raise TypeError("Startup builder must be a ProductionStartupBuilder.")
        if authenticator is not None and not isinstance(
            authenticator, ProductionAuthenticator
        ):
            raise TypeError("Authenticator must be a ProductionAuthenticator or None.")
        self._builder = startup_builder
        self._authenticator = authenticator
        self._instruments = instruments
        self._state = ProductionApplicationState.CREATED
        self._components = None
        self._authentication_result = None
        self._startup_result = None
        self._restart_result = None

    @property
    def state(self):
        return self._state

    @property
    def config(self):
        return self._builder.config

    @property
    def components(self):
        return self._components

    @property
    def authentication_result(self):
        return self._authentication_result

    @property
    def startup_result(self):
        return self._startup_result

    @property
    def restart_result(self):
        return self._restart_result

    def start(self, now=None):
        if self._state not in (
            ProductionApplicationState.CREATED,
            ProductionApplicationState.LOGIN_REQUIRED,
        ):
            raise ProductionApplicationError("Application cannot start from its current state.")
        self._state = ProductionApplicationState.STARTING
        try:
            if self._builder.config.execution_mode is ExecutionMode.PAPER:
                self._components = self._builder.build()
                self._state = ProductionApplicationState.INITIALIZED
                return self._components

            if self._authenticator is None:
                raise ProductionApplicationError("LIVE startup requires an authenticator.")
            if self._instruments is None:
                raise ProductionApplicationError("LIVE startup requires instruments.")
            authentication = self._authenticator.restore(now)
            if not isinstance(authentication, ProductionAuthenticationResult):
                raise TypeError(
                    "Authenticator must return a ProductionAuthenticationResult."
                )
            self._authentication_result = authentication
            if authentication.state is ProductionAuthenticationState.LOGIN_REQUIRED:
                self._state = ProductionApplicationState.LOGIN_REQUIRED
                return authentication

            self._components = self._builder.build(
                authentication.kite_client, self._instruments
            )
            runtime = self._components.runtime
            recovery = runtime.session_bootstrap.initialize()
            self._startup_result = LiveStartupResult(
                runtime, self._components.market, recovery
            )
            self._restart_result = LiveRestartOrchestrator(
                runtime.position_store,
                runtime.closed_position_history_store,
            ).orchestrate(self._startup_result)
            if self._restart_result.state is LiveRestartOrchestrationState.BLOCKED:
                raise ProductionApplicationError("LIVE restart continuity is blocked.")
            self._state = ProductionApplicationState.INITIALIZED
            return self._restart_result
        except Exception:
            self._fail_closed()
            raise

    def run(self, symbol="NIFTY 50"):
        if self._state is not ProductionApplicationState.INITIALIZED:
            raise ProductionApplicationError("Application must be initialized before run.")
        if self._components.runtime is None:
            self._state = ProductionApplicationState.RUNNING
            return self._components
        authorization = LiveRunAuthorizer().authorize(self._startup_result)
        if not authorization.is_permitted:
            raise ProductionApplicationError(authorization.message)
        self._state = ProductionApplicationState.RUNNING
        try:
            self._components.market.connect_live(symbol)
        except Exception:
            self._fail_closed()
            raise
        return self._components

    def shutdown(self):
        if self._state is ProductionApplicationState.STOPPED:
            return False
        self._state = ProductionApplicationState.STOPPING
        try:
            if self._components is not None and self._components.runtime is not None:
                runtime = self._components.runtime
                runtime.execution_coordinator.disable()
                runtime.readiness_gate.revoke()
                self._components.market.disconnect_live()
            self._state = ProductionApplicationState.STOPPED
            return True
        except Exception:
            self._state = ProductionApplicationState.FAILED
            raise

    def _fail_closed(self):
        if self._components is not None and self._components.runtime is not None:
            self._components.runtime.execution_coordinator.disable()
            self._components.runtime.readiness_gate.revoke()
            try:
                self._components.market.disconnect_live()
            except Exception:
                pass
        self._state = ProductionApplicationState.FAILED
