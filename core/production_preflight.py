"""Read-only static operational preflight over a production application."""

from dataclasses import dataclass
from enum import Enum
import json

from core.production_application import ProductionApplication, ProductionApplicationState
from core.production_auth import (
    ProductionAuthenticationResult,
    ProductionAuthenticationState,
)
from core.production_health import (
    production_authority_identity_issues,
    production_position_context_consistent,
)
from trading.execution_mode import ExecutionMode
from trading.live_recovery import LiveRecoveryState
from trading.live_restart_orchestration import LiveRestartOrchestrationState


class ProductionPreflightStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ProductionPreflightCheck:
    identifier: str
    status: ProductionPreflightStatus
    message: str

    def __post_init__(self):
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("Check identifier must be a non-empty string.")
        if not isinstance(self.status, ProductionPreflightStatus):
            raise TypeError("Check status must be a ProductionPreflightStatus.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Check message must be a non-empty string.")
        object.__setattr__(self, "identifier", self.identifier.strip())
        object.__setattr__(self, "message", self.message.strip())

    def to_dict(self):
        return {
            "identifier": self.identifier,
            "status": self.status.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class ProductionPreflightReport:
    status: ProductionPreflightStatus
    execution_mode: ExecutionMode
    execution_enabled: bool
    checks: tuple[ProductionPreflightCheck, ...]

    def __post_init__(self):
        if self.status not in (
            ProductionPreflightStatus.PASS,
            ProductionPreflightStatus.FAIL,
        ):
            raise ValueError("Overall preflight status must be PASS or FAIL.")
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError("Execution mode must be an ExecutionMode.")
        if not isinstance(self.execution_enabled, bool):
            raise TypeError("Execution enabled must be a bool.")
        if not isinstance(self.checks, tuple) or not self.checks:
            raise ValueError("Checks must be a non-empty tuple.")
        if any(not isinstance(item, ProductionPreflightCheck) for item in self.checks):
            raise TypeError("Every check must be a ProductionPreflightCheck.")
        identifiers = tuple(item.identifier for item in self.checks)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Check identifiers must be unique.")
        expected = (
            ProductionPreflightStatus.FAIL
            if any(item.status is ProductionPreflightStatus.FAIL for item in self.checks)
            else ProductionPreflightStatus.PASS
        )
        if self.status is not expected:
            raise ValueError("Overall status must reflect mandatory check failures.")

    @property
    def passed(self):
        return self.status is ProductionPreflightStatus.PASS

    def check(self, identifier):
        for item in self.checks:
            if item.identifier == identifier:
                return item
        raise KeyError(identifier)

    def to_dict(self):
        return {
            "status": self.status.value,
            "execution_mode": self.execution_mode.value,
            "execution_enabled": self.execution_enabled,
            "checks": [item.to_dict() for item in self.checks],
        }

    def to_json(self):
        return json.dumps(
            self.to_dict(), sort_keys=True, allow_nan=False, separators=(",", ":")
        )


_AUTHORITY_CHECKS = (
    ("POSITION_MANAGER_IDENTITY", "POSITION_MANAGER_IDENTITY_MISMATCH"),
    ("MARKET_DATA_TRACKER_IDENTITY", "MARKET_DATA_TRACKER_IDENTITY_MISMATCH"),
    ("READINESS_GATE_IDENTITY", "READINESS_GATE_IDENTITY_MISMATCH"),
    ("EXECUTION_COORDINATOR_IDENTITY", "EXECUTION_COORDINATOR_IDENTITY_MISMATCH"),
    ("CLOSED_POSITION_HISTORY_IDENTITY", "CLOSED_POSITION_HISTORY_IDENTITY_MISMATCH"),
    ("RISK_CONFIGURATION", ("RISK_CONFIGURATION_MISMATCH", "RISK_NOT_CONFIGURED")),
    ("RISK_IDENTITY", "RISK_IDENTITY_MISMATCH"),
    ("RISK_AUTHORITY_IDENTITY", "RISK_AUTHORITY_IDENTITY_MISMATCH"),
    ("AUDIT_CONFIGURATION", "AUDIT_NOT_CONFIGURED"),
    ("AUDIT_IDENTITY", "AUDIT_IDENTITY_MISMATCH"),
    ("EXECUTION_GUARD_IDENTITY", "EXECUTION_GUARD_IDENTITY_MISMATCH"),
)


class ProductionPreflightInspector:
    """Aggregate existing initialized-state authorities without side effects."""

    def inspect(self, application):
        if not isinstance(application, ProductionApplication):
            raise TypeError("Application must be a ProductionApplication.")

        config = application.config
        components = application.components
        checks = []
        add = lambda identifier, passed, message: checks.append(
            ProductionPreflightCheck(
                identifier,
                ProductionPreflightStatus.PASS
                if passed else ProductionPreflightStatus.FAIL,
                message,
            )
        )
        add(
            "APPLICATION_INITIALIZED",
            application.state is ProductionApplicationState.INITIALIZED,
            "Application lifecycle state is initialized."
            if application.state is ProductionApplicationState.INITIALIZED
            else "Application lifecycle state is not initialized.",
        )
        add("CONFIGURATION_VALIDATED", True, "Production configuration is validated.")
        add(
            "COMPONENT_CONFIG_IDENTITY",
            components is not None and components.config is config,
            "Startup components retain the configured production identity."
            if components is not None and components.config is config
            else "Startup components do not retain the configured production identity.",
        )
        add(
            "EXECUTION_MODE_CONSISTENT",
            components is not None
            and components.market.execution_router.mode is config.execution_mode,
            "Execution mode matches production configuration."
            if components is not None
            and components.market.execution_router.mode is config.execution_mode
            else "Execution mode does not match production configuration.",
        )

        if config.execution_mode is ExecutionMode.PAPER:
            add(
                "EXECUTION_ENABLEMENT_CONSISTENT",
                config.execution_enabled is False,
                "PAPER execution remains disabled.",
            )
            self._add_not_applicable_live_checks(checks)
            return self._report(config, checks)

        runtime = components.runtime if components is not None else None
        market = components.market if components is not None else None
        add(
            "EXECUTION_ENABLEMENT_CONSISTENT",
            runtime is not None
            and runtime.execution_coordinator.enabled is config.execution_enabled,
            "LIVE execution enablement matches production configuration."
            if runtime is not None
            and runtime.execution_coordinator.enabled is config.execution_enabled
            else "LIVE execution enablement does not match production configuration.",
        )
        authentication = application.authentication_result
        add(
            "AUTHENTICATION_RESULT_PRESENT",
            isinstance(authentication, ProductionAuthenticationResult),
            "Authentication result is retained."
            if isinstance(authentication, ProductionAuthenticationResult)
            else "Authentication result is absent.",
        )
        authenticated = (
            isinstance(authentication, ProductionAuthenticationResult)
            and authentication.state is ProductionAuthenticationState.AUTHENTICATED
        )
        add(
            "AUTHENTICATION_STATE_AUTHENTICATED",
            authenticated,
            "Authentication state is authenticated."
            if authenticated else "Authentication state is not authenticated.",
        )
        add(
            "AUTHENTICATED_CLIENT_IDENTITY",
            authenticated and market is not None
            and authentication.kite_client is market.kite,
            "Authenticated client identity matches production composition."
            if authenticated and market is not None
            and authentication.kite_client is market.kite
            else "Authenticated client identity does not match production composition.",
        )

        startup = application.startup_result
        restart = application.restart_result
        add(
            "STARTUP_RESULT_PRESENT",
            startup is not None,
            "LIVE startup result is retained."
            if startup is not None else "LIVE startup result is absent.",
        )
        startup_identity = (
            startup is not None and runtime is not None and market is not None
            and startup.runtime is runtime and startup.market is market
        )
        add(
            "STARTUP_COMPONENT_IDENTITY",
            startup_identity,
            "Startup result retains exact runtime and market identities."
            if startup_identity else "Startup result identities are inconsistent.",
        )
        recovery_identity = (
            startup_identity
            and startup.recovery_result is runtime.readiness_gate.last_recovery_result
        )
        add(
            "RECOVERY_RESULT_IDENTITY",
            recovery_identity,
            "Recovery diagnostic identity is retained."
            if recovery_identity else "Recovery diagnostic identity is inconsistent.",
        )
        add(
            "RESTART_RESULT_PRESENT",
            restart is not None,
            "Restart orchestration result is retained."
            if restart is not None else "Restart orchestration result is absent.",
        )
        restart_safe = restart is not None and restart.state in (
            LiveRestartOrchestrationState.READY_FLAT,
            LiveRestartOrchestrationState.READY_RESTORED,
        )
        add(
            "RESTART_STATE_SAFE",
            restart_safe,
            "Restart orchestration state is safe."
            if restart_safe else "Restart orchestration state is not safe.",
        )
        continuity_consistent = (
            restart_safe and restart.startup_result is startup
            and runtime is not None and runtime.readiness_gate.is_ready
        )
        add(
            "READINESS_RECOVERY_CONTINUITY_CONSISTENT",
            continuity_consistent,
            "Readiness agrees with recovery and continuity diagnostics."
            if continuity_consistent
            else "Readiness does not agree with recovery and continuity diagnostics.",
        )

        recovery = startup.recovery_result if startup is not None else None
        recovery_has_pending = recovery is not None and (
            recovery.state is LiveRecoveryState.PENDING_ORDER
            or bool(recovery.relevant_order_statuses)
        )
        if recovery_has_pending:
            pending_consistent = (
                restart is not None
                and restart.state is LiveRestartOrchestrationState.BLOCKED
                and application.state is not ProductionApplicationState.INITIALIZED
            )
        else:
            pending_consistent = market is not None and market.pending_live_order is None
        add(
            "PENDING_ORDER_STATE_CONSISTENT",
            pending_consistent,
            "Pending-order state agrees with recovery authority."
            if pending_consistent else "Pending-order state conflicts with recovery authority.",
        )
        position_consistent = runtime is not None and market is not None and (
            production_position_context_consistent(
                runtime.position_manager.active_position,
                market.live_execution_context,
            )
        )
        add(
            "POSITION_CONTEXT_CONSISTENT",
            position_consistent,
            "Managed position and execution context agree."
            if position_consistent else "Managed position and execution context disagree.",
        )

        authority_issues = set(
            production_authority_identity_issues(runtime, market)
            if runtime is not None and market is not None else ()
        )
        for identifier, issue_codes in _AUTHORITY_CHECKS:
            codes = (issue_codes,) if isinstance(issue_codes, str) else issue_codes
            passed = runtime is not None and market is not None and not (
                authority_issues.intersection(codes)
            )
            add(
                identifier,
                passed,
                f"{identifier.replace('_', ' ').capitalize()} is consistent."
                if passed else f"{identifier.replace('_', ' ').capitalize()} is inconsistent.",
            )

        risk_pnl_configured = (
            runtime is not None
            and runtime.risk_evaluator is not None
            and components.runtime_pnl is not None
            and runtime.risk_evaluator.session_net_pnl_aggregator
            is components.runtime_pnl.session_net_pnl_aggregator
        )
        add(
            "RISK_PNL_CONFIGURATION", risk_pnl_configured,
            "Runtime risk and reporting share one P&L dependency."
            if risk_pnl_configured else "Runtime risk and reporting P&L are inconsistent.",
        )
        audit_path = (
            runtime is not None
            and runtime.audit_sink is not None
            and getattr(runtime.audit_sink, "path", None)
            == config.log_directory / "audit.jsonl"
        )
        add(
            "AUDIT_PATH_CONSISTENT", audit_path,
            "Audit path matches production configuration."
            if audit_path else "Audit path does not match production configuration.",
        )

        position_store_configured = runtime is not None and runtime.position_store is not None
        add(
            "POSITION_STORE_CONFIGURED", position_store_configured,
            "Position store is configured." if position_store_configured
            else "Position store is not configured.",
        )
        position_store_identity = position_store_configured and (
            runtime.position_store is market.live_position_store
        )
        add(
            "POSITION_STORE_IDENTITY", position_store_identity,
            "Position store identity is shared." if position_store_identity
            else "Position store identity is inconsistent.",
        )
        position_path = position_store_configured and (
            runtime.position_store.path == config.position_store_path
        )
        add(
            "POSITION_STORE_PATH_CONSISTENT", position_path,
            "Position store path matches configuration." if position_path
            else "Position store path does not match configuration.",
        )
        history_store_configured = (
            runtime is not None and runtime.closed_position_history_store is not None
        )
        add(
            "HISTORY_STORE_CONFIGURED", history_store_configured,
            "Closed-history store is configured." if history_store_configured
            else "Closed-history store is not configured.",
        )
        history_store_identity = history_store_configured and (
            runtime.closed_position_history_store
            is market.live_closed_position_history_store
        )
        add(
            "HISTORY_STORE_IDENTITY", history_store_identity,
            "Closed-history store identity is shared." if history_store_identity
            else "Closed-history store identity is inconsistent.",
        )
        history_path = history_store_configured and (
            runtime.closed_position_history_store.path
            == config.closed_position_history_store_path
        )
        add(
            "HISTORY_STORE_PATH_CONSISTENT", history_path,
            "Closed-history store path matches configuration." if history_path
            else "Closed-history store path does not match configuration.",
        )
        checks.append(ProductionPreflightCheck(
            "MARKET_DATA_RUNTIME_HEALTH",
            ProductionPreflightStatus.NOT_APPLICABLE,
            "Dynamic market-data health is outside static preflight.",
        ))
        return self._report(config, checks)

    @staticmethod
    def _report(config, checks):
        status = (
            ProductionPreflightStatus.FAIL
            if any(item.status is ProductionPreflightStatus.FAIL for item in checks)
            else ProductionPreflightStatus.PASS
        )
        return ProductionPreflightReport(
            status, config.execution_mode, config.execution_enabled, tuple(checks)
        )

    @staticmethod
    def _add_not_applicable_live_checks(checks):
        identifiers = (
            "AUTHENTICATION_RESULT_PRESENT", "AUTHENTICATION_STATE_AUTHENTICATED",
            "AUTHENTICATED_CLIENT_IDENTITY", "STARTUP_RESULT_PRESENT",
            "STARTUP_COMPONENT_IDENTITY", "RECOVERY_RESULT_IDENTITY",
            "RESTART_RESULT_PRESENT", "RESTART_STATE_SAFE",
            "READINESS_RECOVERY_CONTINUITY_CONSISTENT",
            "PENDING_ORDER_STATE_CONSISTENT", "POSITION_CONTEXT_CONSISTENT",
            *tuple(item[0] for item in _AUTHORITY_CHECKS),
            "RISK_PNL_CONFIGURATION", "AUDIT_PATH_CONSISTENT",
            "POSITION_STORE_CONFIGURED", "POSITION_STORE_IDENTITY",
            "POSITION_STORE_PATH_CONSISTENT", "HISTORY_STORE_CONFIGURED",
            "HISTORY_STORE_IDENTITY", "HISTORY_STORE_PATH_CONSISTENT",
            "MARKET_DATA_RUNTIME_HEALTH",
        )
        for identifier in identifiers:
            checks.append(ProductionPreflightCheck(
                identifier,
                ProductionPreflightStatus.NOT_APPLICABLE,
                "Check is not applicable to PAPER execution.",
            ))
