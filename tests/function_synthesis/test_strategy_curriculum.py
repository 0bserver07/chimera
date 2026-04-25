from __future__ import annotations

from chimera.training.strategies.curriculum import CurriculumStrategy
from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult
from chimera.training.architecture import Architecture, Layer
from chimera.training.spec import Spec
from chimera.types import AgentResult, TestResult


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

class MockAgent:
    """Tracks which prompts it receives so we can verify layer ordering."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str, env: object) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.1, success=True)


class MockEnv:
    """Environment that always returns passing tests."""

    def __init__(self, passed: int = 5, failed: int = 0) -> None:
        self._passed = passed
        self._failed = failed

    def run_tests(self) -> TestResult:
        return TestResult(passed=self._passed, failed=self._failed, errors=0, output="ok")

    def checkpoint(self) -> str:
        return "cp_1"

    def restore(self, cp_id: str) -> None:
        pass


class TrackingCallback(Callback):
    """Records all callback events."""

    def __init__(self) -> None:
        self.started = False
        self.ended = False
        self.epochs: list[EpochResult] = []
        self.final_result: SynthesisResult | None = None

    def on_synthesis_start(self) -> None:
        self.started = True

    def on_epoch_end(self, epoch: EpochResult) -> None:
        self.epochs.append(epoch)

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        self.ended = True
        self.final_result = result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCurriculumStrategy:
    def test_runs_layers_in_order(self):
        """Curriculum should process layers in topological order."""
        arch = Architecture([
            Layer("models"),
            Layer("storage", depends_on=["models"]),
            Layer("api", depends_on=["storage"]),
        ])
        agent = MockAgent()
        env = MockEnv()
        spec = Spec.from_string("Build a REST API")
        strategy = CurriculumStrategy(architecture=arch)

        result = strategy.run(agent, spec, env)

        # Agent should have been called 3 times (one per non-frozen layer)
        assert len(agent.prompts) == 3

        # Verify topological order in the prompts
        assert "models" in agent.prompts[0]
        assert "storage" in agent.prompts[1]
        assert "api" in agent.prompts[2]

        # Second prompt should note models is a dependency
        assert "models" in agent.prompts[1]  # mentioned as dependency
        # Third prompt should note storage is a dependency
        assert "storage" in agent.prompts[2]

        assert result.iterations == 3
        assert len(result.history) == 3

    def test_single_layer(self):
        """Works with a single layer."""
        arch = Architecture([Layer("core", description="Core logic")])
        agent = MockAgent()
        env = MockEnv()
        spec = Spec.from_string("Build core")
        strategy = CurriculumStrategy(architecture=arch)

        result = strategy.run(agent, spec, env)

        assert len(agent.prompts) == 1
        assert "core" in agent.prompts[0]
        assert result.iterations == 1
        assert len(result.history) == 1
        assert result.converged  # all tests pass

    def test_frozen_layers_skipped(self):
        """Frozen layers should not be synthesized."""
        arch = Architecture([
            Layer("config", frozen=True),
            Layer("models"),
            Layer("api", depends_on=["config", "models"]),
        ])
        agent = MockAgent()
        env = MockEnv()
        spec = Spec.from_string("Build with frozen config")
        strategy = CurriculumStrategy(architecture=arch)

        result = strategy.run(agent, spec, env)

        # Only 2 layers should be processed (config is frozen)
        assert len(agent.prompts) == 2
        assert result.iterations == 2

        # Verify frozen layer was not in any prompt as "Focus on layer"
        for prompt in agent.prompts:
            assert "Focus on layer: config" not in prompt

        # models comes first, then api
        assert "Focus on layer: models" in agent.prompts[0]
        assert "Focus on layer: api" in agent.prompts[1]

    def test_callbacks_called(self):
        """Synthesis callbacks should be invoked at each stage."""
        arch = Architecture([
            Layer("data"),
            Layer("logic", depends_on=["data"]),
        ])
        agent = MockAgent()
        env = MockEnv()
        spec = Spec.from_string("Build it")
        cb = TrackingCallback()
        strategy = CurriculumStrategy(architecture=arch)

        result = strategy.run(agent, spec, env, callbacks=[cb])

        assert cb.started
        assert cb.ended
        assert len(cb.epochs) == 2
        assert cb.epochs[0].epoch == 1
        assert cb.epochs[1].epoch == 2
        assert cb.final_result is not None
        assert cb.final_result.converged == result.converged
