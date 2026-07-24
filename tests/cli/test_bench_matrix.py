"""Plumbing tests for the bench-matrix CLI (no LLM / provider required)."""

from __future__ import annotations

import argparse

import pytest

from chimera.cli.bench_matrix import (
    _sandbox_env_factory,
    add_bench_matrix_parser,
    missing_sandbox_credentials,
    run_bench_matrix,
)
from chimera.eval.runners import CliTemplateRunner
from chimera.eval.runners.registry import AgentSpec, resolve


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_bench_matrix_parser(subparsers)
    return parser.parse_args(argv)


def test_parser_registers_and_parses() -> None:
    ns = _parse(
        ["bench-matrix", "--agents", "react,codex", "--benchmarks", "human-eval,mbpp"]
    )
    assert ns.command == "bench-matrix"
    assert ns.agents == "react,codex"
    assert ns.benchmarks == "human-eval,mbpp"
    assert ns.model == "glm-5"  # default


def test_unknown_agent_fails_fast_without_provider(capsys) -> None:
    ns = _parse(["bench-matrix", "--agents", "no-such-agent", "--benchmarks", "human-eval"])
    rc = run_bench_matrix(ns)
    assert rc == 1
    assert "Unknown agent" in capsys.readouterr().err


def test_unknown_benchmark_fails_fast_without_provider(capsys) -> None:
    ns = _parse(["bench-matrix", "--agents", "react", "--benchmarks", "no-such-bench"])
    rc = run_bench_matrix(ns)
    assert rc == 1
    assert "Unknown benchmark" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Managed-sandbox --env selection (E2B / Daytona), mirroring --env modal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_kind", "env_var"),
    [("e2b", "E2B_API_KEY"), ("daytona", "DAYTONA_API_KEY")],
)
def test_sandbox_env_is_selectable(env_kind: str, env_var: str) -> None:
    ns = _parse(
        ["bench-matrix", "--benchmarks", "human-eval", "--env", env_kind,
         "--sandbox-image", "img:1"]
    )
    assert ns.env_kind == env_kind and ns.sandbox_image == "img:1"


@pytest.mark.parametrize(
    ("env_kind", "env_var"),
    [("e2b", "E2B_API_KEY"), ("daytona", "DAYTONA_API_KEY")],
)
def test_missing_credentials_detected(
    env_kind: str, env_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(env_var, raising=False)
    assert missing_sandbox_credentials(env_kind) == env_var
    monkeypatch.setenv(env_var, "present")
    assert missing_sandbox_credentials(env_kind) is None


@pytest.mark.parametrize(
    ("env_kind", "env_var"),
    [("e2b", "E2B_API_KEY"), ("daytona", "DAYTONA_API_KEY")],
)
def test_uncredentialed_run_exits_2_instead_of_running_locally(
    env_kind: str, env_var: str, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The whole point of the gate — a local result must never masquerade
    as a cloud result."""
    monkeypatch.delenv(env_var, raising=False)
    ns = _parse(
        ["bench-matrix", "--agents", "react", "--benchmarks", "human-eval",
         "--env", env_kind]
    )
    assert run_bench_matrix(ns) == 2
    err = capsys.readouterr().err
    assert env_var in err and env_kind in err


def test_sandbox_factory_constructs_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness calls setup() per task; provisioning early would leak."""
    seen: list[tuple[str, dict[str, object]]] = []

    def _fake_create(provider: str, **opts: object) -> object:
        seen.append((provider, opts))
        return object()

    monkeypatch.setattr("chimera.env.factory.create_environment", _fake_create)
    _sandbox_env_factory("e2b", "my-template")()
    _sandbox_env_factory("daytona", "python:3.11-slim")()
    _sandbox_env_factory("daytona", None)()
    assert seen == [
        ("e2b", {"template": "my-template"}),
        ("daytona", {"image": "python:3.11-slim"}),
        ("daytona", {}),
    ]


def test_resolve_constructs_real_external_runner() -> None:
    # Closes the coverage gap flagged during wave 1: resolve() actually builds an
    # external runner now that the runner modules are integrated.
    spec = AgentSpec(id="local-cli", kind="cli-template", cmd="echo {prompt}")
    runner = resolve(spec)
    assert isinstance(runner, CliTemplateRunner)
    assert runner.id == "local-cli"
