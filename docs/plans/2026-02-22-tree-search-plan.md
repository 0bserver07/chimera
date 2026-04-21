# Tree Search Strategy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `TreeSearch` strategy that explores multiple solution branches via environment checkpointing and parallel execution, enabling non-linear problem solving.

**Architecture:** Best-first tree search over `SearchNode` dataclass objects. Each expansion step forks N branches (parallel via `concurrent.futures.ThreadPoolExecutor` with cloned environments), scores by test pass rate, and expands the highest-scoring frontier node next. Limits: max depth, max nodes, optional max cost.

**Tech Stack:** Python 3.11+, `concurrent.futures`, `shutil.copytree`, `dataclasses`

---

### Task 67: SearchNode data model

**Files:**
- Create: `chimera/training/strategies/tree_search.py`
- Test: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing tests**

```python
# tests/test_strategy_tree_search.py
"""Tests for TreeSearch strategy."""
from __future__ import annotations

import pytest

from chimera.training.strategies.tree_search import SearchNode


class TestSearchNode:
    def test_create_root_node(self):
        node = SearchNode(
            id="root",
            parent_id=None,
            depth=0,
            checkpoint_id="cp0",
            pass_rate=0.0,
            passed=0,
            total=2,
            cost=0.0,
            agent_output="",
            children=[],
        )
        assert node.id == "root"
        assert node.parent_id is None
        assert node.depth == 0
        assert node.is_root
        assert node.is_leaf

    def test_create_child_node(self):
        node = SearchNode(
            id="n1",
            parent_id="root",
            depth=1,
            checkpoint_id="cp1",
            pass_rate=0.5,
            passed=1,
            total=2,
            cost=0.1,
            agent_output="wrote code",
            children=[],
        )
        assert not node.is_root
        assert node.is_leaf

    def test_node_with_children_is_not_leaf(self):
        node = SearchNode(
            id="root",
            parent_id=None,
            depth=0,
            checkpoint_id="cp0",
            pass_rate=0.0,
            passed=0,
            total=2,
            cost=0.0,
            agent_output="",
            children=["n1", "n2"],
        )
        assert not node.is_leaf
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.training.strategies.tree_search'`

**Step 3: Write minimal implementation**

```python
# chimera/training/strategies/tree_search.py
"""Tree search strategy for non-linear synthesis."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchNode:
    """A node in the search tree.

    Each node represents a state of the codebase after an agent action.
    The tree is stored as a flat dict[str, SearchNode], with parent/child
    links via string IDs.
    """

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
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
cd . && git add chimera/training/strategies/tree_search.py tests/test_strategy_tree_search.py && git commit -m "feat: add SearchNode data model for tree search"
```

---

### Task 68: TreeSearch skeleton — constructor and run() stub

**Files:**
- Modify: `chimera/training/strategies/tree_search.py`
- Modify: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing tests**

Append to `tests/test_strategy_tree_search.py`:

```python
from chimera.training.strategies.tree_search import TreeSearch


class TestTreeSearchInit:
    def test_default_params(self):
        ts = TreeSearch()
        assert ts.branch_factor == 3
        assert ts.max_depth == 5
        assert ts.max_nodes == 20
        assert ts.max_cost is None
        assert ts.min_pass_rate == 0.0
        assert ts.branch_fn is None

    def test_custom_params(self):
        ts = TreeSearch(
            branch_factor=5,
            max_depth=10,
            max_nodes=50,
            max_cost=5.0,
            min_pass_rate=0.2,
        )
        assert ts.branch_factor == 5
        assert ts.max_depth == 10
        assert ts.max_nodes == 50
        assert ts.max_cost == 5.0
        assert ts.min_pass_rate == 0.2

    def test_is_strategy_subclass(self):
        from chimera.training.strategies.base import Strategy
        assert issubclass(TreeSearch, Strategy)
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py::TestTreeSearchInit -v`
Expected: FAIL — `ImportError: cannot import name 'TreeSearch'`

**Step 3: Write minimal implementation**

Add to `chimera/training/strategies/tree_search.py`:

