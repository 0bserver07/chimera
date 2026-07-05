from unittest.mock import MagicMock
from chimera.agents.presets.agent_styles import AgentPreset


def _mock_provider():
    p = MagicMock()
    p.model_name = "test"
    p.complete.return_value = MagicMock(
        content="done",
        tool_calls=[],
        usage={"input_tokens": 10, "output_tokens": 5},
        has_tool_calls=False,
    )
    return p


def test_retry_min_preset_exists():
    assert AgentPreset.RETRY_MIN.name == "retry-min"
    assert "retry" == AgentPreset.RETRY_MIN.loop_type


def test_react_full_preset_exists():
    assert AgentPreset.REACT_FULL.name == "react-full"
    assert "AGENT_TOOLS" in AgentPreset.REACT_FULL.tool_names


def test_lint_loop_preset_exists():
    assert AgentPreset.LINT_LOOP.name == "lint-loop"
    assert "lint_feedback" == AgentPreset.LINT_LOOP.loop_type


def test_plan_act_preset_exists():
    assert AgentPreset.PLAN_ACT.name == "plan-act"
    assert "plan_act" == AgentPreset.PLAN_ACT.loop_type


def test_back_compat_aliases_resolve():
    # The replicas were formerly named after the coding agents they imitate.
    # Those attributes remain as back-compat aliases of the canonical presets.
    assert AgentPreset.SWE_AGENT is AgentPreset.RETRY_MIN
    assert AgentPreset.CODEX is AgentPreset.REACT_FULL
    assert AgentPreset.AIDER is AgentPreset.LINT_LOOP
    assert AgentPreset.CLINE is AgentPreset.PLAN_ACT


def test_build_retry_min():
    # _compose() is the in-tree path used by tests of the legacy
    # Agent + loop wiring. End users should call
    # CodingAgent.from_preset("swebench") instead — the public
    # AgentPreset.build() shim was removed in v0.7.0.
    agent = AgentPreset.RETRY_MIN._compose(_mock_provider())
    assert agent is not None
    assert len(agent.tools) >= 3  # minimal tool set


def test_build_react_full():
    agent = AgentPreset.REACT_FULL._compose(_mock_provider())
    assert len(agent.tools) >= 10  # AGENT_TOOLS = 13


def test_build_lint_loop():
    agent = AgentPreset.LINT_LOOP._compose(_mock_provider())
    assert agent is not None


def test_build_plan_act():
    agent = AgentPreset.PLAN_ACT._compose(_mock_provider())
    assert agent is not None


def test_retry_min_runs():
    provider = _mock_provider()
    agent = AgentPreset.RETRY_MIN._compose(provider)
    result = agent.run("Fix the bug", env=None)
    assert result.success


def test_react_full_runs():
    provider = _mock_provider()
    agent = AgentPreset.REACT_FULL._compose(provider)
    result = agent.run("Write code", env=None)
    assert result.success


def test_custom_preset():
    custom = AgentPreset(
        name="custom",
        description="My custom agent",
        tool_names=["read_file", "bash"],
        loop_type="react",
        max_steps=10,
        system_prompt="You are custom.",
    )
    agent = custom._compose(_mock_provider())
    assert len(agent.tools) == 2


def test_preset_descriptions():
    for preset in [
        AgentPreset.RETRY_MIN,
        AgentPreset.REACT_FULL,
        AgentPreset.LINT_LOOP,
        AgentPreset.PLAN_ACT,
    ]:
        assert preset.description != ""
        assert preset.system_prompt != ""
