"""External-agent registry example tests for the agent × benchmark matrix.

No LLM, no network, no subprocess: these exercise the shipped
``docs/examples/agent-registry.example.json`` roster purely through
:func:`load_registry` (merge/override) and :func:`resolve` (construct only —
never ``.run()``, so no external CLI/harness/ACP server is spawned).

The example file demonstrates all three external kinds: ``acp`` (opencode),
``cli-template`` (codex, aider), and ``native-harness`` (mini-swe-agent,
agentless). Every field used there must be one :meth:`AgentSpec.from_dict`
accepts, and every spec must resolve to the runner class its ``kind`` maps to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import chimera.eval.runners.registry as reg_mod
from chimera.eval.runners import (
    AgentSpec,
    CliTemplateRunner,
    NativeHarnessRunner,
    default_agent_specs,
    load_registry,
    resolve,
)

#: The example registry ships next to the registry module inside the package.
# The example lives in docs/, not inside the chimera package — external tool
# names are docs-only interop pointers, never shipped package data.
EXAMPLE_JSON = (
    Path(reg_mod.__file__).resolve().parents[3]
    / "docs"
    / "examples"
    / "agent-registry.example.json"
)

#: The five external agents the example file is required to enumerate.
EXTERNAL_IDS = ("opencode", "codex-cli", "aider-cli", "mini-swe-agent", "agentless")


def test_example_file_exists_and_is_valid_json() -> None:
    """The shipped example must be present and parse as a JSON list of dicts."""
    assert EXAMPLE_JSON.is_file(), f"missing example registry at {EXAMPLE_JSON}"
    entries = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and entries
    assert all(isinstance(entry, dict) for entry in entries)


def test_example_entries_use_only_known_agentspec_fields() -> None:
    """Every key in every entry must be a field ``AgentSpec.from_dict`` accepts.

    ``from_dict`` silently ignores unknown keys, so a typo'd field would pass
    unnoticed; this guards the JSON against drift from the dataclass contract.
    """
    known = set(AgentSpec.__dataclass_fields__)  # id, kind, factory, command, ...
    entries = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    for entry in entries:
        unknown = set(entry) - known
        assert not unknown, f"{entry.get('id')!r} has unknown fields: {sorted(unknown)}"


def test_load_registry_adds_all_five_on_top_of_builtins() -> None:
    """Loading the example merges its five ids over the built-in roster."""
    registry = load_registry(paths=[str(EXAMPLE_JSON)])

    # All five external example ids are present...
    for ext_id in EXTERNAL_IDS:
        assert ext_id in registry, f"{ext_id!r} missing from merged registry"

    # ...on top of the built-in in-process roster — all preserved, because the
    # external examples use distinct ids (codex-cli, not codex), so nothing is
    # clobbered.
    for builtin in ("react", "plan-execute", "reflexion", "tree-of-thought", "codex", "kimi"):
        assert builtin in registry, f"built-in {builtin!r} was dropped"

    # codex (internal replica, in-process) and codex-cli (real CLI, cli-template)
    # coexist — this is precisely what enables the replica-vs-real fidelity run.
    assert registry["codex"].kind == "in-process"
    assert registry["codex-cli"].kind == "cli-template"

    # Collision guard: NO external example id may shadow a built-in — every
    # replica style must survive the merge so replica-vs-real pairs stay intact
    # (aider vs aider-cli, codex vs codex-cli, ...).
    builtin_ids = {spec.id for spec in default_agent_specs()}
    external_ids = {entry["id"] for entry in json.loads(EXAMPLE_JSON.read_text())}
    clobbered = builtin_ids & external_ids
    assert not clobbered, f"external example ids shadow built-ins: {sorted(clobbered)}"
    assert registry["aider"].kind == "in-process"
    assert registry["aider-cli"].kind == "cli-template"


def test_resolve_cli_template_specs_build_cli_template_runner() -> None:
    """The two cli-template example specs resolve to ``CliTemplateRunner``."""
    registry = load_registry(paths=[str(EXAMPLE_JSON)])

    codex = resolve(registry["codex-cli"])
    assert isinstance(codex, CliTemplateRunner)
    assert codex.id == "codex-cli"
    assert codex.cmd == "codex exec --prompt-file {prompt_file} --cd {repo}"
    # ``options`` forwards to the constructor: patch_from was set in the JSON.
    assert codex.patch_from == "git-diff"

    aider = resolve(registry["aider-cli"])
    assert isinstance(aider, CliTemplateRunner)
    assert aider.id == "aider-cli"
    assert aider.cmd == "aider --yes --message-file {prompt_file} {repo}"


def test_resolve_native_harness_specs_build_native_harness_runner() -> None:
    """The two native-harness example specs resolve to ``NativeHarnessRunner``."""
    registry = load_registry(paths=[str(EXAMPLE_JSON)])

    mini = resolve(registry["mini-swe-agent"])
    assert isinstance(mini, NativeHarnessRunner)
    assert mini.id == "mini-swe-agent"
    assert mini.harness_cmd == "python -m minisweagent.run --subset {subset} --output {out_dir}"
    assert mini.predictions_glob == "{out_dir}/preds.jsonl"

    agentless = resolve(registry["agentless"])
    assert isinstance(agentless, NativeHarnessRunner)
    assert agentless.id == "agentless"
    assert agentless.harness_cmd == "python agentless/run.py --output {out_dir}"
    assert agentless.predictions_glob == "{out_dir}/all_preds.jsonl"


def test_acp_spec_kind_and_command_round_trip() -> None:
    """The acp example spec is well-formed: kind + command survive from_dict."""
    registry = load_registry(paths=[str(EXAMPLE_JSON)])
    opencode = registry["opencode"]

    assert opencode.kind == "acp"
    assert opencode.command == ["opencode", "acp"]
    # from_dict(to_dict(...)) is a faithful round-trip for the acp entry.
    assert AgentSpec.from_dict(opencode.to_dict()) == opencode


def test_resolve_acp_spec_builds_acp_runner() -> None:
    """The acp spec resolves to an ``ACPRunner`` (construct only, no spawn)."""
    pytest.importorskip("chimera.eval.runners.acp")
    from chimera.eval.runners import ACPRunner

    registry = load_registry(paths=[str(EXAMPLE_JSON)])
    runner = resolve(registry["opencode"])

    assert isinstance(runner, ACPRunner)
    assert runner.id == "opencode"
    # The command flowed into the ACPSessionConfig resolve() built.
    assert runner.config.command == ["opencode", "acp"]


def test_resolve_cli_template_missing_cmd_raises_clear_error() -> None:
    """A cli-template spec with no ``cmd`` fails loudly via resolve()."""
    # Model the realistic path: a registry entry (dict) → from_dict → resolve.
    spec = AgentSpec.from_dict({"id": "codex-nocmd", "kind": "cli-template", "sandbox": "docker"})
    with pytest.raises(ValueError, match="cmd"):
        resolve(spec)


def test_resolve_native_harness_missing_field_raises_clear_error() -> None:
    """A native-harness spec missing ``predictions_glob`` fails loudly."""
    spec = AgentSpec.from_dict(
        {"id": "mini-nopreds", "kind": "native-harness", "harness_cmd": "python x.py"}
    )
    with pytest.raises(ValueError, match="harness_cmd.*predictions_glob|predictions_glob"):
        resolve(spec)
