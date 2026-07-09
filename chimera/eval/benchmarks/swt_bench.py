"""SWT-Bench benchmark: software testing generation from real GitHub issues.

SWT-Bench (logic-star-ai/swt-bench, NeurIPS 2024) evaluates an agent's
ability to generate tests that *reproduce* a reported bug — tests that
fail on the original buggy code (Fail) and pass after the gold patch is
applied (Pass). This is the dual of SWE-bench, which measures the
ability to fix issues. The benchmark uses the same repository structure
as SWE-bench (1,983 instances on GitHub repos up to ~700k LOC).

Two evaluation modes:
- ``unit_test``: agent output is a unit test integrated into the suite.
- ``reproduction``: agent output is a standalone script; success is by
  exit code (non-zero pre-patch, zero post-patch).

Two metrics:
- Success Rate (S): fraction of instances with at least one F2P test
  and no F2F or P2F regressions.
- Change Coverage (C): fraction of gold-patch-modified lines covered by
  the generated tests.

Paper: https://arxiv.org/abs/2406.12952
Dataset: https://github.com/logic-star-ai/swt-bench
Site:    https://swtbench.com/
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.swe_bench import _as_test_list
from chimera.eval.harness import Benchmark


@dataclass
class SWTBenchInstance:
    """A single SWT-Bench task instance.

    Mirrors the SWE-bench instance schema. The ``patch`` field is the
    gold code fix; the agent must generate tests (not the patch). The
    ``test_patch`` field, when present, contains the gold tests used as
    a reference / oracle by the harness.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    patch: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "hints": self.hints_text,
            "test_patch": self.test_patch,
            "patch": self.patch,
            "FAIL_TO_PASS": self.fail_to_pass,
            "PASS_TO_PASS": self.pass_to_pass,
        }


# System prompt augmentation: agents trained on swebench preset will
# default to writing fixes. SWT-Bench requires the inverse: write tests
# that reproduce the bug, do NOT modify product code.
SWT_BENCH_SYSTEM_HINT = (
    "You are evaluating SWT-Bench. Your task is to write a test (or "
    "reproduction script) that demonstrates the reported bug. The test "
    "MUST FAIL on the current (buggy) codebase and PASS after the bug "
    "is fixed. Do not modify product code. Integrate into the existing "
    "test framework when possible."
)


class SWTBench(Benchmark):
    """SWT-Bench: test-generation benchmark over real GitHub issues.

    Args:
        dataset_path: Path to a JSON / JSONL file of SWT-Bench instances.
            Accepts the same schema as SWE-bench (instance_id, repo,
            base_commit, problem_statement, patch, test_patch,
            FAIL_TO_PASS, PASS_TO_PASS).
        limit: Maximum number of tasks to load.
        split: Dataset split to use (``"test"`` / ``"dev"`` /
            ``"verified"`` / ``"lite"``). Currently informational —
            the caller should pass the appropriate file in
            ``dataset_path``.
        mode: Evaluation mode. ``"unit_test"`` (default) treats the
            agent output as a unit test patch; ``"reproduction"`` treats
            it as a standalone script judged by exit code.
    """

    VALID_MODES = ("unit_test", "reproduction")

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        split: str = "lite",
        mode: str = "unit_test",
    ) -> None:
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"mode must be one of {self.VALID_MODES}, got {mode!r}"
            )
        self._dataset_path = dataset_path
        self._limit = limit
        self._split = split
        self._mode = mode
        self._instances: list[SWTBenchInstance] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        """Load instances from JSON array or JSON lines."""
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
            for line in text.strip().splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            self._instances.append(SWTBenchInstance(
                instance_id=item.get("instance_id", item.get("id", "")),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                problem_statement=item.get(
                    "problem_statement",
                    item.get("description", item.get("prompt", "")),
                ),
                hints_text=item.get("hints_text", ""),
                test_patch=item.get("test_patch", ""),
                patch=item.get("patch", ""),
                # Official SWT/SWE-bench datasets store these columns as
                # JSON-encoded strings; normalize to a list of node ids.
                fail_to_pass=_as_test_list(
                    item.get("FAIL_TO_PASS", item.get("fail_to_pass"))
                ),
                pass_to_pass=_as_test_list(
                    item.get("PASS_TO_PASS", item.get("pass_to_pass"))
                ),
            ))

        if self._limit:
            self._instances = self._instances[: self._limit]

    def name(self) -> str:
        return "swt-bench"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def split(self) -> str:
        return self._split

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Evaluate whether the agent-generated test reproduces the bug.

        F2P contract:
          1. Apply the agent's test patch (or write the script).
          2. Run tests on the buggy base — at least one new test MUST FAIL.
          3. Apply the gold ``patch`` (the fix).
          4. Re-run — those same tests MUST PASS, with no P2F regressions.

        Without an environment, falls back to a non-empty heuristic so
        the harness can still run smoke tests.

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: Agent's final output (a unit-test patch or
                a reproduction script).
            env: Execution environment (required for true F2P scoring).

        Returns:
            True iff F2P holds (or, in fallback mode, output is non-trivial).
        """
        if env is None:
            return bool(agent_output and len(agent_output.strip()) > 10)

        gold_patch = task.get("patch", "")
        if not gold_patch:
            # Cannot verify F2P without the gold fix.
            return False

        if self._mode == "reproduction":
            return self._evaluate_reproduction(task, agent_output, env, gold_patch)
        return self._evaluate_unit_test(task, agent_output, env, gold_patch)

    def _evaluate_unit_test(
        self,
        task: dict[str, Any],
        agent_output: str,
        env: Any,
        gold_patch: str,
    ) -> bool:
        """Apply agent test patch, then F2P-check via gold patch."""
        if not (hasattr(env, "write_file") and hasattr(env, "run_command") and hasattr(env, "run_tests")):
            return False
        try:
            env.write_file("_agent_tests.diff", agent_output)
            applied = env.run_command("git apply _agent_tests.diff")
            if not applied.success:
                return False

            pre = env.run_tests()
            if pre.all_passed:
                # Tests must FAIL on buggy code to count as reproducing.
                return False

            env.write_file("_gold_patch.diff", gold_patch)
            patched = env.run_command("git apply _gold_patch.diff")
            if not patched.success:
                return False

            post = env.run_tests()
            return bool(post.all_passed)
        except Exception:
            return False

    def _evaluate_reproduction(
        self,
        task: dict[str, Any],
        agent_output: str,
        env: Any,
        gold_patch: str,
    ) -> bool:
        """Run agent's standalone reproduction script: nonzero pre, zero post."""
        if not (hasattr(env, "write_file") and hasattr(env, "run_command")):
            return False
        try:
            env.write_file("_repro.py", agent_output)
            pre = env.run_command("python _repro.py")
            if pre.success:
                # Must fail on buggy code.
                return False

            env.write_file("_gold_patch.diff", gold_patch)
            patched = env.run_command("git apply _gold_patch.diff")
            if not patched.success:
                return False

            post = env.run_command("python _repro.py")
            return bool(post.success)
        except Exception:
            return False

    @property
    def instances(self) -> list[SWTBenchInstance]:
        return list(self._instances)

    def add_instance(self, instance: SWTBenchInstance) -> None:
        """Add an instance programmatically (useful for tests)."""
        self._instances.append(instance)
