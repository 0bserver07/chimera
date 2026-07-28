"""Terminal-Bench adapter for ``chimera shrew bench terminal-bench``.

Terminal-Bench is a suite of command-line tasks where the agent is
given a short instruction (set up a tool, fix a config, write a
shell pipeline) inside a fresh working directory. Tasks are scored by
running a per-task **verify** shell command after the agent has
finished — exit code ``0`` means pass.

This adapter is the **shrew flavour**: stdlib-only, dataset-staged-or-
skip, and built on the standard :class:`chimera.eval.harness.Benchmark`
ABC so the same harness drives all four shrew benchmarks. We
deliberately do not depend on the upstream `terminal-bench` Python
package (which pulls Docker and asciinema); shrew's flavour runs the
verify command directly with :mod:`subprocess` against a per-task
working tree.

Naming: ``Terminal-Bench`` is a third-party benchmark name. Like GAIA
/ Aider Polyglot / Harbor, it is **not** the upstream small-model
coding agent brand; naming it directly is fine.

Dataset layout (we deliberately do **not** vendor upstream):

    ~/.chimera/datasets/terminal-bench/
        tasks.json              # list of task dicts (see schema below)
        tasks/<task-id>/        # optional per-task workdir (copied per run)
            ...                 # files referenced by the task instruction

Override the root via ``CHIMERA_TERMINAL_BENCH_PATH=/abs/path``.

Per-task schema (one entry of ``tasks.json``):

    {
      "task_id": "tb-001",                # required, used as task_id
      "instruction": "Find the largest …", # required; the prompt body
      "verify_command": "test -f result.txt && grep -q OK result.txt",
      "task_dir": "tb-001",               # optional subdir under tasks/
      "timeout_s": 60                     # optional; default 60s
    }

``"instruction"`` / ``"prompt"`` and ``"verify_command"`` /
``"verify"`` keys are accepted for upstream-schema flexibility.

Scoring: the agent's ``output`` is *not* parsed. After the agent
finishes, the verify command runs (in the per-task working dir if
staged, otherwise in ``env.workdir``, otherwise ``cwd``). Pass on
exit code 0; fail on non-zero, timeout, or OS error.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark
from chimera.config.paths import STATE_DIRNAME, store_path

__all__ = [
    "TerminalBench",
    "DEFAULT_DATASET_DIR",
    "ENV_DATASET_PATH",
    "default_dataset_path",
    "dataset_available",
    "setup_hint",
]


DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/terminal-bench"
"""Default on-disk location for the staged Terminal-Bench dataset."""

ENV_DATASET_PATH = "CHIMERA_TERMINAL_BENCH_PATH"
"""Environment variable that overrides :data:`DEFAULT_DATASET_DIR`."""

_DEFAULT_VERIFY_TIMEOUT_S = 60
"""Per-task verify subprocess timeout (default 60s)."""


def default_dataset_path() -> Path:
    """Return the resolved Terminal-Bench dataset root.

    Reads :data:`ENV_DATASET_PATH` when set; otherwise falls back to
    :data:`DEFAULT_DATASET_DIR` expanded against the user's home dir.

    Returns:
        Absolute :class:`Path` to the dataset root (existence not
        guaranteed).
    """
    raw = os.environ.get(ENV_DATASET_PATH)
    if raw:
        return Path(raw).expanduser()
    return store_path("datasets") / "terminal-bench"


def dataset_available(path: Path | None = None) -> bool:
    """Return ``True`` when a usable Terminal-Bench dataset is staged.

    A dataset is considered available when ``<root>/tasks.json`` exists.

    Args:
        path: Optional dataset root override. When ``None``, the resolved
            :func:`default_dataset_path` is used.

    Returns:
        Whether a ``tasks.json`` file is present at the expected path.
    """
    base = path or default_dataset_path()
    return (base / "tasks.json").is_file()


def setup_hint(path: Path | None = None) -> str:
    """Return a multiline setup-instructions string for the user.

    Used by the CLI when the dataset is missing.

    Args:
        path: Optional dataset root to embed in the hint. When ``None``,
            uses :func:`default_dataset_path`.

    Returns:
        A user-facing string with staging steps and the env-var override.
    """
    resolved = path or default_dataset_path()
    return (
        "Terminal-Bench dataset not staged.\n"
        f"  expected dir:  {resolved}\n"
        "  expected file: tasks.json (list of {task_id, instruction,"
        " verify_command, ...})\n"
        f"  override:      {ENV_DATASET_PATH}=/abs/path/to/dir\n"
        "  setup:\n"
        "    1. Clone the upstream terminal-bench task corpus locally.\n"
        "    2. Author tasks.json — see chimera/shrew/benchmarks/\n"
        "       terminal_bench.py docstring for the schema.\n"
        "    3. Optionally stage per-task working trees under tasks/.\n"
        "  note: we do NOT vendor upstream — license varies by task."
    )


class TerminalBench(Benchmark):
    """Terminal-Bench adapter for the standard :class:`Benchmark` ABC.

    Loads per-task instructions + verify shell commands from a staged
    JSON dump. Scoring is exit-code-based: after the agent runs, the
    verify command runs in a subprocess and pass = exit code 0.

    Args:
        dataset_path: Optional override for the dataset root directory.
            When ``None``, :func:`default_dataset_path` is used.
        limit: Optional cap on the number of tasks returned. ``None`` /
            non-positive means no cap.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit if (limit is None or limit > 0) else None
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        """Return the benchmark identifier."""
        return "terminal-bench"

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks (cached after first call).

        Each returned dict carries an ``id`` key (from ``task_id``), a
        ``prompt`` key (synthesised from ``instruction``), and the
        original ``verify_command`` for :meth:`evaluate`.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Score one task by running the verify command.

        ``agent_output`` is intentionally ignored: terminal-bench is
        side-effect grading. Pass = verify exits 0; fail = anything else
        (non-zero exit, OS error, or timeout).

        Args:
            task: One entry from :meth:`tasks` (must include
                ``"verify_command"``).
            agent_output: Unused (kept for the :class:`Benchmark` ABC).
            env: Optional environment with a ``workdir`` attribute. Used
                to resolve the verify command's cwd.

        Returns:
            Whether the verify command exits 0.
        """
        verify_cmd = (
            task.get("verify_command")
            or task.get("verify")
            or ""
        )
        if not verify_cmd:
            return False
        timeout = int(task.get("timeout_s") or _DEFAULT_VERIFY_TIMEOUT_S)
        cwd = self._resolve_cwd(task, env)
        try:
            result = subprocess.run(  # noqa: S603 — user-provided cmd
                str(verify_cmd),
                shell=True,  # noqa: S602 — shell metachars expected
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        """Return the resolved dataset root (no existence check)."""
        if self._dataset_path:
            return Path(self._dataset_path).expanduser()
        return default_dataset_path()

    def _resolve_cwd(self, task: dict[str, Any], env: Any) -> Path:
        """Pick the directory the verify command runs from.

        Resolution order:
          1. ``env.workdir`` if set — the agent's actual working tree.
          2. ``<dataset_root>/tasks/<task_dir>/`` if staged.
          3. ``Path.cwd()`` — last resort.
        """
        workdir = getattr(env, "workdir", None) if env is not None else None
        if workdir:
            return Path(workdir)
        td = task.get("task_dir")
        if td:
            staged = self._root() / "tasks" / str(td)
            if staged.is_dir():
                return staged
        return Path.cwd()

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Read ``tasks.json``, apply limit + prompt build."""
        root = self._root()
        manifest = root / "tasks.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        records = data if isinstance(data, list) else data.get("tasks", [])
        if not isinstance(records, list):
            return []

        out: list[dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            out.append(self._normalise_task(rec))

        if self._limit:
            out = out[: self._limit]
        return out

    @staticmethod
    def _normalise_task(rec: dict[str, Any]) -> dict[str, Any]:
        """Add ``id`` / ``prompt`` shims while preserving raw fields.

        The harness expects every task dict to expose ``"id"`` (used as
        ``task_id`` in the result) and ``"prompt"`` (fed to the agent).
        Upstream Terminal-Bench records use ``task_id`` and either
        ``instruction`` or ``prompt``; we accept both.
        """
        body = (
            rec.get("instruction")
            or rec.get("prompt")
            or ""
        )
        task_id = rec.get("task_id") or rec.get("id") or ""
        out = dict(rec)
        out["id"] = task_id
        out["prompt"] = (
            "You are solving a Terminal-Bench command-line task. Use "
            "shell tools to make the verify command pass.\n\n"
            f"Task:\n{body}\n\n"
            "When you believe the task is complete, stop. The grader "
            "will run a verify command in your working directory; you "
            "do not need to run it yourself."
        )
        return out
