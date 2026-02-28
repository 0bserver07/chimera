"""GLM-5 integration tests for MajorityVoting strategy.

Configure via:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token-here"
    export ANTHROPIC_MODEL="glm-5"

Skipped when no credentials are set.
"""
from __future__ import annotations

import os
import re

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.providers.anthropic import AnthropicProvider
from chimera.training.spec import Spec
from chimera.training.strategies.majority_voting import MajorityVoting

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


class TestMajorityVotingGLM5:
    def test_reaches_consensus_on_arithmetic(self, glm5_provider):
        """GLM-5 should agree with itself on simple arithmetic."""
        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=2),
        )
        spec = Spec.from_string(
            "What is 17 * 23? Compute the exact value. "
            "State your final answer as: ANSWER: <integer>"
        )
        strategy = MajorityVoting(n_samples=3, min_agreement=2)
        result = strategy.run(agent, spec, env=None)

        assert result.converged is True, (
            f"Expected consensus. outputs={[h.agent_output[:60] for h in result.history]}"
        )
        # 17 * 23 = 391
        assert any("391" in h.agent_output for h in result.history)

    def test_iterations_tracked(self, glm5_provider):
        """Each sample should be tracked as an iteration."""
        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=2),
        )
        spec = Spec.from_string("What is 6 * 7? Answer as: ANSWER: <integer>")
        strategy = MajorityVoting(n_samples=2, min_agreement=2)
        result = strategy.run(agent, spec, env=None)

        assert result.iterations >= 2
        assert len(result.history) >= 2

    def test_custom_extract_fn(self, glm5_provider):
        """A custom extract_fn should be used instead of the AIMO default."""
        def extract_boxed(text: str) -> int | None:
            m = re.search(r"ANSWER:\s*(\d+)", text)
            return int(m.group(1)) if m else None

        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=2),
        )
        spec = Spec.from_string("What is 8 + 9? Answer as: ANSWER: <integer>")
        strategy = MajorityVoting(
            n_samples=2,
            min_agreement=2,
            extract_fn=extract_boxed,
        )
        result = strategy.run(agent, spec, env=None)

        assert result.iterations >= 2
