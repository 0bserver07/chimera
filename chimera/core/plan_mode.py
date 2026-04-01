"""Plan mode guard — blocks write/execute tools when plan mode is active.

The guard is meant to be checked by the tool executor before dispatching
any tool call.  Read-only tools (search, read, think, etc.) are always
allowed; write and execute tools are blocked until plan mode is exited.
"""
from __future__ import annotations


class PlanModeGuard:
    """Blocks write/execute tools when plan mode is active."""

    BLOCKED_TOOLS: frozenset[str] = frozenset({
        "bash",
        "write_file",
        "edit_file",
        "replace_in_file",
        "apply_patch",
        "git",
    })

    def __init__(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def check(self, tool_name: str) -> tuple[bool, str]:
        """Return ``(allowed, message)``.

        If the tool is not allowed the message explains why.
        """
        if not self._active:
            return True, ""
        if tool_name in self.BLOCKED_TOOLS:
            return (
                False,
                f"Blocked: '{tool_name}' is not available in plan mode. "
                "Exit plan mode first.",
            )
        return True, ""
