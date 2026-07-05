"""Registry + resolve() plumbing tests for the agent × benchmark matrix.

No LLM, no network. Exercises :class:`AgentSpec` (de)serialisation, JSON
override merging, and the in-process :func:`resolve` path with a *fake* factory.
The external kinds (acp / cli-template / native-harness) are only asserted to
fail loudly when their runner module is absent — those runners land in a later
phase.
"""

from __future__ import annotations

import json
import sys

import pytest

from chimera.eval.runners import registry as reg
from chimera.eval.runners.in_process import InProcessRunner
from chimera.eval.runners.registry import (
    AgentSpec,
    default_agent_specs,
    load_registry,
    resolve,
)
from chimera.types import AgentResult


class _FakeAgent:
    """A tiny agent satisfying the Harness ``run(prompt, env)`` contract."""

    def run(self, prompt: str, env: object = None) -> AgentResult:
        return AgentResult(
            output="fake-ok",
            steps=1,
            tool_calls_total=2,
            cost=0.0,
            success=True,
        )


def _fake_factory(provider: object = None) -> _FakeAgent:
    """An ``agent_factory(provider) -> agent`` returning the fake agent."""
    return _FakeAgent()


def test_agent_spec_round_trip_in_process() -> None:
    spec = AgentSpec(
        id="react",
        kind="in-process",
        factory="chimera.core.loop:ReAct",
        model="glm-5",
        options={"max_steps": 40},
    )
    restored = AgentSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_agent_spec_round_trip_external_is_json_safe() -> None:
    spec = AgentSpec(
        id="codex",
        kind="cli-template",
        cmd="codex exec --prompt-file {prompt_file} --cd {repo}",
        sandbox="docker",
    )
    payload = spec.to_dict()
    # to_dict must be pure-JSON round-trippable and omit unset fields.
    assert json.loads(json.dumps(payload)) == payload
    assert "factory" not in payload
    assert AgentSpec.from_dict(payload) == spec


def test_default_roster_is_representative_in_process() -> None:
    specs = {s.id: s for s in default_agent_specs()}
    # Mixes loop postures and assembly presets, all in-process. (full-tools /
    # action-first are the codex / kimi presets under loop-descriptive ids.)
    for expected in ("react", "plan-execute", "reflexion", "tree-of-thought", "full-tools", "action-first"):
        assert expected in specs
    assert all(s.kind == "in-process" for s in specs.values())
    assert all(s.factory for s in specs.values())


def test_load_registry_merges_and_overrides(tmp_path) -> None:
    override = [
        # Override a built-in id...
        {"id": "react", "kind": "in-process", "factory": "custom.mod:make"},
        # ...and add a brand-new external entry.
        {"id": "opencode", "kind": "acp", "command": ["opencode", "acp"], "sandbox": "docker"},
    ]
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(override), encoding="utf-8")

    registry = load_registry([str(path)])

    assert registry["react"].factory == "custom.mod:make"  # overridden
    assert registry["opencode"].command == ["opencode", "acp"]  # added
    assert "plan-execute" in registry  # untouched built-in preserved


def test_load_registry_last_file_wins(tmp_path) -> None:
    first = tmp_path / "a.json"
    first.write_text(json.dumps([{"id": "x", "kind": "in-process", "factory": "a:a"}]), "utf-8")
    second = tmp_path / "b.json"
    second.write_text(json.dumps([{"id": "x", "kind": "in-process", "factory": "b:b"}]), "utf-8")

    registry = load_registry([str(first), str(second)])
    assert registry["x"].factory == "b:b"


def test_load_registry_skips_missing_files(tmp_path) -> None:
    registry = load_registry([str(tmp_path / "does-not-exist.json")])
    # Built-ins remain; a missing file is skipped without error.
    assert "react" in registry


def test_resolve_in_process_with_fake_factory(monkeypatch) -> None:
    # Inject the fake factory onto the (importable) registry module so the
    # "module:callable" reference resolves to it.
    monkeypatch.setattr(reg, "_test_fake_factory", _fake_factory, raising=False)
    spec = AgentSpec(
        id="fake",
        kind="in-process",
        factory="chimera.eval.runners.registry:_test_fake_factory",
    )

    runner = resolve(spec)

    assert isinstance(runner, InProcessRunner)
    assert runner.id == "fake"
    result = runner.run({"id": "t1", "prompt": "hello"})
    assert result.answer == "fake-ok"
    assert result.tool_calls == 2
    assert result.status == "completed"


def test_resolve_in_process_requires_factory() -> None:
    with pytest.raises(ValueError, match="factory"):
        resolve(AgentSpec(id="bad", kind="in-process"))


def test_resolve_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown agent kind"):
        resolve(AgentSpec(id="bad", kind="totally-bogus"))


def test_resolve_external_runner_absent_raises_clearly(monkeypatch) -> None:
    # Force the lazy import to fail deterministically regardless of whether the
    # (parallel-built) runner module exists yet: a None entry in sys.modules
    # makes ``import chimera.eval.runners.acp`` raise ImportError.
    monkeypatch.setitem(sys.modules, "chimera.eval.runners.acp", None)
    with pytest.raises(RuntimeError, match="ACP runner"):
        resolve(AgentSpec(id="opencode", kind="acp", command=["opencode", "acp"]))


def test_resolve_builds_external_runners_from_real_constructors() -> None:
    # The acp / cli-template / native-harness runner modules exist; resolve()
    # must construct them from the spec fields with no kwargs mismatch. Construct
    # only (never .run()), so nothing is spawned. Skip if a sibling module is
    # unavailable (they are built in parallel).
    pytest.importorskip("chimera.eval.runners.acp")
    pytest.importorskip("chimera.eval.runners.cli_template")
    pytest.importorskip("chimera.eval.runners.native_harness")
    from chimera.eval.runners.base import AgentRunner

    acp = resolve(AgentSpec(id="opencode", kind="acp", command=["opencode", "acp"]))
    assert isinstance(acp, AgentRunner) and acp.id == "opencode"
    assert type(acp).__name__ == "ACPRunner"

    cli = resolve(
        AgentSpec(id="codex", kind="cli-template", cmd="codex exec --prompt-file {prompt_file}")
    )
    assert isinstance(cli, AgentRunner) and cli.id == "codex"
    assert type(cli).__name__ == "CliTemplateRunner"

    nat = resolve(
        AgentSpec(
            id="mini-swe",
            kind="native-harness",
            harness_cmd="python -m minisweagent.run --output {out_dir}",
            predictions_glob="{out_dir}/preds.jsonl",
        )
    )
    assert isinstance(nat, AgentRunner) and nat.id == "mini-swe"
    assert type(nat).__name__ == "NativeHarnessRunner"


def test_resolve_external_missing_required_field_raises() -> None:
    with pytest.raises(ValueError, match="cmd"):
        resolve(AgentSpec(id="codex", kind="cli-template"))
    with pytest.raises(ValueError, match="harness_cmd"):
        resolve(AgentSpec(id="x", kind="native-harness", predictions_glob="p"))
