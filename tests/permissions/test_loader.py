"""Tests for chimera.permissions.loader — PermissionRuleLoader."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from chimera.permissions.context import PermissionContext
from chimera.permissions.loader import PermissionRuleLoader
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import RuleSource


class TestPermissionRuleLoader:
    def _write_settings(self, directory: Path, data: dict) -> None:
        settings_dir = directory / ".chimera"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.json").write_text(json.dumps(data))

    def test_load_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert isinstance(ctx, PermissionContext)
            assert ctx.mode == PermissionMode.DEFAULT

    def test_load_allow_rules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_settings(Path(td), {
                "permissions": {
                    "allow": ["Bash", "Write"],
                },
            })
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert RuleSource.PROJECT in ctx.allow_rules
            assert "Bash" in ctx.allow_rules[RuleSource.PROJECT]
            assert "Write" in ctx.allow_rules[RuleSource.PROJECT]

    def test_load_deny_rules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_settings(Path(td), {
                "permissions": {
                    "deny": ["Bash(rm -rf *)"],
                },
            })
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert RuleSource.PROJECT in ctx.deny_rules
            assert "Bash(rm -rf *)" in ctx.deny_rules[RuleSource.PROJECT]

    def test_load_ask_rules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_settings(Path(td), {
                "permissions": {
                    "ask": ["Bash"],
                },
            })
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert RuleSource.PROJECT in ctx.ask_rules

    def test_load_user_overrides(self) -> None:
        """User-level settings are loaded with RuleSource.USER."""
        with tempfile.TemporaryDirectory() as proj, \
             tempfile.TemporaryDirectory() as user:
            self._write_settings(Path(proj), {
                "permissions": {"allow": ["Bash"]},
            })
            self._write_settings(Path(user), {
                "permissions": {"allow": ["Write"]},
            })
            loader = PermissionRuleLoader(project_dir=proj, user_dir=user)
            ctx = loader.load()
            assert "Bash" in ctx.allow_rules.get(RuleSource.PROJECT, [])
            assert "Write" in ctx.allow_rules.get(RuleSource.USER, [])

    def test_load_mode_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_settings(Path(td), {
                "permissions": {
                    "mode": "bypass_permissions",
                    "allow": [],
                },
            })
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert ctx.mode == PermissionMode.BYPASS

    def test_missing_settings_dir(self) -> None:
        """Non-existent directory should not raise — just return defaults."""
        loader = PermissionRuleLoader(project_dir="/nonexistent/path")
        ctx = loader.load()
        assert ctx.mode == PermissionMode.DEFAULT

    def test_malformed_json_graceful(self) -> None:
        """Malformed JSON should not crash — return defaults."""
        with tempfile.TemporaryDirectory() as td:
            settings_dir = Path(td) / ".chimera"
            settings_dir.mkdir()
            (settings_dir / "settings.json").write_text("{invalid json")
            loader = PermissionRuleLoader(project_dir=td)
            ctx = loader.load()
            assert ctx.mode == PermissionMode.DEFAULT
