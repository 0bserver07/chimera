"""Tests for ``chimera/otter/benchmarks.py``.

Covers:

* :func:`build_otter_agent_for_eval` — provider construction is patched
  so no LLM SDK is required to import / run the test.
* :func:`run_humaneval` — wires the existing HumanEval adapter through
  a mocked Harness; asserts the EvalResult round-trips and the dataset
  path defaults to the vendored ``data/humaneval.json``.
* :func:`run_tau_bench` — raises :class:`NotImplementedError` when the
  dataset is missing; runs through the harness when patched in.
* :func:`dispatch_bench` — CLI exit codes for valid / invalid / missing
  benchmark names and the propagation of NotImplementedError.

All tests stub out the eval harness + provider construction; nothing
hits the network or the filesystem outside the repo.
"""
from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.otter import benchmarks as bench_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_eval_result(
    name: str = "human-eval", passed: int = 2, total: int = 3
) -> Any:
    """Build a real :class:`EvalResult` so callers exercise the dataclass."""
    from chimera.eval.harness import EvalResult, TaskEvalResult

    results = [
        TaskEvalResult(
            task_id=f"t{i}",
            passed=i < passed,
            output="ok" if i < passed else "fail",
            cost=0.001,
            steps=1,
        )
        for i in range(total)
    ]
    return EvalResult(
        benchmark=name,
        total=total,
        passed=passed,
        pass_rate=passed / total if total else 0.0,
        results=results,
        total_cost=0.003,
    )


