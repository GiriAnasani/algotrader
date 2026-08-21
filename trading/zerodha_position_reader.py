"""Single-read access to Zerodha net broker positions."""

from collections.abc import Mapping

from trading.zerodha_position_adapter import ZerodhaPositionAdapter


class ZerodhaPositionReader:
    """Reads Zerodha's ``net`` positions through an injected client.

    Zerodha's ``net`` group is used because it represents the current net
    exposure; the separate ``day`` group is not position truth for this use.
    """

    def __init__(self, kite_client, adapter=None):
        if kite_client is None:
            raise ValueError("A Kite-compatible client is required.")

        if adapter is None:
            adapter = ZerodhaPositionAdapter()

        if not isinstance(adapter, ZerodhaPositionAdapter):
            raise TypeError("Adapter must be a ZerodhaPositionAdapter.")

        self._kite_client = kite_client
        self._adapter = adapter

    def read(self):
        """Calls ``positions()`` once and returns normalized net positions."""
        response = self._kite_client.positions()
        if not isinstance(response, Mapping):
            raise TypeError("Broker positions response must be a mapping.")

        if "net" not in response:
            raise ValueError("Broker positions response must contain a net collection.")

        net_positions = response["net"]
        if not isinstance(net_positions, list):
            raise TypeError("Broker net positions must be a list.")

        return tuple(
            self._adapter.to_broker_position(row)
            for row in net_positions
        )
