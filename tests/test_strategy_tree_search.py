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
                assert cloned.workdir != env.workdir
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)

    def test_clone_is_independent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            env.write_file("data.txt", "original")

            cloned = _clone_environment(env, suffix="branch-1")
            try:
                cloned.write_file("data.txt", "modified")
                assert env.read_file("data.txt") == "original"
                assert cloned.read_file("data.txt") == "modified"
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)

    def test_clone_workdir_contains_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()

            cloned = _clone_environment(env, suffix="branch-42")
            try:
                assert "branch-42" in str(cloned.workdir)
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)


from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.providers.base import Provider, Response
from chimera.tools.write import WriteFileTool
from chimera.training.spec import Spec
from chimera.training.strategies.base import Callback
from chimera.types import ToolCall


class FixedProvider(Provider):
    """Always writes correct calculator code."""

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
        return "gpt-4o-mini"


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
        return "gpt-4o-mini"


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

            # max_depth=1: root (depth 0) + children (depth 1) only
            assert len(result.history) <= 3

    def test_callbacks_are_called(self):
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
                    self.epochs.append(result if result is not None else epoch)
                    return True

                def on_synthesis_end(self, result):
                    self.ended = True

            cb = RecordingCallback()
            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=10)
            ts.run(agent, spec, env, callbacks=[cb])

            assert cb.started is True
            assert cb.ended is True
            assert len(cb.epochs) >= 1

    def test_best_checkpoint_restored_at_end(self):
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

            calc_content = env.read_file("calc.py")
            assert "def add" in calc_content
            assert "def subtract" in calc_content

    def test_history_contains_epoch_results(self):
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


class TestBranchFn:
    def test_custom_branch_fn_provides_prompts(self):
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
            assert len(prompts_seen) == 3
            assert all("Approach" in p for p in prompts_seen)


class TestAllBranchesFail:
    """Regression test: search terminates when all branches raise exceptions."""

    def test_all_branches_fail_terminates(self):
        from chimera.providers.base import Provider, Response

        class AlwaysFailProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                raise RuntimeError("Simulated network failure")

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "always-fail"

        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_test_env(tmpdir)
            agent = Agent(
                provider=AlwaysFailProvider(),
                tools=[WriteFileTool()],
                loop=ReAct(max_steps=3),
            )
            spec = Spec.from_tests(tmpdir, "Implement calculator")
            ts = TreeSearch(branch_factor=2, max_depth=2, max_nodes=10)

            result = ts.run(agent, spec, env)
            assert result.converged is False
            assert result.iterations == 0


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
