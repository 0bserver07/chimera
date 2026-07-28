# chimera/training/strategies/tree_search.py
"""Tree search strategy for non-linear synthesis."""
from __future__ import annotations

import concurrent.futures
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from chimera.env.local import WORKSPACE_STATE_DIRS, LocalEnvironment

_logger = logging.getLogger(__name__)
from chimera.training.strategies.base import (  # noqa: E402
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


@dataclass
class SearchNode:
    """A node in the search tree."""

    id: str
    parent_id: str | None
    depth: int
    checkpoint_id: str
    pass_rate: float
    passed: int
    total: int
    cost: float
    agent_output: str
    children: list[str] = field(default_factory=list)

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


def _clone_environment(env: LocalEnvironment, suffix: str = "clone") -> LocalEnvironment:
    """Create an independent copy of a LocalEnvironment."""
    parent = env.workdir.parent
    clone_dir = Path(tempfile.mkdtemp(prefix=f"chimera-{suffix}-", dir=parent))

    for item in env.workdir.iterdir():
        # Chimera's own state, including the checkpoint store that now lives
        # under ``.chimera`` — copying it would clone every checkpoint too.
        if item.is_dir() and item.name in WORKSPACE_STATE_DIRS:
            continue
        dest = clone_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    cloned = LocalEnvironment(
        workdir=str(clone_dir),
        test_cmd=env.test_cmd,
        timeout=env.timeout,
    )
    cloned.setup()
    return cloned


class TreeSearch(Strategy):
    """Best-first tree search over solution branches."""

    def __init__(
        self,
        branch_factor: int = 3,
        max_depth: int = 5,
        max_nodes: int = 20,
        max_cost: float | None = None,
        min_pass_rate: float = 0.0,
        branch_fn: Callable[..., list[str]] | None = None,
    ) -> None:
        self.branch_factor = branch_factor
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_cost = max_cost
        self.min_pass_rate = min_pass_rate
        self.branch_fn = branch_fn

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _get_prompts(self, spec: Spec, parent: SearchNode, n: int) -> list[str]:
        if self.branch_fn is not None:
            return self.branch_fn(spec, parent, n)
        base = spec.to_prompt()
        if parent.pass_rate > 0:
            base += (
                f"\n\nPrevious attempt: {parent.passed}/{parent.total} tests passed "
                f"({parent.pass_rate:.0%}). Try a different approach."
            )
        return [base] * n

    def _run_branch(
        self,
        agent: Agent,
        clone: LocalEnvironment,
        prompt: str,
    ) -> dict[str, Any]:
        """Run a single branch: agent generates code, then run tests."""
        agent_result = agent.run(prompt, clone)
        test_result = clone.run_tests()
        return {
            "pass_rate": test_result.pass_rate,
            "passed": test_result.passed,
            "total": test_result.total,
            "cost": agent_result.cost,
            "agent_output": agent_result.output,
        }

    def _expand_parallel(
        self,
        agent: Agent,
        env: LocalEnvironment,
        parent: SearchNode,
        prompts: list[str],
    ) -> list[dict[str, Any]]:
        """Expand parent node into N branches in parallel.

        1. Restore parent checkpoint in main env
        2. Clone env N times
        3. Run agent + tests in each clone (parallel via ThreadPoolExecutor)
        4. Copy each clone's files back to main env and checkpoint them
        5. Clean up clones
        6. Return list of result dicts

        NOTE: ``agent.provider`` is shared across threads. This is safe for
        stateless HTTP-based providers (AnthropicProvider, OpenAIProvider)
        since each ``complete()`` call is an independent request. Providers
        with mutable per-request state may need external synchronisation.
        """
        env.restore(parent.checkpoint_id)
        n = len(prompts)
        clones: list[LocalEnvironment] = []
        for i in range(n):
            clones.append(_clone_environment(env, suffix=f"branch-{i}"))

        results: list[dict[str, Any]] = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
                futures = {
                    executor.submit(self._run_branch, agent, clones[i], prompts[i]): i
                    for i in range(n)
                }
                branch_results: dict[int, dict[str, Any] | None] = {}
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    try:
                        branch_results[idx] = future.result()
                    except Exception as exc:
                        _logger.warning(
                            "Branch %d expansion failed (%s: %s); skipping.",
                            idx, type(exc).__name__, exc,
                        )
                        branch_results[idx] = None

            # Copy each clone's files back to main env and checkpoint
            for i in range(n):
                br = branch_results.get(i)
                if br is None:
                    continue
                # Copy clone files to main env
                env.restore(parent.checkpoint_id)
                # Remove non-checkpoint files in main env, then copy from clone
                for item in env.workdir.iterdir():
                    if item.is_dir() and item.name in WORKSPACE_STATE_DIRS:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                for item in clones[i].workdir.iterdir():
                    if item.is_dir() and item.name in WORKSPACE_STATE_DIRS:
                        continue
                    dest = env.workdir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                cp_id = env.checkpoint()
                br["checkpoint_id"] = cp_id
                results.append(br)
        finally:
            for clone in clones:
                try:
                    shutil.rmtree(clone.workdir)
                except Exception:
                    pass

        return results

    # ------------------------------------------------------------------
    # Core search loop
    # ------------------------------------------------------------------

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        callbacks = callbacks or []
        constraints = constraints or []

        for cb in callbacks:
            cb.on_synthesis_start()

        # Node storage
        nodes: dict[str, SearchNode] = {}
        history: list[EpochResult] = []
        total_cost = 0.0
        best_node: SearchNode | None = None
        epoch_num = 0

        # --- Create root node ---
        root_cp = env.checkpoint()
        test_result = env.run_tests()
        root = SearchNode(
            id=self._make_id(),
            parent_id=None,
            depth=0,
            checkpoint_id=root_cp,
            pass_rate=test_result.pass_rate,
            passed=test_result.passed,
            total=test_result.total,
            cost=0.0,
            agent_output="",
            children=[],
        )
        nodes[root.id] = root
        best_node = root

        # If tests already pass at root, converge immediately
        if test_result.all_passed and test_result.total > 0:
            constraints_ok = True
            for constraint in constraints:
                cr = constraint.evaluate(env)
                if not cr.satisfied:
                    constraints_ok = False
            if constraints_ok:
                result = SynthesisResult(
                    converged=True,
                    iterations=0,
                    total_cost=0.0,
                    best_pass_rate=test_result.pass_rate,
                    history=history,
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

        # --- Main loop ---
        node_count = 1  # root
        while node_count < self.max_nodes:
            # SELECT: find best frontier node (leaf with highest pass_rate, depth < max_depth)
            frontier = [
                n for n in nodes.values()
                if n.is_leaf and n.depth < self.max_depth
            ]
            if not frontier:
                break

            parent = max(frontier, key=lambda n: n.pass_rate)

            # EXPAND: generate branch prompts
            remaining = self.max_nodes - node_count
            n_branches = min(self.branch_factor, remaining)
            if n_branches <= 0:
                break

            prompts = self._get_prompts(spec, parent, n_branches)

            # Run branches in parallel
            branch_results = self._expand_parallel(agent, env, parent, prompts)  # type: ignore[arg-type]  # caller passes LocalEnvironment via typed entry

            # If all branches failed, mark parent as exhausted so it
            # drops off the frontier (prevents infinite loop).
            if not branch_results:
                parent.children.append("__failed__")
                continue

            converged = False
            for br in branch_results:
                epoch_num += 1
                node_count += 1

                node_id = self._make_id()
                improved = br["pass_rate"] > best_node.pass_rate

                child = SearchNode(
                    id=node_id,
                    parent_id=parent.id,
                    depth=parent.depth + 1,
                    checkpoint_id=br["checkpoint_id"],
                    pass_rate=br["pass_rate"],
                    passed=br["passed"],
                    total=br["total"],
                    cost=br["cost"],
                    agent_output=br["agent_output"],
                    children=[],
                )
                nodes[child.id] = child
                parent.children.append(child.id)
                total_cost += br["cost"]

                if child.pass_rate > best_node.pass_rate:
                    best_node = child

                epoch = EpochResult(
                    epoch=epoch_num,
                    pass_rate=br["pass_rate"],
                    passed=br["passed"],
                    total=br["total"],
                    agent_output=br["agent_output"],
                    improved=improved,
                    cost=br["cost"],
                    checkpoint_id=br["checkpoint_id"],
                )
                history.append(epoch)

                for cb_inst in callbacks:
                    cb_inst.on_epoch_end(epoch_num, epoch)

                # Check convergence
                if br["pass_rate"] >= 1.0:
                    constraints_ok = True
                    if constraints:
                        env.restore(br["checkpoint_id"])
                        for constraint in constraints:
                            cr = constraint.evaluate(env)
                            if not cr.satisfied:
                                constraints_ok = False
                    if constraints_ok:
                        converged = True
                        best_node = child
                        break

            if converged:
                break

            # PRUNE: mark branches below min_pass_rate as non-leaf
            # (by giving them an empty placeholder child so they are not selected again)
            if self.min_pass_rate > 0:
                for n in list(nodes.values()):
                    if n.is_leaf and not n.is_root and n.pass_rate < self.min_pass_rate:
                        # Mark as pruned by adding a sentinel child id
                        n.children.append("__pruned__")

            # Cost budget check
            if self.max_cost is not None and total_cost >= self.max_cost:
                break

        # --- Restore best node's checkpoint ---
        if best_node is not None and best_node.checkpoint_id:
            env.restore(best_node.checkpoint_id)

        is_converged = best_node is not None and best_node.pass_rate >= 1.0
        result = SynthesisResult(
            converged=is_converged,
            iterations=epoch_num,
            total_cost=total_cost,
            best_pass_rate=best_node.pass_rate if best_node else 0.0,
            history=history,
            failure_reason=None if is_converged else "Search exhausted",
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
