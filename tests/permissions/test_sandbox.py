"""Tests for chimera.permissions.sandbox — SandboxConfig, CommandResult, SandboxAdapter."""
from __future__ import annotations

import asyncio

import pytest

from chimera.permissions.sandbox import CommandResult, SandboxAdapter, SandboxConfig


class TestSandboxConfig:
    def test_defaults(self) -> None:
        cfg = SandboxConfig()
        assert cfg.fs_allow_paths == []
        assert cfg.fs_deny_paths == []
        assert cfg.network_allow_domains == []
        assert cfg.network_deny_domains == []

    def test_always_deny_defaults(self) -> None:
        cfg = SandboxConfig()
        assert ".chimera/settings.json" in cfg.ALWAYS_DENY
        assert ".chimera/skills/" in cfg.ALWAYS_DENY

    def test_custom_deny_paths(self) -> None:
        cfg = SandboxConfig(fs_deny_paths=["/etc/passwd"])
        assert "/etc/passwd" in cfg.fs_deny_paths


class TestCommandResult:
    def test_defaults(self) -> None:
        result = CommandResult()
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.returncode == 0

    def test_custom_values(self) -> None:
        result = CommandResult(stdout="hello", stderr="warn", returncode=1)
        assert result.stdout == "hello"
        assert result.stderr == "warn"
        assert result.returncode == 1


class TestSandboxAdapterPathDenied:
    def test_always_deny_settings(self) -> None:
        adapter = SandboxAdapter()
        assert adapter.is_path_denied(".chimera/settings.json") is True

    def test_always_deny_skills(self) -> None:
        adapter = SandboxAdapter()
        assert adapter.is_path_denied(".chimera/skills/my_skill.py") is True

    def test_always_deny_skills_dir(self) -> None:
        adapter = SandboxAdapter()
        assert adapter.is_path_denied("/home/user/.chimera/skills/") is True

    def test_allowed_path(self) -> None:
        adapter = SandboxAdapter()
        assert adapter.is_path_denied("src/main.py") is False

    def test_custom_fs_deny_paths(self) -> None:
        cfg = SandboxConfig(fs_deny_paths=["/etc/passwd"])
        adapter = SandboxAdapter(config=cfg)
        assert adapter.is_path_denied("/etc/passwd") is True
        assert adapter.is_path_denied("src/main.py") is False

    def test_combined_always_deny_and_custom(self) -> None:
        cfg = SandboxConfig(fs_deny_paths=["/secret/key"])
        adapter = SandboxAdapter(config=cfg)
        # ALWAYS_DENY still enforced
        assert adapter.is_path_denied(".chimera/settings.json") is True
        # Custom deny also enforced
        assert adapter.is_path_denied("/secret/key") is True


class TestSandboxAdapterRefreshConfig:
    def test_refresh_updates_config(self) -> None:
        adapter = SandboxAdapter()
        assert adapter.is_path_denied("/etc/shadow") is False

        new_cfg = SandboxConfig(fs_deny_paths=["/etc/shadow"])
        adapter.refresh_config(new_cfg)
        assert adapter.is_path_denied("/etc/shadow") is True

    def test_refresh_replaces_old_config(self) -> None:
        cfg1 = SandboxConfig(fs_deny_paths=["/old/path"])
        adapter = SandboxAdapter(config=cfg1)
        assert adapter.is_path_denied("/old/path") is True

        cfg2 = SandboxConfig(fs_deny_paths=["/new/path"])
        adapter.refresh_config(cfg2)
        # Old custom deny no longer applies (ALWAYS_DENY still does)
        assert adapter.is_path_denied("/old/path") is False
        assert adapter.is_path_denied("/new/path") is True


@pytest.mark.asyncio
class TestSandboxAdapterExecute:
    async def test_execute_simple_command(self) -> None:
        adapter = SandboxAdapter()
        result = await adapter.execute("echo hello", cwd="/tmp")
        assert result.returncode == 0
        assert "hello" in result.stdout

    async def test_execute_failing_command(self) -> None:
        adapter = SandboxAdapter()
        result = await adapter.execute("false", cwd="/tmp")
        assert result.returncode != 0

    async def test_execute_stderr(self) -> None:
        adapter = SandboxAdapter()
        result = await adapter.execute("echo error >&2", cwd="/tmp")
        assert "error" in result.stderr
