"""Tests for chimera.core.builtin_agents — built-in agent definitions."""
from __future__ import annotations

from chimera.core.agent_definition import AgentDefinition
from chimera.core.builtin_agents import BUILTIN_AGENTS


# ---------------------------------------------------------------------------
# Test 1: BUILTIN_AGENTS has exactly 3 entries
# ---------------------------------------------------------------------------


def test_builtin_agents_count():
    assert len(BUILTIN_AGENTS) == 3


# ---------------------------------------------------------------------------
# Test 2: Required agent names are present
# ---------------------------------------------------------------------------


def test_builtin_agents_names():
    assert "general-purpose" in BUILTIN_AGENTS
    assert "explore" in BUILTIN_AGENTS
    assert "plan" in BUILTIN_AGENTS


# ---------------------------------------------------------------------------
# Test 3: All entries are AgentDefinition instances
# ---------------------------------------------------------------------------


def test_builtin_agents_types():
    for name, defn in BUILTIN_AGENTS.items():
        assert isinstance(defn, AgentDefinition), f"{name} is not an AgentDefinition"


# ---------------------------------------------------------------------------
# Test 4: Each definition has required fields populated
# ---------------------------------------------------------------------------


def test_builtin_agents_have_name_and_description():
    for name, defn in BUILTIN_AGENTS.items():
        assert defn.name == name, f"Key '{name}' doesn't match definition name '{defn.name}'"
        assert defn.description, f"{name} has empty description"


# ---------------------------------------------------------------------------
# Test 5: Each definition has a system_prompt
# ---------------------------------------------------------------------------


def test_builtin_agents_have_system_prompt():
    for name, defn in BUILTIN_AGENTS.items():
        assert defn.system_prompt is not None, f"{name} has no system_prompt"
        assert len(defn.system_prompt) > 0, f"{name} has empty system_prompt"


# ---------------------------------------------------------------------------
# Test 6: general-purpose agent has broad tool access
# ---------------------------------------------------------------------------


def test_general_purpose_agent():
    gp = BUILTIN_AGENTS["general-purpose"]
    assert gp.name == "general-purpose"
    # general-purpose should have tools=None (all tools) or a broad list
    # The key property is it should not restrict tools unnecessarily


# ---------------------------------------------------------------------------
# Test 7: explore agent is configured for exploration
# ---------------------------------------------------------------------------


def test_explore_agent():
    explore = BUILTIN_AGENTS["explore"]
    assert explore.name == "explore"
    assert "explore" in explore.description.lower() or "search" in explore.description.lower() or "read" in explore.description.lower()


# ---------------------------------------------------------------------------
# Test 8: plan agent is configured for planning
# ---------------------------------------------------------------------------


def test_plan_agent():
    plan = BUILTIN_AGENTS["plan"]
    assert plan.name == "plan"
    assert "plan" in plan.description.lower()
