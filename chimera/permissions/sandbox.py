"""Sandbox adapter for controlled command execution and path filtering."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

__all__ = ["SandboxConfig", "CommandResult", "SandboxAdapter"]


@dataclass
class SandboxConfig:
    """Configuration for the sandbox adapter.

    Attributes:
        fs_allow_paths:          Paths explicitly allowed for filesystem access.
        fs_deny_paths:           Paths explicitly denied for filesystem access.
        network_allow_domains:   Domains explicitly allowed for network access.
        network_deny_domains:    Domains explicitly denied for network access.
        ALWAYS_DENY:             Paths that are always denied regardless of other rules.
    """

    fs_allow_paths: list[str] = field(default_factory=list)
    fs_deny_paths: list[str] = field(default_factory=list)
    network_allow_domains: list[str] = field(default_factory=list)
    network_deny_domains: list[str] = field(default_factory=list)
    ALWAYS_DENY: list[str] = field(default_factory=lambda: [
        ".chimera/settings.json",
        ".chimera/skills/",
    ])


@dataclass
class CommandResult:
    """Result of a sandboxed command execution."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class SandboxAdapter:
    """Execute commands with sandbox awareness and path filtering.

    Args:
        config: Sandbox configuration. Uses defaults if not provided.
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def execute(
        self,
        command: str,
        cwd: str,
        env: dict | None = None,
    ) -> CommandResult:
        """Execute command. For now, delegates to subprocess (sandbox enforcement
        is a future enhancement)."""
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return CommandResult(
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
            returncode=proc.returncode or 0,
        )

    def is_path_denied(self, path: str) -> bool:
        """Check whether *path* is denied by ALWAYS_DENY or fs_deny_paths."""
        for denied in self.config.ALWAYS_DENY + self.config.fs_deny_paths:
            if path.endswith(denied) or denied in path:
                return True
        return False

    def refresh_config(self, new_config: SandboxConfig) -> None:
        """Replace the current configuration with *new_config*."""
        self.config = new_config
