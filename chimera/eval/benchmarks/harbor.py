"""Harbor task-format benchmark adapter.

Harbor (<https://www.harborframework.com/docs/tasks>) is the task format
used by long-horizon SWE benchmarks such as DeepSWE. Each task is a
directory:

    <task-id>/
        task.toml           # metadata, environment, agent/verifier budgets
        instruction.md      # prompt shown to the agent verbatim
        environment/        # Dockerfile fallback when docker_image is absent
        tests/test.sh       # verifier entry point
        tests/test.patch    # applied at grading time, not during the run
        solution/           # reference patch (held out from the agent)

This adapter consumes any directory of Harbor-format tasks, so new
benchmarks shipped in the format need no per-benchmark code. Field
mapping follows the ``schema_version = "1.1"`` layout observed in the
real DeepSWE task set (``repository_url`` / ``base_commit_hash`` live in
``[metadata]``; resource limits and ``docker_image`` in
``[environment]``), with missing optional fields tolerated.
"""
from __future__ import annotations

import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


class HarborParseError(ValueError):
    """Raised when a task directory is not a parseable Harbor task."""


@dataclass(frozen=True)
class HarborTask:
    """A single parsed Harbor task.

    Attributes mirror the on-disk ``task.toml`` plus the instruction text;
    ``task_dir`` anchors the verifier assets (``tests/``, ``solution/``,
    ``environment/``).
    """

    task_id: str
    instruction: str
    repository_url: str
    base_commit_hash: str
    language: str
    docker_image: str
    allow_internet: bool
    cpus: float
    memory_mb: int
    storage_mb: int
    gpus: int
    agent_timeout_sec: float
    verifier_timeout_sec: float
    build_timeout_sec: float
    task_dir: Path
    verifier_env: dict[str, str] = field(default_factory=dict)

    @property
    def test_sh_path(self) -> Path:
        """Path to the verifier entry point script."""
        return self.task_dir / "tests" / "test.sh"

    @property
    def test_patch_path(self) -> Path:
        """Path to the grading-time test patch."""
        return self.task_dir / "tests" / "test.patch"

    @property
    def environment_dir(self) -> Path | None:
        """The Dockerfile fallback directory, when present."""
        env_dir = self.task_dir / "environment"
        return env_dir if env_dir.is_dir() else None

    @property
    def solution_dir(self) -> Path | None:
        """The held-out reference solution directory, when present."""
        sol_dir = self.task_dir / "solution"
        return sol_dir if sol_dir.is_dir() else None

    def to_task(self) -> dict[str, Any]:
        """Render as a JSON-safe harness task dict.

        Returns:
            Dict with the harness-required ``id`` / ``prompt`` keys plus
            the Harbor metadata needed to provision an environment and
            run the verifier.
        """
        return {
            "id": self.task_id,
            "prompt": self.instruction,
            "description": self.instruction,
            "repository_url": self.repository_url,
            "base_commit": self.base_commit_hash,
            "language": self.language,
            "docker_image": self.docker_image,
            "allow_internet": self.allow_internet,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "storage_mb": self.storage_mb,
            "gpus": self.gpus,
            "agent_timeout_sec": self.agent_timeout_sec,
            "verifier_timeout_sec": self.verifier_timeout_sec,
            "task_dir": str(self.task_dir),
        }


def parse_harbor_task(task_dir: Path | str) -> HarborTask:
    """Parse one Harbor task directory into a :class:`HarborTask`.

    Args:
        task_dir: Directory containing ``task.toml`` and ``instruction.md``.

    Returns:
        The parsed task. Optional ``task.toml`` fields fall back to
        permissive defaults; ``task_id`` falls back to the directory name.

    Raises:
        HarborParseError: If ``task.toml`` or ``instruction.md`` is
            missing, or ``task.toml`` is not valid TOML.
    """
    task_dir = Path(task_dir)
    toml_path = task_dir / "task.toml"
    instruction_path = task_dir / "instruction.md"

    if not toml_path.is_file():
        raise HarborParseError(f"{task_dir}: missing task.toml")
    if not instruction_path.is_file():
        raise HarborParseError(f"{task_dir}: missing instruction.md")

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise HarborParseError(f"{toml_path}: invalid TOML: {exc}") from exc

    metadata = data.get("metadata", {})
    environment = data.get("environment", {})
    verifier = data.get("verifier", {})
    agent = data.get("agent", {})

    verifier_env_raw = verifier.get("env", {})
    verifier_env = {str(k): str(v) for k, v in verifier_env_raw.items()}

    return HarborTask(
        task_id=str(metadata.get("task_id") or task_dir.name),
        instruction=instruction_path.read_text(encoding="utf-8"),
        repository_url=str(metadata.get("repository_url", "")),
        base_commit_hash=str(metadata.get("base_commit_hash", "")),
        language=str(metadata.get("language", "")),
        docker_image=str(environment.get("docker_image", "")),
        allow_internet=bool(environment.get("allow_internet", False)),
        cpus=float(environment.get("cpus", 1)),
        memory_mb=int(environment.get("memory_mb", 2048)),
        storage_mb=int(environment.get("storage_mb", 10240)),
        gpus=int(environment.get("gpus", 0)),
        agent_timeout_sec=float(agent.get("timeout_sec", 3600.0)),
        verifier_timeout_sec=float(verifier.get("timeout_sec", 1800.0)),
        build_timeout_sec=float(environment.get("build_timeout_sec", 1800.0)),
        task_dir=task_dir,
        verifier_env=verifier_env,
    )


