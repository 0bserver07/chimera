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
    Full tau-bench execution requires the upstream ``tau-bench`` /
    ``tau2-bench`` package for its simulated environments, API tools, and
    user simulator. We do **not** vendor or pip-install upstream. The
    adapter loads task definitions from a local dataset directory and
    delegates evaluation to the upstream environment when supplied
    explicitly via *env*; otherwise it falls back to a structural
    comparison of the agent's terminal action against the task's
    annotated ``actions`` list.

Default dataset location: ``~/.chimera/datasets/tau-bench/``. Override via
the ``CHIMERA_TAU_BENCH_PATH`` environment variable. Within the dataset
directory the loader picks up files matching ``<domain>_*.json`` (e.g.
``retail_tasks.json``, ``airline_tasks.json``).

Reference:
    - Paper: https://arxiv.org/abs/2406.12045
    - Source: https://github.com/sierra-research/tau-bench
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark
from chimera.config.paths import STATE_DIRNAME, store_path

VALID_DOMAINS = ("airline", "retail", "telecom", "banking", "mock")
DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/tau-bench"
ENV_DATASET_PATH = "CHIMERA_TAU_BENCH_PATH"


def default_dataset_path() -> Path:
    """Return the resolved dataset directory.

    Reads the ``CHIMERA_TAU_BENCH_PATH`` environment variable when set;
    otherwise falls back to ``~/.chimera/datasets/tau-bench/``. The path
    may or may not exist; callers are expected to check.
    """
    raw = os.environ.get(ENV_DATASET_PATH)
    if raw:
        return Path(raw).expanduser()
    return store_path("datasets") / "tau-bench"


def dataset_available(path: Path | None = None, domain: str | None = None) -> bool:
    """Return True when at least one task file exists for *domain*.

    When *domain* is ``None``, returns True if any ``*.json`` file lives
    under *path*. When *path* is ``None``, the resolved default dataset
    directory is used.
    """
    base = path or default_dataset_path()
    if not base.exists() or not base.is_dir():
        return False
    if domain is None:
        return any(base.glob("*.json"))
    return any(base.glob(f"{domain}*.json"))


