"""GLM-5 integration tests for TreeSearch strategy.

Configure via:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token-here"
    export ANTHROPIC_MODEL="glm-5"

Skipped when no credentials are set.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.anthropic import AnthropicProvider
from chimera.tools.bash import BashTool
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.training.spec import Spec
from chimera.training.strategies.tree_search import TreeSearch

_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
_model = os.environ.get("ANTHROPIC_MODEL", "glm-5")

pytestmark = pytest.mark.skipif(
    not _api_key,
    reason="Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN to run integration tests",
)


@pytest.fixture(scope="module")
def glm5_provider() -> AnthropicProvider:
    return AnthropicProvider(
        model=_model,
        api_key=_api_key,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )


def _make_multiply_env(tmpdir: str) -> LocalEnvironment:
    """Create env with a simple multiply test."""
    Path(tmpdir, "test_math_ops.py").write_text(
        "from math_ops import multiply\n\n"
        "def test_multiply_positive():\n"
        "    assert multiply(3, 4) == 12\n\n"
        "def test_multiply_zero():\n"
        "    assert multiply(0, 5) == 0\n"
    )
    env = LocalEnvironment(
        workdir=tmpdir,
        test_cmd="python -m pytest test_math_ops.py -v --tb=short",
    )
    env.setup()
    return env


class TestTreeSearchGLM5:
    def test_solves_simple_coding_problem(self, glm5_provider):
        """TreeSearch with GLM-5 should make progress on a trivial problem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_multiply_env(tmpdir)
            agent = Agent(
                provider=glm5_provider,
                tools=[ReadFileTool(), WriteFileTool(), BashTool()],
                loop=ReAct(max_steps=10),
            )
            spec = Spec.from_tests(
                tmpdir,
                "Implement a function multiply(a, b) in math_ops.py that returns a * b.",
            )
            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=8)
            result = ts.run(agent, spec, env)

            assert result.best_pass_rate > 0.0, (
                f"Expected at least one test to pass. "
                f"History: {[(e.passed, e.total) for e in result.history]}"
            )
            assert len(result.history) >= 1

    def test_converges_on_easy_problem(self, glm5_provider):
        """TreeSearch must converge to 100% on an easy problem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_multiply_env(tmpdir)
            agent = Agent(
                provider=glm5_provider,
                tools=[ReadFileTool(), WriteFileTool(), BashTool()],
                loop=ReAct(max_steps=10),
            )
            spec = Spec.from_tests(
                tmpdir,
                "Implement multiply(a, b) in math_ops.py. Return a * b.",
            )
            ts = TreeSearch(branch_factor=2, max_depth=3, max_nodes=12)
            result = ts.run(agent, spec, env)

            assert result.converged is True, (
                f"TreeSearch did not converge. best_pass_rate={result.best_pass_rate}, "
                f"history={[(e.passed, e.total, e.pass_rate) for e in result.history]}"
            )
            assert result.best_pass_rate == 1.0


