"""Tests for SynthesisTuner hyperparameter search (Feature 4)."""

from __future__ import annotations

from unittest.mock import MagicMock

from chimera.training.strategies.base import EpochResult, SynthesisResult
from chimera.training.tuner import SearchSpace, SynthesisTuner


# ---------------------------------------------------------------------------
# SearchSpace tests
# ---------------------------------------------------------------------------


def test_search_space_combinations():
    """2x2 grid produces 4 configs."""
    space = SearchSpace()
    space.choice("a", [1, 2])
    space.choice("b", ["x", "y"])
    configs = space.configurations()
    assert len(configs) == 4
    assert {"a": 1, "b": "x"} in configs
    assert {"a": 1, "b": "y"} in configs
    assert {"a": 2, "b": "x"} in configs
    assert {"a": 2, "b": "y"} in configs


def test_search_space_single():
    """Single parameter with 3 values produces 3 configs."""
    space = SearchSpace()
    space.choice("model", ["a", "b", "c"])
    assert len(space.configurations()) == 3


def test_search_space_empty():
    """Empty search space produces a single empty config."""
    space = SearchSpace()
    assert space.configurations() == [{}]


# ---------------------------------------------------------------------------
# Helpers for tuner tests
# ---------------------------------------------------------------------------


def _make_mock_env():
    """Create a mock environment with setup/cleanup methods."""
    env = MagicMock()
    env.setup.return_value = None
    env.cleanup.return_value = None
    return env


def _make_synthesis_result(pass_rate: float, cost: float, iterations: int = 1) -> SynthesisResult:
    """Create a SynthesisResult with the given metrics."""
    return SynthesisResult(
        converged=pass_rate == 1.0,
        iterations=iterations,
        total_cost=cost,
        best_pass_rate=pass_rate,
        history=[
            EpochResult(
                epoch=1,
                pass_rate=pass_rate,
                passed=int(pass_rate * 10),
                total=10,
                agent_output="ok",
                cost=cost,
            )
        ],
    )


def _make_tuner_with_results(
    results: dict[str, SynthesisResult],
) -> SynthesisTuner:
    """Create a SynthesisTuner where each config key maps to a predefined result.

    Args:
        results: Mapping from a string key (config["key"]) to the
            SynthesisResult that the mock trainer should return.
    """
    from chimera.training.spec import Spec

    spec = Spec.from_string("test spec")

    def agent_factory(config):
        agent = MagicMock()
        # Store the key so the mock strategy can look it up
        agent._tuner_key = config.get("key", "default")
        return agent

    # Patch Trainer.synthesize to return the pre-configured result
    import chimera.training.trainer as trainer_mod

    original_trainer_init = trainer_mod.Trainer.__init__
    original_trainer_synthesize = trainer_mod.Trainer.synthesize

    def patched_init(self, spec, agent, env, **kwargs):
        original_trainer_init(self, spec=spec, agent=agent, env=env, **kwargs)

    def patched_synthesize(self, strategy=None, callbacks=None):
        key = self.agent._tuner_key
        return results.get(key, _make_synthesis_result(0.0, 0.0))

    trainer_mod.Trainer.__init__ = patched_init
    trainer_mod.Trainer.synthesize = patched_synthesize

    tuner = SynthesisTuner(
        spec=spec,
        env_factory=_make_mock_env,
        agent_factory=agent_factory,
    )

    # Restore originals after creating tuner (search will still use patched)
    # We attach cleanup so tests can restore after search()
    tuner._restore = lambda: (
        setattr(trainer_mod.Trainer, "__init__", original_trainer_init),
        setattr(trainer_mod.Trainer, "synthesize", original_trainer_synthesize),
    )
    return tuner


# ---------------------------------------------------------------------------
# SynthesisTuner tests
# ---------------------------------------------------------------------------


