"""ProgramBench: rebuild a working codebase from a binary + docs.

ProgramBench (Yang et al., 2026) flips the SWE-bench paradigm: instead
of patching an existing codebase, the agent receives only a compiled
binary and its documentation and must reconstruct a complete codebase
that reproduces the original program's behavior. Grading is via the
upstream ``programbench eval`` CLI, which runs the agent's
submission.tar.gz inside a per-task Docker container and parses pytest
JUnit XML to count passing branches.

This adapter is **orchestration-only**: it builds the run-directory
layout the upstream CLI expects, shells out to ``programbench eval``,
and parses the resulting ``<instance_id>.eval.json`` files. We don't
re-implement the harness.

Refs:
    - HuggingFace: programbench/ProgramBench-Tests
    - GitHub: github.com/SWE-agent/ProgramBench
    - Paper: arXiv:2605.03546 (sic — labelled 2026)

References:
    The upstream tool requires Docker images built for ``linux/amd64``.
    On other platforms the adapter raises :class:`BenchmarkSkipped`
    rather than attempting a slow QEMU run.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


class BenchmarkSkipped(Exception):
    """Raised when the benchmark cannot run on the current host.

    Reasons include: Docker not installed, Docker daemon not reachable,
    or the host is not ``linux/amd64`` (ProgramBench images are
    x86_64-only).
    """


@dataclass
class ProgramBenchInstance:
    """A single ProgramBench task instance.

    Attributes:
        instance_id: Upstream identifier of the form
            ``<owner>__<repo>.<short_sha>``
            (e.g. ``"abishekvashok__cmatrix.5c082c6"``).
        repo: ``"<owner>/<repo>"`` slug.
        commit: Full commit SHA.
        language: Source language label from the upstream task.yaml
            (``c``, ``cpp``, ``rust``, ``go``, ``python``, ...).
        difficulty: ``easy`` / ``medium`` / ``hard``.
        eval_clean_hashes: Reference build hashes (used for executable
            hash sanity checks; we don't enforce, just record).
    """

    instance_id: str
    repo: str
    commit: str
    language: str = ""
    difficulty: str = ""
    eval_clean_hashes: list[str] = field(default_factory=list)

    def short_sha(self) -> str:
        """Return the short SHA portion of the instance id."""
        if "." in self.instance_id:
            return self.instance_id.rsplit(".", 1)[-1]
        return self.commit[:7]

    def cleanroom_image(self, tag: str = "task_cleanroom") -> str:
        """Return the Docker image to run the agent inside.

        The upstream naming convention replaces ``__`` with ``_1776_``
        in the image name. So ``abishekvashok__cmatrix.5c082c6`` →
        ``programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom``.
        """
        image_id = self.instance_id.replace("__", "_1776_")
        return f"programbench/{image_id}:{tag}"

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": (
                "Rebuild the program documented in this task. The compiled "
                "binary and documentation are mounted in the cleanroom "
                "container; produce a working source tree that reproduces "
                "the binary's behaviour."
            ),
            "instance_id": self.instance_id,
            "repo": self.repo,
            "commit": self.commit,
            "language": self.language,
            "difficulty": self.difficulty,
            "eval_clean_hashes": list(self.eval_clean_hashes),
            "cleanroom_image": self.cleanroom_image(),
        }


class ProgramBench(Benchmark):
    """ProgramBench adapter — orchestrates the upstream ``programbench eval``.

    Args:
        dataset_path: Path to a JSON / JSON-lines dump (e.g. an export of
            HuggingFace ``programbench/ProgramBench-Tests``). Mutually
            exclusive with ``tasks_dir``.
        tasks_dir: Path to the upstream ``src/programbench/data/tasks``
            directory. Each subdirectory must contain a ``task.yaml``.
        language: Optional language filter (e.g. ``"c"``, ``"rust"``).
        difficulty: Optional difficulty filter.
        limit: Maximum number of tasks to keep after filtering.
        image_tag: Docker tag to run inference against
            (default ``task_cleanroom``).
        run_dir: Directory used to stage submissions and receive eval
            output. If ``None``, :meth:`evaluate` will refuse to grade
            (no place to drop the tarball).
        programbench_cli: Override the CLI command used to grade
            (default: ``["programbench", "eval"]``).

    Raises:
        ValueError: If both ``dataset_path`` and ``tasks_dir`` are set.
        FileNotFoundError: If a path argument points nowhere.
    """

    DEFAULT_CLI: tuple[str, ...] = ("programbench", "eval")

    def __init__(
        self,
        dataset_path: str | None = None,
        tasks_dir: str | None = None,
        language: str | None = None,
        difficulty: str | None = None,
        limit: int | None = None,
        image_tag: str = "task_cleanroom",
        run_dir: str | None = None,
        programbench_cli: tuple[str, ...] | None = None,
    ) -> None:
        if dataset_path and tasks_dir:
            raise ValueError(
                "Provide dataset_path or tasks_dir, not both."
            )
        self._dataset_path = dataset_path
        self._tasks_dir = tasks_dir
        self._language = language
        self._difficulty = difficulty
        self._limit = limit
        self._image_tag = image_tag
        self._run_dir = Path(run_dir) if run_dir else None
        self._cli = programbench_cli or self.DEFAULT_CLI
        self._instances: list[ProgramBenchInstance] = []
        self._cached_tasks: list[dict[str, Any]] | None = None

        if dataset_path:
            self._load_json(dataset_path)
        elif tasks_dir:
            self._load_tasks_dir(tasks_dir)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load_json(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        text = data_path.read_text()
        try:
            items = json.loads(text)
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if isinstance(items, dict) and "instances" in items:
                items = items["instances"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for raw_line in text.strip().splitlines():
                line = raw_line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            self._add(self._parse_item(item))
        self._apply_filters()

    def _load_tasks_dir(self, path: str) -> None:
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"Tasks directory not found: {path}")
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - pyyaml is a hard dep
            raise RuntimeError(
                "PyYAML is required to load ProgramBench tasks from a "
                "directory. Install with `uv pip install pyyaml`."
            ) from exc

        for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            yaml_path = task_dir / "task.yaml"
            if not yaml_path.exists():
                continue
            with open(yaml_path) as f:
                meta = yaml.safe_load(f) or {}
            self._add(
                ProgramBenchInstance(
                    instance_id=task_dir.name,
                    repo=meta.get("repository", ""),
                    commit=meta.get("commit", ""),
                    language=meta.get("language", ""),
                    difficulty=meta.get("difficulty", ""),
                    eval_clean_hashes=list(
                        meta.get("eval_clean_hashes", []) or []
                    ),
                )
            )
        self._apply_filters()

    @staticmethod
    def _parse_item(item: dict[str, Any]) -> ProgramBenchInstance:
        instance_id = item.get("instance_id") or item.get("id") or ""
        return ProgramBenchInstance(
            instance_id=instance_id,
            repo=item.get("repo", item.get("repository", "")),
            commit=item.get("commit", item.get("base_commit", "")),
            language=item.get("language", ""),
            difficulty=item.get("difficulty", ""),
            eval_clean_hashes=list(item.get("eval_clean_hashes", []) or []),
        )

    def _add(self, instance: ProgramBenchInstance) -> None:
        if self._language and instance.language != self._language:
            return
        if self._difficulty and instance.difficulty != self._difficulty:
            return
        self._instances.append(instance)

    def _apply_filters(self) -> None:
        if self._limit:
            self._instances = self._instances[: self._limit]
        self._cached_tasks = None

    # ------------------------------------------------------------------
    # Benchmark interface
    # ------------------------------------------------------------------
    def name(self) -> str:
        suffix = ""
        if self._language:
            suffix += f"-{self._language}"
        if self._difficulty:
            suffix += f"-{self._difficulty}"
        return f"programbench{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [i.to_task() for i in self._instances]
        return self._cached_tasks

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any = None
    ) -> bool:
        """Grade a single task by invoking the upstream CLI.

        ``agent_output`` is the path to the ``submission.tar.gz`` the
        agent produced. The adapter copies it under
        ``run_dir/<instance_id>/submission.tar.gz``, runs
        ``programbench eval <run_dir>``, and parses the resulting
        ``<instance_id>.eval.json``.

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            agent_output: Path to the agent's submission tarball.
            env: Unused — grading happens in the upstream CLI's own
                Docker pool, not in a Chimera-managed environment.

        Returns:
            ``True`` iff every test in every active branch passes.

        Raises:
            BenchmarkSkipped: If Docker is unavailable, the host is not
                ``linux/amd64``, or no ``run_dir`` was configured.
        """
        check_runtime_or_skip()
        if self._run_dir is None:
            raise BenchmarkSkipped(
                "ProgramBench.evaluate requires run_dir to stage the "
                "submission tarball. Pass run_dir=... to the constructor."
            )

        instance_id = task.get("instance_id") or task.get("id", "")
        if not instance_id:
            return False

        target_dir = self._run_dir / instance_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_tar = target_dir / "submission.tar.gz"

        src = Path(agent_output)
        if not src.exists():
            raise BenchmarkSkipped(
                f"Agent submission not found: {agent_output}"
            )
        if src.resolve() != target_tar.resolve():
            shutil.copy2(src, target_tar)

        eval_json = target_dir / f"{instance_id}.eval.json"
        if eval_json.exists():
            eval_json.unlink()

        cmd = [
            *self._cli,
            str(self._run_dir),
            "--filter",
            f"^{instance_id}$",
            "--image-tag",
            self._image_tag.replace("_cleanroom", ""),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise BenchmarkSkipped(
                f"programbench CLI not found on PATH: {self._cli[0]}"
            ) from exc
        except subprocess.CalledProcessError:
            return False

        if not eval_json.exists():
            return False
        summary = parse_eval_json(eval_json)
        return bool(summary["total"] > 0 and summary["passed"] == summary["total"])

    # ------------------------------------------------------------------
    # Diagnostics & helpers
    # ------------------------------------------------------------------
    @property
    def instances(self) -> list[ProgramBenchInstance]:
        return list(self._instances)

    def add_instance(self, instance: ProgramBenchInstance) -> None:
        """Add an instance programmatically (useful for tests)."""
        self._instances.append(instance)
        self._cached_tasks = None

    def language_breakdown(self) -> dict[str, int]:
        """Count loaded instances per language."""
        out: dict[str, int] = {}
        for inst in self._instances:
            out[inst.language] = out.get(inst.language, 0) + 1
        return out

    def difficulty_breakdown(self) -> dict[str, int]:
        """Count loaded instances per difficulty."""
        out: dict[str, int] = {}
        for inst in self._instances:
            out[inst.difficulty] = out.get(inst.difficulty, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Module-level helpers — exposed for testing and reuse
# ---------------------------------------------------------------------------


def docker_available() -> bool:
    """Return ``True`` if the ``docker`` CLI is on PATH and responsive."""
    docker_path = shutil.which("docker")
    if not docker_path:
        return False
    try:
        result = subprocess.run(
            [docker_path, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def is_linux_amd64() -> bool:
    """Return ``True`` on linux/amd64 hosts (where the images run native)."""
    if platform.system().lower() != "linux":
        return False
    machine = platform.machine().lower()
    return machine in {"x86_64", "amd64"}


def check_runtime_or_skip() -> None:
    """Raise :class:`BenchmarkSkipped` if the host can't run ProgramBench.

    The check is permissive: setting ``CHIMERA_PROGRAMBENCH_LIVE=1`` in
    the environment forces the runtime check to pass so callers can opt
    into a slow QEMU run (the upstream docs warn against this but don't
    forbid it).
    """
    if os.environ.get("CHIMERA_PROGRAMBENCH_LIVE", "") == "1":
        return
    if not docker_available():
        raise BenchmarkSkipped("Docker daemon not reachable.")
    if not is_linux_amd64():
        raise BenchmarkSkipped(
            "ProgramBench images require linux/amd64; "
            "set CHIMERA_PROGRAMBENCH_LIVE=1 to attempt a QEMU run anyway."
        )


def parse_eval_json(path: Path | str) -> dict[str, Any]:
    """Parse an upstream ``<instance_id>.eval.json`` and return a summary.

    Returns a dict with keys:
        - ``passed`` (int): number of test results with status ``passed``.
        - ``total`` (int): total number of test results.
        - ``branches`` (int): number of test branches.
        - ``error_code`` (str | None): top-level error code, if any.
        - ``warnings`` (list[str]): top-level warnings.

    Args:
        path: Path to the ``<instance_id>.eval.json`` file.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"eval.json not found: {p}")
    data = json.loads(p.read_text())
    test_results = data.get("test_results") or []
    passed = sum(1 for t in test_results if t.get("status") == "passed")
    return {
        "passed": passed,
        "total": len(test_results),
        "branches": len(data.get("test_branches") or []),
        "error_code": data.get("error_code"),
        "warnings": list(data.get("warnings") or []),
    }


__all__ = [
    "BenchmarkSkipped",
    "ProgramBench",
    "ProgramBenchInstance",
    "check_runtime_or_skip",
    "docker_available",
    "is_linux_amd64",
    "parse_eval_json",
]
