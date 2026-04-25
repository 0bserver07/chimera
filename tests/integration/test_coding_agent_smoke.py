"""Smoke tests for `CodingAgent.from_preset()` — the "build a full-featured
coding agent in one line" promise.

These tests don't hit any network; they assert that each preset instantiates
cleanly with the advertised tool count. A failure here means the canonical
entry point for the library is broken.
"""
from __future__ import annotations

import importlib.util

import pytest

# The `openai` SDK is an optional extra; the `codex` preset routes to
# OpenAIProvider, so its parametrize row is skipped when the extra isn't
# installed (instead of failing with `pip install chimera-run[openai]`).
_HAS_OPENAI = importlib.util.find_spec("openai") is not None


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    """openai SDK checks for OPENAI_API_KEY at client construction.
    Anthropic/others are lazy, so we only need to satisfy openai here."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-smoke")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-smoke")


@pytest.mark.parametrize(
    "preset, model, expected_tools",
    [
        ("coding_agent", "claude-sonnet-4-20250514", 24),
        ("claude_code", "claude-sonnet-4-20250514", 24),
        pytest.param(
            "codex", "gpt-4o", 24,
            marks=pytest.mark.skipif(
                not _HAS_OPENAI,
                reason="codex preset needs `openai` extra (pip install chimera-run[openai])",
            ),
        ),
        ("minimal", "claude-sonnet-4-20250514", 4),
        ("explore", "claude-sonnet-4-20250514", 3),
    ],
)
def test_preset_instantiates_with_expected_tool_count(preset, model, expected_tools, tmp_path):
    from chimera.assembly.coding_agent import CodingAgent

    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", DeprecationWarning)
        agent = CodingAgent.from_preset(preset, model=model, project_dir=str(tmp_path))
    assert len(agent.tools) == expected_tools, (
        f"preset {preset!r} has {len(agent.tools)} tools, expected {expected_tools}"
    )
    assert agent.provider is not None
    assert agent.provider.model_name == model


def test_coding_agent_default_preset_is_coding_agent(tmp_path):
    """Passing no preset argument should give the full coding_agent stack."""
    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent(model="claude-sonnet-4-20250514", project_dir=str(tmp_path))
    assert len(agent.tools) == 24
    assert agent._config.name == "coding_agent"


def test_coding_agent_preset_works(tmp_path):
    """The canonical 'coding_agent' preset and its deprecated 'claude_code'
    alias must produce identical Agent + tool sets + system prompt."""
    import warnings as _warnings

    from chimera.assembly.coding_agent import CodingAgent

    canonical = CodingAgent.from_preset(
        "coding_agent",
        model="claude-sonnet-4-20250514",
        project_dir=str(tmp_path),
    )
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        legacy = CodingAgent.from_preset(
            "claude_code",
            model="claude-sonnet-4-20250514",
            project_dir=str(tmp_path),
        )

    # Same tool count
    assert len(canonical.tools) == len(legacy.tools) == 24

    # Same tool names (sorted to ignore ordering jitter)
    canonical_names = sorted(getattr(t, "name", type(t).__name__) for t in canonical.tools)
    legacy_names = sorted(getattr(t, "name", type(t).__name__) for t in legacy.tools)
    assert canonical_names == legacy_names

    # Same system prompt content
    assert canonical._system_prompt_text == legacy._system_prompt_text

    # Deprecation warning emitted for the legacy alias
    deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecation, "claude_code preset must emit a DeprecationWarning"
    assert "coding_agent" in str(deprecation[0].message)


def test_coding_agent_alias_registered_in_presets():
    """Registry sanity: both keys live in PRESETS and the alias map points at
    the canonical name."""
    from chimera.assembly.presets import DEPRECATED_PRESET_ALIASES, PRESETS

    assert "coding_agent" in PRESETS
    assert "claude_code" in PRESETS
    assert DEPRECATED_PRESET_ALIASES["claude_code"] == "coding_agent"


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