def test_tuner_picks_best():
    """Tuner selects the configuration with the highest pass_rate."""
    results = {
        "a": _make_synthesis_result(pass_rate=0.6, cost=0.10),
        "b": _make_synthesis_result(pass_rate=0.9, cost=0.20),
        "c": _make_synthesis_result(pass_rate=0.3, cost=0.05),
    }
    tuner = _make_tuner_with_results(results)
    try:
        space = SearchSpace().choice("key", ["a", "b", "c"])
        result = tuner.search(space, metric="pass_rate")

        assert result.best_config == {"key": "b"}
        assert result.best_score == 0.9
        assert len(result.trials) == 3
    finally:
        tuner._restore()


def test_tuner_max_trials():
    """max_trials limits the number of configurations tried."""
    results = {
        str(i): _make_synthesis_result(pass_rate=i * 0.1, cost=0.01)
        for i in range(5)
    }
    tuner = _make_tuner_with_results(results)
    try:
        space = SearchSpace().choice("key", ["0", "1", "2", "3", "4"])
        result = tuner.search(space, max_trials=3)

        assert len(result.trials) == 3
        # Only the first 3 configs should have been tried
        tried_keys = [t.config["key"] for t in result.trials]
        assert tried_keys == ["0", "1", "2"]
    finally:
        tuner._restore()


def test_tuner_total_cost():
    """total_cost sums all trial costs."""
    results = {
        "a": _make_synthesis_result(pass_rate=0.5, cost=0.10),
        "b": _make_synthesis_result(pass_rate=0.7, cost=0.25),
        "c": _make_synthesis_result(pass_rate=0.3, cost=0.15),
    }
    tuner = _make_tuner_with_results(results)
    try:
        space = SearchSpace().choice("key", ["a", "b", "c"])
        result = tuner.search(space)

        assert abs(result.total_cost - 0.50) < 1e-9
    finally:
        tuner._restore()


def test_tuner_custom_metric():
    """Can sort by cost instead of pass_rate (lower cost = higher score)."""
    results = {
        "cheap": _make_synthesis_result(pass_rate=0.5, cost=0.01),
        "expensive": _make_synthesis_result(pass_rate=0.9, cost=1.00),
    }
    tuner = _make_tuner_with_results(results)
    try:
        space = SearchSpace().choice("key", ["cheap", "expensive"])
        result = tuner.search(space, metric="cost")

        # "cheap" has cost 0.01 -> score -0.01 (higher than -1.00)
        assert result.best_config == {"key": "cheap"}
        assert result.best_score == -0.01
    finally:
        tuner._restore()


def test_tuner_handles_failed_trial():
    """A trial that raises an exception is recorded with score 0."""
    from chimera.training.spec import Spec
    import chimera.training.trainer as trainer_mod

    spec = Spec.from_string("test spec")

    call_count = 0

    original_init = trainer_mod.Trainer.__init__
    original_synthesize = trainer_mod.Trainer.synthesize

    def patched_init(self, spec, agent, env, **kwargs):
        original_init(self, spec=spec, agent=agent, env=env, **kwargs)

    def patched_synthesize(self, strategy=None, callbacks=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("provider unavailable")
        return _make_synthesis_result(pass_rate=0.8, cost=0.10)

    trainer_mod.Trainer.__init__ = patched_init
    trainer_mod.Trainer.synthesize = patched_synthesize

    try:
        tuner = SynthesisTuner(
            spec=spec,
            env_factory=_make_mock_env,
            agent_factory=lambda config: MagicMock(),
        )
        space = SearchSpace().choice("key", ["fail", "succeed"])
        result = tuner.search(space)

        assert len(result.trials) == 2
        # First trial failed
        assert result.trials[0].score == 0.0
        assert result.trials[0].synthesis_result.failure_reason == "provider unavailable"
        # Second trial succeeded
        assert result.trials[1].score == 0.8
        # Best should be the successful one
        assert result.best_config == {"key": "succeed"}
    finally:
        trainer_mod.Trainer.__init__ = original_init
        trainer_mod.Trainer.synthesize = original_synthesize
