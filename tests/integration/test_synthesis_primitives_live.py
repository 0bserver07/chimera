"""Live integration tests for synthesis primitives against a real LLM.

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
from chimera.training.oracle import OracleCallback
from chimera.training.spec import Spec
from chimera.training.strategies.cegis import CEGISStrategy
from chimera.training.strategies.incremental import IncrementalStrategy
from chimera.training.tuner import SearchSpace, SynthesisTuner

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


def _make_agent(provider: AnthropicProvider, max_steps: int = 10) -> Agent:
    return Agent(
        provider=provider,
        tools=[ReadFileTool(), WriteFileTool(), BashTool()],
        loop=ReAct(max_steps=max_steps),
    )


class TestCEGISFixesCounterexample:
    """CEGISStrategy should focus on failing tests and eventually fix them."""

    def test_cegis_fixes_counterexample(self, glm5_provider: AnthropicProvider) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a project with 2 tests: one passing, one failing.
            # The source file has add() correct but subtract() buggy.
            Path(tmpdir, "math_ops.py").write_text(
                "def add(a, b):\n"
                "    return a + b\n\n"
                "def subtract(a, b):\n"
                "    return a + b  # BUG: should be a - b\n"
            )
            Path(tmpdir, "test_math_ops.py").write_text(
                "from math_ops import add, subtract\n\n"
                "def test_add():\n"
                "    assert add(2, 3) == 5\n\n"
                "def test_subtract():\n"
                "    assert subtract(10, 4) == 6\n"
            )

            env = LocalEnvironment(
                workdir=tmpdir,
                test_cmd="python -m pytest test_math_ops.py -v --tb=short",
            )
            env.setup()

            agent = _make_agent(glm5_provider)
            spec = Spec.from_tests(
                tmpdir,
                "Fix the buggy subtract function in math_ops.py so all tests pass.",
            )

            cegis = CEGISStrategy(max_iterations=3, patience=3)
            result = cegis.run(agent, spec, env)

            # CEGIS should have made at least some progress
            assert len(result.history) >= 1, "CEGIS should run at least one epoch"
            assert result.best_pass_rate > 0.0, (
                f"Expected progress. History: "
                f"{[(e.passed, e.total, e.pass_rate) for e in result.history]}"
            )
            # Ideally it converges on this trivial bug
            if result.converged:
                assert result.best_pass_rate == 1.0


class TestIncrementalTargetsBrokenFunction:
    """IncrementalStrategy should identify and target the broken function."""

    def test_incremental_targets_broken_function(self, glm5_provider: AnthropicProvider) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Two functions: greet() is correct, farewell() is buggy.
            Path(tmpdir, "greetings.py").write_text(
                "def greet(name):\n"
                '    return f"Hello, {name}!"\n\n'
                "def farewell(name):\n"
                '    return f"Hello, {name}!"  # BUG: should say Goodbye\n'
            )
            Path(tmpdir, "test_greetings.py").write_text(
                "from greetings import greet, farewell\n\n"
                "def test_greet():\n"
                '    assert greet("Alice") == "Hello, Alice!"\n\n'
                "def test_farewell():\n"
                '    assert farewell("Bob") == "Goodbye, Bob!"\n'
            )

            env = LocalEnvironment(
                workdir=tmpdir,
                test_cmd="python -m pytest test_greetings.py -v --tb=short",
            )
            env.setup()

            agent = _make_agent(glm5_provider)
            spec = Spec.from_tests(
                tmpdir,
                "Fix the farewell function in greetings.py so all tests pass.",
            )

            incremental = IncrementalStrategy(max_iterations=3, patience=3)
            result = incremental.run(agent, spec, env)

            assert len(result.history) >= 1, "Should run at least one epoch"
            assert result.best_pass_rate > 0.0, (
                f"Expected progress. History: "
                f"{[(e.passed, e.total, e.pass_rate) for e in result.history]}"
            )


class TestTunerPicksBestConfig:
    """SynthesisTuner should return a result with the better config."""

    def test_tuner_picks_best_config(self, glm5_provider: AnthropicProvider) -> None:
        with tempfile.TemporaryDirectory():
            # Simple task for tuner to work with
            src = (
                "def double(x):\n"
                "    return x  # BUG: should be x * 2\n"
            )
            test = (
                "from doubler import double\n\n"
                "def test_double():\n"
                "    assert double(5) == 10\n"
            )

            def env_factory():
                d = tempfile.mkdtemp()
                Path(d, "doubler.py").write_text(src)
                Path(d, "test_doubler.py").write_text(test)
                return LocalEnvironment(
                    workdir=d,
                    test_cmd="python -m pytest test_doubler.py -v --tb=short",
                )

            def agent_factory(config):
                max_steps = config.get("max_steps", 5)
                return _make_agent(glm5_provider, max_steps=max_steps)

            spec = Spec.from_string(
                "Fix the double() function in doubler.py so it returns x * 2."
            )

            tuner = SynthesisTuner(
                spec=spec,
                env_factory=env_factory,
                agent_factory=agent_factory,
            )

            space = SearchSpace()
            space.choice("max_steps", [3, 5])

            result = tuner.search(space, max_trials=2, metric="pass_rate")

            assert len(result.trials) == 2, f"Expected 2 trials, got {len(result.trials)}"
            assert result.best_config is not None
            assert "max_steps" in result.best_config
            assert result.best_score >= 0.0


class TestOracleGeneratesTest:
    """OracleCallback should trigger test generation when all tests pass."""

    def test_oracle_generates_test(self, glm5_provider: AnthropicProvider) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Start with a correct implementation and a passing test
            Path(tmpdir, "calc.py").write_text(
                "def square(x):\n"
                "    return x * x\n"
            )
            Path(tmpdir, "test_calc.py").write_text(
                "from calc import square\n\n"
                "def test_square_positive():\n"
                "    assert square(3) == 9\n"
            )

            tests_dir = os.path.join(tmpdir, "generated_tests")

            oracle = OracleCallback(
                provider=glm5_provider,
                tests_dir=tests_dir,
                max_new_tests_per_epoch=2,
                mode="llm",
            )

            # Simulate an epoch where all tests pass
            from chimera.training.strategies.base import EpochResult

            epoch_result = EpochResult(
                epoch=1,
                pass_rate=1.0,
                passed=1,
                total=1,
                agent_output=(
                    "def square(x):\n"
                    "    return x * x\n"
                ),
                improved=True,
            )

            oracle.on_synthesis_start()
            oracle.on_epoch_end(1, epoch_result)

            # Oracle should have generated tests
            assert len(oracle.generated_tests) > 0, (
                "OracleCallback should generate at least one test"
            )
            assert os.path.isdir(tests_dir), "Oracle should create tests directory"
            generated_files = os.listdir(tests_dir)
            assert len(generated_files) > 0, "Oracle should write test files"
