"""T4.6 — bench-compare exposes all 8 loop types, each Agent-runnable."""

from __future__ import annotations

from chimera.cli.bench_compare import LOOP_TYPES, _build_factories
from chimera.providers.faux import FauxProvider


def test_roster_has_eight_loops() -> None:
    assert set(LOOP_TYPES) == {
        "react", "plan-execute", "reflexion", "tree-of-thought",
        "retry", "plan-act", "lint-feedback", "autonomous",
    }


def test_every_loop_builds_and_runs_on_faux() -> None:
    """Each registered loop constructs and completes a trivial task offline.

    The four newer loops reject max_steps= and take config-only; the factory
    must handle both. Running proves they satisfy the Agent loop interface,
    not merely that they import.
    """
    from chimera.core.loop_config import LoopConfig

    factories = _build_factories(list(LOOP_TYPES), max_steps=5)
    assert set(factories) == set(LOOP_TYPES)

    for name, factory in factories.items():
        provider = FauxProvider(script=[{"text": "done"}])
        agent = factory(provider, LoopConfig())
        result = agent.run("say hi", None)
        assert result.success, f"{name} did not complete"


def test_unknown_loop_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown agent loop"):
        _build_factories(["react", "no-such-loop"], max_steps=5)
