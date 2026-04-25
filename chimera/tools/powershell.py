"""PowerShell execution tool.

Mirrors :class:`~chimera.tools.bash.BashTool`'s ergonomics but invokes
PowerShell (``pwsh``) instead of ``/bin/sh``. On non-Windows hosts the tool
falls through to ``pwsh`` if PowerShell Core is on ``PATH``; otherwise it
returns a clear error.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


def _pwsh_path() -> str | None:
    """Return the path to a usable PowerShell binary, or ``None``."""
    return shutil.which("pwsh") or shutil.which("powershell")


class PowerShellTool(BaseTool):
    """Run a command in PowerShell.

    The command is passed as a single ``-Command`` string. ``timeout`` caps
    wall-clock execution. Output and stderr are returned similarly to
    :class:`~chimera.tools.bash.BashTool`.
    """

    name = "powershell"
    description = "Execute a PowerShell command and return its output."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell command"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": 120,
            },
        },
        "required": ["command"],
    }

    def get_permission_content(self, args: dict[str, Any]) -> str | None:
        """Expose the command string for permission rule matching."""
        cmd = args.get("command")
        return cmd if isinstance(cmd, str) else None

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        pwsh = _pwsh_path()
        if pwsh is None:
            return ToolResult(
                output="",
                error="PowerShell not available on this platform",
            )
        timeout = args.get("timeout", 120)
        try:
            proc = subprocess.run(
                [pwsh, "-NoLogo", "-NonInteractive", "-Command", args["command"]],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(output="", error=f"timed out after {timeout}s")
        out = proc.stdout
        if proc.stderr:
            out += f"\nSTDERR:\n{proc.stderr}"
        if proc.returncode == 0:
            return ToolResult(output=out)
        return ToolResult(output=out, error=f"Exit code {proc.returncode}")


__all__ = ["PowerShellTool"]
