"""Tests for interruption-safe :class:`~chimera.eval.harness.Harness`.

Covers the incremental JSONL sidecar (``progress_path``) and the ``resume``
skip-and-aggregate behaviour. All fakes are in-process — no live models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark, Harness

# -- Helpers ------------------------------------------------------------------


class SimpleBenchmark(Benchmark):
    """Benchmark with canned tasks; a task passes when its output holds 'ok'."""

    def __init__(self, task_list: list[dict[str, Any]]) -> None:
        self._tasks = task_list

    def name(self) -> str:
        return "simple"

    def tasks(self) -> list[dict[str, Any]]:
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        return "ok" in agent_output.lower()


@dataclass
class FakeAgentResult:
    output: str
    cost: float
    steps: int


class CountingAgent:
    """Agent that records which prompts it was asked to run.

    Args:
        responses: Optional per-prompt output overrides.
        default: Output returned when a prompt has no override.
    """

    def __init__(
        self, responses: dict[str, str] | None = None, default: str = "ok"
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self.calls: list[str] = []

    def run(self, task: str, env: Any) -> FakeAgentResult:
        self.calls.append(task)
        output = self._responses.get(task, self._default)
        return FakeAgentResult(output=output, cost=0.01, steps=3)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Return the parsed JSON objects from a JSONL sidecar file."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- persist ------------------------------------------------------------------


class TestPersist:
    def test_writes_one_line_per_task_with_schema(self, tmp_path: Path) -> None:
        tasks = [
            {"id": "t1", "prompt": "p1"},
            {"id": "t2", "prompt": "p2"},
            {"id": "t3", "prompt": "p3"},
        ]
        progress = tmp_path / "progress.jsonl"
        agent = CountingAgent(responses={"p1": "ok", "p2": "fail", "p3": "ok"})
        harness = Harness(
            SimpleBenchmark(tasks), agent, progress_path=str(progress)
        )
        result = harness.run()

        lines = _read_lines(progress)
        assert len(lines) == 3
        assert [line["task_id"] for line in lines] == ["t1", "t2", "t3"]
        # Exact schema: task_id/passed/output/cost/steps and nothing else.
        for line in lines:
            assert set(line) == {"task_id", "passed", "output", "cost", "steps"}
            assert isinstance(line["task_id"], str)
            assert isinstance(line["passed"], bool)
            assert isinstance(line["output"], str)
            assert isinstance(line["cost"], float)
            assert isinstance(line["steps"], int)
        assert lines[0]["passed"] is True
        assert lines[1]["passed"] is False
        assert result.passed == 2
        assert result.total == 3

    def test_task_id_falls_back_to_task_id_key_then_index(
        self, tmp_path: Path
    ) -> None:
        tasks = [
            {"task_id": "alt", "prompt": "p1"},  # uses task_id key
            {"prompt": "p2"},  # falls back to index 1
        ]
        progress = tmp_path / "progress.jsonl"
        harness = Harness(
            SimpleBenchmark(tasks), CountingAgent(), progress_path=str(progress)
        )
        harness.run()

        assert [line["task_id"] for line in _read_lines(progress)] == ["alt", "1"]


# -- resume -------------------------------------------------------------------


class TestResume:
    def test_skips_recorded_tasks_and_counts_them(self, tmp_path: Path) -> None:
        progress = tmp_path / "progress.jsonl"
        # Pre-record t1 (passed) and t2 (failed) as already done.
        progress.write_text(
            json.dumps(
                {"task_id": "t1", "passed": True, "output": "ok", "cost": 0.5, "steps": 7}
            )
            + "\n"
            + json.dumps(
                {"task_id": "t2", "passed": False, "output": "no", "cost": 0.5, "steps": 7}
            )
            + "\n"
        )

        tasks = [
            {"id": "t1", "prompt": "p1"},
            {"id": "t2", "prompt": "p2"},
            {"id": "t3", "prompt": "p3"},
        ]
        agent = CountingAgent(default="ok")
        harness = Harness(
            SimpleBenchmark(tasks),
            agent,
            progress_path=str(progress),
            resume=True,
        )
        result = harness.run()

        # Agent only ran the one un-recorded task.
        assert agent.calls == ["p3"]
        # Cached t1 (pass) + cached t2 (fail) + new t3 (pass) => 2/3.
        assert result.total == 3
        assert result.passed == 2
        assert {r.task_id for r in result.results} == {"t1", "t2", "t3"}
        # Cached cost is preserved and folded in with the new task's cost.
        assert result.total_cost == 0.5 + 0.5 + 0.01
        # Sidecar now also contains the newly-run task appended after resume.
        assert [line["task_id"] for line in _read_lines(progress)] == ["t1", "t2", "t3"]

    def test_resume_with_no_existing_file_runs_all(self, tmp_path: Path) -> None:
        progress = tmp_path / "missing.jsonl"
        tasks = [{"id": "t1", "prompt": "p1"}, {"id": "t2", "prompt": "p2"}]
        agent = CountingAgent(default="ok")
        harness = Harness(
            SimpleBenchmark(tasks),
            agent,
            progress_path=str(progress),
            resume=True,
        )
        result = harness.run()

        assert agent.calls == ["p1", "p2"]
        assert result.total == 2


# -- fresh (resume=False) -----------------------------------------------------


class TestFresh:
    def test_existing_file_is_overwritten_and_all_tasks_rerun(
        self, tmp_path: Path
    ) -> None:
        progress = tmp_path / "progress.jsonl"
        # Stale content from a prior run that must be discarded.
        progress.write_text(
            json.dumps(
                {"task_id": "old", "passed": True, "output": "x", "cost": 9.0, "steps": 1}
            )
            + "\n"
        )

        tasks = [{"id": "t1", "prompt": "p1"}, {"id": "t2", "prompt": "p2"}]
        agent = CountingAgent(default="ok")
        harness = Harness(
            SimpleBenchmark(tasks),
            agent,
            progress_path=str(progress),
            resume=False,
        )
        result = harness.run()

        assert agent.calls == ["p1", "p2"]
        lines = _read_lines(progress)
        assert [line["task_id"] for line in lines] == ["t1", "t2"]
        assert "old" not in {line["task_id"] for line in lines}
        assert result.total == 2
        assert result.passed == 2


# -- backward compatibility ---------------------------------------------------


class TestBackwardCompat:
    def test_progress_path_none_writes_nothing(self, tmp_path: Path) -> None:
        tasks = [{"id": "t1", "prompt": "p1"}, {"id": "t2", "prompt": "p2"}]
        agent = CountingAgent(default="ok")
        harness = Harness(SimpleBenchmark(tasks), agent)
        result = harness.run()

        assert agent.calls == ["p1", "p2"]
        assert result.total == 2
        assert result.passed == 2
        # No stray sidecar files created anywhere in the temp dir.
        assert list(tmp_path.iterdir()) == []
