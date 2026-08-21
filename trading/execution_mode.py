"""Explicit execution modes for strategy actions."""

from enum import Enum


class ExecutionMode(Enum):
    """Supported representations of strategy execution."""

    PAPER = "PAPER"
    LIVE = "LIVE"
