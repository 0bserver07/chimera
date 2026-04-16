"""Smoke tests for `CodingAgent.from_preset()` — the "rebuild Claude Code in
one line" promise.

These tests don't hit any network; they assert that each preset instantiates
cleanly with the advertised tool count. A failure here means the canonical
entry point for the library is broken.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    """openai SDK checks for OPENAI_API_KEY at client construction.
    Anthropic/others are lazy, so we only need to satisfy openai here."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-smoke")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-smoke")


@pytest.mark.parametrize(
    "preset, model, expected_tools",
    [
        ("claude_code", "claude-sonnet-4-20250514", 24),
        ("codex", "gpt-4o", 24),
        ("minimal", "claude-sonnet-4-20250514", 4),
        ("explore", "claude-sonnet-4-20250514", 3),
    ],
)
def test_preset_instantiates_with_expected_tool_count(preset, model, expected_tools, tmp_path):
    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent.from_preset(preset, model=model, project_dir=str(tmp_path))
    assert len(agent.tools) == expected_tools, (
        f"preset {preset!r} has {len(agent.tools)} tools, expected {expected_tools}"
    )
    assert agent.provider is not None
    assert agent.provider.model_name == model


def test_coding_agent_default_preset_is_claude_code(tmp_path):
    """Passing no preset argument should give the full claude_code stack."""
    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent(model="claude-sonnet-4-20250514", project_dir=str(tmp_path))
    assert len(agent.tools) == 24


def test_coding_agent_exposes_run_coroutine(tmp_path):
    """The `.run(task)` async generator is the public entry point — don't
    regress its presence."""
    import inspect

    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent.from_preset(
        "minimal", model="claude-sonnet-4-20250514", project_dir=str(tmp_path)
    )
    assert hasattr(agent, "run")
    assert inspect.isasyncgenfunction(agent.run) or inspect.iscoroutinefunction(agent.run)
