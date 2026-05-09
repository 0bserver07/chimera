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


def test_swe_agent_preset_exists():
    assert AgentPreset.SWE_AGENT.name == "swe_agent"
    assert "retry" == AgentPreset.SWE_AGENT.loop_type


def test_codex_preset_exists():
    assert AgentPreset.CODEX.name == "codex"
    assert "AGENT_TOOLS" in AgentPreset.CODEX.tool_names


def test_aider_preset_exists():
    assert AgentPreset.AIDER.name == "aider"
    assert "lint_feedback" == AgentPreset.AIDER.loop_type


def test_cline_preset_exists():
    assert AgentPreset.CLINE.name == "cline"
    assert "plan_act" == AgentPreset.CLINE.loop_type


def test_build_swe_agent():
    # _compose() is the in-tree path used by tests of the legacy
    # Agent + loop wiring. End users should call
    # CodingAgent.from_preset("swebench") instead — the public
    # AgentPreset.build() shim was removed in v0.7.0.
    agent = AgentPreset.SWE_AGENT._compose(_mock_provider())
    assert agent is not None
    assert len(agent.tools) >= 3  # minimal tool set


def test_build_codex():
    agent = AgentPreset.CODEX._compose(_mock_provider())
    assert len(agent.tools) >= 10  # AGENT_TOOLS = 13


def test_build_aider():
    agent = AgentPreset.AIDER._compose(_mock_provider())
    assert agent is not None


def test_build_cline():
    agent = AgentPreset.CLINE._compose(_mock_provider())
    assert agent is not None


def test_swe_agent_runs():
    provider = _mock_provider()
    agent = AgentPreset.SWE_AGENT._compose(provider)
    result = agent.run("Fix the bug", env=None)
    assert result.success


def test_codex_runs():
    provider = _mock_provider()
    agent = AgentPreset.CODEX._compose(provider)
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
        AgentPreset.SWE_AGENT,
        AgentPreset.CODEX,
        AgentPreset.AIDER,
        AgentPreset.CLINE,
    ]:
        assert preset.description != ""
        assert preset.system_prompt != ""
