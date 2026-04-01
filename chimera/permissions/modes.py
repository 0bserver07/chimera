"""Permission modes controlling the overall approval behaviour."""
from __future__ import annotations

from enum import Enum

__all__ = ["PermissionMode"]


class PermissionMode(Enum):
    """High-level mode that governs how permission checks behave.

    DEFAULT  – normal interactive behaviour (ask for dangerous ops).
    PLAN     – read-only planning; all writes denied.
    ACCEPT_EDITS – auto-approve file edits, ask for everything else.
    BYPASS   – skip all permission prompts (dangerous).
    DONT_ASK – deny anything that would normally prompt.
    AUTO     – fully autonomous; equivalent to BYPASS but logged.
    """

    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass_permissions"
    DONT_ASK = "dont_ask"
    AUTO = "auto"
