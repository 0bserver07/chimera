"""General Aider Polyglot benchmark adapter.

The Aider Polyglot benchmark (`github.com/Aider-AI/polyglot-benchmark`)
is a multi-language coding benchmark drawn from Exercism exercises
across six target languages: Python, JavaScript, Rust, Go, Java, and
C++. Each task ships a stub file plus a read-only test file; the agent
is asked to fill in the stub.

This adapter is the **general** flavour usable by every Chimera CLI
(otter / weasel / shrew / mink / ferret). The shrew flavour at
:mod:`chimera.shrew.benchmarks.aider_polyglot` is a thin wrapper that
defaults to a tighter step budget and the small-model-friendly subset.

Grading:

* **diff-match** — when the staged task ships an ``expected_files``
  dict, every expected file is compared to the agent's working-copy
  contents byte-for-byte (after rstrip).
* **test-pass** — when the staged task ships a shell command in
  ``test_command``, we ``subprocess.run`` it from the staged exercise
  copy and pass on exit code 0.

Both modes coexist on a per-task basis; ``evaluate()`` falls back to
``test_command`` only when no ``expected_files`` are present.

Dataset layout (we deliberately do **not** vendor upstream — license is
a mix of Exercism + per-exercise contributions):

    ~/.chimera/datasets/aider-polyglot/
        tasks.json                     # list of task dicts (see schema)
        exercises/<id>/                # optional staged exercise tree
            stub.<ext>
            <id>_test.<ext>
            ...

Override the root via ``CHIMERA_AIDER_POLYGLOT_PATH=/abs/path``.

Per-task schema (one entry of ``tasks.json``):

    {
      "id": "python/hello-world",     # required, used as task_id
      "language": "python",            # one of the 6 supported languages
      "prompt": "Implement ...",       # required; agent prompt
      "expected_files": {              # optional; diff-match mode
        "hello_world.py": "def hello():\n    return 'Hello, World!'\n"
      },
      "test_command": "pytest -x -q",  # optional; test-pass mode
      "exercise_dir": "hello-world",   # optional; subdir under exercises/
      "timeout_s": 90                  # optional; default 90s for tests
    }
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
    "AiderPolyglot",
    "DEFAULT_DATASET_DIR",
    "ENV_DATASET_PATH",
    "SUPPORTED_LANGUAGES",
    "default_dataset_path",
    "dataset_available",
    "setup_hint",
]


DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/aider-polyglot"
"""Default on-disk location for the staged Aider Polyglot dataset."""

ENV_DATASET_PATH = "CHIMERA_AIDER_POLYGLOT_PATH"
"""Environment variable that overrides :data:`DEFAULT_DATASET_DIR`."""

SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "rust",
    "go",
    "java",
    "cpp",
)
"""The six languages covered by Aider Polyglot upstream."""

_DEFAULT_TEST_TIMEOUT_S = 90
"""Per-task subprocess timeout when ``test_command`` mode is in play."""


def default_dataset_path() -> Path:
    """Return the resolved Aider Polyglot dataset root.

    Reads :data:`ENV_DATASET_PATH` when set; otherwise falls back to
    :data:`DEFAULT_DATASET_DIR` expanded against the user's home dir.
    The path may or may not exist — callers should pre-flight with
    :func:`dataset_available` for a friendly skip.

    Returns:
        Absolute :class:`Path` to the dataset root (existence not
        guaranteed).
    """
    raw = os.environ.get(ENV_DATASET_PATH)
    if raw:
        return Path(raw).expanduser()
    return store_path("datasets") / "aider-polyglot"


def dataset_available(path: Path | None = None) -> bool:
    """Return ``True`` when a usable Aider Polyglot dataset is staged.

    A dataset is considered available when ``<root>/tasks.json`` exists
    and is non-empty. We deliberately don't validate the JSON shape here
    — the adapter's ``_load_tasks`` will surface schema issues with a
    clearer error message.

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

    Used by CLIs when the dataset is missing. Mirrors the friendly
    setup hints used by other Chimera benchmark adapters (tau-bench,
    SWE-bench).

    Args:
        path: Optional dataset root to embed in the hint. When ``None``,
            uses :func:`default_dataset_path`.

    Returns:
        A user-facing string with staging steps and the env-var override.
    """
    resolved = path or default_dataset_path()
    return (
        "Aider Polyglot dataset not staged.\n"
        f"  expected dir:  {resolved}\n"
        "  expected file: tasks.json (list of {id, language, prompt, ...})\n"
        f"  override:      {ENV_DATASET_PATH}=/abs/path/to/dir\n"
        "  setup:\n"
        "    1. Clone github.com/Aider-AI/polyglot-benchmark locally.\n"
        "    2. Author tasks.json — see chimera/eval/benchmarks/\n"
        "       aider_polyglot.py docstring for the schema.\n"
        "    3. Optionally stage exercise trees under exercises/<id>/.\n"
        "  note: we do NOT vendor upstream — licenses are mixed."
    )


class AiderPolyglot(Benchmark):
    """Aider Polyglot adapter for the standard :class:`Benchmark` ABC.

    Loads multi-language code-edit tasks from a staged JSON dump. Two
    grading modes coexist on a per-task basis (diff-match takes
    precedence over test-pass).

    Args:
        dataset_path: Optional path to a directory containing
            ``tasks.json`` and an optional ``exercises/`` subtree. When
            ``None``, :func:`default_dataset_path` is used.
        limit: Optional cap on the number of tasks returned. ``None`` /
            non-positive means no cap.
        languages: Optional list of language filters — when set, only
            tasks whose ``language`` field matches (case-insensitive)
            any of the provided languages are returned. Each entry must
            be one of :data:`SUPPORTED_LANGUAGES`. ``None`` means no
            filter (all languages).
        language: Optional single-language filter (back-compat). When
            *both* ``languages`` and ``language`` are given,
            ``languages`` wins. The single-language form is preserved
            so the older ``AiderPolyglot(language="python")`` call site
            keeps working.

    Attributes:
        languages: The active language filter list, or ``None``.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        languages: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit if (limit is None or limit > 0) else None
        # Resolve filter: list-form wins over single-form.
        chosen: list[str] | None = None
        if languages:
            chosen = [str(lang).lower() for lang in languages]
        elif language:
            chosen = [language.lower()]
        self.languages: list[str] | None = chosen
        self._tasks: list[dict[str, Any]] | None = None

    @property
    def language(self) -> str | None:
        """Back-compat single-language accessor.

        Returns the first entry of :attr:`languages` when exactly one
        language is set; ``None`` when the filter is unset or covers
        multiple languages.
        """
        if self.languages and len(self.languages) == 1:
            return self.languages[0]
        return None

    def name(self) -> str:
        """Return the benchmark identifier (suffixed with the filter)."""
        if not self.languages:
            return "aider-polyglot"
        if len(self.languages) == 1:
            return f"aider-polyglot:{self.languages[0]}"
        return "aider-polyglot:" + "+".join(sorted(self.languages))

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks (cached after first call).

        Returns an empty list when the dataset is missing — callers
        should pre-flight with :func:`dataset_available` for a friendly
        skip path with setup instructions.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Score a single task.

        Strategy (in order):

        1. ``expected_files`` present → diff-match every file. The file
           contents come from ``env.read_file()`` when an environment is
           available; otherwise we fall back to scanning ``agent_output``
           for a fenced code block whose content matches.
        2. ``test_command`` present → run it via :mod:`subprocess` in the
           staged exercise directory, pass on exit code 0.
        3. Neither present → return ``False`` (under-specified task).

        Args:
            task: One entry from :meth:`tasks`.
            agent_output: Final assistant text from the agent run.
            env: Optional :class:`~chimera.env.base.Environment`.

        Returns:
            Whether the task passed.
        """
        expected = task.get("expected_files") or {}
        if expected:
            return self._evaluate_diff(task, agent_output, env, expected)

        test_cmd = task.get("test_command")
        if test_cmd:
            return self._evaluate_test_command(task, env, str(test_cmd))

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        """Return the resolved dataset root (no existence check)."""
        if self._dataset_path:
            return Path(self._dataset_path).expanduser()
        return default_dataset_path()

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Read ``tasks.json``, apply language filter + limit.

        Returns an empty list (rather than raising) when the dataset
        directory or ``tasks.json`` is missing — keeps the skip path
        friendly for ``--limit 1`` smoke runs on an unset machine.
        """
        root = self._root()
        manifest = root / "tasks.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        raw_tasks = data if isinstance(data, list) else data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            return []
        # Drop any non-dict entries up-front so downstream code is safe.
        tasks: list[dict[str, Any]] = [t for t in raw_tasks if isinstance(t, dict)]
        if self.languages:
            wanted = set(self.languages)
            tasks = [
                t for t in tasks if str(t.get("language", "")).lower() in wanted
            ]
        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _evaluate_diff(
        self,
        task: dict[str, Any],
        agent_output: str,
        env: Any,
        expected: dict[str, Any],
    ) -> bool:
        """Compare every entry of ``expected_files`` to the agent's output.

        Resolution order for each file's "actual" contents:

        1. ``env.read_file(path)`` if *env* is non-None and exposes
           :meth:`read_file`.
        2. The file at ``<env.workdir>/path`` when *env* exposes
           ``workdir``.
        3. The agent_output itself (best-effort: most polyglot tasks
           produce a single file and the agent's reply is its body).

        We strip trailing whitespace before comparison so a missing
        terminal newline doesn't fail an otherwise-correct solution.
        """
        for rel_path, gold in expected.items():
            actual = self._read_file(env, rel_path)
            if actual is None:
                actual = self._extract_from_output(agent_output)
            if actual is None:
                return False
            if actual.rstrip() != str(gold).rstrip():
                return False
        return True

    def _evaluate_test_command(
        self,
        task: dict[str, Any],
        env: Any,
        test_command: str,
    ) -> bool:
        """Run ``test_command`` and pass on exit code 0.

        Working directory resolution:

        1. ``env.workdir`` if available.
        2. ``<dataset_root>/exercises/<exercise_dir>/`` if
           ``task["exercise_dir"]`` is set.
        3. The current working directory (last resort — most CI setups
           will have staged the exercise tree before invoking).
        """
        timeout = int(task.get("timeout_s") or _DEFAULT_TEST_TIMEOUT_S)
        cwd = self._resolve_cwd(task, env)
        try:
            result = subprocess.run(  # noqa: S603 — user-provided cmd
                test_command,
                shell=True,  # noqa: S602 — same; shell metachars expected
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0

    def _resolve_cwd(self, task: dict[str, Any], env: Any) -> Path:
        """Pick the directory to run a test command from."""
        workdir = getattr(env, "workdir", None) if env is not None else None
        if workdir:
            return Path(workdir)
        ex_dir = task.get("exercise_dir")
        if ex_dir:
            staged = self._root() / "exercises" / str(ex_dir)
            if staged.is_dir():
                return staged
        return Path.cwd()

    @staticmethod
    def _read_file(env: Any, rel_path: str) -> str | None:
        """Read ``rel_path`` via the env, or its workdir, or return ``None``."""
        if env is None:
            return None
        reader = getattr(env, "read_file", None)
        if callable(reader):
            try:
                return str(reader(rel_path))
            except (OSError, FileNotFoundError):
                return None
        workdir = getattr(env, "workdir", None)
        if workdir:
            full = Path(workdir) / rel_path
            if full.is_file():
                try:
                    return full.read_text()
                except OSError:
                    return None
        return None

    @staticmethod
    def _extract_from_output(text: str) -> str | None:
        """Extract a single fenced code block from ``text``, or return raw.

        When the agent emits ``` ```python ... ``` ``` we strip the
        fence and return the body. When no fence is present we return
        the whole text — the caller will diff-match against gold and
        either succeed (single-file solutions) or fail (multi-file).
        """
        if not text:
            return None
        if "```" not in text:
            return text
        lines = text.splitlines()
        body: list[str] = []
        inside = False
        for line in lines:
            if line.lstrip().startswith("```"):
                if inside:
                    break
                inside = True
                continue
            if inside:
                body.append(line)
        if not body:
            return text
        return "\n".join(body)