class TauBench(Benchmark):
    """tau-bench adapter for tool-use and business-task evaluation.

    Loads tau-bench tasks for a single domain from a local dataset
    directory and exposes them through the standard :class:`Benchmark`
    interface. Each task is a multi-turn conversation with a simulated
    user; success is determined by comparing the database state at the
    end of the conversation against the annotated goal state.

    Args:
        domain: One of ``"airline"``, ``"retail"``, ``"telecom"``,
            ``"banking"``, or ``"mock"``.
        dataset_path: Optional path to a JSON dump of tasks **or** to a
            directory of ``<domain>_*.json`` files. When ``None``, the
            adapter resolves :func:`default_dataset_path`.
        num_trials: Number of trials per task for ``pass^k`` reliability.
            Used by callers driving the harness multiple times.
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
        ``actions`` (annotated terminal action sequence), and
        ``domain``. When the dataset is not available, returns an empty
        list — callers should pre-flight with :func:`dataset_available`
        for a friendly skip path.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Compare end-state / terminal action against the task goal.

        When the upstream tau-bench environment is supplied via *env*, the
        adapter delegates to its built-in evaluator (which performs
        database-state comparison). Otherwise it falls back to a
        structural comparison of the agent's reported final action against
        the task's annotated ``actions`` list (or ``goal_state`` when
        provided).

        Two acceptable agent_output shapes for the in-process fallback:

        1. JSON object embedding ``{"final_state": ...}`` — compared
           against ``task["goal_state"]``.
        2. JSON object embedding ``{"actions": [...]}`` — compared
           against ``task["actions"]`` (terminal action match).

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: The agent's final output string.
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

        if not isinstance(agent_output, str) or not agent_output.strip():
            return False

        # Try parsing as JSON first
        payload: dict[str, Any] = {}
        stripped = agent_output.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    payload = parsed
            except ValueError:
                payload = {}

        # Path 1: explicit final_state vs goal_state
        goal_state = task.get("goal_state")
        if goal_state is not None and "final_state" in payload:
            return bool(payload["final_state"] == goal_state)

        # Path 2: terminal action match
        expected_actions = task.get("actions")
        if expected_actions:
            agent_actions = payload.get("actions")
            if agent_actions is not None:
                return _actions_match(agent_actions, expected_actions)
            # Best-effort: scan for the terminal action name in the output
            terminal = _terminal_action_name(expected_actions)
            if terminal:
                return terminal in agent_output
        return False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Load tasks from the resolved dataset path."""
        path = (
            Path(self._dataset_path).expanduser()
            if self._dataset_path
            else default_dataset_path()
        )
        if not path.exists():
            return []

        tasks: list[dict[str, Any]] = []
        if path.is_file():
            tasks.extend(self._load_file(path))
        elif path.is_dir():
            files = sorted(path.glob(f"{self.domain}*.json"))
            for fp in files:
                tasks.extend(self._load_file(fp))

        # Normalise + tag with domain
        normalised = [self._normalise_task(t, i) for i, t in enumerate(tasks)]

        if self._limit:
            normalised = normalised[: self._limit]
        return normalised

    @staticmethod
    def _load_file(path: Path) -> list[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
        if isinstance(data, dict):
            raw = data.get("tasks", [])
            if isinstance(raw, list):
                return [t for t in raw if isinstance(t, dict)]
        return []

    def _normalise_task(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        """Coerce a raw task dict into the harness contract."""
        task = dict(raw)
        task.setdefault("id", f"{self.domain}-{index}")
        task.setdefault("domain", self.domain)
        # Harness reads "prompt"; some sources use "instruction" / "user_request"
        if "prompt" not in task:
            for alt in ("instruction", "user_request", "user_instruction"):
                if alt in task:
                    task["prompt"] = task[alt]
                    break
        task.setdefault("prompt", "")
        return task


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _terminal_action_name(actions: Any) -> str | None:
    """Return the ``name`` of the last action in *actions*, if any."""
    if not isinstance(actions, list) or not actions:
        return None
    last = actions[-1]
    if isinstance(last, dict):
        name = last.get("name") or last.get("tool")
        return name if isinstance(name, str) else None
    if isinstance(last, str):
        return last
    return None


def _actions_match(agent_actions: Any, expected: list[Any]) -> bool:
    """Return True when the agent's terminal action matches *expected*.

    tau-bench evaluates by terminal action: the final tool call the agent
    made should match (by name + arguments) the last action in the
    annotated sequence. We match the last entry only — that's the one
    that mutates the database into the goal state.
    """
    if not isinstance(agent_actions, list) or not agent_actions:
        return False
    if not expected:
        return False
    return _action_equal(agent_actions[-1], expected[-1])


def _action_equal(a: Any, b: Any) -> bool:
    """Return True when two action records denote the same call."""
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        a_name = a.get("name") or a.get("tool")
        b_name = b.get("name") or b.get("tool")
        if a_name != b_name:
            return False
        a_args = a.get("arguments") or a.get("args") or {}
        b_args = b.get("arguments") or b.get("args") or {}
        return a_args == b_args
    if isinstance(a, dict):
        a_name = a.get("name") or a.get("tool")
        return a_name == b
    if isinstance(b, dict):
        b_name = b.get("name") or b.get("tool")
        return a == b_name
    return False


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


_SETUP_HINT = """\
tau-bench dataset not found.

Looked under: {path}

To run this adapter end-to-end:
  1. Clone upstream tasks (we do NOT vendor or pip install upstream):
       git clone https://github.com/sierra-research/tau-bench /tmp/tau-bench
  2. Stage the JSON task dumps:
       mkdir -p ~/.chimera/datasets/tau-bench
       cp /tmp/tau-bench/tau_bench/envs/retail/tasks_train.json \\
          ~/.chimera/datasets/tau-bench/retail_train.json
       cp /tmp/tau-bench/tau_bench/envs/airline/tasks.json \\
          ~/.chimera/datasets/tau-bench/airline.json
  3. Re-run with --domain airline (or --domain retail).

Override the dataset directory via CHIMERA_TAU_BENCH_PATH=/path/to/dir.
"""


def _format_table(rows: list[tuple[str, str, str]], use_color: bool) -> str:
    """Render a 3-column ``id | passed | output`` table."""
    if not rows:
        return "(no tasks)"
    headers = ("task_id", "passed", "output (truncated)")
    cols = list(zip(headers, *rows, strict=False))
    widths = [max(len(str(c)) for c in col) for col in cols]
    sep = "  "

    def colour(text: str, ok: bool) -> str:
        if not use_color:
            return text
        return ("\033[32m" if ok else "\033[31m") + text + "\033[0m"

    lines = [
        sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        sep.join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        passed = row[1].strip().lower() == "true"
        cells = [
            row[0].ljust(widths[0]),
            colour(row[1].ljust(widths[1]), passed),
            row[2].ljust(widths[2]),
        ]
        lines.append(sep.join(cells))
    return "\n".join(lines)


def _truncate(text: str, n: int = 60) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "..."


def _run_cli(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m chimera.eval.benchmarks.tau_bench``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="chimera.eval.benchmarks.tau_bench",
        description="Run the tau-bench adapter against a local dataset.",
    )
    parser.add_argument(
        "--domain",
        default="airline",
        choices=list(VALID_DOMAINS),
        help="tau-bench domain to load (default: airline).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of tasks to run (default: 3).",
    )
    parser.add_argument(
        "--model",
        default="glm-5",
        help="Provider model id used by the agent (default: glm-5).",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help=(
            "Override the dataset path (file or directory). Defaults to "
            f"$CHIMERA_TAU_BENCH_PATH or {DEFAULT_DATASET_DIR}."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour in the results table.",
    )
    args = parser.parse_args(argv)

    bench = TauBench(
        domain=args.domain,
        dataset_path=args.dataset,
        limit=args.limit,
    )

    resolved = (
        Path(args.dataset).expanduser()
        if args.dataset
        else default_dataset_path()
    )
    tasks = bench.tasks()
    if not tasks:
        print(_SETUP_HINT.format(path=resolved))
        return 2

    # Build a minimal Agent. We import lazily so the CLI doesn't pull in
    # the agent stack at module import time.
    try:
        from chimera.core.agent import Agent
        from chimera.providers.factory import create_provider
    except Exception as exc:  # pragma: no cover - import wiring only
        print(f"tau-bench: cannot import Agent/provider stack: {exc}")
        return 3

    try:
        provider = create_provider(args.model)
    except Exception as exc:
        print(f"tau-bench: cannot construct provider for {args.model!r}: {exc}")
        return 3

    agent = Agent(provider=provider)
    rows: list[tuple[str, str, str]] = []
    passed = 0
    for task in tasks:
        try:
            result = agent.run(task.get("prompt", ""), None)
            output = getattr(result, "output", "") or ""
        except Exception as exc:
            output = f"<agent error: {exc}>"
        ok = bench.evaluate(task, output, None)
        if ok:
            passed += 1
        rows.append((str(task.get("id", "?")), str(ok), _truncate(output)))

    print(_format_table(rows, use_color=not args.no_color))
    total = len(rows)
    rate = passed / total if total else 0.0
    print(f"\ntau-bench:{args.domain}  passed={passed}/{total}  rate={rate:.1%}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_cli())
