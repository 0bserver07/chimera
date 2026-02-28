# chimera/permissions/presets.py
from __future__ import annotations

from typing import Any

from chimera.permissions.base import PermissionAction, PermissionPolicy

__all__ = ["AutoApprove", "AlwaysDeny", "AllowList", "ReadOnly", "Interactive"]


class AutoApprove(PermissionPolicy):
    """Approve everything unconditionally."""

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        return PermissionAction.ALLOW


class AlwaysDeny(PermissionPolicy):
    """Deny everything unconditionally."""

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        return PermissionAction.DENY


class AllowList(PermissionPolicy):
    """Only allow tools whose names appear in the allow list; deny all others."""

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        if tool_name in self._allowed:
            return PermissionAction.ALLOW
        return PermissionAction.DENY


class ReadOnly(PermissionPolicy):
    """Allow read-only tools; deny everything else."""

    ALLOW_TOOLS: frozenset[str] = frozenset({
        "read_file",
        "search",
        "list_files",
        "repo_map",
    })

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        if tool_name in self.ALLOW_TOOLS:
            return PermissionAction.ALLOW
        return PermissionAction.DENY


class Interactive(PermissionPolicy):
    """Ask for write/destructive operations, allow reads automatically."""

    READ_TOOLS: frozenset[str] = frozenset({
        "read_file",
        "search",
        "list_files",
        "repo_map",
    })

    ASK_TOOLS: frozenset[str] = frozenset({
        "bash",
        "write_file",
        "edit_file",
        "replace_in_file",
        "git",
    })

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        if tool_name in self.READ_TOOLS:
            return PermissionAction.ALLOW
        if tool_name in self.ASK_TOOLS:
            return PermissionAction.ASK
        return PermissionAction.ASK
