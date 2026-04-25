"""Sandbox adapter for controlled command execution and path filtering."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SandboxConfig", "CommandResult", "SandboxAdapter", "toggle"]


def toggle(session: Any) -> bool:
    """Toggle a sandbox flag on the supplied session.

    Stores the boolean state on ``session._sandbox_enabled`` so the
    interactive REPL's ``/sandbox`` command and downstream tools can
    inspect it. The first call enables the sandbox; subsequent calls
    flip the bit.

    Args:
        session: The active :class:`chimera.sessions.session.Session`-like
            object. The function only reads/writes the
            ``_sandbox_enabled`` attribute, so any object will do.

    Returns:
        The new sandbox state (``True`` = enabled, ``False`` = disabled).
    """
    # WHY (audit M-1): the slash command surface promised a sandbox
    # toggle but no callable existed. We keep the implementation
    # deliberately minimal — a flag the REPL can broadcast — so callers
    # can wire deeper enforcement (path filtering, command analysis)
    # incrementally without breaking the slash-command contract.
    current = bool(getattr(session, "_sandbox_enabled", False))
    new_state = not current
    try:
        setattr(session, "_sandbox_enabled", new_state)
    except (AttributeError, TypeError):
        # Read-only session-like (frozen dataclass / SimpleNamespace
        # without dict): surface the requested state without mutating.
        pass
    return new_state


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
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute command with sandbox restrictions enforced."""
        # 1. Pre-execution: check for denied path access in the command
        denied = self._check_command_for_denied_paths(command)
        if denied:
            return CommandResult(
                stdout="",
                stderr=f"Sandbox: access denied to {denied}",
                returncode=1,
            )

        # 2. Execute
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        # 3. Post-execution: scrub bare-repo files
        self._scrub_bare_repo_files(cwd)

        return CommandResult(
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
            returncode=proc.returncode or 0,
        )

    def _check_command_for_denied_paths(self, command: str) -> str | None:
        """Check if command references any denied paths."""
        all_denied = self.config.ALWAYS_DENY + self.config.fs_deny_paths
        for denied_path in all_denied:
            if denied_path in command:
                return denied_path
        return None

    def _scrub_bare_repo_files(self, cwd: str) -> None:
        """Remove planted bare-repo files after sandboxed execution.

        Attackers can plant HEAD, objects/, refs/ in cwd to trick git into
        treating the directory as a repo and loading hooks. Scrub these
        after sandboxed commands.
        """
        import os

        suspicious = ["HEAD", "config", "description"]
        suspicious_dirs = ["objects", "refs", "hooks"]

        for fname in suspicious:
            fpath = os.path.join(cwd, fname)
            # Only remove if it looks like a bare repo file (not a normal project file)
            if os.path.isfile(fpath):
                try:
                    with open(fpath) as f:
                        content = f.read(100)
                    if content.startswith("ref: refs/") or "Unnamed repository" in content:
                        os.unlink(fpath)
                except (OSError, UnicodeDecodeError):
                    pass

        for dname in suspicious_dirs:
            dpath = os.path.join(cwd, dname)
            if os.path.isdir(dpath):
                # Check if it's a small, suspicious directory (bare repo artifacts are tiny)
                try:
                    entries = os.listdir(dpath)
                    if len(entries) <= 3 and dname == "objects":
                        import shutil
                        shutil.rmtree(dpath, ignore_errors=True)
                except OSError:
                    pass

    def is_path_denied(self, path: str) -> bool:
        """Check whether *path* is denied by ALWAYS_DENY or fs_deny_paths."""
        for denied in self.config.ALWAYS_DENY + self.config.fs_deny_paths:
            if path.endswith(denied) or denied in path:
                return True
        return False

    def refresh_config(self, new_config: SandboxConfig) -> None:
        """Replace the current configuration with *new_config*."""
        self.config = new_config
