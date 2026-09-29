from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Strategy2PendingEntry:
    """Frozen Strategy 2 confirmation waiting for the next option minute."""

    direction: str
    confirmation_time: datetime
    expected_entry_time: datetime
    atm_strike: float
    otm_strike: float
    contract_symbol: str
    instrument_token: int


class Strategy2PendingEntryManager:
    """Owns the single pending Strategy 2 entry allowed at a time."""

    def __init__(self):
        self.pending_entry = None

    def create(
        self,
        *,
        direction,
        confirmation_time,
        contract,
        active_position=False,
    ):
        direction = str(direction).strip().upper()

        if direction not in ("CE", "PE"):
            raise ValueError("Strategy 2 pending direction must be CE or PE.")

        if not isinstance(confirmation_time, datetime):
            raise TypeError("confirmation_time must be a datetime.")

        if active_position:
            raise RuntimeError(
                "Cannot create a Strategy 2 pending entry while a position is active."
            )

        if self.pending_entry is not None:
            raise RuntimeError("Strategy 2 already has a pending entry.")

        contract_side = str(contract.get("option_type", "")).strip().upper()
        if contract_side != direction:
            raise ValueError(
                "Strategy 2 pending direction must match the selected contract."
            )

        pending = Strategy2PendingEntry(
            direction=direction,
            confirmation_time=confirmation_time,
            expected_entry_time=confirmation_time + timedelta(minutes=1),
            atm_strike=float(contract["atm_strike"]),
            otm_strike=float(contract["otm_strike"]),
            contract_symbol=str(contract["tradingsymbol"]),
            instrument_token=int(contract["instrument_token"]),
        )

        self.pending_entry = pending
        return pending

    def clear(self):
        pending = self.pending_entry
        self.pending_entry = None
        return pending