```python
from typing import Any, Callable, TYPE_CHECKING

from chimera.training.strategies.base import (
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


class TreeSearch(Strategy):
    """Best-first tree search over solution branches.

    At each step, expands the highest-scoring frontier node into N branches
    (parallel execution with cloned environments). Branches are scored by
    test pass rate. Continues until convergence or limits are hit.

    Args:
        branch_factor: Number of branches per expansion step.
        max_depth: Maximum depth from root to any leaf.
        max_nodes: Total number of nodes to evaluate before stopping.
        max_cost: Optional dollar cost limit across all branches.
        min_pass_rate: Prune branches scoring below this threshold.
        branch_fn: Optional callable(spec, node, n) -> list[str] that
            returns n task prompts. If None, uses default temperature
            diversity (same prompt, n calls).
    """

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

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        raise NotImplementedError("TreeSearch.run() not yet implemented")
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
cd . && git add chimera/training/strategies/tree_search.py tests/test_strategy_tree_search.py && git commit -m "feat: add TreeSearch constructor and Strategy subclass"
```

---

### Task 69: Environment cloning helper

The tree search needs to create temporary copies of the environment for parallel branch evaluation. This is a private helper within the strategy.

**Files:**
- Modify: `chimera/training/strategies/tree_search.py`
- Modify: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing tests**

Append to `tests/test_strategy_tree_search.py`:

```python
import tempfile
from pathlib import Path

from chimera.env.local import LocalEnvironment
from chimera.training.strategies.tree_search import _clone_environment


class TestCloneEnvironment:
    def test_clone_copies_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            env.write_file("hello.py", "print('hello')")
            env.write_file("sub/deep.txt", "deep")

            cloned = _clone_environment(env, suffix="branch-0")
            try:
                assert cloned.read_file("hello.py") == "print('hello')"
                assert cloned.read_file("sub/deep.txt") == "deep"
                # Different workdir
                assert cloned.workdir != env.workdir
            finally:
                cloned.cleanup()

    def test_clone_is_independent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            env.write_file("data.txt", "original")

            cloned = _clone_environment(env, suffix="branch-1")
            try:
                cloned.write_file("data.txt", "modified")
                # Original is untouched
                assert env.read_file("data.txt") == "original"
                assert cloned.read_file("data.txt") == "modified"
            finally:
                cloned.cleanup()

    def test_clone_workdir_contains_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()

            cloned = _clone_environment(env, suffix="branch-42")
            try:
                assert "branch-42" in str(cloned.workdir)
            finally:
                cloned.cleanup()
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py::TestCloneEnvironment -v`
Expected: FAIL — `ImportError: cannot import name '_clone_environment'`

**Step 3: Write minimal implementation**

Add to `chimera/training/strategies/tree_search.py` (at module level, before the `TreeSearch` class):

```python
import shutil
import tempfile
from pathlib import Path

from chimera.env.local import LocalEnvironment


def _clone_environment(env: LocalEnvironment, suffix: str = "clone") -> LocalEnvironment:
    """Create an independent copy of a LocalEnvironment.

    Copies the entire workdir to a temp directory. The caller is
    responsible for calling cleanup() on the returned environment
    (which is a no-op, but the temp dir should be removed via
    shutil.rmtree on the parent).

    Args:
        env: The environment to clone.
        suffix: A label for the cloned directory name.

    Returns:
        A new LocalEnvironment pointing to the copied workdir.
    """
    # Create a temp dir next to the original workdir
    parent = env.workdir.parent
    clone_dir = Path(tempfile.mkdtemp(prefix=f"chimera-{suffix}-", dir=parent))

    # Copy everything except .chimera_checkpoints
    for item in env.workdir.iterdir():
        if item.name == ".chimera_checkpoints":
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
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: 9 passed

**Step 5: Commit**

```bash
cd . && git add chimera/training/strategies/tree_search.py tests/test_strategy_tree_search.py && git commit -m "feat: add _clone_environment helper for parallel branch execution"
```

---

### Task 70: TreeSearch.run() — core search loop

This is the main logic. Uses a mock provider/agent for testing.

**Files:**
- Modify: `chimera/training/strategies/tree_search.py`
- Modify: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing tests**

Append to `tests/test_strategy_tree_search.py`:

```python
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.providers.base import Provider, Response
from chimera.tools.write import WriteFileTool
from chimera.training.spec import Spec
from chimera.types import ToolCall


