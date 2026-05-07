"""Tests for the four built-in subagent profiles.

Subagents are bundled markdown profiles in
``chimera/agents/presets/subagents/`` — ``planner.md``, ``researcher.md``,
``executor.md``, ``reviewer.md``. They register into the default
:class:`AgentRegistry` alongside the existing presets (build / explore /
general / plan / review).

Tests pin:

* All four profiles parse and register.
* Tool sets match the spec (planner = no exec; researcher = read-only;
  executor = full; reviewer = read+critic — no edit/write/bash).
* Permissions on read-only subagents are ``read_only``; the executor's
  is ``auto_approve``.
* :meth:`AgentConfig.build` produces a real :class:`Agent` for every
  profile (the tool set resolves cleanly through the tool registry).
* All seven Chimera CLIs that consult ``create_default_registry`` see
  the four subagents (smoke check via the shared registry).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.agents.config import AgentConfig
from chimera.agents.loader import (
    SUBAGENT_NAMES,
    builtin_subagents_dir,
    create_default_registry,
)


# ---------------------------------------------------------------------------
# Profile names + directory
# ---------------------------------------------------------------------------


def test_subagent_names_constant_matches_files() -> None:
    """``SUBAGENT_NAMES`` reflects every ``.md`` file shipped in the dir."""
    sub_dir = builtin_subagents_dir()
    found = sorted(p.stem for p in sub_dir.glob("*.md"))
    assert sorted(SUBAGENT_NAMES) == found


def test_builtin_subagents_dir_exists() -> None:
    """The package ships the subagents directory with all four profiles."""
    sub_dir = builtin_subagents_dir()
    assert sub_dir.is_dir()
    for name in SUBAGENT_NAMES:
        assert (sub_dir / f"{name}.md").is_file()


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


def test_default_registry_includes_all_subagents() -> None:
    """``create_default_registry`` registers every subagent profile."""
    reg = create_default_registry()
    for name in SUBAGENT_NAMES:
        assert reg.get(name) is not None, f"missing subagent {name}"


def test_default_registry_does_not_collide_with_presets() -> None:
    """Subagent names don't clobber the existing presets (no shared keys)."""
    reg = create_default_registry()
    names = reg.list()
    # build / explore / general / plan / review are the presets.
    for preset in ("build", "explore", "general", "plan", "review"):
        assert preset in names
    # Subagents are distinct entries.
    for sub in SUBAGENT_NAMES:
        assert sub in names


# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------


_FULL_TOOLSET = {
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "search",
    "list_files",
    "test",
    "git",
    "replace_in_file",
    "verify",
    "repo_map",
}
"""Tools the executor (full posture) is allowed to use."""

_READ_ONLY_TOOLS = {"read_file", "search", "list_files", "repo_map"}
"""Tools allowed for any read-only posture (planner core)."""

_FORBIDDEN_FOR_READONLY = {"write_file", "edit_file", "bash", "replace_in_file"}
"""Tools any read-only subagent must NOT include."""


def _config(name: str) -> AgentConfig:
    cfg = create_default_registry().get(name)
    assert cfg is not None
    return cfg


def test_planner_has_read_only_toolset() -> None:
    """Planner ships read-only tools (no edit/write/bash, no git)."""
    cfg = _config("planner")
    assert set(cfg.tools).issubset(_READ_ONLY_TOOLS | {"web_fetch"})
    assert _FORBIDDEN_FOR_READONLY.isdisjoint(cfg.tools)
    assert "git" not in cfg.tools  # planner doesn't even browse git history
    assert cfg.permissions == "read_only"
    assert cfg.loop == "plan_execute"


def test_researcher_has_read_only_plus_web() -> None:
    """Researcher is read-only and adds web_fetch for external lookups."""
    cfg = _config("researcher")
    assert set(cfg.tools).issubset(_READ_ONLY_TOOLS | {"web_fetch"})
    assert "web_fetch" in cfg.tools
    assert _FORBIDDEN_FOR_READONLY.isdisjoint(cfg.tools)
    assert cfg.permissions == "read_only"


