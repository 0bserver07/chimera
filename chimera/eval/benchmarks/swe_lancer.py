"""SWE-Lancer: real-world freelance software-engineering tasks.

SWE-Lancer (Miserendino et al., OpenAI, 2025) evaluates coding agents on a
suite of 1,400+ Upwork freelance bug-fix and feature-implementation tasks
totaling over $1M in payouts. Each task carries a payout, a category
(``ic_swe`` for individual contributor or ``swe_manager`` for managerial
trade-off picks), and an end-to-end test harness running inside a Docker
sandbox. The score is the dollar-weighted resolve rate.

This module is a SCAFFOLD:

* The data loader reads the upstream JSON schema (id, title, payout,
  category, repo, test_harness_path, ...) and exposes
  :class:`SWELancerTask` instances.
* :meth:`SWELancer.evaluate` is :class:`NotImplementedError` because the
  upstream test harness expects a containerized Playwright browser
  environment that is provisioned by :mod:`chimera.env.docker`. Live
  integration is a follow-up.
* :meth:`SWELancer.dollar_weighted_pass_rate` is implemented so that
  callers running pre-graded result sets can compute the headline metric
  without the harness.

References:
    - Paper: arXiv:2502.12115
    - GitHub: github.com/openai/SWELancer-Benchmark
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark

#: Recognized task categories from the upstream dataset.
TASK_CATEGORIES: frozenset[str] = frozenset({"ic_swe", "swe_manager"})


@dataclass
class SWELancerTask:
    """A single SWE-Lancer task instance.

    Attributes:
        task_id: Upstream task identifier (e.g. ``"sl-bug-00042"``).
        title: Short summary of the freelance ticket.
        description: Full task description (acceptance criteria etc.).
        payout_usd: Dollar payout — used to weight the headline metric.
        category: ``ic_swe`` (write code) or ``swe_manager`` (pick the
            best of N proposed fixes).
        repo: Upstream repository name.
        base_commit: Commit SHA the harness is rooted at.
        test_harness_path: Path inside the sandbox that runs the
            end-to-end test (typically a Playwright script).
        choices: For ``swe_manager`` tasks, the list of proposed fixes
            among which the agent must pick the correct one.
        correct_choice: Index of the ground-truth choice (manager tasks
            only).
        tags: Optional metadata tags from the upstream dataset.
    """

    task_id: str
    title: str
    description: str
    payout_usd: float = 0.0
    category: str = "ic_swe"
    repo: str = ""
    base_commit: str = ""
    test_harness_path: str = ""
    choices: list[str] = field(default_factory=list)
    correct_choice: int | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "prompt": self.description,
            "title": self.title,
            "description": self.description,
            "payout_usd": self.payout_usd,
            "category": self.category,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "test_harness_path": self.test_harness_path,
            "choices": list(self.choices),
            "correct_choice": self.correct_choice,
            "tags": list(self.tags),
        }


class SWELancer(Benchmark):
    """SWE-Lancer scaffold.

    Loads instances from a local JSON / JSON-lines dump and exposes a
    dollar-weighted scoring helper. Live execution against the upstream
    Docker harness is a follow-up — :meth:`evaluate` raises
    :class:`NotImplementedError` so misuse is loud.

    Args:
        dataset_path: Path to JSON or JSON-lines file. If ``None``, the
            benchmark starts empty.
        category: Optional filter — ``ic_swe`` or ``swe_manager``.
        min_payout: Drop tasks with a smaller payout (useful for smoke
            runs against the highest-value subset).
        limit: Maximum number of tasks to keep after filtering.

    Raises:
        ValueError: If ``category`` is unsupported.
        FileNotFoundError: If ``dataset_path`` is set but missing.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        category: str | None = None,
        min_payout: float = 0.0,
        limit: int | None = None,
    ) -> None:
        if category is not None and category not in TASK_CATEGORIES:
            raise ValueError(
                f"Unsupported category '{category}'. "
                f"Choose one of {sorted(TASK_CATEGORIES)}."
            )
        self._dataset_path = dataset_path
        self._category = category
        self._min_payout = float(min_payout)
        self._limit = limit
        self._tasks: list[SWELancerTask] = []
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        try:
            items = json.loads(text)
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for raw_line in text.strip().splitlines():
                line = raw_line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            category = item.get("category", "ic_swe")
            if category not in TASK_CATEGORIES:
                continue
            if self._category and category != self._category:
                continue
            payout = float(item.get("payout_usd", 0.0) or 0.0)
            if payout < self._min_payout:
                continue
            self._tasks.append(
                SWELancerTask(
                    task_id=item.get("task_id", item.get("id", "")),
                    title=item.get("title", ""),
                    description=item.get(
                        "description",
                        item.get("problem_statement", item.get("prompt", "")),
                    ),
                    payout_usd=payout,
                    category=category,
                    repo=item.get("repo", ""),
                    base_commit=item.get("base_commit", ""),
                    test_harness_path=item.get("test_harness_path", ""),
                    choices=list(item.get("choices", []) or []),
                    correct_choice=item.get("correct_choice"),
                    tags=list(item.get("tags", []) or []),
                )
            )

        if self._limit:
            self._tasks = self._tasks[: self._limit]

    def name(self) -> str:
        suffix = f"-{self._category}" if self._category else ""
        return f"swe-lancer{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._tasks]

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any = None
    ) -> bool:
        """Grade a SWE-Lancer task.

        **Status:** scaffold. The upstream test harness needs a Docker
        environment with a working Playwright stack — that integration
        lives in a follow-up. This method raises
        :class:`NotImplementedError` so misuse is loud rather than
        silently returning ``False``.

        For ``swe_manager`` tasks, callers can use
        :meth:`grade_manager_choice` directly (it does not require an
        environment).

        Raises:
            NotImplementedError: Live execution is a follow-up.
        """
        raise NotImplementedError(
            "SWE-Lancer live grading requires the upstream Docker "
            "harness with Playwright. Tracked as a follow-up; use "
            "SWELancer.grade_manager_choice for swe_manager tasks."
        )

    def grade_manager_choice(
        self, task: dict[str, Any], chosen_index: int
    ) -> bool:
        """Grade a ``swe_manager`` task without a live environment.

        Args:
            task: Task dictionary from :meth:`tasks`.
            chosen_index: Index the agent picked among ``task["choices"]``.

        Returns:
            ``True`` iff ``chosen_index`` matches ``correct_choice``.
        """
        if task.get("category") != "swe_manager":
            return False
        correct = task.get("correct_choice")
        if correct is None:
            return False
        return bool(chosen_index == correct)

    def dollar_weighted_pass_rate(
        self, results: list[tuple[str, bool]]
    ) -> float:
        """Compute the headline metric from pre-graded results.

        Args:
            results: List of ``(task_id, passed)`` pairs.

        Returns:
            Sum of payouts for passed tasks divided by total payout
            across all known tasks. Returns ``0.0`` when no payouts are
            recorded.
        """
        payout_by_id = {t.task_id: t.payout_usd for t in self._tasks}
        total = sum(payout_by_id.values())
        if total <= 0:
            return 0.0
        earned = sum(
            payout_by_id.get(task_id, 0.0) for task_id, passed in results if passed
        )
        return earned / total

    @property
    def instances(self) -> list[SWELancerTask]:
        return list(self._tasks)

    def add_instance(self, task: SWELancerTask) -> None:
        self._tasks.append(task)


__all__ = ["SWELancer", "SWELancerTask", "TASK_CATEGORIES"]
