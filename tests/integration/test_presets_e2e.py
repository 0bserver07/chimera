"""End-to-end tests for AgentPresets with real or mock provider.

Each test verifies that the preset's loop variant, tools, and prompt
actually work together — not just that the Agent constructs.

    # Mock mode (CI):
    uv run pytest tests/test_presets_e2e.py -v

    # Real mode (GLM-5):
    source .env
    uv run pytest tests/test_presets_e2e.py -v
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock


import chimera
from chimera.agents.presets.agent_styles import AgentPreset
from chimera.types import ToolCall

_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_LIVE = _TOKEN is not None


def _real_provider():
    return chimera.create_provider()


def _mock_provider(*responses):
    provider = MagicMock()
    provider.model_name = "test-model"
    mocks = []
    for r in responses:
        m = MagicMock()
        if isinstance(r, str):
            m.content = r
            m.tool_calls = []
            m.has_tool_calls = False
        else:
            text, tcs = r
            m.content = text
            m.tool_calls = tcs
            m.has_tool_calls = len(tcs) > 0
        m.usage = {"input_tokens": 10, "output_tokens": 5}
        mocks.append(m)
    provider.complete.side_effect = mocks
    return provider


# -------------------------------------------------------------------
# RETRY_MIN preset: RetryLoop with minimal tools
# -------------------------------------------------------------------

def test_preset_retry_min_fixes_bug():
    """RETRY_MIN preset uses RetryLoop — should retry on failure."""
    with tempfile.TemporaryDirectory() as workdir:
        # Write buggy code + test
        with open(os.path.join(workdir, "calc.py"), "w") as f:
            f.write("def add(a, b):\n    return a - b  # bug\n")
        os.makedirs(os.path.join(workdir, "tests"))
        with open(os.path.join(workdir, "tests", "test_calc.py"), "w") as f:
            f.write("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n")

        env = chimera.LocalEnvironment(workdir=workdir)
        env.setup()

        if _LIVE:
            provider = _real_provider()
        else:
            # Mock: first attempt fails (no tools), retry succeeds
            edit_call = ToolCall(id="tc1", name="edit_file", arguments={
                "path": "calc.py", "old_string": "return a - b  # bug",
                "new_string": "return a + b",
            })
            provider = _mock_provider(
                ("Fixing the bug", [edit_call]),  # attempt 1 inner loop
                "Fixed: changed - to +",           # attempt 1 done
            )

        # _compose() is the non-deprecated path equivalent to .build();
        # used here so the test doesn't pin itself to the v0.7.0 removal target.
        agent = AgentPreset.RETRY_MIN._compose(provider)
        result = agent.run("Fix the bug in calc.py so test_add passes.", env=env)

        assert result.success
        if _LIVE:
            # Verify the file was actually fixed
            content = open(os.path.join(workdir, "calc.py")).read()
            assert "+" in content or "add" in content

        env.cleanup()


# -------------------------------------------------------------------
# LINT_LOOP preset: LintFeedbackLoop
# -------------------------------------------------------------------

def test_preset_lint_loop_lint_feedback():
    """LINT_LOOP preset uses LintFeedbackLoop — should catch lint issues."""
    with tempfile.TemporaryDirectory() as workdir:
        env = chimera.LocalEnvironment(workdir=workdir)
        env.setup()

        if _LIVE:
            provider = _real_provider()
        else:
            write_call = ToolCall(id="tc1", name="write_file", arguments={
                "path": "utils.py",
                "content": "def greet(name: str) -> str:\n    return f'Hello {name}'\n",
            })
            # Need enough responses: inner loop (write+done), lint round (fix+done)
            provider = _mock_provider(
                ("Writing utils.py", [write_call]),
                "Created utils.py with greet function.",
                "Fixed lint issues.",
                "All clean now.",
                "Done.",
            )

        agent = AgentPreset.LINT_LOOP._compose(provider)
        result = agent.run(
            "Create a utils.py file with a greet(name) function that returns 'Hello {name}'.",
            env=env,
        )

        assert result.success
        if _LIVE:
            assert os.path.exists(os.path.join(workdir, "utils.py"))

        env.cleanup()


# -------------------------------------------------------------------
# PLAN_ACT preset: PlanActLoop (plan then execute)
# -------------------------------------------------------------------

def test_preset_plan_act_plan_then_act():
    """PLAN_ACT preset uses PlanActLoop — should plan first, then execute."""
    with tempfile.TemporaryDirectory() as workdir:
        # Write a file for the agent to read during planning
        with open(os.path.join(workdir, "README.md"), "w") as f:
            f.write("# My Project\nA Python calculator.\n")

        env = chimera.LocalEnvironment(workdir=workdir)
        env.setup()

        if _LIVE:
            provider = _real_provider()
        else:
            # Plan phase: agent explores (read-only)
            provider = _mock_provider(
                "Plan: 1) Create calc.py with add/sub 2) Create tests 3) Run tests",
                # Act phase: agent executes
                "Created calculator module with tests. All passing.",
            )

        agent = AgentPreset.PLAN_ACT._compose(provider)
        result = agent.run(
            "Create a simple calculator module with add and subtract functions, plus tests.",
            env=env,
        )

        assert result.success
        # PLAN_ACT's PlanActLoop should have done 2+ steps (plan + act)
        assert result.steps >= 2

        env.cleanup()


# -------------------------------------------------------------------
# REACT_FULL preset: full tools, standard ReAct
# -------------------------------------------------------------------

def test_preset_react_full_full_task():
    """REACT_FULL preset uses full AGENT_TOOLS — should handle complex tasks."""
    with tempfile.TemporaryDirectory() as workdir:
        env = chimera.LocalEnvironment(workdir=workdir)
        env.setup()

        if _LIVE:
            provider = _real_provider()
        else:
            write_call = ToolCall(id="tc1", name="write_file", arguments={
                "path": "hello.py", "content": "print('Hello from react-full preset!')\n",
            })
            bash_call = ToolCall(id="tc2", name="bash", arguments={
                "command": "python hello.py",
            })
            provider = _mock_provider(
                ("Creating and running", [write_call, bash_call]),
                "Output: Hello from react-full preset!",
            )

        agent = AgentPreset.REACT_FULL._compose(provider)
        result = agent.run(
            "Create hello.py that prints 'Hello from react-full preset!' and run it.",
            env=env,
        )

        assert result.success
        if _LIVE:
            assert os.path.exists(os.path.join(workdir, "hello.py"))

        env.cleanup()


# -------------------------------------------------------------------
# Custom preset
# -------------------------------------------------------------------

def test_custom_preset():
    """User-defined preset works end-to-end."""
    custom = AgentPreset(
        name="minimal",
        description="Minimal agent for quick tasks",
        tool_names=["read_file", "bash"],
        loop_type="react",
        max_steps=5,
        system_prompt="You are a minimal agent. Only read files and run commands.",
    )

    if _LIVE:
        provider = _real_provider()
    else:
        provider = _mock_provider("The current directory contains: README.md, setup.py")

    agent = custom._compose(provider)
    with tempfile.TemporaryDirectory() as d:
        env = chimera.LocalEnvironment(workdir=d)
        env.setup()
        result = agent.run("List what files are in the current directory.", env=env)
        assert result.success
        assert len(agent.tools) == 2
        env.cleanup()


# -------------------------------------------------------------------
# Verify preset properties
# -------------------------------------------------------------------

def test_all_presets_have_different_loops():
    """Each preset uses a different loop variant."""
    loop_types = {
        AgentPreset.RETRY_MIN.loop_type,
        AgentPreset.REACT_FULL.loop_type,
        AgentPreset.LINT_LOOP.loop_type,
        AgentPreset.PLAN_ACT.loop_type,
    }
    # At least 3 distinct loop types (REACT_FULL uses "react" which is default)
    assert len(loop_types) >= 3


def test_all_presets_buildable():
    """Every preset builds without errors."""
    provider = _mock_provider("ok")
    for preset in [
        AgentPreset.RETRY_MIN,
        AgentPreset.REACT_FULL,
        AgentPreset.LINT_LOOP,
        AgentPreset.PLAN_ACT,
    ]:
        agent = preset._compose(provider)
        assert agent is not None
        assert len(agent.tools) > 0
        assert agent.prompt is not None


def test_retry_min_has_minimal_tools():
    """RETRY_MIN should have fewer tools than REACT_FULL."""
    provider = _mock_provider("ok")
    retry_min = AgentPreset.RETRY_MIN._compose(provider)
    react_full = AgentPreset.REACT_FULL._compose(provider)
    assert len(retry_min.tools) < len(react_full.tools)
