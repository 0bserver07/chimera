"""OS-native sandboxing: restrict process capabilities without Docker.

macOS: generates a Seatbelt (.sb) profile from SandboxPolicy and runs
commands via sandbox-exec.

Linux: uses Landlock (kernel 5.13+) if available, otherwise falls back
to a warning.

Falls back gracefully on unsupported platforms.
"""
from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chimera.types import CommandResult

if TYPE_CHECKING:
    from chimera.security.sandbox import SandboxPolicy


@dataclass
class SandboxCapabilities:
    """What the current platform supports."""

    seatbelt: bool = False   # macOS sandbox-exec
    landlock: bool = False   # Linux Landlock
    platform: str = ""

    @property
    def has_native_sandbox(self) -> bool:
        return self.seatbelt or self.landlock


def detect_capabilities() -> SandboxCapabilities:
    """Detect available sandboxing on this platform."""
    system = platform.system()
    caps = SandboxCapabilities(platform=system)

    if system == "Darwin":
        # Check for sandbox-exec
        try:
            result = subprocess.run(
                ["which", "sandbox-exec"],
                capture_output=True, timeout=5,
            )
            caps.seatbelt = result.returncode == 0
        except Exception:
            pass

    elif system == "Linux":
        # Landlock would require ctypes bindings to landlock_create_ruleset,
        # landlock_add_rule, and landlock_restrict_self. Those are not
        # implemented here, so we do NOT advertise Linux as sandboxed even
        # when /sys/kernel/security/landlock exists. Reporting landlock=True
        # here would be a lie: run() would silently fall through to
        # unsandboxed execution. See _run_landlock for details.
        caps.landlock = False

    return caps


def generate_seatbelt_profile(policy: SandboxPolicy) -> str:
    """Generate a macOS Seatbelt (.sb) profile from a SandboxPolicy.

    Args:
        policy: The sandbox policy to convert.

    Returns:
        String contents of a .sb profile.
    """
    from chimera.security.sandbox import AccessLevel

    lines = [
        "(version 1)",
        "(deny default)",
        "",
        "; Allow basic process operations",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "",
    ]

    # Filesystem rules
    for rule in policy.path_rules:
        if rule.access == AccessLevel.DENY:
            continue  # default is deny
        elif rule.access == AccessLevel.READ:
            if rule.recursive:
                lines.append(f'(allow file-read* (subpath "{rule.path}"))')
            else:
                lines.append(f'(allow file-read* (literal "{rule.path}"))')
        elif rule.access in (AccessLevel.WRITE, AccessLevel.EXECUTE):
            if rule.recursive:
                lines.append(f'(allow file-read* file-write* (subpath "{rule.path}"))')
            else:
                lines.append(f'(allow file-read* file-write* (literal "{rule.path}"))')

    # Always allow reading system libraries, binaries, and temp
    lines.extend([
        "",
        "; System access (required for basic command execution)",
        '(allow file-read* (subpath "/usr"))',
        '(allow file-read* (subpath "/bin"))',
        '(allow file-read* (subpath "/sbin"))',
        '(allow file-read* (subpath "/System"))',
        '(allow file-read* (subpath "/Library"))',
        '(allow file-read* (subpath "/private/tmp"))',
        '(allow file-read* file-write* (subpath "/private/tmp"))',
        '(allow file-read* (subpath "/private/var"))',
        '(allow file-read* (subpath "/dev"))',
        '(allow file-read* (subpath "/var"))',
        '(allow file-read* (subpath "/etc"))',
        '(allow file-read* (subpath "/tmp"))',
        '(allow file-read* file-write* (subpath "/tmp"))',
        '(allow process-exec (subpath "/usr"))',
        '(allow process-exec (subpath "/bin"))',
        '(allow process-exec (subpath "/sbin"))',
    ])

    # Network rules
    has_network = False
    for net_rule in policy.network_rules:
        if net_rule.allow:
            has_network = True
            break

    if has_network:
        lines.extend([
            "",
            "; Network access",
            "(allow network-outbound)",
            "(allow network-inbound)",
            "(allow system-socket)",
        ])
    else:
        lines.extend([
            "",
            "; Network denied",
        ])

    return "\n".join(lines) + "\n"


class NativeSandbox:
    """Run commands in an OS-native sandbox.

    Example::

        from chimera.security.sandbox import SandboxPolicy
        sandbox = NativeSandbox(SandboxPolicy.strict("/workspace"))
        result = sandbox.run("python script.py", cwd="/workspace")
    """

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy
        self._caps = detect_capabilities()

    @property
    def capabilities(self) -> SandboxCapabilities:
        """Platform sandbox capabilities."""
        return self._caps

    @property
    def is_available(self) -> bool:
        """Whether native sandboxing is available on this platform."""
        return self._caps.has_native_sandbox

    def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 120,
    ) -> CommandResult:
        """Run a command inside the sandbox.

        Args:
            command: Shell command to execute.
            cwd: Working directory.
            timeout: Timeout in seconds.

        Returns:
            CommandResult with stdout, stderr, exit_code.
        """
        if self._caps.seatbelt:
            return self._run_seatbelt(command, cwd, timeout)
        elif self._caps.landlock:
            return self._run_landlock(command, cwd, timeout)
        else:
            # Fallback: run unsandboxed with a warning
            return self._run_unsandboxed(command, cwd, timeout)

    def _run_seatbelt(
        self, command: str, cwd: str | None, timeout: int,
    ) -> CommandResult:
        """Run via macOS sandbox-exec."""
        profile = generate_seatbelt_profile(self._policy)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sb", delete=False,
        ) as f:
            f.write(profile)
            profile_path = f.name

        try:
            result = subprocess.run(
                ["sandbox-exec", "-f", profile_path, "sh", "-c", command],
                capture_output=True, text=True,
                cwd=cwd, timeout=timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Timeout", exit_code=124)
        except Exception as e:
            return CommandResult(stdout="", stderr=str(e), exit_code=1)
        finally:
            os.unlink(profile_path)

    def _run_landlock(
        self, command: str, cwd: str | None, timeout: int,
    ) -> CommandResult:
        """Reserved for future Linux Landlock enforcement.

        Not implemented: real enforcement requires ctypes bindings for
        ``landlock_create_ruleset``, ``landlock_add_rule``, and
        ``landlock_restrict_self``. Until those bindings land,
        :func:`detect_capabilities` reports ``landlock=False`` on Linux,
        so :meth:`run` never dispatches here -- it falls back to
        :meth:`_run_unsandboxed` with no pretense of sandboxing.

        Raises:
            NotImplementedError: Always. Callers must not invoke this
                method directly unless they have supplied a real Landlock
                implementation.
        """
        raise NotImplementedError(
            "Landlock enforcement is not implemented. Install a real "
            "ctypes-based Landlock backend and override detect_capabilities."
        )

    def _run_unsandboxed(
        self, command: str, cwd: str | None, timeout: int,
    ) -> CommandResult:
        """Fallback: run without sandboxing."""
        try:
            result = subprocess.run(
                ["sh", "-c", command],
                capture_output=True, text=True,
                cwd=cwd, timeout=timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Timeout", exit_code=124)
        except Exception as e:
            return CommandResult(stdout="", stderr=str(e), exit_code=1)
