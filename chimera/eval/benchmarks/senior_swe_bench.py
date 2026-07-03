"""Senior SWE-Bench (Snorkel AI) adapter — **SCAFFOLD**.

Senior SWE-Bench (``v2026.06``) evaluates a coding agent as a *senior*
engineer: under-specified, multi-file, multi-stack tasks mined from real
merged pull requests across production repositories (PostHog, Gitea,
Electric, Immich, Prefect, Teleport, Turborepo, Plausible, Firezone,
Paperless-ngx, Better-Auth, Harbor). The public split is **50 tasks**; a
further set is held private.

Distribution & format
----------------------
The public dataset is published as a **Harbor task directory tree** — the
exact format Chimera already ingests via
:class:`chimera.eval.benchmarks.harbor.HarborBenchmark`. Each task is a
directory::

    <repo>-<type>-<slug>/
        task.toml          # metadata + environment + verifier/agent budgets
        instruction.md     # the prompt shown to the agent verbatim
        environment/       # per-task Docker build context (image baked at base_commit)
        tests/             # the grading pipeline (see "Grading" below)
        solution/          # held-out reference PR

so this class is a thin **profile** over ``HarborBenchmark`` that only remaps
the handful of Senior-SWE-Bench-specific ``task.toml`` keys.

``task.toml`` deltas vs. the DeepSWE Harbor layout the base parser assumes
(verified against the public repo, 2026-07-03):

* top-level ``version = "1.0"``            (base parser tolerates / ignores it)
* ``[environment].base_image``             (base reads ``docker_image``)
* ``[environment].memory = "8G"``          (base reads int ``memory_mb``)
* ``[environment].storage = "20G"``        (base reads int ``storage_mb``)
* ``[metadata.origin].repo``               (base reads ``repository_url``)
* ``[metadata.origin].base_commit``        (base reads ``base_commit_hash``)
* ``[metadata.taxonomy].stack``            (base reads ``language``)
* rich taxonomy — ``task_type`` (feature|bug|perf|refactor), ``segment``,
  ``variant`` (easy|hard), ``[metadata.oracle_scope]`` (sloc/files/hunks) — is
  surfaced onto each task dict for matrix slicing.

Because the remap populates ``docker_image`` from ``base_image``, the existing
:func:`chimera.eval.benchmarks.harbor.docker_env_factory` provisions Senior
tasks unchanged.

Grading — READ THIS
-------------------
Senior SWE-Bench does **not** grade with a deterministic ``test.sh`` exit code
the way vanilla Harbor tasks do. Its ``tests/`` directory ships a multi-stage
*agentic* verifier — ``run_verify.py`` (runtime correctness),
``run_validate.py`` (a validation agent that writes behavioural tests adapted
to the submitted diff), ``run_judge.py`` (an LLM taste/quality judge), and
``run_aggregate.py`` — all orchestrated by ``tests/test.sh``. The headline
metric is a **"tasteful solve"**: functional pass AND validation-agent pass
AND quality thresholds (rubric / bloat / practice / relative-taste).
Consequently faithful grading:

* needs the task's prebuilt Docker image (repo baked at ``base_commit``),
* needs ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``PORTKEY_API_KEY`` and
  the ``SSB_OVERRIDE_*`` model env from ``[verifier.env]``,
* is itself LLM-driven — it costs money per graded task and is not
  bit-reproducible.

The inherited :meth:`HarborBenchmark.evaluate` runs ``tests/test.sh`` inside
the environment and treats exit 0 as pass, which *does* invoke the real
Snorkel pipeline when keys + image are present. Whether ``test.sh``'s exit code
equals the published "tasteful-solve" threshold **must be confirmed on a live
run** before any number is reported — this module does not claim it. If it does
not hold, the honest path is the native-harness runner (drive
``harbor run --repo snorkel-ai/senior-swe-bench-v2026.06``) and parse its
results.

Status: **SCAFFOLD.** Ingestion (discover / parse / prompt) is real and matches
the verified public schema. The dataset is **not vendored** — the public repo
carries no license, so clone it yourself at eval time. No task has been run or
graded through Chimera yet. Not registered in ``chimera/cli/main.py`` — the
lead wires that.

References
----------
* Site (leaderboard; numbers unverified): <https://senior-swe-bench.snorkel.ai/>
* Dataset (Harbor tasks, 50 public): <https://github.com/snorkel-ai/senior-swe-bench-v2026.06>
"""
from __future__ import annotations

