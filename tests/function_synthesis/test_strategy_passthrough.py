from __future__ import annotations

from chimera.training.strategies.passthrough import Passthrough
from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult
from chimera.training.spec import Spec
from chimera.types import AgentResult, TestResult


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

class MockAgent:
    def __init__(self) -> None:
        self.call_count = 0

    def run(self, task: str, env: object) -> AgentResult:
        self.call_count += 1
        return AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.1, success=True)


class MockEnv:
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

class TestPassthrough:
    def test_single_shot(self):
        """Should run the agent exactly once and return a result."""
        agent = MockAgent()
        env = MockEnv(passed=3, failed=2)
        spec = Spec.from_string("Build something")
        strategy = Passthrough()

        result = strategy.run(agent, spec, env)

        assert agent.call_count == 1
        assert result.iterations == 1
        assert len(result.history) == 1
        assert result.history[0].epoch == 1
        assert result.history[0].passed == 3
        assert result.history[0].total == 5
        assert result.total_cost == 0.1

    def test_converged_if_all_pass(self):
        """converged=True when all tests pass."""
        agent = MockAgent()
        env = MockEnv(passed=5, failed=0)
        spec = Spec.from_string("Build it")
        strategy = Passthrough()

        result = strategy.run(agent, spec, env)

        assert result.converged is True
        assert result.best_pass_rate == 1.0
        assert result.history[0].pass_rate == 1.0

    def test_not_converged_if_fails(self):
        """converged=False when some tests fail."""
        agent = MockAgent()
        env = MockEnv(passed=3, failed=2)
        spec = Spec.from_string("Build it")
        strategy = Passthrough()

        result = strategy.run(agent, spec, env)

        assert result.converged is False
        assert result.best_pass_rate == 3 / 5
        assert result.history[0].pass_rate == 3 / 5

    def test_callbacks_called(self):
        """Synthesis callbacks should be invoked."""
        agent = MockAgent()
        env = MockEnv()
        spec = Spec.from_string("Build it")
        cb = TrackingCallback()
        strategy = Passthrough()

        result = strategy.run(agent, spec, env, callbacks=[cb])

        assert cb.started
        assert cb.ended
        assert len(cb.epochs) == 0  # Passthrough doesn't call on_epoch_end
        assert cb.final_result is not None
        assert cb.final_result.converged == result.converged
