"""BigCodeBench adapter — practical Python coding with library calls.

BigCodeBench (Zhuo et al., 2024) evaluates code generation on 1,140 practical
programming tasks that exercise calls into common Python libraries (numpy,
pandas, requests, scikit-learn, ...). Compared to HumanEval/MBPP, BCB tasks
require reaching into the standard library and the broader scientific Python
stack rather than implementing pure-Python algorithms in isolation.

The benchmark ships in two variants:

* ``complete``    — the prompt is a partial function (signature + docstring +
                    helper imports) and the model fills in the body.
* ``instruct``    — the prompt is a natural-language description of the task;
                    the model emits the full function from scratch.

Each task carries:

* ``task_id``        — e.g. ``BigCodeBench/0``
* ``complete_prompt``— prompt for the ``complete`` split
* ``instruct_prompt``— prompt for the ``instruct`` split
* ``code_prompt``    — function signature + imports (``complete`` skeleton)
* ``test``           — Python test code defining ``check(<entry_point>)`` or
                       a ``unittest.TestCase`` subclass
* ``entry_point``    — name of the function under test
* ``libs``           — list of third-party libraries the task depends on

This adapter normalises tasks to the standard
:class:`~chimera.eval.harness.Benchmark` shape so they can be driven by
:class:`~chimera.eval.harness.Harness`. We do **not** vendor or pip-install
the upstream dataset. The loader auto-detects a local copy under
``~/.chimera/datasets/bigcodebench/`` (override via
``CHIMERA_BIGCODEBENCH_PATH``); when no dataset is staged ``tasks()`` returns
``[]`` so callers can pre-flight via :func:`dataset_available`.

Reference:
    - Paper:  https://arxiv.org/abs/2406.15877
    - Source: https://github.com/bigcode-project/bigcodebench
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any, Literal

from chimera.eval.harness import Benchmark
from chimera.config.paths import STATE_DIRNAME, store_path

VALID_SPLITS = ("complete", "instruct")
DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/bigcodebench"
ENV_DATASET_PATH = "CHIMERA_BIGCODEBENCH_PATH"


def default_dataset_path() -> Path:
    """Return the resolved BigCodeBench dataset path.

    Reads :envvar:`CHIMERA_BIGCODEBENCH_PATH` when set; otherwise falls
    back to ``~/.chimera/datasets/bigcodebench/``. The path may or may
    not exist on disk; callers are expected to check.
    """
    raw = os.environ.get(ENV_DATASET_PATH)
    if raw:
        return Path(raw).expanduser()
    return store_path("datasets") / "bigcodebench"


def dataset_available(path: Path | None = None) -> bool:
    """Return ``True`` when a usable BigCodeBench dataset is staged.

    Accepts either:

    * a directory containing ``*.json`` / ``*.jsonl`` task dumps, or
    * a single ``.json`` / ``.jsonl`` file.
    """
    base = path or default_dataset_path()
    if not base.exists():
        return False
    if base.is_file():
        return base.suffix in (".json", ".jsonl")
    if base.is_dir():
        return any(base.glob("*.json")) or any(base.glob("*.jsonl"))
    return False


def _strip_code_fences(text: str) -> str:
    """Extract Python source from a markdown-fenced agent reply.

    Mirrors :func:`chimera.otter.benchmarks._strip_code_fences` so the
    fence-stripping behaviour is identical across HumanEval and BCB
    runs. When *text* contains no ``` fences it is returned unchanged.
    """
    if "```" not in text:
        return text
    parts: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            inside = not inside
            continue
        if inside:
            parts.append(line)
    if not parts:
        return text
    return "\n".join(parts)


