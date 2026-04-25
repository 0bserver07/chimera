"""Tests for chimera.tools.powershell."""

from __future__ import annotations

import shutil

import pytest

from chimera.tools.powershell import PowerShellTool

pwsh_missing = shutil.which("pwsh") is None and shutil.which("powershell") is None


@pytest.mark.skipif(pwsh_missing, reason="pwsh not installed on PATH")
def test_run_simple_command() -> None:
    tool = PowerShellTool()
    res = tool.execute({"command": "Write-Output hi"}, env=None)
    assert res.success, res.error
    assert "hi" in res.output


@pytest.mark.skipif(pwsh_missing, reason="pwsh not installed on PATH")
def test_nonzero_exit_returns_error() -> None:
    tool = PowerShellTool()
    res = tool.execute({"command": "exit 7"}, env=None)
    assert not res.success
    assert "7" in (res.error or "")


def test_permission_content_extraction() -> None:
    tool = PowerShellTool()
    assert tool.get_permission_content({"command": "Get-ChildItem"}) == "Get-ChildItem"
    assert tool.get_permission_content({}) is None


def test_unavailable_platform_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """When pwsh and powershell are both absent, a clear error is returned."""
    import chimera.tools.powershell as ps

    monkeypatch.setattr(ps.shutil, "which", lambda _name: None)
    res = PowerShellTool().execute({"command": "Write-Output hi"}, env=None)
    assert not res.success
    assert "PowerShell not available" in (res.error or "")