import random
import tomllib
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.harbor import (
    HarborBenchmark,
    HarborParseError,
    HarborTask,
    discover_harbor_tasks,
)


def _parse_size_mb(value: Any, default_mb: int) -> int:
    """Parse a Harbor size (``"8G"`` / ``"512M"`` / ``2048``) to megabytes.

    Senior SWE-Bench writes ``memory`` / ``storage`` as human strings with a
    unit suffix, unlike the integer ``memory_mb`` / ``storage_mb`` the base
    Harbor parser expects. Unrecognised input falls back to *default_mb*.

    Args:
        value: The raw ``[environment]`` value (str with G/M/K suffix, or a
            number).
        default_mb: Fallback when *value* is missing or unparseable.

    Returns:
        The size in whole megabytes.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly.
        return default_mb
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return default_mb
    text = value.strip().upper()
    try:
        if text.endswith("G"):
            return int(float(text[:-1]) * 1024)
        if text.endswith("M"):
            return int(float(text[:-1]))
        if text.endswith("K"):
            return max(1, int(float(text[:-1]) / 1024))
        return int(float(text))
    except ValueError:
        return default_mb


def parse_senior_task(task_dir: Path | str) -> tuple[HarborTask, dict[str, Any]]:
    """Parse one Senior-SWE-Bench task directory.

    Reuses the Harbor on-disk contract (``task.toml`` + ``instruction.md``)
    but reads Senior-SWE-Bench's own key layout — ``base_image``,
    ``[metadata.origin]``, ``[metadata.taxonomy]``, ``[metadata.oracle_scope]``.

    Args:
        task_dir: Directory containing ``task.toml`` and ``instruction.md``.

    Returns:
        A ``(HarborTask, extras)`` pair: the normalised task plus a dict of
        Senior-specific taxonomy fields to merge onto the harness task dict.

    Raises:
        HarborParseError: If ``task.toml`` / ``instruction.md`` are missing or
            ``task.toml`` is invalid TOML (mirrors the base contract).
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
    origin = metadata.get("origin", {})
    taxonomy = metadata.get("taxonomy", {})
    oracle = metadata.get("oracle_scope", {})
    environment = data.get("environment", {})
    verifier = data.get("verifier", {})
    agent = data.get("agent", {})

    stack = taxonomy.get("stack") or metadata.get("tags") or []
    language = str(stack[0]) if isinstance(stack, list) and stack else ""

    verifier_env_raw = verifier.get("env", {})
    verifier_env = {str(k): str(v) for k, v in verifier_env_raw.items()}

    task = HarborTask(
        task_id=str(metadata.get("family") or task_dir.name),
        instruction=instruction_path.read_text(encoding="utf-8"),
        repository_url=str(origin.get("repo", "")),
        base_commit_hash=str(origin.get("base_commit", "")),
        language=language,
        docker_image=str(environment.get("base_image", "")),
        allow_internet=bool(environment.get("allow_internet", False)),
        cpus=float(environment.get("cpus", 1)),
        memory_mb=_parse_size_mb(environment.get("memory"), 2048),
        storage_mb=_parse_size_mb(environment.get("storage"), 10240),
        gpus=int(environment.get("gpus", 0)),
        agent_timeout_sec=float(agent.get("timeout_sec", 7200.0)),
        verifier_timeout_sec=float(verifier.get("timeout_sec", 1800.0)),
        build_timeout_sec=float(environment.get("build_timeout_sec", 1800.0)),
        task_dir=task_dir,
        verifier_env=verifier_env,
    )

    extras: dict[str, Any] = {
        "task_type": str(taxonomy.get("task_type", "")),
        "stack": list(stack) if isinstance(stack, list) else [],
        "segment": str(metadata.get("segment", "")),
        "variant": str(metadata.get("variant", "")),
        "visibility": str(metadata.get("visibility", "")),
        "dataset_version": str(metadata.get("version", "")),
        "oracle_files": int(oracle.get("files", 0) or 0),
        "oracle_sloc": int(oracle.get("sloc", 0) or 0),
        "pr_numbers": list(origin.get("pr_numbers", []) or []),
    }
    return task, extras


