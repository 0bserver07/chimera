from __future__ import annotations

from chimera.training.strategies.ensemble import EnsembleStrategy
from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult
from chimera.training.spec import Spec
from chimera.types import AgentResult, TestResult


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

class VaryingEnv:
    """Environment that returns different test results on each call.

    ``pass_rates`` is a list of (passed, failed) tuples, one per call
    to ``run_tests()``.
    """

    def __init__(self, pass_rates: list[tuple[int, int]]) -> None:
        self._pass_rates = list(pass_rates)
        self._call = 0
        self._checkpoints: dict[str, int] = {}
        self._cp_counter = 0

    def run_tests(self) -> TestResult:
        idx = min(self._call, len(self._pass_rates) - 1)
        passed, failed = self._pass_rates[idx]
        self._call += 1
        return TestResult(passed=passed, failed=failed, errors=0, output="ok")

    def checkpoint(self) -> str:
        self._cp_counter += 1
        cp_id = f"cp_{self._cp_counter}"
        self._checkpoints[cp_id] = self._call
        return cp_id

    def restore(self, cp_id: str) -> None:
        pass


class MockAgent:
    def __init__(self) -> None:
        self.call_count = 0

    def run(self, task: str, env: object) -> AgentResult:
        self.call_count += 1
        return AgentResult(output=f"attempt-{self.call_count}", steps=1, tool_calls_total=0, cost=0.1, success=True)


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

class TestEnsembleStrategy:
    def test_picks_best_result(self):
        """Should select the attempt with the highest pass rate."""
        # 3 attempts: 2/5, 4/5, 3/5 -> best is attempt 2 (4/5)
        env = VaryingEnv(pass_rates=[(2, 3), (4, 1), (3, 2)])
        agent = MockAgent()
        spec = Spec.from_string("Build something")
        strategy = EnsembleStrategy(attempts=3)

        result = strategy.run(agent, spec, env)

        assert agent.call_count == 3
        assert result.best_pass_rate == 4 / 5
        assert result.iterations == 3
        assert len(result.history) == 3
        assert not result.converged  # 4/5 != 1.0

    def test_single_attempt(self):
        """Works with attempts=1."""
        env = VaryingEnv(pass_rates=[(5, 0)])
        agent = MockAgent()
        spec = Spec.from_string("Build it")
        strategy = EnsembleStrategy(attempts=1)

        result = strategy.run(agent, spec, env)

        assert agent.call_count == 1
        assert result.best_pass_rate == 1.0
        assert result.converged
        assert result.iterations == 1
        assert len(result.history) == 1

    def test_callbacks_called(self):
        """Callbacks should fire for start, each attempt, and end."""
        env = VaryingEnv(pass_rates=[(3, 2), (5, 0)])
        agent = MockAgent()
        spec = Spec.from_string("Build it")
        cb = TrackingCallback()
        strategy = EnsembleStrategy(attempts=2)

        result = strategy.run(agent, spec, env, callbacks=[cb])

        assert cb.started
        assert cb.ended
        assert len(cb.epochs) == 2
        assert cb.epochs[0].epoch == 1
        assert cb.epochs[1].epoch == 2
        assert cb.final_result is not None
        assert cb.final_result.best_pass_rate == result.best_pass_rate
