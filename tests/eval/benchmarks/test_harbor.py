"""Tests for the Harbor task-format benchmark adapter."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.harbor import (
    HarborBenchmark,
    HarborParseError,
    HarborTask,
    discover_harbor_tasks,
    parse_harbor_task,
)

FIXTURES = Path(__file__).parent / "fixtures" / "harbor"
DEEPSWE_TASKS = Path(__file__).resolve().parents[3] / "data" / "vendor" / "deep-swe" / "tasks"


def _write_minimal_task(root: Path, name: str) -> Path:
    task_dir = root / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        f'schema_version = "1.1"\n[metadata]\ntask_id = "{name}"\n',
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text(f"Do {name}.\n", encoding="utf-8")
    return task_dir


class _FakeEnv:
    """Duck-typed environment recording verifier interactions."""

    def __init__(self, apply_ok: bool = True, test_ok: bool = True) -> None:
        self._apply_ok = apply_ok
        self._test_ok = test_ok
        self.files: dict[str, str] = {}
        self.commands: list[tuple[str, int]] = []

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main"):
        self.commands.append((cmd, timeout))
        ok = self._apply_ok if "git apply" in cmd else self._test_ok
        return SimpleNamespace(success=ok)


class TestParseHarborTask:
    def test_full_v11_schema_maps_all_fields(self) -> None:
        task = parse_harbor_task(FIXTURES / "mini-go-task")
        assert task.task_id == "mini-go-task"
        assert task.instruction.startswith("Implement the mini feature")
        assert task.repository_url == "https://github.com/example/mini"
        assert task.base_commit_hash == "0123456789abcdef0123456789abcdef01234567"
        assert task.language == "go"
        assert task.docker_image == "example.test/mini:latest"
        assert task.allow_internet is False
        assert task.cpus == 2.0
        assert task.memory_mb == 4096
        assert task.storage_mb == 8192
        assert task.gpus == 0
        assert task.agent_timeout_sec == 1200.0
        assert task.verifier_timeout_sec == 600.0
        assert task.build_timeout_sec == 300.0
        assert task.verifier_env == {"MINI_FLAG": "1"}
        assert task.test_sh_path.is_file()
        assert task.test_patch_path.is_file()
        assert task.environment_dir is not None
        assert task.solution_dir is not None

    def test_bare_task_uses_defaults_and_dirname_id(self) -> None:
        task = parse_harbor_task(FIXTURES / "bare-task")
        assert task.task_id == "bare-task"
        assert task.repository_url == "https://github.com/example/bare"
        assert task.docker_image == ""
        assert task.allow_internet is False
        assert task.agent_timeout_sec == 3600.0
        assert task.verifier_timeout_sec == 1800.0
        assert task.environment_dir is None
        assert task.solution_dir is None

    def test_missing_task_toml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "instruction.md").write_text("x", encoding="utf-8")
        with pytest.raises(HarborParseError, match="missing task.toml"):
            parse_harbor_task(tmp_path)

    def test_missing_instruction_raises(self, tmp_path: Path) -> None:
        (tmp_path / "task.toml").write_text('schema_version = "1.1"', encoding="utf-8")
        with pytest.raises(HarborParseError, match="missing instruction.md"):
            parse_harbor_task(tmp_path)

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "task.toml").write_text("not = [valid", encoding="utf-8")
        (tmp_path / "instruction.md").write_text("x", encoding="utf-8")
        with pytest.raises(HarborParseError, match="invalid TOML"):
            parse_harbor_task(tmp_path)


class TestDiscovery:
    def test_discovers_sorted_task_dirs_only(self, tmp_path: Path) -> None:
        _write_minimal_task(tmp_path, "zeta")
        _write_minimal_task(tmp_path, "alpha")
        (tmp_path / "not-a-task").mkdir()
        (tmp_path / "loose-file.txt").write_text("x", encoding="utf-8")
        dirs = discover_harbor_tasks(tmp_path)
        assert [d.name for d in dirs] == ["alpha", "zeta"]

    def test_single_task_layout(self, tmp_path: Path) -> None:
        task_dir = _write_minimal_task(tmp_path, "solo")
        assert discover_harbor_tasks(task_dir) == [task_dir]

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_harbor_tasks(tmp_path / "nope")


class TestHarborBenchmark:
    def test_loads_fixture_tasks(self) -> None:
        bench = HarborBenchmark(dataset_path=str(FIXTURES))
        assert bench.name() == "harbor"
        tasks = bench.tasks()
        assert [t["id"] for t in tasks] == ["bare-task", "mini-go-task"]
        # tasks dicts must be JSON-safe for result files
        json.dumps(tasks)
        assert all(t["prompt"] for t in tasks)

    def test_seeded_subset_is_deterministic(self, tmp_path: Path) -> None:
        for i in range(6):
            _write_minimal_task(tmp_path, f"task-{i}")
        a = HarborBenchmark(dataset_path=str(tmp_path), limit=3, seed=0)
        b = HarborBenchmark(dataset_path=str(tmp_path), limit=3, seed=0)
        c = HarborBenchmark(dataset_path=str(tmp_path), limit=3, seed=7)
        ids_a = [t["id"] for t in a.tasks()]
        ids_b = [t["id"] for t in b.tasks()]
        ids_c = [t["id"] for t in c.tasks()]
        assert ids_a == ids_b
        assert len(ids_a) == 3
        assert ids_c != ids_a  # seed 7 happens to differ from seed 0 here
        assert ids_a == sorted(ids_a)

    def test_limit_at_or_above_total_keeps_all(self, tmp_path: Path) -> None:
        for i in range(3):
            _write_minimal_task(tmp_path, f"task-{i}")
        bench = HarborBenchmark(dataset_path=str(tmp_path), limit=5)
        assert len(bench.tasks()) == 3


class TestEvaluate:
    def _task(self) -> tuple[dict, HarborBenchmark]:
        bench = HarborBenchmark(dataset_path=str(FIXTURES / "mini-go-task"))
        return bench.tasks()[0], bench

    def test_pass_path_applies_patch_then_runs_test_sh(self) -> None:
        task, bench = self._task()
        env = _FakeEnv()
        assert bench.evaluate(task, "irrelevant", env) is True
        assert "_harbor_test.patch" in env.files
        assert "_harbor_test.sh" in env.files
        cmds = [c for c, _ in env.commands]
        assert cmds == ["git apply _harbor_test.patch", "bash _harbor_test.sh"]
        # verifier timeout from task.toml flows through
        assert env.commands[1][1] == 600

    def test_patch_apply_failure_fails_task(self) -> None:
        task, bench = self._task()
        env = _FakeEnv(apply_ok=False)
        assert bench.evaluate(task, "", env) is False
        assert [c for c, _ in env.commands] == ["git apply _harbor_test.patch"]

    def test_verifier_failure_fails_task(self) -> None:
        task, bench = self._task()
        assert bench.evaluate(task, "", _FakeEnv(test_ok=False)) is False

    def test_no_env_fails(self) -> None:
        task, bench = self._task()
        assert bench.evaluate(task, "", None) is False

    def test_env_without_contract_fails(self) -> None:
        task, bench = self._task()
        assert bench.evaluate(task, "", object()) is False

    def test_task_without_test_sh_fails(self, tmp_path: Path) -> None:
        _write_minimal_task(tmp_path, "no-verifier")
        bench = HarborBenchmark(dataset_path=str(tmp_path))
        assert bench.evaluate(bench.tasks()[0], "", _FakeEnv()) is False

    def test_task_without_patch_skips_apply(self, tmp_path: Path) -> None:
        task_dir = _write_minimal_task(tmp_path, "patchless")
        tests_dir = task_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        bench = HarborBenchmark(dataset_path=str(tmp_path))
        env = _FakeEnv()
        assert bench.evaluate(bench.tasks()[0], "", env) is True
        assert [c for c, _ in env.commands] == ["bash _harbor_test.sh"]


class TestCLIRegistry:
    def test_load_benchmark_harbor(self) -> None:
        from chimera.cli.main import _load_benchmark

        bench = _load_benchmark("harbor", dataset=str(FIXTURES), limit=1)
        assert isinstance(bench, HarborBenchmark)
        assert len(bench.tasks()) == 1


@pytest.mark.skipif(
    not DEEPSWE_TASKS.is_dir(),
    reason="real DeepSWE checkout not present under data/vendor/deep-swe",
)
class TestRealDeepSWE:
    """Acceptance sweep against a real DeepSWE checkout (when present)."""

    def test_parses_every_real_task(self) -> None:
        dirs = discover_harbor_tasks(DEEPSWE_TASKS)
        assert len(dirs) >= 100
        parsed = [parse_harbor_task(d) for d in dirs]
        assert all(isinstance(t, HarborTask) for t in parsed)
        assert all(t.instruction.strip() for t in parsed)
        assert all(t.docker_image for t in parsed)
        assert all(t.repository_url.startswith("https://") for t in parsed)
        assert all(t.test_sh_path.is_file() for t in parsed)
