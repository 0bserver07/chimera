"""Tests for the Senior SWE-Bench (Snorkel) adapter — a Harbor-format profile.

Covers the ``_parse_size_mb`` unit helper, the Senior-specific
``parse_senior_task`` field remap (``base_image`` -> ``docker_image``,
``"8G"`` -> ``memory_mb``, ``[metadata.origin]`` / ``[metadata.taxonomy]`` /
``[metadata.oracle_scope]`` -> task fields + extras), the parse error contract,
the scaffold loader (absent / stale dataset -> **zero** tasks, never raises),
task discovery under both a repo-root and a ``tasks/`` layout, deterministic
seeded sampling, and the enriched task-dict shape.

Grading is deliberately NOT exercised: a faithful Senior grade drives a
multi-stage agentic verifier that needs the task's prebuilt Docker image plus
LLM API keys. Per the source, this module only asserts the honest
absent-grading behavior — no env (or an env without the write_file/run_command
contract) returns ``False`` rather than a fabricated pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.eval.benchmarks.harbor import HarborParseError
from chimera.eval.benchmarks.senior_swe_bench import (
    SeniorSWEBench,
    _parse_size_mb,
    parse_senior_task,
)

_TASK_TOML = """\
version = "1.0"

[metadata]
family = "{family}"
version = "v2026.06"
segment = "backend"
variant = "hard"
visibility = "public"

[metadata.origin]
repo = "https://github.com/PostHog/posthog"
base_commit = "abc123def456"
pr_numbers = [123, 456]

[metadata.taxonomy]
stack = ["python", "typescript"]
task_type = "bug"

[metadata.oracle_scope]
files = 3
sloc = 120

[environment]
base_image = "ghcr.io/example/posthog:latest"
memory = "8G"
storage = "20G"
cpus = 2
allow_internet = false

[verifier]
timeout_sec = 1800

[verifier.env]
SSB_OVERRIDE_MODEL = "claude"

