"""Declarative sandbox policy for agent execution.

Defines filesystem, network, and command restrictions that environments
can enforce. The policy itself is declarative — it describes intent.
Environments enforce it (DockerEnvironment can map to Docker flags,
LocalEnvironment can check before executing).

Inspired by Codex's Seatbelt SBPL policies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "AccessLevel",
    "NetworkRule",
    "PathRule",
    "SandboxPolicy",
]


class AccessLevel(Enum):
    """Filesystem access level for a path rule."""

    DENY = "deny"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


@dataclass
class PathRule:
    """A filesystem access rule.

    Args:
        path: The filesystem path this rule applies to.
        access: The maximum access level granted.
        recursive: Whether the rule applies to all descendants.
    """

    path: str
    access: AccessLevel
    recursive: bool = True


@dataclass
class NetworkRule:
    """A network access rule.

    Args:
        host: Host to match. ``"*"`` matches all hosts.
        port: Port to match. ``None`` matches all ports.
        allow: Whether the connection is allowed.
    """

    host: str
    port: int | None = None
    allow: bool = True


@dataclass
class SandboxPolicy:
    """Declarative sandbox policy for agent execution.

    Defines what an agent is allowed to access:
    - Filesystem paths (read/write/execute/deny)
    - Network hosts (allow/deny)
    - Allowed/denied commands

    The policy is declarative — it describes intent. Environments
    enforce it (DockerEnvironment can map to Docker flags,
    LocalEnvironment can check before executing).

    Inspired by Codex's Seatbelt SBPL policies.

    Args:
        name: Human-readable policy name.
        path_rules: Filesystem rules evaluated in order (first match wins).
        network_rules: Network rules evaluated in order (first match wins).
        allowed_commands: Command allowlist. ``None`` means all allowed.
        denied_commands: Command denylist (checked before allowlist).
        max_processes: Maximum number of concurrent processes.
        max_memory_mb: Memory limit in megabytes.
        timeout_seconds: Wall-clock timeout for the entire session.
    """

    name: str = "default"

    # Filesystem rules (evaluated in order, first match wins)
    path_rules: list[PathRule] = field(default_factory=list)

    # Network rules
    network_rules: list[NetworkRule] = field(default_factory=list)

    # Command allowlist/denylist
    allowed_commands: list[str] | None = None  # None = all allowed
    denied_commands: list[str] = field(default_factory=list)

    # Process limits
    max_processes: int | None = None
    max_memory_mb: int | None = None
    timeout_seconds: int | None = None

    def check_path(self, path: str, access: AccessLevel) -> bool:
        """Check if a path access is allowed by this policy.

        Args:
            path: The filesystem path to check.
            access: The requested access level.

        Returns:
            True if the access is allowed.
        """
        import os

        abs_path = os.path.abspath(path)

        for rule in self.path_rules:
            rule_path = os.path.abspath(rule.path)
            if rule.recursive:
                if abs_path.startswith(rule_path) or abs_path == rule_path:
                    return self._access_allowed(rule.access, access)
            else:
                if abs_path == rule_path:
                    return self._access_allowed(rule.access, access)

        # Default: deny if any rules exist, allow if no rules
        return len(self.path_rules) == 0

    def check_network(self, host: str, port: int | None = None) -> bool:
        """Check if a network connection is allowed.

        Args:
            host: The target hostname.
            port: The target port (optional).

        Returns:
            True if the connection is allowed.
        """
        for rule in self.network_rules:
            if rule.host == "*" or rule.host == host:
                if rule.port is None or rule.port == port:
                    return rule.allow
        # Default: allow if no rules, deny if rules exist
        return len(self.network_rules) == 0

    def check_command(self, command: str) -> bool:
        """Check if a command is allowed.

        Args:
            command: The full command string.

        Returns:
            True if the command is allowed.
        """
        # Extract the base command (first word)
        base = command.split()[0] if command.strip() else ""

        if base in self.denied_commands:
            return False
        if self.allowed_commands is not None:
            return base in self.allowed_commands
        return True

    @staticmethod
    def _access_allowed(rule_access: AccessLevel, requested: AccessLevel) -> bool:
        """Check if a rule's access level permits the requested access.

        Args:
            rule_access: The access level granted by the rule.
            requested: The access level being requested.

        Returns:
            True if the rule permits the request.
        """
        if rule_access == AccessLevel.DENY:
            return False
        hierarchy = {
            AccessLevel.READ: 1,
            AccessLevel.WRITE: 2,
            AccessLevel.EXECUTE: 3,
        }
        return hierarchy.get(rule_access, 0) >= hierarchy.get(requested, 0)

    # --- Presets ---

    @classmethod
    def permissive(cls) -> SandboxPolicy:
        """No restrictions — for trusted environments."""
        return cls(name="permissive")

    @classmethod
    def workspace_only(cls, workspace: str) -> SandboxPolicy:
        """Read/write only within workspace, read-only elsewhere.

        Args:
            workspace: Path to the workspace directory.
        """
        return cls(
            name="workspace_only",
            path_rules=[
                PathRule(path=workspace, access=AccessLevel.WRITE, recursive=True),
                PathRule(path="/", access=AccessLevel.READ, recursive=True),
            ],
        )

    @classmethod
    def strict(cls, workspace: str) -> SandboxPolicy:
        """Workspace write, no network, limited commands.

        Args:
            workspace: Path to the workspace directory.
        """
        return cls(
            name="strict",
            path_rules=[
                PathRule(path=workspace, access=AccessLevel.WRITE, recursive=True),
                PathRule(path="/tmp", access=AccessLevel.WRITE, recursive=True),
                PathRule(path="/", access=AccessLevel.READ, recursive=True),
            ],
            network_rules=[NetworkRule(host="*", allow=False)],
            denied_commands=[
                "rm",
                "sudo",
                "chmod",
                "chown",
                "kill",
                "reboot",
                "shutdown",
            ],
            timeout_seconds=300,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            A JSON-serializable dictionary representation.
        """
        return {
            "name": self.name,
            "path_rules": [
                {
                    "path": r.path,
                    "access": r.access.value,
                    "recursive": r.recursive,
                }
                for r in self.path_rules
            ],
            "network_rules": [
                {"host": r.host, "port": r.port, "allow": r.allow}
                for r in self.network_rules
            ],
            "allowed_commands": self.allowed_commands,
            "denied_commands": self.denied_commands,
            "max_processes": self.max_processes,
            "max_memory_mb": self.max_memory_mb,
            "timeout_seconds": self.timeout_seconds,
        }