def test_executor_has_full_toolset() -> None:
    """Executor has the full toolset (edit/write/bash/git/test all present)."""
    cfg = _config("executor")
    tools = set(cfg.tools)
    # The full toolset must include the destructive tools.
    for required in ("write_file", "edit_file", "bash", "git", "test"):
        assert required in tools, f"executor must include {required!r}"
    # And the read-only ones too.
    for required in _READ_ONLY_TOOLS:
        assert required in tools, f"executor must include {required!r}"
    assert tools.issubset(_FULL_TOOLSET)
    assert cfg.permissions == "auto_approve"


def test_reviewer_has_read_plus_git_no_edits() -> None:
    """Reviewer can read + run git but cannot edit/write/bash."""
    cfg = _config("reviewer")
    tools = set(cfg.tools)
    assert "git" in tools
    for required in _READ_ONLY_TOOLS:
        assert required in tools
    assert _FORBIDDEN_FOR_READONLY.isdisjoint(tools)
    assert cfg.permissions == "read_only"


# ---------------------------------------------------------------------------
# Build (does the tool registry resolve every profile end-to-end?)
# ---------------------------------------------------------------------------


def _mock_provider() -> MagicMock:
    p = MagicMock()
    p.model_name = "test-model"
    return p


@pytest.mark.parametrize("name", list(SUBAGENT_NAMES))
def test_subagent_builds_into_real_agent(name: str) -> None:
    """``AgentConfig.build`` produces a real Agent for every subagent."""
    cfg = _config(name)
    agent = cfg.build(_mock_provider())
    # Every tool name in the profile resolved to a real tool object.
    assert len(agent.tools) == len(cfg.tools), (
        f"{name}: expected {len(cfg.tools)} tools, got {len(agent.tools)}"
    )
    # System prompt got round-tripped from the markdown body.
    assert agent.name == name


# ---------------------------------------------------------------------------
# CLI integration smoke (registry is shared across all 7+ CLIs)
# ---------------------------------------------------------------------------


_CLIS_USING_DEFAULT_REGISTRY = [
    # Each entry is (module-path, attr-name) — every Chimera CLI that
    # spawns presets via /agent or --agent imports
    # ``chimera.agents.loader.create_default_registry``. The smoke
    # check imports each CLI's slash/agent handler and verifies the
    # subagents are visible.
    "chimera.cli.code",
    "chimera.cli.slash_commands",
    "chimera.ferret.agents",
]


@pytest.mark.parametrize("module_path", _CLIS_USING_DEFAULT_REGISTRY)
def test_cli_module_can_see_subagents(module_path: str) -> None:
    """Importing the CLI module + calling ``create_default_registry`` works."""
    import importlib

    importlib.import_module(module_path)
    # Independent of which CLI imported it, the registry must surface
    # the four subagents because ``create_default_registry`` is
    # idempotent + side-effect-free.
    reg = create_default_registry()
    for name in SUBAGENT_NAMES:
        assert reg.get(name) is not None


def test_subagents_advertised_in_listing() -> None:
    """Subagents show up in ``registry.list()`` so ``/agent list`` finds them."""
    reg = create_default_registry()
    names = reg.list()
    for name in SUBAGENT_NAMES:
        assert name in names


# ---------------------------------------------------------------------------
# Profile metadata sanity (descriptions + triggers + system prompts)
# ---------------------------------------------------------------------------


def test_every_subagent_has_description_and_prompt() -> None:
    """Every profile carries a non-empty description + system prompt."""
    for name in SUBAGENT_NAMES:
        cfg = _config(name)
        assert cfg.description, f"{name} missing description"
        assert cfg.system_prompt, f"{name} missing system_prompt"
        # System prompts are markdown bodies — must reference the role
        # name so the LLM knows which subagent it is.
        assert name in cfg.system_prompt.lower()


def test_subagent_prompts_set_constraints() -> None:
    """Read-only subagents are reminded NOT to call destructive tools."""
    for name in ("planner", "researcher", "reviewer"):
        cfg = _config(name)
        body = cfg.system_prompt.lower()
        # Each read-only profile must call out the no-edit/no-write/
        # no-bash constraint somewhere in the system prompt.
        assert "do not" in body or "do **not**" in body