class FixedProvider(Provider):
    """Always writes the same correct calculator code."""

    def __init__(self) -> None:
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
        return Response(
            content="Writing code.",
            tool_calls=[
                ToolCall(
                    id=f"c{self._call}",
                    name="write_file",
                    arguments={
                        "path": "calc.py",
                        "content": "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n",
                    },
                )
            ],
            usage={"input_tokens": 100, "output_tokens": 50},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "fixed-mock"


class BrokenProvider(Provider):
    """Always writes broken code (add works, subtract doesn't)."""

    def __init__(self) -> None:
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
        return Response(
            content="Writing code.",
            tool_calls=[
                ToolCall(
                    id=f"c{self._call}",
                    name="write_file",
                    arguments={
                        "path": "calc.py",
                        "content": "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a  # BUG\n",
                    },
                )
            ],
            usage={"input_tokens": 100, "output_tokens": 50},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "broken-mock"


def _make_test_env(tmpdir):
    """Set up a temp env with a calculator test file."""
    test_content = (
        "from calc import add, subtract\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "def test_subtract():\n"
        "    assert subtract(5, 3) == 2\n"
    )
    Path(tmpdir, "test_calc.py").write_text(test_content)
    env = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest test_calc.py -v")
    env.setup()
    return env


class TestTreeSearchRun:
    def test_converges_when_all_branches_succeed(self):
        """If all branches produce correct code, converges on first expansion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=FixedProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            ts = TreeSearch(branch_factor=2, max_depth=3, max_nodes=10)
            result = ts.run(agent, spec, env)

            assert result.converged is True
            assert result.best_pass_rate == 1.0
            assert len(result.history) >= 1

    def test_does_not_converge_with_broken_agent(self):
        """Broken agent never achieves 100% — stops at max_nodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=BrokenProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=6)
            result = ts.run(agent, spec, env)

            assert result.converged is False
            assert result.best_pass_rate == 0.5
            assert len(result.history) <= 6

    def test_max_depth_limits_tree(self):
        """Nodes beyond max_depth are not created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=BrokenProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            ts = TreeSearch(branch_factor=2, max_depth=1, max_nodes=20)
            result = ts.run(agent, spec, env)

            # With max_depth=1, only root (depth 0) and its children (depth 1) exist
            # So at most 1 + branch_factor = 3 nodes
            assert len(result.history) <= 3

    def test_callbacks_are_called(self):
        """Callbacks receive on_synthesis_start/end and on_epoch_end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=FixedProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            class RecordingCallback(Callback):
                def __init__(self):
                    self.started = False
                    self.ended = False
                    self.epochs = []

                def on_synthesis_start(self):
                    self.started = True

                def on_epoch_end(self, epoch, result=None):
                    self.epochs.append(epoch if result is None else result)
                    return True

                def on_synthesis_end(self, result):
                    self.ended = True

            from chimera.training.strategies.base import Callback

            cb = RecordingCallback()
            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=10)
            ts.run(agent, spec, env, callbacks=[cb])

            assert cb.started is True
            assert cb.ended is True
            assert len(cb.epochs) >= 1

    def test_best_checkpoint_restored_at_end(self):
        """After search completes, the best solution is in the environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=FixedProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=10)
            result = ts.run(agent, spec, env)

            # The best code should be in the environment
            calc_content = env.read_file("calc.py")
            assert "def add" in calc_content
            assert "def subtract" in calc_content

    def test_history_contains_epoch_results(self):
        """Each evaluated node produces an EpochResult in history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=FixedProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=10)
            result = ts.run(agent, spec, env)

            for epoch in result.history:
                assert hasattr(epoch, "pass_rate")
                assert hasattr(epoch, "cost")
                assert epoch.total > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py::TestTreeSearchRun -v`
Expected: FAIL — `NotImplementedError: TreeSearch.run() not yet implemented`

**Step 3: Write minimal implementation**

Replace the `run()` method in `TreeSearch` class in `chimera/training/strategies/tree_search.py` with the full implementation. Also add the required imports at the top of the file:

```python
import uuid
import concurrent.futures
```

The full `run()` method and its helpers:

```python
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

        tree: dict[str, SearchNode] = {}
        history: list[EpochResult] = []
        total_cost = 0.0
        node_count = 0
        expansion_count = 0

        # Create root node from current environment state
        root_checkpoint = env.checkpoint()
        test_result = env.run_tests()
        root = SearchNode(
            id=self._make_id(),
            parent_id=None,
            depth=0,
            checkpoint_id=root_checkpoint,
            pass_rate=test_result.pass_rate,
            passed=test_result.passed,
            total=test_result.total,
            cost=0.0,
            agent_output="",
        )
        tree[root.id] = root
        node_count += 1

        best_node = root
        best_pass_rate = root.pass_rate

        # Check if already converged
        if test_result.all_passed:
            result = SynthesisResult(
                converged=True,
                iterations=0,
                total_cost=0.0,
                best_pass_rate=best_pass_rate,
                history=history,
            )
            for cb in callbacks:
                cb.on_synthesis_end(result)
            return result

        while node_count < self.max_nodes:
            # SELECT: best frontier node (leaf with highest pass_rate within depth limit)
            frontier = [
                n for n in tree.values()
                if n.is_leaf and n.depth < self.max_depth
            ]
            if not frontier:
                break

            frontier.sort(key=lambda n: n.pass_rate, reverse=True)
            parent = frontier[0]

            expansion_count += 1
            for cb in callbacks:
                cb.on_epoch_start(expansion_count)

            # How many branches can we still create?
            remaining = self.max_nodes - node_count
            n_branches = min(self.branch_factor, remaining)
            if n_branches <= 0:
                break

            # Generate task prompts for each branch
            prompts = self._get_prompts(spec, parent, n_branches)

            # EXPAND + EVALUATE: run branches in parallel with cloned envs
            branch_results = self._expand_parallel(
                agent, env, parent, prompts
            )

            # RECORD: add nodes to tree
            converged = False
            for br in branch_results:
                if self.max_cost is not None and total_cost + br["cost"] > self.max_cost:
                    break

                total_cost += br["cost"]
                node_count += 1

                node = SearchNode(
                    id=self._make_id(),
                    parent_id=parent.id,
                    depth=parent.depth + 1,
                    checkpoint_id=br["checkpoint_id"],
                    pass_rate=br["pass_rate"],
                    passed=br["passed"],
                    total=br["total"],
                    cost=br["cost"],
                    agent_output=br["agent_output"],
                )
                tree[node.id] = node
                parent.children.append(node.id)

                improved = node.pass_rate > best_pass_rate

                epoch = EpochResult(
                    epoch=node_count,
                    pass_rate=node.pass_rate,
                    passed=node.passed,
                    total=node.total,
                    agent_output=node.agent_output,
                    improved=improved,
                    cost=node.cost,
                    checkpoint_id=node.checkpoint_id,
                )
                history.append(epoch)

                for cb in callbacks:
                    cb.on_epoch_end(expansion_count, epoch)

                if improved:
                    best_pass_rate = node.pass_rate
                    best_node = node

                # PRUNE: skip low-scoring branches
                if node.pass_rate < self.min_pass_rate:
                    # Mark as expanded (not leaf) so it won't be selected
                    node.children.append("__pruned__")

                # Check convergence
                if node.pass_rate == 1.0:
                    # Check constraints
                    constraints_ok = True
                    if constraints:
                        env.restore(node.checkpoint_id)
                        for constraint in constraints:
                            cr = constraint.evaluate(env)
                            if not cr.satisfied:
                                constraints_ok = False
                    if constraints_ok:
                        converged = True
                        break

            if converged:
                break

            # Check cost limit
            if self.max_cost is not None and total_cost >= self.max_cost:
                break

        # Restore best solution into the main environment
        if best_node.checkpoint_id:
            env.restore(best_node.checkpoint_id)

        result = SynthesisResult(
            converged=best_pass_rate == 1.0,
            iterations=expansion_count,
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
            failure_reason=None if best_pass_rate == 1.0 else self._failure_reason(node_count, expansion_count, total_cost),
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result

    def _make_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _get_prompts(self, spec: Spec, parent: SearchNode, n: int) -> list[str]:
        """Generate n task prompts for branch expansion."""
        if self.branch_fn is not None:
            return self.branch_fn(spec, parent, n)
        # Default: same prompt for all branches (temperature diversity)
        base = spec.to_prompt()
        if parent.pass_rate > 0:
            base += (
                f"\n\nPrevious attempt: {parent.passed}/{parent.total} tests passed "
                f"({parent.pass_rate:.0%}). Try a different approach."
            )
        return [base] * n

    def _expand_parallel(
        self,
        agent: Agent,
        env: Environment,
        parent: SearchNode,
        prompts: list[str],
    ) -> list[dict]:
        """Run branches in parallel with cloned environments.

        Returns a list of dicts with keys:
        pass_rate, passed, total, cost, agent_output, checkpoint_id
        """
        from chimera.env.local import LocalEnvironment

        # Restore parent state in main env, then checkpoint it
        env.restore(parent.checkpoint_id)

        # Clone environments for each branch
        clones: list[LocalEnvironment] = []
        for i, prompt in enumerate(prompts):
            clone = _clone_environment(env, suffix=f"branch-{i}")
            clones.append(clone)

        results = []

        def _run_branch(clone_env: LocalEnvironment, prompt: str) -> dict:
            """Execute one branch: run agent, run tests, checkpoint."""
            agent_result = agent.run(prompt, clone_env)
            test_result = clone_env.run_tests()
            cp_id = clone_env.checkpoint()
            return {
                "pass_rate": test_result.pass_rate,
                "passed": test_result.passed,
                "total": test_result.total,
                "cost": agent_result.cost,
                "agent_output": agent_result.output,
                "checkpoint_id": cp_id,
                "clone_env": clone_env,
            }

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(prompts)) as executor:
                futures = [
                    executor.submit(_run_branch, clone, prompt)
                    for clone, prompt in zip(clones, prompts)
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        pass  # Branch failed — skip it

            # Find the best branch and checkpoint its state into the main env
            if results:
                best = max(results, key=lambda r: r["pass_rate"])
                # Copy best branch's workdir back to main env as a checkpoint
                best_clone = best["clone_env"]
                # Create a checkpoint in the main env from the best branch's files
                # We do this by copying files from clone to main workdir, then checkpointing
                main_env = env
                if isinstance(main_env, LocalEnvironment):
                    import shutil as _shutil
                    # Clean main workdir (except checkpoints)
                    for item in main_env.workdir.iterdir():
                        if item.name == ".chimera_checkpoints":
                            continue
                        if item.is_dir():
                            _shutil.rmtree(item)
                        else:
                            item.unlink()
                    # Copy from best clone
                    for item in best_clone.workdir.iterdir():
                        if item.name == ".chimera_checkpoints":
                            continue
                        dest = main_env.workdir / item.name
                        if item.is_dir():
                            _shutil.copytree(item, dest)
                        else:
                            _shutil.copy2(item, dest)
                    cp = main_env.checkpoint()
                    # Update checkpoint ID for all results to use main env's checkpoints
                    best["checkpoint_id"] = cp

                # For non-best results, also checkpoint them into main env
                for r in results:
                    if r is not best:
                        clone_env = r["clone_env"]
                        if isinstance(main_env, LocalEnvironment):
                            for item in main_env.workdir.iterdir():
                                if item.name == ".chimera_checkpoints":
                                    continue
                                if item.is_dir():
                                    _shutil.rmtree(item)
                                else:
                                    item.unlink()
                            for item in clone_env.workdir.iterdir():
                                if item.name == ".chimera_checkpoints":
                                    continue
                                dest = main_env.workdir / item.name
                                if item.is_dir():
                                    _shutil.copytree(item, dest)
                                else:
                                    _shutil.copy2(item, dest)
                            r["checkpoint_id"] = main_env.checkpoint()
        finally:
            # Cleanup all cloned environments
            for clone in clones:
                try:
                    _shutil.rmtree(clone.workdir)
                except Exception:
                    pass

        # Remove clone_env references from results
        for r in results:
            r.pop("clone_env", None)

        return results

    def _failure_reason(self, node_count: int, expansion_count: int, total_cost: float) -> str:
        if self.max_cost is not None and total_cost >= self.max_cost:
            return f"Cost limit reached (${total_cost:.2f})"
        if node_count >= self.max_nodes:
            return f"Max nodes reached ({self.max_nodes})"
        return "No expandable frontier nodes"
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: 15 passed

**Step 5: Commit**

```bash
cd . && git add chimera/training/strategies/tree_search.py tests/test_strategy_tree_search.py && git commit -m "feat: implement TreeSearch.run() with parallel branch execution"
```

---

### Task 71: Custom branch_fn support

**Files:**
- Modify: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing test**

Append to `tests/test_strategy_tree_search.py`:

```python
class TestBranchFn:
    def test_custom_branch_fn_provides_prompts(self):
        """A custom branch_fn generates different prompts per branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=FixedProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=5),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")

            prompts_seen = []

            def my_branch_fn(spec, node, n):
                ps = [f"Approach {i}: {spec.to_prompt()}" for i in range(n)]
                prompts_seen.extend(ps)
                return ps

            ts = TreeSearch(branch_factor=3, max_depth=1, max_nodes=5, branch_fn=my_branch_fn)
            result = ts.run(agent, spec, env)

            assert result.converged is True
            # branch_fn was called and generated 3 prompts
            assert len(prompts_seen) == 3
            assert all("Approach" in p for p in prompts_seen)
```

**Step 2: Run test to verify it passes**

This should already pass since `_get_prompts()` calls `self.branch_fn` when set.

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py::TestBranchFn -v`
Expected: 1 passed

**Step 3: Commit**

```bash
cd . && git add tests/test_strategy_tree_search.py && git commit -m "test: verify custom branch_fn integration with TreeSearch"
```

---

### Task 72: Export TreeSearch from packages

**Files:**
- Modify: `chimera/training/strategies/__init__.py`
- Modify: `chimera/__init__.py`
- Modify: `tests/test_strategy_tree_search.py`

**Step 1: Write the failing test**

Append to `tests/test_strategy_tree_search.py`:

```python
class TestExports:
    def test_importable_from_strategies(self):
        from chimera.training.strategies import TreeSearch as TS
        assert TS is TreeSearch

    def test_importable_from_chimera(self):
        import chimera
        assert hasattr(chimera, "TreeSearch")

    def test_search_node_importable(self):
        from chimera.training.strategies.tree_search import SearchNode as SN
        assert SN is SearchNode
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py::TestExports -v`
Expected: FAIL on `test_importable_from_strategies` and `test_importable_from_chimera`

**Step 3: Write minimal implementation**

In `chimera/training/strategies/__init__.py`, add:

```python
from chimera.training.strategies.tree_search import TreeSearch
```

And add `"TreeSearch"` to `__all__`.

In `chimera/__init__.py`, update the strategies import from:

```python
from chimera.training.strategies import (
    Callback,
    CurriculumStrategy,
    EnsembleStrategy,
    EpochResult,
    Passthrough,
    Strategy,
    SynthesisResult,
    TestConvergence,
)
```

to also include `TreeSearch`:

```python
from chimera.training.strategies import (
    Callback,
    CurriculumStrategy,
    EnsembleStrategy,
    EpochResult,
    Passthrough,
    Strategy,
    SynthesisResult,
    TestConvergence,
    TreeSearch,
)
```

And add `"TreeSearch"` to the `__all__` list in the Training section.

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_tree_search.py -v`
Expected: All pass

**Step 5: Commit**

```bash
cd . && git add chimera/training/strategies/__init__.py chimera/__init__.py tests/test_strategy_tree_search.py && git commit -m "feat: export TreeSearch from strategies and chimera packages"
```

---

### Task 73: Full regression

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd . && python -m pytest -v --tb=short 2>&1 | tail -20`
Expected: 410+ passed, 0 failed

**Step 2: Fix any breakage, commit if needed**

---

### Task 74: Update docs

**Files:**
- Modify: `docs/task-status.md`
- Modify: `CONTEXT.md`

**Step 1: Update task-status.md**

Add Phase 15 section:

```markdown
## Phase 15: Tree Search Strategy

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 67 | 15 - Tree Search | SearchNode data model | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 68 | 15 - Tree Search | TreeSearch constructor | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 69 | 15 - Tree Search | Environment cloning | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 70 | 15 - Tree Search | Core search loop | `chimera/training/strategies/tree_search.py` | 6 | DONE |
| 71 | 15 - Tree Search | Custom branch_fn | `tests/test_strategy_tree_search.py` | 1 | DONE |
| 72 | 15 - Tree Search | Package exports | `chimera/training/strategies/__init__.py`, `chimera/__init__.py` | 3 | DONE |
```

Update Phase Summary table, total test count, and "What's Next" section.

**Step 2: Update CONTEXT.md**

Add Phase 15 section under Implementation Progress. Update total test count.

**Step 3: Commit**

```bash
cd . && git add docs/task-status.md CONTEXT.md && git commit -m "docs: update progress for Phase 15 (tree search strategy)"
```
