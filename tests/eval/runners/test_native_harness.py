"""Plumbing tests for NativeHarnessRunner (no real harness / subprocess / LLM).

The subprocess callable is injected as a fake and a predictions file is written
to a tmp path, so these exercise harness-command substitution and the
predictions -> AgentRunResult mapping without running any external harness.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from chimera.eval.runners import AgentRunner, AgentRunResult
from chimera.eval.runners.native_harness import NativeHarnessRunner


class _FakeRunner:
    """Records argv/kwargs per call and returns a canned CompletedProcess."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        raises: BaseException | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr=self.stderr)


def _write_jsonl(path: Path, *records: dict[str, Any]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_run_all_maps_predictions(tmp_path: Path) -> None:
    preds = tmp_path / "preds.jsonl"
    _write_jsonl(
        preds,
        {"instance_id": "repo__issue-1", "model_patch": "PATCH-1"},
        {"instance_id": "repo__issue-2", "prediction": "PATCH-2"},
    )
    fake = _FakeRunner(returncode=0)
    runner = NativeHarnessRunner(
        "mini-swe-agent",
        harness_cmd="python -m minisweagent.run --subset {subset} --output {out_dir}",
        predictions_glob=str(preds),
        runner=fake,
    )

    cells = runner.run_all(
        [{"instance_id": "repo__issue-1"}, {"instance_id": "repo__issue-2"}]
    )

    assert set(cells) == {"repo__issue-1", "repo__issue-2"}
    assert cells["repo__issue-1"].patch == "PATCH-1"  # from model_patch
    assert cells["repo__issue-2"].patch == "PATCH-2"  # from prediction
    assert cells["repo__issue-1"].status == "completed"
    assert cells["repo__issue-1"].cost_usd == 0.0
    assert cells["repo__issue-1"].raw["cost"] == "unknown"  # never fabricated
    assert cells["repo__issue-1"].wall_clock_sec == 0.0  # per-cell time not fabricated
    assert cells["repo__issue-1"].raw["batch_wall_clock_sec"] >= 0.0

    # Harness command was substituted and tokenized.
    argv, kwargs = fake.calls[0]
    assert "--subset" in argv
    subset = argv[argv.index("--subset") + 1]
    assert subset == "repo__issue-1,repo__issue-2"  # comma-joined requested ids
    out_dir = argv[argv.index("--output") + 1]
    assert Path(out_dir).is_dir()  # {out_dir} is a real fresh directory
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_missing_prediction_becomes_error_cell(tmp_path: Path) -> None:
    preds = tmp_path / "preds.jsonl"
    _write_jsonl(preds, {"instance_id": "found-1", "model_patch": "P"})
    fake = _FakeRunner(returncode=0)
    runner = NativeHarnessRunner(
        "h", harness_cmd="run {subset} {out_dir}", predictions_glob=str(preds), runner=fake
    )

    cells = runner.run_all([{"instance_id": "found-1"}, {"instance_id": "missing-1"}])

    assert cells["found-1"].status == "completed"
    assert cells["found-1"].patch == "P"
    assert cells["missing-1"].status == "error"
    assert cells["missing-1"].patch is None
    assert "no prediction" in cells["missing-1"].raw["note"]


def test_run_single_task_returns_cell(tmp_path: Path) -> None:
    preds = tmp_path / "preds.jsonl"
    _write_jsonl(preds, {"instance_id": "solo", "patch": "SOLO-PATCH"})
    fake = _FakeRunner(returncode=0)
    runner = NativeHarnessRunner(
        "h", harness_cmd="run {subset}", predictions_glob=str(preds), runner=fake
    )

    out = runner.run({"instance_id": "solo"})

    assert isinstance(out, AgentRunResult)
    assert out.patch == "SOLO-PATCH"  # from patch key
    assert out.status == "completed"


def test_run_without_instance_id_raises() -> None:
    fake = _FakeRunner(returncode=0)
    runner = NativeHarnessRunner(
        "h", harness_cmd="run", predictions_glob="/nonexistent/*.jsonl", runner=fake
    )

    with pytest.raises(ValueError, match="instance_id"):
        runner.run({"no_id_here": 1})
    assert fake.calls == []  # raises before running the harness


def test_harness_timeout_marks_requested_timeout(tmp_path: Path) -> None:
    fake = _FakeRunner(raises=subprocess.TimeoutExpired(cmd="run", timeout=1.0))
    runner = NativeHarnessRunner(
        "h",
        harness_cmd="run {subset}",
        predictions_glob=str(tmp_path / "none-*.jsonl"),  # matches nothing
        timeout=1.0,
        runner=fake,
    )

    cells = runner.run_all([{"instance_id": "a"}, {"instance_id": "b"}])

    assert cells["a"].status == "timeout"
    assert cells["b"].status == "timeout"


def test_whole_file_json_array_is_tolerated(tmp_path: Path) -> None:
    preds = tmp_path / "preds.json"
    preds.write_text(
        json.dumps([{"instance_id": "arr-1", "model_patch": "AP"}]), encoding="utf-8"
    )
    fake = _FakeRunner(returncode=0)
    runner = NativeHarnessRunner(
        "h", harness_cmd="run {subset}", predictions_glob=str(preds), runner=fake
    )

    cells = runner.run_all([{"instance_id": "arr-1"}])

    assert cells["arr-1"].patch == "AP"


def test_is_agent_runner() -> None:
    runner = NativeHarnessRunner("h", harness_cmd="run", predictions_glob="*.jsonl")
    assert isinstance(runner, AgentRunner)  # runtime_checkable
