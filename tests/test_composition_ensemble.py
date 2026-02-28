from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from chimera.composition.ensemble import Ensemble
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.base import Environment
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message


class LabelProvider(Provider):
    def __init__(self, label: str):
        self.label = label
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content=f"Result from {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return self.label


class TestEnsemble:
    def test_all_agents_run(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 2
            labels = [r.output for r in results]
            assert any("A" in l for l in labels)
            assert any("B" in l for l in labels)

    def test_best_result(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            best = ensemble.best(results)
            assert best.success

    def test_empty_ensemble(self):
        ensemble = Ensemble([])
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert results == []


class SlowProvider(Provider):
    def __init__(self, delay: float = 1.0):
        self.delay = delay
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        time.sleep(self.delay)
        return Response(content="slow result", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return "slow"


class NonCloneableEnv(Environment):
    """Stub environment that does not support clone()."""

    def setup(self): pass
    def cleanup(self): pass
    def read_file(self, path): return ""
    def write_file(self, path, content): pass
    def list_files(self, pattern="**/*"): return []
    def run_command(self, cmd, timeout=120, shell_name="main"):
        from chimera.types import CommandResult
        return CommandResult(stdout="", stderr="", exit_code=0)
    def run_tests(self):
        from chimera.types import TestResult
        return TestResult(passed=0, failed=0, errors=0, output="")
    def checkpoint(self): return "0"
    def restore(self, checkpoint_id): pass
    # clone() intentionally NOT overridden → raises NotImplementedError


class TestParallelEnsemble:
    def test_parallel_with_cloneable_env(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 2

    def test_sequential_fallback(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        env = NonCloneableEnv()
        results = ensemble.run("task", env)
        assert len(results) == 2

    def test_env_none_runs_sequentially(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        results = ensemble.run("task", None)
        assert len(results) == 2

    def test_max_workers_parameter(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents, max_workers=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 2

    def test_timeout_produces_failure(self):
        agents = [
            Agent(provider=SlowProvider(delay=2.0), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents, timeout=0.1)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 1
            assert not results[0].success
            assert results[0].error == "Timeout"

    def test_order_preserved(self):
        agents = [
            Agent(provider=LabelProvider("X"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("Y"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("Z"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 3
            assert "X" in results[0].output
            assert "Y" in results[1].output
            assert "Z" in results[2].output

    def test_backward_compatible_constructor(self):
        agents = [Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1))]
        ensemble = Ensemble(agents)
        assert ensemble.max_workers is None
        assert ensemble.timeout is None
        results = ensemble.run("task", None)
        assert len(results) == 1

    def test_clones_cleaned_up(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            # Track cloned dirs before run
            cloned_dirs: list[Path] = []
            original_clone = env.clone

            def tracking_clone():
                cloned = original_clone()
                cloned_dirs.append(cloned.workdir)
                return cloned

            env.clone = tracking_clone  # type: ignore[method-assign]
            ensemble.run("task", env)

            assert len(cloned_dirs) == 2
            for d in cloned_dirs:
                assert not d.exists(), f"Clone dir {d} was not cleaned up"


# --- Async + first_success tests ---

import pytest
from chimera.types import AgentResult


class AsyncLabelProvider(LabelProvider):
    """LabelProvider with async_complete support."""

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return self.complete(messages, tools, temperature, max_tokens)


class TestAsyncEnsemble:
    @pytest.mark.asyncio
    async def test_async_run_all(self) -> None:
        """async_run runs all agents and returns results."""
        agents = [
            Agent(provider=AsyncLabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=AsyncLabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        results = await ensemble.async_run("task", None)
        assert len(results) == 2
        assert any("A" in r.output for r in results)
        assert any("B" in r.output for r in results)

    @pytest.mark.asyncio
    async def test_async_run_with_cloneable_env(self) -> None:
        """async_run with cloneable env still works."""
        agents = [
            Agent(provider=AsyncLabelProvider("X"), loop=ReAct(max_steps=1)),
            Agent(provider=AsyncLabelProvider("Y"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = await ensemble.async_run("task", env)
            assert len(results) == 2


class TestFirstSuccess:
    def test_first_success_returns_winner(self) -> None:
        """first_success returns first successful result."""
        results = [
            AgentResult(output="fail", steps=1, tool_calls_total=0, cost=0, success=False, error="nope"),
            AgentResult(output="win", steps=1, tool_calls_total=0, cost=0, success=True),
            AgentResult(output="also win", steps=1, tool_calls_total=0, cost=0, success=True),
        ]
        assert Ensemble.first_success(results).output == "win"

    def test_first_success_returns_first_on_all_fail(self) -> None:
        """first_success returns first result when none succeed."""
        results = [
            AgentResult(output="fail1", steps=1, tool_calls_total=0, cost=0, success=False, error="a"),
            AgentResult(output="fail2", steps=1, tool_calls_total=0, cost=0, success=False, error="b"),
        ]
        assert Ensemble.first_success(results).output == "fail1"

    def test_first_success_empty(self) -> None:
        """first_success on empty list returns failure result."""
        result = Ensemble.first_success([])
        assert not result.success


class TestGitEnvironmentClone:
    def test_git_env_clone(self) -> None:
        """GitEnvironment.clone() creates a working git-based clone."""
        from chimera.env.git_env import GitEnvironment

        with tempfile.TemporaryDirectory() as tmpdir:
            env = GitEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("hello.txt", "world")
            env.checkpoint()

            cloned = env.clone()
            try:
                assert cloned.read_file("hello.txt") == "world"
                # Verify clone is a git repo
                result = cloned.run_command("git rev-parse HEAD")
                assert result.exit_code == 0
            finally:
                import shutil
                shutil.rmtree(str(cloned.workdir), ignore_errors=True)
                cloned.cleanup()