def _ns(**kwargs: Any) -> argparse.Namespace:
    """Build an argparse.Namespace with bench-relevant defaults."""
    base: dict[str, Any] = {
        "subcommand": "bench",
        "sub_action": None,
        "sub_target": None,
        "model": "claude-sonnet-4-6",
        "bench_limit": 3,
        "bench_domain": "airline",
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# build_otter_agent_for_eval
# ---------------------------------------------------------------------------


def test_build_otter_agent_for_eval_uses_provider_factory() -> None:
    """Agent should be constructed via ``_build_provider(model)``."""
    fake_provider = MagicMock(name="provider")
    fake_provider.model_name = "claude-sonnet-4-6"

    with patch(
        "chimera.otter.cli._build_provider", return_value=fake_provider
    ) as build:
        agent = bench_mod.build_otter_agent_for_eval(model="claude-sonnet-4-6")

    build.assert_called_once_with("claude-sonnet-4-6")
    assert agent.provider is fake_provider
    # Tools list should be populated (full AGENT_TOOLS — non-empty).
    assert len(agent.tools) > 0
    assert agent.name == "otter-bench"


def test_build_otter_agent_for_eval_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``model=None`` should resolve via $OTTER_MODEL or the default."""
    monkeypatch.setenv("OTTER_MODEL", "env-model-x")
    fake_provider = MagicMock(name="provider", model_name="env-model-x")
    with patch(
        "chimera.otter.cli._build_provider", return_value=fake_provider
    ) as build:
        bench_mod.build_otter_agent_for_eval(model=None)
    build.assert_called_once_with("env-model-x")


def test_build_otter_agent_for_eval_default_model_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var, no model arg -> the otter default is used."""
    monkeypatch.delenv("OTTER_MODEL", raising=False)
    from chimera.otter.cli import _DEFAULT_MODEL

    fake_provider = MagicMock(name="provider", model_name=_DEFAULT_MODEL)
    with patch(
        "chimera.otter.cli._build_provider", return_value=fake_provider
    ) as build:
        bench_mod.build_otter_agent_for_eval(model=None)
    build.assert_called_once_with(_DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# run_humaneval
# ---------------------------------------------------------------------------


def test_run_humaneval_wires_harness_and_humaneval() -> None:
    """run_humaneval should construct HumanEval + Harness and call .run()."""
    expected = _fake_eval_result(name="human-eval", passed=2, total=3)
    fake_harness_instance = MagicMock(name="harness")
    fake_harness_instance.run.return_value = expected
    fake_agent = MagicMock(name="agent")

    with (
        patch.object(
            bench_mod, "build_otter_agent_for_eval", return_value=fake_agent
        ) as build_agent,
        patch(
            "chimera.eval.benchmarks.human_eval.HumanEval"
        ) as humaneval_cls,
        patch(
            "chimera.eval.harness.Harness", return_value=fake_harness_instance
        ) as harness_cls,
    ):
        humaneval_cls.return_value = MagicMock(name="humaneval")
        result = bench_mod.run_humaneval(limit=3, model="m1")

    assert result is expected
    build_agent.assert_called_once_with("m1")
    humaneval_cls.assert_called_once()
    # Verify the dataset_path arg is the vendored default.
    kwargs = humaneval_cls.call_args.kwargs
    assert kwargs["limit"] == 3
    assert "humaneval.json" in kwargs["dataset_path"]
    harness_cls.assert_called_once()
    fake_harness_instance.run.assert_called_once_with()


def test_run_humaneval_zero_limit_means_unlimited() -> None:
    """A non-positive ``limit`` should pass ``None`` to the adapter."""
    fake_harness = MagicMock(name="harness")
    fake_harness.run.return_value = _fake_eval_result()

    with (
        patch.object(
            bench_mod, "build_otter_agent_for_eval", return_value=MagicMock()
        ),
        patch(
            "chimera.eval.benchmarks.human_eval.HumanEval"
        ) as humaneval_cls,
        patch("chimera.eval.harness.Harness", return_value=fake_harness),
    ):
        bench_mod.run_humaneval(limit=0, model="m1")

    assert humaneval_cls.call_args.kwargs["limit"] is None


def test_run_humaneval_dataset_path_override() -> None:
    """Caller-supplied ``dataset_path`` should override the default."""
    fake_harness = MagicMock(name="harness")
    fake_harness.run.return_value = _fake_eval_result()

    with (
        patch.object(
            bench_mod, "build_otter_agent_for_eval", return_value=MagicMock()
        ),
        patch(
            "chimera.eval.benchmarks.human_eval.HumanEval"
        ) as humaneval_cls,
        patch("chimera.eval.harness.Harness", return_value=fake_harness),
    ):
        bench_mod.run_humaneval(limit=1, model="m1", dataset_path="/tmp/he.json")

    assert humaneval_cls.call_args.kwargs["dataset_path"] == "/tmp/he.json"


# ---------------------------------------------------------------------------
# run_tau_bench
# ---------------------------------------------------------------------------


def test_run_tau_bench_raises_when_dataset_missing(tmp_path) -> None:
    """Missing dataset -> NotImplementedError with a clear staging hint."""
    missing = tmp_path / "no-dataset"
    with pytest.raises(NotImplementedError) as excinfo:
        bench_mod.run_tau_bench(
            limit=2, model="m1", domain="airline", dataset_path=str(missing)
        )
    msg = str(excinfo.value)
    assert "tau-bench" in msg
    assert "CHIMERA_TAU_BENCH_PATH" in msg
    assert "airline" in msg


def test_run_tau_bench_runs_through_harness_when_dataset_available(
    tmp_path,
) -> None:
    """When dataset_available returns True, the harness is invoked."""
    expected = _fake_eval_result(name="tau-bench:airline", passed=1, total=2)
    fake_harness = MagicMock(name="harness")
    fake_harness.run.return_value = expected

    with (
        patch(
            "chimera.eval.benchmarks.tau_bench.dataset_available",
            return_value=True,
        ),
        patch(
            "chimera.eval.benchmarks.tau_bench.TauBench"
        ) as taubench_cls,
        patch("chimera.eval.harness.Harness", return_value=fake_harness),
        patch.object(
            bench_mod, "build_otter_agent_for_eval", return_value=MagicMock()
        ),
    ):
        taubench_cls.return_value = MagicMock(name="taubench")
        result = bench_mod.run_tau_bench(
            limit=2, model="m1", domain="airline", dataset_path=str(tmp_path)
        )

    assert result is expected
    taubench_cls.assert_called_once()
    kwargs = taubench_cls.call_args.kwargs
    assert kwargs["domain"] == "airline"
    assert kwargs["limit"] == 2


# ---------------------------------------------------------------------------
# dispatch_bench
# ---------------------------------------------------------------------------


def test_dispatch_bench_missing_name_returns_2(capsys) -> None:
    rc = bench_mod.dispatch_bench(_ns(sub_action=None))
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires a benchmark name" in err
    assert "humaneval" in err


def test_dispatch_bench_unknown_name_returns_2(capsys) -> None:
    rc = bench_mod.dispatch_bench(_ns(sub_action="bogus"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown benchmark" in err


def test_dispatch_bench_humaneval_success(capsys) -> None:
    expected = _fake_eval_result(name="human-eval", passed=2, total=3)
    with patch.object(bench_mod, "run_humaneval", return_value=expected) as run_he:
        rc = bench_mod.dispatch_bench(_ns(sub_action="humaneval", bench_limit=3))
    assert rc == 0
    run_he.assert_called_once_with(limit=3, model="claude-sonnet-4-6")
    out = capsys.readouterr().out
    assert "human-eval" in out
    assert "passed=2/3" in out


def test_dispatch_bench_humaneval_zero_pass_returns_1() -> None:
    expected = _fake_eval_result(name="human-eval", passed=0, total=2)
    with patch.object(bench_mod, "run_humaneval", return_value=expected):
        rc = bench_mod.dispatch_bench(_ns(sub_action="humaneval"))
    assert rc == 1


def test_dispatch_bench_humaneval_default_limit_when_zero() -> None:
    """``--bench-limit 0`` (or unset) should fall back to the safe default of 5."""
    expected = _fake_eval_result(passed=1, total=1)
    with patch.object(bench_mod, "run_humaneval", return_value=expected) as run_he:
        rc = bench_mod.dispatch_bench(_ns(sub_action="humaneval", bench_limit=0))
    assert rc == 0
    # The dispatcher promotes 0 -> 5 for safety; this is the contract.
    assert run_he.call_args.kwargs["limit"] == 5


def test_dispatch_bench_tau_bench_not_implemented_returns_3(capsys) -> None:
    with patch.object(
        bench_mod,
        "run_tau_bench",
        side_effect=NotImplementedError("dataset missing"),
    ):
        rc = bench_mod.dispatch_bench(
            _ns(sub_action="tau-bench", bench_domain="airline")
        )
    assert rc == 3
    err = capsys.readouterr().err
    assert "tau-bench" in err
    assert "dataset missing" in err


def test_dispatch_bench_tau_bench_success(capsys) -> None:
    expected = _fake_eval_result(name="tau-bench:airline", passed=1, total=2)
    with patch.object(bench_mod, "run_tau_bench", return_value=expected) as run_tb:
        rc = bench_mod.dispatch_bench(
            _ns(sub_action="tau-bench", bench_domain="airline", bench_limit=2)
        )
    assert rc == 0
    run_tb.assert_called_once_with(
        limit=2, model="claude-sonnet-4-6", domain="airline"
    )
    out = capsys.readouterr().out
    assert "tau-bench:airline" in out


def test_dispatch_bench_provider_failure_returns_3(capsys) -> None:
    """Generic exceptions from the runner surface as exit code 3."""
    with patch.object(
        bench_mod, "run_humaneval", side_effect=RuntimeError("no provider")
    ):
        rc = bench_mod.dispatch_bench(_ns(sub_action="humaneval"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "no provider" in err


# ---------------------------------------------------------------------------
# CLI integration: chimera/otter/cli.py wires "bench" through dispatch
# ---------------------------------------------------------------------------


def test_cli_subcommand_bench_routes_to_dispatch_bench() -> None:
    """`chimera otter bench humaneval` should call ``dispatch_bench``."""
    from chimera.otter import cli as otter_cli

    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    args = parser.parse_args(["bench", "humaneval", "--bench-limit", "1"])
    assert args.subcommand == "bench"
    assert args.sub_action == "humaneval"
    assert args.bench_limit == 1

    expected = _fake_eval_result(passed=1, total=1)
    with patch.object(bench_mod, "run_humaneval", return_value=expected):
        rc = otter_cli.run(args)
    assert rc == 0


def test_cli_bench_help_lists_bench_flags() -> None:
    """``add_arguments`` should expose --bench-limit and --bench-domain."""
    from chimera.otter import cli as otter_cli

    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    help_text = parser.format_help()
    assert "--bench-limit" in help_text
    assert "--bench-domain" in help_text
