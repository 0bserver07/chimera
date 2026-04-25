"""tau-bench adapter — tool-use and business tasks.

tau-bench (and successor tau2/tau3-bench) evaluates conversational agents on
multi-turn, tool-use interactions in real-world customer service domains
(airline, retail, telecom, banking). Unlike code benchmarks, tau-bench tests
reasoning, tool-use, policy adherence, and communication over dynamic
conversations with a simulated user. Evaluation is *stateful* — the database
state at the end of the conversation is compared against an annotated goal
state — and reliability is measured via ``pass^k`` (consistency over multiple
trials of the same task).

This adapter wraps tasks loaded from a tau-bench JSON dump (or the upstream
package, when installed) and exposes them through the standard
:class:`~chimera.eval.harness.Benchmark` interface so they can be driven by
:class:`~chimera.eval.harness.Harness`.

Note:
    Full tau-bench execution requires the upstream ``tau2-bench`` package
    for its simulated environments, API tools, and user simulator. This
    adapter is the integration point — it loads task definitions and
    delegates evaluation to the upstream environment when available, and
    falls back to a structural goal-state comparison otherwise.

Reference:
    - Paper: https://arxiv.org/abs/2406.12045
    - Source: https://github.com/sierra-research/tau2-bench
    - Verified: https://github.com/amazon-agi/tau2-bench-verified

Relevance to mink:
    tau-bench is the canonical *tool-use* benchmark — it stresses
    function-calling agents in stateful, multi-turn settings. The mink
    research thread (tool-use scaffolds, Ollama function-calling) should
    use this adapter as its primary benchmark for non-coding tool-use.
"""

from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark

VALID_DOMAINS = ("airline", "retail", "telecom", "banking", "mock")


class TauBench(Benchmark):
    """tau-bench adapter for tool-use and business-task evaluation.

    Loads tau-bench tasks for a single domain and exposes them through the
    standard :class:`Benchmark` interface. Each task is a multi-turn
    conversation with a simulated user; success is determined by comparing
    the database state at the end of the conversation against the annotated
    goal state.

    Args:
        domain: One of ``"airline"``, ``"retail"``, ``"telecom"``,
            ``"banking"``, or ``"mock"``.
        dataset_path: Optional path to a JSON dump of tasks. When ``None``,
            the adapter attempts to import ``tau2`` from the upstream
            package; if that fails, ``tasks()`` returns an empty list.
        num_trials: Number of trials per task for ``pass^k`` reliability.
            Used by callers driving the harness multiple times; the adapter
            itself returns each task once and exposes the trial count via
            :attr:`num_trials`.
        limit: Optional cap on the number of tasks returned.
        user_llm: Identifier of the LLM to play the simulated user
            (passed to upstream when the package is available).

    Attributes:
        domain: The selected domain.
        num_trials: Trial count for reliability metrics.
    """

    def __init__(
        self,
        domain: str = "airline",
        dataset_path: str | None = None,
        num_trials: int = 1,
        limit: int | None = None,
        user_llm: str | None = None,
    ) -> None:
        if domain not in VALID_DOMAINS:
            raise ValueError(
                f"Unknown tau-bench domain {domain!r}; "
                f"expected one of {VALID_DOMAINS}"
            )
        if num_trials < 1:
            raise ValueError("num_trials must be >= 1")
        self.domain = domain
        self.num_trials = num_trials
        self.user_llm = user_llm
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return f"tau-bench:{self.domain}"

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks for the configured domain.

        Tasks are loaded lazily on first call and cached. Each task dict
        contains at minimum ``id``, ``prompt`` (initial user request),
        ``goal_state`` (annotated end-state for evaluation), and
        ``domain``. When the upstream package is not available and no
        dataset path is provided, returns an empty list.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Compare end-state against goal state.

        When the upstream tau-bench environment is supplied via *env*, the
        adapter delegates to its built-in evaluator (which performs
        database-state comparison). Otherwise it falls back to a
        structural comparison of the agent's reported final state against
        the task's ``goal_state`` field.

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: The agent's final output string. May contain a
                JSON-serialised state under a ``"final_state"`` key, used
                by the in-process fallback evaluator.
            env: tau-bench environment, or ``None``.

        Returns:
            ``True`` when the conversation reached the goal state.
        """
        # Prefer upstream evaluation when available
        if env is not None and hasattr(env, "evaluate_task"):
            try:
                return bool(env.evaluate_task(task, agent_output))
            except Exception:
                return False

        # Structural fallback: agent_output should embed final_state JSON
        goal_state = task.get("goal_state")
        if goal_state is None:
            return False
        try:
            import json

            payload = json.loads(agent_output) if agent_output.strip().startswith("{") else {}
            final_state = payload.get("final_state")
        except (ValueError, AttributeError):
            return False
        return final_state == goal_state

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Load tasks from upstream package or local JSON dump."""
        tasks: list[dict[str, Any]] = []

        if self._dataset_path:
            import json
            from pathlib import Path

            data = json.loads(Path(self._dataset_path).read_text())
            raw = data if isinstance(data, list) else data.get("tasks", [])
            for i, t in enumerate(raw):
                tasks.append(self._normalise_task(t, i))
        else:
            # Best-effort upstream import; silently empty when unavailable
            try:
                from tau2.data_model.tasks import get_tasks  # type: ignore[import-not-found]

                upstream = get_tasks(domain=self.domain)
                for i, t in enumerate(upstream):
                    tasks.append(self._normalise_task(t, i))
            except Exception:
                tasks = []

        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _normalise_task(self, raw: Any, index: int) -> dict[str, Any]:
        """Coerce upstream/raw task into the harness contract."""
        if isinstance(raw, dict):
            task = dict(raw)
        else:
            # Try to extract attributes from an upstream object
            task = {
                attr: getattr(raw, attr)
                for attr in ("id", "prompt", "instruction", "goal_state", "actions")
                if hasattr(raw, attr)
            }
        task.setdefault("id", f"{self.domain}-{index}")
        task.setdefault("domain", self.domain)
        # Harness reads "prompt"; some sources use "instruction"
        if "prompt" not in task and "instruction" in task:
            task["prompt"] = task["instruction"]
        task.setdefault("prompt", "")
        return task
