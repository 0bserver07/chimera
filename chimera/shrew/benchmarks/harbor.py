"""Harbor benchmark adapter for ``chimera shrew bench harbor``.

Harbor is a maritime / logistics task suite — short prompts asking the
agent to reason about port operations, vessel scheduling, container
manifests, and similar deterministic-answer logistics questions. It
slots between Aider Polyglot (code-edit) and GAIA (open research) as a
**closed-domain reasoning** benchmark: every task ships a single gold
answer (string, integer, or short list) with no required tool use.

Naming: ``Harbor`` is a third-party benchmark name. Like GAIA / Aider
Polyglot, it is **not** the upstream small-model coding agent brand;
naming it directly is fine.

Dataset layout (we deliberately do **not** vendor upstream — license
varies by task source):

    ~/.chimera/datasets/harbor/
        tasks.json              # list of task dicts (see schema below)

Override the root via ``CHIMERA_HARBOR_PATH=/abs/path``.

Per-task schema (one entry of ``tasks.json``):

    {
      "task_id": "harbor-001",         # required, used as task_id
      "prompt": "Vessel A arrives ...", # required; the prompt body
      "answer": "14:30",                # gold answer (string/number/list)
      "category": "scheduling",         # informational filter slot
      "difficulty": 1                   # informational (1=easy / 3=hard)
    }

Both ``"task_id"`` / ``"id"`` and ``"answer"`` / ``"final_answer"`` /
``"gold"`` keys are accepted to match likely upstream parquet schemas.

Scoring: extracts an ``Answer: <value>`` line from the agent's reply
and compares it to the gold using the same normalisation rules as the
GAIA scorer (case / accent / punctuation / article folding, plus
list-set and numeric-tolerance branches). The shared helper lives in
:mod:`chimera.shrew.benchmarks.gaia` so the two benchmarks stay in
lockstep.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark
from chimera.shrew.benchmarks.gaia import extract_final_answer, score_answer
from chimera.config.paths import STATE_DIRNAME, store_path

__all__ = [
    "HarborBench",
    "DEFAULT_DATASET_DIR",
    "ENV_DATASET_PATH",
    "default_dataset_path",
    "dataset_available",
    "setup_hint",
]


DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/harbor"
"""Default on-disk location for the staged Harbor dataset."""

ENV_DATASET_PATH = "CHIMERA_HARBOR_PATH"
"""Environment variable that overrides :data:`DEFAULT_DATASET_DIR`."""


def default_dataset_path() -> Path:
    """Return the resolved Harbor dataset root.

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
    return store_path("datasets") / "harbor"


def dataset_available(path: Path | None = None) -> bool:
    """Return ``True`` when a usable Harbor dataset is staged.

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
        "Harbor dataset not staged.\n"
        f"  expected dir:  {resolved}\n"
        "  expected file: tasks.json (list of {task_id, prompt, answer,"
        " ...})\n"
        f"  override:      {ENV_DATASET_PATH}=/abs/path/to/dir\n"
        "  setup:\n"
        "    1. Obtain the Harbor task corpus from upstream (license"
        " varies).\n"
        "    2. Convert to tasks.json — see the docstring on\n"
        "       chimera/shrew/benchmarks/harbor.py for the schema.\n"
        "    3. Drop tasks.json under the expected dir.\n"
        "  note: we do NOT vendor upstream — license varies by task."
    )


class HarborBench(Benchmark):
    """Harbor maritime/logistics benchmark adapter.

    Loads short closed-domain reasoning tasks from a staged JSON dump
    and grades each agent reply by extracting an ``Answer:`` line and
    comparing it to the gold via :func:`chimera.shrew.benchmarks.gaia.
    score_answer` (the GAIA-style normalised scorer).

    Args:
        dataset_path: Optional override for the dataset root directory.
            When ``None``, :func:`default_dataset_path` is used.
        limit: Optional cap on the number of tasks returned. ``None`` /
            non-positive means no cap.
        category: Optional category filter (e.g. ``"scheduling"``,
            ``"manifest"``). Compared against ``task["category"]`` after
            ``str.lower()``.
        difficulty: Optional difficulty filter (``1`` / ``2`` / ``3``).
            Compared against ``task["difficulty"]`` after str-coercion
            so a JSON int or string both match.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        category: str | None = None,
        difficulty: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit if (limit is None or limit > 0) else None
        self.category = category.lower() if category else None
        self.difficulty = difficulty
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        """Return the benchmark identifier (suffixed with active filters)."""
        suffixes: list[str] = []
        if self.category is not None:
            suffixes.append(self.category)
        if self.difficulty is not None:
            suffixes.append(f"d{self.difficulty}")
        if not suffixes:
            return "harbor"
        return "harbor:" + "+".join(suffixes)

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks (cached after first call).

        Each returned dict carries an ``id`` key (from ``task_id``) and a
        ``prompt`` key (synthesised with the Answer-line protocol). The
        original fields (``answer``, ``category``, ``difficulty``) are
        preserved for :meth:`evaluate`.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Score one task by extracting the agent's final answer.

        Args:
            task: One entry from :meth:`tasks` (must include one of
                ``"answer"`` / ``"final_answer"`` / ``"gold"``).
            agent_output: The agent's raw final reply.
            env: Unused (Harbor grading is text-only).

        Returns:
            Whether :func:`score_answer` accepts the prediction. When no
            gold answer is present, returns ``False`` so test-split runs
            never accidentally count as correct.
        """
        gold = (
            task.get("answer")
            or task.get("final_answer")
            or task.get("gold")
            or ""
        )
        if not gold:
            return False
        predicted = extract_final_answer(agent_output)
        ok, _reason = score_answer(predicted, str(gold))
        return ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        """Return the resolved dataset root (no existence check)."""
        if self._dataset_path:
            return Path(self._dataset_path).expanduser()
        return default_dataset_path()

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Read ``tasks.json``, apply filters + limit + prompt build."""
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
            if self.category is not None:
                rec_cat = str(rec.get("category", "")).lower()
                if rec_cat != self.category:
                    continue
            if self.difficulty is not None:
                rec_diff = rec.get("difficulty")
                if str(rec_diff) != str(self.difficulty):
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
        We accept upstream variants (``task_id`` / ``id``) and synthesise
        a deterministic answer-protocol prompt body.
        """
        body = (
            rec.get("prompt")
            or rec.get("question")
            or rec.get("Question")
            or ""
        )
        task_id = rec.get("task_id") or rec.get("id") or ""
        out = dict(rec)
        out["id"] = task_id
        out["prompt"] = (
            "You are solving a Harbor maritime / logistics question. "
            "Reason step-by-step using the facts in the prompt.\n\n"
            f"Question:\n{body}\n\n"
            "End your final reply with a single line:\n"
            "  Answer: <value>\n"
            "Numbers should be plain digits (no units unless asked). "
            "Lists are comma-separated."
        )
        return out