class SeniorSWEBench(HarborBenchmark):
    """Senior SWE-Bench adapter — a Harbor-format profile.

    Inherits discovery, sampling, and the verifier flow from
    :class:`~chimera.eval.benchmarks.harbor.HarborBenchmark`; overrides only
    the per-task parse (Senior's ``task.toml`` key layout) and the name.

    Args:
        dataset_path: Path to a local checkout of the public dataset. Either
            the repo root (``…/senior-swe-bench-v2026.06``) or its ``tasks/``
            subdirectory is accepted. When ``None`` — or a path that does not
            exist — the benchmark loads **zero** tasks (this is a scaffold; it
            never raises on an absent dataset).
        limit: Optional cap on the number of tasks. When fewer than the total,
            a deterministic seeded sample is taken.
        seed: Sampling seed; the same ``(dataset, limit, seed)`` always yields
            the same subset.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        seed: int = 0,
    ) -> None:
        # Initialised before super().__init__ so the parent's _load call (if
        # dataset_path is truthy) sees a valid attribute to populate.
        self._extras: dict[str, dict[str, Any]] = {}
        super().__init__(dataset_path=dataset_path, limit=limit, seed=seed)

    def _load(self, path: str) -> None:
        """Discover + parse Senior tasks under ``path``.

        Scaffold-friendly: an absent or empty dataset yields no tasks rather
        than raising, so ``SeniorSWEBench()`` and a stale path are both safe.
        """
        self._extras = {}
        root = Path(path)
        if not root.exists():
            self._tasks = []
            return
        # Accept the repo root or its tasks/ dir; discover_harbor_tasks wants
        # the directory that *contains* the per-task directories.
        tasks_root = root / "tasks" if (root / "tasks").is_dir() else root
        try:
            task_dirs = discover_harbor_tasks(tasks_root)
        except FileNotFoundError:
            self._tasks = []
            return

        if self._limit and self._limit < len(task_dirs):
            rng = random.Random(self._seed)
            task_dirs = sorted(rng.sample(task_dirs, self._limit), key=lambda d: d.name)

        tasks: list[HarborTask] = []
        for task_dir in task_dirs:
            task, extras = parse_senior_task(task_dir)
            tasks.append(task)
            self._extras[task.task_id] = extras
        self._tasks = tasks

    def name(self) -> str:
        return "senior-swe-bench"

    def tasks(self) -> list[dict[str, Any]]:
        """Harness task dicts, enriched with Senior taxonomy fields.

        Each dict carries the base Harbor keys (``id`` / ``prompt`` /
        ``docker_image`` / ``base_commit`` / …) plus ``task_type``, ``stack``,
        ``segment``, ``variant``, ``oracle_files``, ``oracle_sloc``, and
        ``pr_numbers`` for slicing a matrix by task shape.
        """
        if self._cached_tasks is None:
            merged: list[dict[str, Any]] = []
            for task in self._tasks:
                row = task.to_task()
                row.update(self._extras.get(task.task_id, {}))
                merged.append(row)
            self._cached_tasks = merged
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Run the Senior verifier — the real agentic grader, when wired.

        Delegates to :meth:`HarborBenchmark.evaluate`, which applies any
        ``tests/test.patch`` then runs ``tests/test.sh`` in *env* and returns
        ``exit == 0``. For Senior tasks ``test.sh`` orchestrates the multi-stage
        verify → validate → judge → aggregate pipeline, so a faithful grade
        requires the task's Docker image plus the ``ANTHROPIC_API_KEY`` /
        ``OPENAI_API_KEY`` / ``PORTKEY_API_KEY`` / ``SSB_OVERRIDE_*`` env from
        ``[verifier.env]``. **Unverified:** that ``test.sh``'s exit code equals
        the published "tasteful-solve" threshold — confirm on a live run before
        reporting a score. Without a capable *env*, returns ``False`` (never
        a fabricated pass).
        """
        return super().evaluate(task, agent_output, env)


__all__ = ["SeniorSWEBench", "parse_senior_task"]