def discover_harbor_tasks(root: Path | str) -> list[Path]:
    """Find Harbor task directories under ``root``.

    A task directory is any direct subdirectory containing a
    ``task.toml``. ``root`` itself qualifies when it contains one
    (single-task layout). Results are sorted by name for determinism.

    Args:
        root: Directory to scan.

    Returns:
        Sorted list of task directory paths.

    Raises:
        FileNotFoundError: If ``root`` does not exist.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"Harbor task root not found: {root}")
    if (root / "task.toml").is_file():
        return [root]
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and (d / "task.toml").is_file()),
        key=lambda d: d.name,
    )


class HarborBenchmark(Benchmark):
    """Benchmark adapter for Harbor-format task directories.

    Loads every task directory under ``dataset_path`` (e.g. a DeepSWE
    checkout's ``tasks/`` dir). ``evaluate()`` runs the Harbor verifier
    flow — apply ``tests/test.patch`` on top of the agent's work, then
    run ``tests/test.sh`` — inside the supplied environment.

    Args:
        dataset_path: Root directory of Harbor task subdirectories.
        limit: Optional cap on the number of tasks. When fewer than the
            total, a deterministic seeded sample is taken.
        seed: Sampling seed; the same ``(dataset, limit, seed)`` always
            yields the same subset.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        seed: int = 0,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit
        self._seed = seed
        self._tasks: list[HarborTask] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        """Discover, sample, and parse task directories under ``path``."""
        task_dirs = discover_harbor_tasks(path)
        if self._limit and self._limit < len(task_dirs):
            rng = random.Random(self._seed)
            task_dirs = sorted(rng.sample(task_dirs, self._limit), key=lambda d: d.name)
        self._tasks = [parse_harbor_task(d) for d in task_dirs]

    def name(self) -> str:
        return "harbor"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [t.to_task() for t in self._tasks]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Run the Harbor verifier flow for one task.

        Applies ``tests/test.patch`` (when present) on top of the agent's
        working tree, then runs ``tests/test.sh`` with the task's
        verifier timeout. Exit 0 means pass. Mirrors the defensive
        duck-typing of the other repo-based adapters: an env without
        ``write_file`` / ``run_command`` (or ``None``) fails the task
        rather than raising.

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: The agent's final output (unused — Harbor
                grades the working tree, not the transcript).
            env: Execution environment holding the agent's working tree.

        Returns:
            ``True`` iff the verifier script exits 0.
        """
        if env is None:
            return False
        if not (hasattr(env, "write_file") and hasattr(env, "run_command")):
            return False

        task_dir = Path(task.get("task_dir", ""))
        test_patch = task_dir / "tests" / "test.patch"
        test_sh = task_dir / "tests" / "test.sh"
        if not test_sh.is_file():
            return False
        timeout = int(float(task.get("verifier_timeout_sec", 1800.0)))

        try:
            if test_patch.is_file():
                patch_text = test_patch.read_text(encoding="utf-8")
                if patch_text.strip():
                    env.write_file("_harbor_test.patch", patch_text)
                    applied = env.run_command("git apply _harbor_test.patch")
                    if not applied.success:
                        return False

            env.write_file("_harbor_test.sh", test_sh.read_text(encoding="utf-8"))
            result = env.run_command("bash _harbor_test.sh", timeout=timeout)
            return bool(result.success)
        except Exception:
            return False

    @property
    def harbor_tasks(self) -> list[HarborTask]:
        """The parsed :class:`HarborTask` records (copy)."""
        return list(self._tasks)
