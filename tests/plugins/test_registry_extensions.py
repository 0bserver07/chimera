"""Tests for ComponentRegistry extension methods (IG-10)."""
from __future__ import annotations

import pytest

from chimera.plugins.base import ComponentRegistry


class TestRegisterCommand:
    def test_register_command_stores_command(self):
        registry = ComponentRegistry()
        cmd = {"name": "test-cmd", "handler": lambda: None}
        registry.register_command(cmd)
        assert cmd in registry.commands

    def test_register_multiple_commands(self):
        registry = ComponentRegistry()
        cmd1 = {"name": "cmd1"}
        cmd2 = {"name": "cmd2"}
        registry.register_command(cmd1)
        registry.register_command(cmd2)
        assert len(registry.commands) == 2

    def test_commands_empty_by_default(self):
        registry = ComponentRegistry()
        assert registry.commands == []


class TestRegisterHook:
    def test_register_hook_stores_by_event(self):
        registry = ComponentRegistry()
        matcher = {"pattern": "bash*"}
        registry.register_hook("PreToolUse", matcher)
        assert "PreToolUse" in registry.hooks
        assert matcher in registry.hooks["PreToolUse"]

    def test_register_multiple_hooks_same_event(self):
        registry = ComponentRegistry()
        m1 = {"pattern": "bash*"}
        m2 = {"pattern": "write*"}
        registry.register_hook("PreToolUse", m1)
        registry.register_hook("PreToolUse", m2)
        assert len(registry.hooks["PreToolUse"]) == 2

    def test_register_hooks_different_events(self):
        registry = ComponentRegistry()
        m1 = {"pattern": "bash*"}
        m2 = {"pattern": "write*"}
        registry.register_hook("PreToolUse", m1)
        registry.register_hook("PostToolUse", m2)
        assert len(registry.hooks) == 2

    def test_hooks_empty_by_default(self):
        registry = ComponentRegistry()
        assert registry.hooks == {}


class TestRegisterSkill:
    def test_register_skill_stores_skill(self):
        registry = ComponentRegistry()
        skill = {"name": "code-review", "fn": lambda: None}
        registry.register_skill(skill)
        assert skill in registry.skills

    def test_register_multiple_skills(self):
        registry = ComponentRegistry()
        s1 = {"name": "skill1"}
        s2 = {"name": "skill2"}
        registry.register_skill(s1)
        registry.register_skill(s2)
        assert len(registry.skills) == 2

    def test_skills_empty_by_default(self):
        registry = ComponentRegistry()
        assert registry.skills == []
