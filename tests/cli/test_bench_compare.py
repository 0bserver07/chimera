"""Smoke tests for the ``chimera bench-compare`` subcommand."""
from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from chimera.cli.bench_compare import (
    LOOP_TYPES,
    _build_factories,
    report_to_dict,
    report_to_html,
    report_to_markdown,
    run_bench_compare,
)
from chimera.providers.base import Provider, Response
from chimera.types import Message


class _DoneProvider(Provider):
    """Scripted provider: answers immediately with no tool calls."""

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> Response:
        return Response(
            content="the answer is 42",
            tool_calls=[],
            usage={"input_tokens": 5, "output_tokens": 5},
        )

    @property
    def context_window(self) -> int:
        return 8192

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "scripted-done"


class _FakeBenchmark:
    def name(self) -> str:
        return "fake"

    def tasks(self) -> list[dict[str, Any]]:
        return [
            {"id": "t1", "prompt": "what is the answer?"},
            {"id": "t2", "prompt": "again?"},
        ]

    def evaluate(self, task: dict[str, Any], output: str, env: Any = None) -> bool:
        return "42" in output


def _args(**overrides: Any) -> argparse.Namespace:
    base = dict(
        agents="react",
        benchmark="fake",
        dataset=None,
        limit=None,
        model="glm-5",
        max_steps=10,
        max_tool_calls=5,
        max_llm_calls=None,
        max_wall_clock=None,
        max_cost=None,
        seed=0,
        fmt="terminal",
        output=None,
        emit_atif=None,
        env_kind="none",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_parser_registers_bench_compare() -> None:
    from chimera.cli.main import build_parser

    args = build_parser().parse_args(
        ["bench-compare", "--benchmark", "harbor", "--dataset", "/tmp/x"]
    )
    assert args.command == "bench-compare"
    assert args.benchmark == "harbor"
    assert args.agents == "react,plan-execute"
    assert args.model == "glm-5"


def test_build_factories_rejects_unknown_loop() -> None:
    with pytest.raises(ValueError, match="Unknown agent loop"):
        _build_factories(["react", "nope"], max_steps=10)


def test_build_factories_covers_all_loop_types() -> None:
    factories = _build_factories(list(LOOP_TYPES), max_steps=10)
    assert sorted(factories) == sorted(LOOP_TYPES)


def test_smoke_run_produces_matrix(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(
        "chimera.cli.main._load_benchmark",
        lambda name, dataset=None, limit=None, tasks_dir=None: _FakeBenchmark(),
    )
    monkeypatch.setattr(
        "chimera.providers.factory.create_provider",
        lambda model=None, **kw: _DoneProvider(),
    )

    out_path = tmp_path / "report.json"
    rc = run_bench_compare(_args(fmt="markdown", output=str(out_path)))
    assert rc == 0

    stdout = capsys.readouterr().out
    assert "| react | 100.0% (2/2) |" in stdout

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["configs"] == ["react"]
    assert written["budget_hits"] == {"react": 0}
    assert written["budget"]["max_tool_calls"] == 5
    assert len(written["results"]["react"]) == 2


def test_smoke_unknown_benchmark_errors_cleanly(monkeypatch, capsys) -> None:
    rc = run_bench_compare(_args(benchmark="definitely-not-registered"))
    assert rc == 1
    assert "Unknown benchmark" in capsys.readouterr().err


def test_renderers_on_synthetic_report() -> None:
    from chimera.core.budget import BudgetSpec
    from chimera.eval.comparative import CompareReport, TaskResult

    report = CompareReport(
        configs=["react"],
        results={
            "react": [TaskResult("p1", "out", cost=0.01, steps=2, passed=True)]
        },
        budget=BudgetSpec(max_tool_calls=3),
        model="glm-5",
        task_pool="unit:fixtures?n=1",
        seed=0,
        budget_hits={"react": 0},
        budget_reasons={"react": []},
    )
    md = report_to_markdown(report)
    assert "| react | 100.0% (1/1) | $0.0100 | 2.0 | 0/1 |" in md
    json.dumps(report_to_dict(report))  # JSON-safe end to end

    html = report_to_html(report)
    assert "<title>Comparative matrix — glm-5</title>" in html
    assert 'data-sort="1.0000"' in html  # sortable pass-rate cell
    assert "react — per-task results" in html
    assert "<script>" in html  # click-to-sort enhancement embedded


def test_html_output_file(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(
        "chimera.cli.main._load_benchmark",
        lambda name, dataset=None, limit=None, tasks_dir=None: _FakeBenchmark(),
    )
    monkeypatch.setattr(
        "chimera.providers.factory.create_provider",
        lambda model=None, **kw: _DoneProvider(),
    )
    out_path = tmp_path / "matrix.html"
    rc = run_bench_compare(_args(fmt="terminal", output=str(out_path)))
    assert rc == 0
    html = out_path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "Controlled comparative matrix" in html
