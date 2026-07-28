"""MBPP (Mostly Basic Python Problems) benchmark adapter.

Issue: #94. MBPP is a 974-problem code-generation benchmark of crowd-sourced
entry-level Python tasks. Each problem ships a natural-language prompt, a
canonical solution, and a ``test_list`` of 3 ``assert``-style cases. A
hand-verified ``sanitized`` subset of 427 problems is the recommended
evaluation split.

This adapter follows the same shape as :class:`chimera.eval.benchmarks.human_eval.HumanEval`:
the dataset is loaded from a local JSON/JSONL file (the harness is
zero-dependency core, so HuggingFace ``datasets`` is intentionally NOT
imported here). Tests can be executed in-process or against an
``Environment`` via ``run_command``.

Dataset format — both upstream variants are accepted::

    # 1. Original full split (mbpp.jsonl)
    {
        "task_id": 1,
        "text": "Write a function to find the minimum cost path...",
        "code": "def min_cost(...): ...",
        "test_list": ["assert min_cost(...) == 8", ...],
        "test_setup_code": "",
    }

    # 2. Sanitized split (sanitized-mbpp.json — 427 hand-verified records)
    {
        "task_id": 2,
        "source_file": "Benchmark Questions Verification V2.ipynb",
        "prompt": "Write a function to find the shared elements...",
        "code": "def similar_elements(...): ...",
        "test_imports": [],
        "test_list": ["assert set(similar_elements(...)) == set((4, 5))", ...],
    }

Setup
-----

The dataset is **not vendored** in this repo. Stage it once::

    mkdir -p ~/.chimera/datasets/mbpp
    curl -sL -o ~/.chimera/datasets/mbpp/sanitized-mbpp.json \\
        https://raw.githubusercontent.com/google-research/google-research/\\
master/mbpp/sanitized-mbpp.json

The CLI (``python -m chimera.eval.benchmarks.mbpp --limit 3``) then picks
this path up automatically; tests that need the dataset call
:func:`default_dataset_path` and :func:`dataset_available`.

License
-------

Upstream MBPP is published by Google Research under CC-BY-4.0
(see <https://github.com/google-research/google-research/tree/master/mbpp>
and <https://huggingface.co/datasets/google-research-datasets/mbpp>).
The dataset can be redistributed with attribution, but for footprint
reasons we leave staging to the user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark
from chimera.config.paths import store_path


__all__ = [
    "MBPP",
    "default_dataset_path",
    "dataset_available",
    "SETUP_HINT",
]


SETUP_HINT = (
    "MBPP dataset not staged. Run:\n"
    "  mkdir -p ~/.chimera/datasets/mbpp\n"
    "  curl -sL -o ~/.chimera/datasets/mbpp/sanitized-mbpp.json \\\n"
    "    https://raw.githubusercontent.com/google-research/"
    "google-research/master/mbpp/sanitized-mbpp.json\n"
    "Or set CHIMERA_MBPP_PATH=/abs/path/to/file.json[l]\n"
    "Upstream license: CC-BY-4.0 (Google Research)."
)


def default_dataset_path() -> Path:
    """Return the resolved MBPP dataset path.

    Resolution order:

    1. ``$CHIMERA_MBPP_PATH`` (absolute path override).
    2. ``~/.chimera/datasets/mbpp/sanitized-mbpp.json`` — the recommended
       sanitized split (427 hand-verified problems).
    3. ``~/.chimera/datasets/mbpp/mbpp.jsonl`` — the full split fallback.
    """
    env = os.environ.get("CHIMERA_MBPP_PATH")
    if env:
        return Path(env).expanduser()
    base = store_path("datasets") / "mbpp"
    sanitized = base / "sanitized-mbpp.json"
    if sanitized.exists():
        return sanitized
    return base / "mbpp.jsonl"


def dataset_available(path: Path | None = None) -> bool:
    """Return True when the MBPP dataset is staged and readable."""
    resolved = path if path is not None else default_dataset_path()
    return resolved.exists() and resolved.is_file()


class MBPP(Benchmark):
    """MBPP benchmark adapter for basic Python code generation.

    Each task contains a natural-language description and a list of
    ``assert`` test cases. The agent generates a Python function which is
    then executed against the assertions. A task passes only when *all*
    assertions in ``test_list`` pass.

    Args:
        dataset_path: Path to a JSON or JSONL file containing MBPP records.
            When ``None``, ``tasks()`` returns an empty list (useful for
            unit tests and dry-run wiring checks).
        split: Logical split name surfaced via ``name()`` (e.g.
            ``"sanitized"``, ``"test"``). Does not filter records on its own.
        limit: Optional cap on the number of tasks returned.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        split: str = "sanitized",
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._split = split
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return f"mbpp-{self._split}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Execute ``test_list`` assertions against the agent's output.

        Args:
            task: MBPP record with ``test_list`` (list of assert strings)
                and optional ``test_setup_code``.
            agent_output: The candidate function source produced by the
                agent. May include surrounding prose; we treat it as
                executable Python and let parse errors register as a fail.
            env: Optional execution environment. When provided, the
                combined source is written to ``solution.py`` and run via
                ``run_command``. When ``None``, falls back to in-process
                ``exec`` in a fresh namespace.

        Returns:
            ``True`` if every assertion in ``test_list`` passes.
        """
        test_list = task.get("test_list") or []
        if not test_list:
            return False

        # Normalize markdown-fenced answers to bare source (see _code_extract).
        from chimera.eval.benchmarks._code_extract import extract_code

        agent_output = extract_code(agent_output)
        if not agent_output.strip():
            # An errored or empty agent run has no candidate function to test;
            # an empty solution must never grade as a pass (measurement
            # integrity), so fail fast before splicing in the assertions.
            return False

        # The original full split uses a single ``test_setup_code`` string;
        # the sanitized split uses ``test_imports`` (list of import lines).
        # Concatenate both — empty strings/lists are no-ops.
        setup_parts: list[str] = []
        test_imports = task.get("test_imports") or []
        if test_imports:
            setup_parts.extend(test_imports)
        setup_code = task.get("test_setup_code") or ""
        if setup_code:
            setup_parts.append(setup_code)
        setup = "\n".join(setup_parts)

        assertions = "\n".join(test_list)
        full_code = (
            f"{setup}\n{agent_output}\n{assertions}\n"
            if setup
            else f"{agent_output}\n{assertions}\n"
        )

        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return bool(result.exit_code == 0)

        try:
            exec(full_code, {})  # noqa: S102
            return True
        except Exception:
            return False

    def _load_tasks(self) -> list[dict[str, Any]]:
        if not self._dataset_path:
            return []
        path = Path(self._dataset_path)
        text = path.read_text()
        records: list[dict[str, Any]] = []

        # Detect JSONL by file extension first (cheap, unambiguous): the
        # full split ships as ``mbpp.jsonl``. Fall back to JSON-array /
        # ``{"tasks": [...]}`` envelope parsing for the sanitized split
        # and any user-supplied envelope dump.
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            for line in text.splitlines():
                stripped_line = line.strip()
                if not stripped_line:
                    continue
                records.append(json.loads(stripped_line))
        else:
            stripped = text.lstrip()
            if stripped.startswith("[") or stripped.startswith("{"):
                data = json.loads(text)
                records = data if isinstance(data, list) else data.get("tasks", [])
            else:
                # Last-resort: treat as JSONL when the suffix is missing.
                for line in text.splitlines():
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    records.append(json.loads(stripped_line))

        normalized = [self._normalize(r) for r in records]
        if self._limit:
            normalized = normalized[: self._limit]
        return normalized

    @staticmethod
    def _normalize(record: dict[str, Any]) -> dict[str, Any]:
        """Normalize an MBPP record to the harness task shape.

        The harness expects ``id`` and ``prompt`` keys. MBPP records use
        ``task_id`` plus either ``text`` (full split) or ``prompt``
        (sanitized split); we copy across without mutating the original
        so ``test_list`` / ``test_imports`` / ``code`` remain accessible.

        Augments the prompt with the assertion list under a "Your code
        should pass these tests:" header. This matches the standard
        MBPP evaluation protocol (the upstream Google Research notebook
        and the BigCode harness both feed assertions to the model so it
        can infer the canonical function name and signature). Without
        this, models invent plausible-but-wrong names like
        ``shared_elements`` instead of ``similar_elements``.
        """
        task_id = record.get("task_id", record.get("id", "unknown"))
        base_prompt = record.get("text") or record.get("prompt", "")
        test_list = record.get("test_list") or []
        if test_list and "Your code should pass these tests" not in base_prompt:
            sample = "\n".join(test_list[:3])
            full_prompt = (
                f"{base_prompt}\n\nYour code should pass these tests:\n{sample}"
            )
        else:
            full_prompt = base_prompt
        out = dict(record)
        out.setdefault("id", f"Mbpp/{task_id}")
        out["prompt"] = full_prompt
        return out


class MBPPPlus(MBPP):
    """MBPP+ (EvalPlus) rows through the MBPP grading path.

    The staged MBPP+ dataset (``chimera bench-fetch mbpp-plus``) is row-for-row
    compatible with :class:`MBPP`; this subclass only relabels the benchmark so
    matrix columns and reports never present an MBPP+ run as ``mbpp-sanitized``.

    Grading honesty: ``evaluate()`` (inherited) runs the base ``test_list``
    asserts only — the EvalPlus expanded ``test`` harness is preserved verbatim
    in every staged row but not yet executed, so results are **base-strength**,
    not plus-strength.
    """

    def name(self) -> str:
        return "mbpp-plus"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    """``python -m chimera.eval.benchmarks.mbpp --limit N --model M``.

    Loads MBPP from :func:`default_dataset_path` (or the
    ``--dataset-path`` override), constructs an otter Agent via
    :func:`chimera.otter.benchmarks.build_otter_agent_for_eval`, and runs
    the benchmark through :class:`chimera.eval.harness.Harness`. Prints a
    one-line summary plus per-task pass/fail; mirrors the otter
    ``run_mbpp`` runner so ``--model glm-5.1:cloud`` smoke runs are a
    one-liner.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m chimera.eval.benchmarks.mbpp",
        description="Run the MBPP benchmark against an otter Agent.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--dataset-path", type=str, default=None)
    parser.add_argument(
        "--split",
        type=str,
        default="sanitized",
        help="Logical split label surfaced via name() (does not filter).",
    )
    args = parser.parse_args(argv)

    dataset = (
        Path(args.dataset_path).expanduser()
        if args.dataset_path
        else default_dataset_path()
    )
    if not dataset_available(dataset):
        print(SETUP_HINT, file=sys.stderr)
        return 3

    # Lazy import to keep ``import chimera.eval.benchmarks.mbpp`` cheap.
    from chimera.otter.benchmarks import run_mbpp

    result = run_mbpp(
        limit=args.limit,
        model=args.model or "",
        dataset_path=str(dataset),
        split=args.split,
    )
    print(
        f"{result.benchmark}: passed={result.passed}/{result.total} "
        f"rate={result.pass_rate:.1%} cost=${result.total_cost:.4f}"
    )
    for task_result in result.results:
        status = "PASS" if task_result.passed else "FAIL"
        print(f"  {status} {task_result.task_id} steps={task_result.steps}")
    return 0 if result.passed > 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
