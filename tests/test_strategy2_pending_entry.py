from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.strategy2_pending_entry import Strategy2PendingEntryManager


IST = ZoneInfo("Asia/Kolkata")


def contract(option_type):
    return {
        "option_type": option_type,
        "atm_strike": 25000.0,
        "otm_strike": 25050.0 if option_type == "CE" else 24950.0,
        "tradingsymbol": (
            "NIFTY26SEP25050CE"
            if option_type == "CE"
            else "NIFTY26SEP24950PE"
        ),
        "instrument_token": 202 if option_type == "CE" else 203,
    }


def test_ce_confirmation_creates_next_minute_pending_entry():
    manager = Strategy2PendingEntryManager()
    confirmation = datetime(2026, 9, 29, 9, 52, tzinfo=IST)

    pending = manager.create(
        direction="CE",
        confirmation_time=confirmation,
        contract=contract("CE"),
    )

    assert pending.direction == "CE"
    assert pending.confirmation_time == confirmation
    assert pending.expected_entry_time == datetime(
        2026, 9, 29, 9, 53, tzinfo=IST
    )
    assert pending.atm_strike == 25000.0
    assert pending.otm_strike == 25050.0
    assert pending.instrument_token == 202


def test_pe_confirmation_creates_next_minute_pending_entry():
    manager = Strategy2PendingEntryManager()
    confirmation = datetime(2026, 9, 29, 9, 58, tzinfo=IST)

    pending = manager.create(
        direction="PE",
        confirmation_time=confirmation,
        contract=contract("PE"),
    )

    assert pending.direction == "PE"
    assert pending.expected_entry_time == datetime(
        2026, 9, 29, 9, 59, tzinfo=IST
    )
    assert pending.otm_strike == 24950.0
    assert pending.instrument_token == 203


def test_duplicate_pending_entry_is_rejected():
    manager = Strategy2PendingEntryManager()
    confirmation = datetime(2026, 9, 29, 9, 52, tzinfo=IST)

    manager.create(
        direction="CE",
        confirmation_time=confirmation,
        contract=contract("CE"),
    )

    with pytest.raises(RuntimeError, match="already has a pending entry"):
        manager.create(
            direction="CE",
            confirmation_time=confirmation,
            contract=contract("CE"),
        )


def test_active_position_blocks_pending_entry():
    manager = Strategy2PendingEntryManager()
    confirmation = datetime(2026, 9, 29, 9, 52, tzinfo=IST)

    with pytest.raises(RuntimeError, match="position is active"):
        manager.create(
            direction="CE",
            confirmation_time=confirmation,
            contract=contract("CE"),
            active_position=True,
        )
