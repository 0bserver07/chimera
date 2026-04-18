"""GLM-5 integration tests for the Eval Harness.

Configure via:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token-here"
    export ANTHROPIC_MODEL="glm-5"

Skipped when no credentials are set.
"""
from __future__ import annotations

import json
import os

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.eval.benchmarks.aimo import AIMOBenchmark
from chimera.eval.harness import Harness
from chimera.providers.anthropic import AnthropicProvider

_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
_model = os.environ.get("ANTHROPIC_MODEL", "glm-5")

pytestmark = pytest.mark.skipif(
    not _api_key,
    reason="Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN to run integration tests",
)

EASY_PROBLEMS = [
    {"id": "p1", "problem": "What is 2 + 2?", "answer": 4},
    {"id": "p2", "problem": "What is 10 * 10?", "answer": 100},
    {"id": "p3", "problem": "What is 100 - 37?", "answer": 63},
]


@pytest.fixture(scope="module")
def glm5_provider() -> AnthropicProvider:
    return AnthropicProvider(
        model=_model,
        api_key=_api_key,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )


@pytest.fixture(scope="module")
def problems_file(tmp_path_factory) -> str:
    p = tmp_path_factory.mktemp("bench") / "problems.json"
    p.write_text(json.dumps(EASY_PROBLEMS))
    return str(p)


class TestEvalHarnessGLM5:
    def test_harness_runs_and_reports_results(self, glm5_provider, problems_file):
        """The harness should run all 3 tasks and return structured results."""
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=3),
        )
        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()

        assert result.benchmark == "aimo3"
        assert result.total == 3
        assert len(result.results) == 3
        # GLM-5 should solve at least 1 of 3 trivial arithmetic problems
        assert result.passed >= 1, (
            "Expected at least 1 pass. Results: "
            + str([(r.task_id, r.passed, r.output[:80]) for r in result.results])
        )

    def test_pass_rate_computation(self, glm5_provider, problems_file):
        """pass_rate should equal passed / total."""
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=3),
        )
        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()

        expected_rate = result.passed / result.total if result.total > 0 else 0.0
        assert result.pass_rate == pytest.approx(expected_rate)

    def test_task_ids_preserved(self, glm5_provider, problems_file):
        """TaskEvalResult should preserve the task IDs from the benchmark."""
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = Agent(
            provider=glm5_provider,
            tools=[],
            loop=ReAct(max_steps=3),
        )
        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()

        ids = [r.task_id for r in result.results]
        assert ids == ["p1", "p2", "p3"]
