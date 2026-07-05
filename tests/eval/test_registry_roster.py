"""Roster tests for the expanded built-in agent registry.

No LLM, no network. Asserts :func:`default_agent_specs` enumerates the full
built-in roster (the six original ids plus the preset/style additions) and that
:func:`resolve` lazily wraps each spec in an :class:`InProcessRunner` with the
correct ``id``. ``resolve`` is *lazy* — it imports and wraps the factory but
never calls it — so every assertion here is offline: no provider is supplied and
no factory is ever invoked against a real model.
"""

from __future__ import annotations

import pytest

from chimera.eval.runners.in_process import InProcessRunner
from chimera.eval.runners.registry import default_agent_specs, resolve

#: The six ids that predate this roster expansion (must never regress).
_ORIGINAL_IDS = ("react", "plan-execute", "reflexion", "tree-of-thought", "codex", "kimi")

#: The ids added by the roster expansion: four assembly presets (including the
#: ``chimera code`` flagship ``coding-agent``) + three replica styles.
_ADDED_IDS = (
    "coding-agent",
    "minimal",
    "explore",
    "swebench",
    "swe-agent",
    "aider",
    "cline",
)

#: The full expected built-in roster (13 ids).
_EXPECTED_IDS = _ORIGINAL_IDS + _ADDED_IDS


def test_default_roster_is_exactly_the_expected_ids() -> None:
    ids = [spec.id for spec in default_agent_specs()]
    # No duplicate ids leaked into the roster.
    assert len(ids) == len(set(ids)), f"duplicate ids in roster: {ids}"
    # The roster is precisely the 13 expected ids — nothing missing, nothing extra.
    assert set(ids) == set(_EXPECTED_IDS)
    assert len(ids) == 13


def test_original_six_ids_preserved() -> None:
    # The expansion is additive: every pre-existing id must still be present.
    ids = {spec.id for spec in default_agent_specs()}
    for expected in _ORIGINAL_IDS:
        assert expected in ids, f"regressed original roster id {expected!r}"


def test_added_ids_present() -> None:
    ids = {spec.id for spec in default_agent_specs()}
    for expected in _ADDED_IDS:
        assert expected in ids, f"missing new roster id {expected!r}"


def test_all_specs_are_in_process_with_a_factory() -> None:
    for spec in default_agent_specs():
        assert spec.kind == "in-process", f"{spec.id!r} is not in-process"
        assert spec.factory, f"{spec.id!r} has no factory reference"


@pytest.mark.parametrize("spec_id", _ADDED_IDS)
def test_resolve_new_spec_returns_inprocess_runner(spec_id: str) -> None:
    # Each NEW spec must resolve (offline, lazily) into an InProcessRunner whose
    # id matches. resolve() imports+wraps the factory but never calls it, so no
    # provider and no LLM are involved.
    spec = next(s for s in default_agent_specs() if s.id == spec_id)
    runner = resolve(spec)
    assert isinstance(runner, InProcessRunner)
    assert runner.id == spec_id


@pytest.mark.parametrize("spec_id", _EXPECTED_IDS)
def test_resolve_every_roster_spec_is_offline_lazy(spec_id: str) -> None:
    # Stronger guard over the WHOLE roster: every built-in resolves into an
    # InProcessRunner without a provider and without invoking its factory.
    spec = next(s for s in default_agent_specs() if s.id == spec_id)
    runner = resolve(spec)
    assert isinstance(runner, InProcessRunner)
    assert runner.id == spec_id