[agent]
timeout_sec = 7200
"""


def _write_senior_task(parent: Path, name: str, family: str | None = None) -> Path:
    task_dir = parent / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        _TASK_TOML.format(family=family or name), encoding="utf-8"
    )
    (task_dir / "instruction.md").write_text(f"Implement {name}.\n", encoding="utf-8")
    return task_dir


# ------------------------------------------------------------------- _parse_size_mb


def test_parse_size_mb_units() -> None:
    assert _parse_size_mb("8G", 2048) == 8192
    assert _parse_size_mb("512M", 2048) == 512
    assert _parse_size_mb("20G", 2048) == 20480
    assert _parse_size_mb("1K", 2048) == 1  # rounds up to at least 1 MB


def test_parse_size_mb_numeric_passthrough() -> None:
    assert _parse_size_mb(4096, 2048) == 4096
    assert _parse_size_mb(2048.0, 1) == 2048


def test_parse_size_mb_falls_back_on_bad_input() -> None:
    assert _parse_size_mb(True, 2048) == 2048  # bool rejected explicitly
    assert _parse_size_mb("garbage", 777) == 777
    assert _parse_size_mb("", 555) == 555
    assert _parse_size_mb(None, 333) == 333


# ----------------------------------------------------------------- parse_senior_task


def test_parse_senior_task_maps_fields(tmp_path: Path) -> None:
    task_dir = _write_senior_task(tmp_path, "posthog-bug-1", family="posthog-bug-1")
    task, _extras = parse_senior_task(task_dir)

    assert task.task_id == "posthog-bug-1"
    assert task.instruction.startswith("Implement posthog-bug-1")
    assert task.docker_image == "ghcr.io/example/posthog:latest"
    assert task.repository_url == "https://github.com/PostHog/posthog"
    assert task.base_commit_hash == "abc123def456"
    assert task.language == "python"  # first of the stack list
    assert task.memory_mb == 8192  # "8G" parsed
    assert task.storage_mb == 20480  # "20G" parsed
    assert task.cpus == 2.0
    assert task.agent_timeout_sec == 7200.0
    assert task.verifier_timeout_sec == 1800.0
    assert task.verifier_env == {"SSB_OVERRIDE_MODEL": "claude"}


def test_parse_senior_task_extras(tmp_path: Path) -> None:
    task_dir = _write_senior_task(tmp_path, "t1")
    _, extras = parse_senior_task(task_dir)
    assert extras["task_type"] == "bug"
    assert extras["stack"] == ["python", "typescript"]
    assert extras["segment"] == "backend"
    assert extras["variant"] == "hard"
    assert extras["visibility"] == "public"
    assert extras["dataset_version"] == "v2026.06"
    assert extras["oracle_files"] == 3
    assert extras["oracle_sloc"] == 120
    assert extras["pr_numbers"] == [123, 456]


def test_parse_senior_task_family_falls_back_to_dirname(tmp_path: Path) -> None:
    # task.toml with no [metadata].family -> task_id is the directory name.
    task_dir = tmp_path / "dir-named"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        '[environment]\nbase_image = "img:1"\n', encoding="utf-8"
    )
    (task_dir / "instruction.md").write_text("x", encoding="utf-8")
    task, _ = parse_senior_task(task_dir)
    assert task.task_id == "dir-named"
    assert task.docker_image == "img:1"


def test_parse_senior_task_missing_toml_raises(tmp_path: Path) -> None:
    (tmp_path / "instruction.md").write_text("x", encoding="utf-8")
    with pytest.raises(HarborParseError, match="missing task.toml"):
        parse_senior_task(tmp_path)


def test_parse_senior_task_missing_instruction_raises(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text('[metadata]\nfamily = "x"\n', encoding="utf-8")
    with pytest.raises(HarborParseError, match="missing instruction.md"):
        parse_senior_task(tmp_path)


def test_parse_senior_task_invalid_toml_raises(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text("not = [valid", encoding="utf-8")
    (tmp_path / "instruction.md").write_text("x", encoding="utf-8")
    with pytest.raises(HarborParseError, match="invalid TOML"):
        parse_senior_task(tmp_path)


# --------------------------------------------------------------------- benchmark


def test_name_is_senior_swe_bench() -> None:
    assert SeniorSWEBench().name() == "senior-swe-bench"


def test_absent_dataset_yields_no_tasks() -> None:
    """Scaffold: no dataset path -> zero tasks, never raises."""
    assert SeniorSWEBench().tasks() == []


def test_stale_dataset_path_yields_no_tasks() -> None:
    """Scaffold: a non-existent path -> zero tasks, never raises."""
    assert SeniorSWEBench(dataset_path="/no/such/senior/checkout").tasks() == []


def test_loads_tasks_from_tasks_subdir(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    _write_senior_task(tasks_root, "alpha")
    _write_senior_task(tasks_root, "beta")
    bench = SeniorSWEBench(dataset_path=str(tmp_path))  # repo root w/ tasks/ subdir
    tasks = bench.tasks()
    assert {t["id"] for t in tasks} == {"alpha", "beta"}


def test_loads_tasks_from_direct_root(tmp_path: Path) -> None:
    _write_senior_task(tmp_path, "solo")  # no tasks/ subdir -> discover under root
    bench = SeniorSWEBench(dataset_path=str(tmp_path))
    assert [t["id"] for t in bench.tasks()] == ["solo"]


def test_task_dict_carries_base_and_taxonomy(tmp_path: Path) -> None:
    _write_senior_task(tmp_path, "posthog-bug-1", family="posthog-bug-1")
    task = SeniorSWEBench(dataset_path=str(tmp_path)).tasks()[0]
    # Base Harbor keys the verifier / provisioner need:
    for key in ("id", "prompt", "docker_image", "base_commit", "task_dir"):
        assert key in task
    # Senior taxonomy keys for matrix slicing:
    assert task["task_type"] == "bug"
    assert task["stack"] == ["python", "typescript"]
    assert task["variant"] == "hard"
    assert task["oracle_files"] == 3
    assert task["pr_numbers"] == [123, 456]
    json.dumps(task)  # JSON-safe for result files


def test_seeded_subset_is_deterministic(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    for i in range(6):
        _write_senior_task(tasks_root, f"task-{i}")
    a = SeniorSWEBench(dataset_path=str(tmp_path), limit=3, seed=0).tasks()
    b = SeniorSWEBench(dataset_path=str(tmp_path), limit=3, seed=0).tasks()
    ids_a = [t["id"] for t in a]
    assert ids_a == [t["id"] for t in b]
    assert len(ids_a) == 3
    assert ids_a == sorted(ids_a)


# ---------------------------------------------------- evaluate (absent-grading only)


def test_evaluate_no_env_is_false(tmp_path: Path) -> None:
    _write_senior_task(tmp_path, "t")
    task = SeniorSWEBench(dataset_path=str(tmp_path)).tasks()[0]
    assert SeniorSWEBench().evaluate(task, "", None) is False


def test_evaluate_env_without_contract_is_false(tmp_path: Path) -> None:
    _write_senior_task(tmp_path, "t")
    task = SeniorSWEBench(dataset_path=str(tmp_path)).tasks()[0]
    # An env lacking write_file/run_command cannot grade -> False (not a fake pass).
    assert SeniorSWEBench().evaluate(task, "", object()) is False
