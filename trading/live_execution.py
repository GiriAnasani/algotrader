"""Explicitly authorized orchestration for isolated live execution."""

from trading.live_order import LiveOrderIntent
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


class LiveExecutionDisabledError(RuntimeError):
    """Raised when live execution is attempted without explicit authorization."""


class LiveExecutionCoordinator:
    """Coordinates intent translation and submission only when enabled."""

    def __init__(self, adapter, submitter, enabled=False):
        if not isinstance(adapter, ZerodhaOrderAdapter):
            raise TypeError("Adapter must be a ZerodhaOrderAdapter.")

        if not isinstance(submitter, ZerodhaOrderSubmitter):
            raise TypeError("Submitter must be a ZerodhaOrderSubmitter.")

        if not isinstance(enabled, bool):
            raise TypeError("Enabled must be a boolean.")

        self._adapter = adapter
        self._submitter = submitter
        self.enabled = enabled

    def execute(self, intent):
        """Submits one live intent only when explicit authorization is enabled."""
        if not isinstance(intent, LiveOrderIntent):
            raise TypeError("Intent must be a LiveOrderIntent.")

        if not self.enabled:
            raise LiveExecutionDisabledError("Live execution is disabled.")

        order_request = self._adapter.to_order_request(intent)
        return self._submitter.submit(order_request)