class BigCodeBench(Benchmark):
    """BigCodeBench adapter — 1,140 practical Python tasks with library calls.

    Args:
        split: ``"instruct"`` (default) for natural-language prompts, or
            ``"complete"`` for fill-in-the-blank prompts where the agent
            completes a partial function.
        dataset_path: Optional path to a local JSON / JSONL dump **or** a
            directory of ``*.json`` / ``*.jsonl`` files. When ``None``,
            :func:`default_dataset_path` is consulted.
        limit: Optional cap on the number of tasks returned.

    Attributes:
        split: The selected prompt split.
    """

    def __init__(
        self,
        split: Literal["complete", "instruct"] = "instruct",
        dataset_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        if split not in VALID_SPLITS:
            raise ValueError(
                f"split must be one of {VALID_SPLITS}, got {split!r}"
            )
        self.split: Literal["complete", "instruct"] = split
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------ Benchmark API

    def name(self) -> str:
        return f"bigcodebench-{self.split}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Execute the task's ``test`` against the agent's emitted code.

        Mirrors the otter-side ``_OtterHumanEval`` patched-evaluate
        pattern (see :file:`research/otter/B1-RESULTS.md`):

        1. Strip markdown code fences from *agent_output*.
        2. Combine the cleaned solution with the task's ``test`` code.
        3. If the combined source already references the entry point in
           a top-level ``check(<entry_point>)`` call, leave it alone.
           Otherwise inject one when the test exposes a free-standing
           ``check`` function. ``unittest.TestCase`` subclasses are run
           via :class:`unittest.TextTestRunner`.
        4. Execute via *env* when supplied (writes ``solution.py`` and
           shells out to ``python solution.py``); otherwise fall back to
           in-process :func:`exec` with a unittest harness when needed.

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: Raw agent output (may include markdown fences).
            env: Optional :class:`~chimera.env.base.Environment`.

        Returns:
            ``True`` iff the test code raises no exceptions / all
            unittest cases pass.
        """
        test_code = task.get("test", "")
        if not test_code:
            return False

        cleaned = _strip_code_fences(agent_output)
        entry_point = task.get("entry_point", "")
        full_code = self._compose_runner(cleaned, test_code, entry_point)

        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return bool(result.exit_code == 0)

        return self._exec_in_process(full_code)

    # ------------------------------------------------------------------ Internals

    def _compose_runner(
        self, solution: str, test_code: str, entry_point: str
    ) -> str:
        """Combine solution + test into a runnable script.

        Two test shapes are supported:

        * **Free function ``check``** — append ``check(<entry_point>)``
          when *entry_point* is non-empty and the call is not already
          present in the test source.
        * **``unittest.TestCase``** — append a ``unittest.main()`` call
          guarded by ``exit=False`` so the script terminates cleanly
          regardless of platform.
        """
        runner = ""
        if "unittest.TestCase" in test_code or "unittest.main" in test_code:
            if "unittest.main" not in test_code:
                runner = (
                    "\n\nif __name__ == '__main__':\n"
                    "    import unittest\n"
                    "    unittest.main(exit=False)\n"
                )
        elif entry_point and f"check({entry_point}" not in test_code:
            runner = f"\n\ncheck({entry_point})\n"
        return f"{solution}\n\n{test_code}{runner}"

    def _exec_in_process(self, full_code: str) -> bool:
        """In-process evaluation fallback.

        Raises caught: any :class:`Exception` (including ``AssertionError``
        and ``unittest.TestCase`` failures surfaced via ``failfast``) maps
        to ``False``.
        """
        import sys as _sys

        ns: dict[str, Any] = {"__name__": "__main__", "unittest": unittest}
        # WHY: the assembled program may include ``unittest.main(exit=False)``
        # for ``unittest.TestCase`` shapes. ``unittest.main`` parses
        # ``sys.argv`` and aborts via ``SystemExit`` when run under pytest
        # (which passes flags like ``-x``, ``-q``). Stub argv to a single
        # program name during exec so the runner sees no flags.
        _saved_argv = _sys.argv
        _sys.argv = [_saved_argv[0] if _saved_argv else "bigcodebench"]
        try:
            try:
                exec(full_code, ns)  # noqa: S102
            except Exception:
                return False
        finally:
            _sys.argv = _saved_argv
        # If a unittest.TestCase subclass was defined, run it explicitly
        # and inspect the result rather than relying on unittest.main()
        # (which calls sys.exit() under some entry points).
        cases = [
            v
            for v in ns.values()
            if isinstance(v, type)
            and issubclass(v, unittest.TestCase)
            and v is not unittest.TestCase
        ]
        if not cases:
            return True
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for case in cases:
            suite.addTests(loader.loadTestsFromTestCase(case))
        runner = unittest.TextTestRunner(stream=_NullStream(), verbosity=0)
        return runner.run(suite).wasSuccessful()

    def _load_tasks(self) -> list[dict[str, Any]]:
        raw = self._load_raw()
        normalised = [self._normalise(t) for t in raw if t]
        if self._limit is not None:
            normalised = normalised[: self._limit]
        return normalised

    def _load_raw(self) -> list[dict[str, Any]]:
        path_str = self._dataset_path
        path = Path(path_str).expanduser() if path_str else default_dataset_path()
        if not path.exists():
            return []
        if path.is_file():
            return self._read_one(path)
        out: list[dict[str, Any]] = []
        for child in sorted(path.iterdir()):
            if child.suffix in (".json", ".jsonl"):
                out.extend(self._read_one(child))
        return out

    @staticmethod
    def _read_one(path: Path) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            return [
                json.loads(line) for line in text.splitlines() if line.strip()
            ]
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            tasks_field = data.get("tasks")
            if isinstance(tasks_field, list):
                return tasks_field
            # HuggingFace-style dict of task_id -> task
            return [
                {**v, "task_id": v.get("task_id", k)}
                for k, v in data.items()
                if isinstance(v, dict)
            ]
        return []

    def _normalise(self, task: dict[str, Any]) -> dict[str, Any]:
        """Normalise a raw BCB record into the harness task shape.

        Builds the agent-facing ``prompt`` from the split-appropriate BCB
        field, with falls-through to neighbouring fields when the
        canonical one is missing. Preserves all original keys for
        downstream evaluation.
        """
        task_id = task.get("task_id") or task.get("id") or ""
        prompt = self._select_prompt(task)
        out: dict[str, Any] = dict(task)
        out["id"] = task_id
        out["task_id"] = task_id
        out["prompt"] = prompt
        return out

    def _select_prompt(self, task: dict[str, Any]) -> str:
        if self.split == "instruct":
            primary = task.get("instruct_prompt") or task.get("instruction")
            fallback = (
                task.get("complete_prompt")
                or task.get("code_prompt")
                or task.get("prompt", "")
            )
        else:
            primary = task.get("complete_prompt") or task.get("code_prompt")
            fallback = task.get("instruct_prompt") or task.get("prompt", "")
        return primary or fallback or ""


class _NullStream:
    """Discard sink for :class:`unittest.TextTestRunner` output."""

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None
