"""Integration tests for the SWE-bench Verified harness wiring (L5/wave-4).

These tests assert that ``Harness.run`` invokes
:meth:`SWEBenchVerified.prepare_agent` so the IPython tool and
LLM-condensation hook end up wired onto the agent and its loop's
:class:`~chimera.core.loop_config.LoopConfig` whenever the benchmark is
``swe-bench-verified``. Tasks are mocked — no Docker, no live LLM, no
upstream dataset is required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.compaction.summary import SummaryCompaction
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.eval.benchmarks.swe_bench_verified import (
    SWEBenchConfig,
    SWEBenchVerified,
)
from chimera.eval.harness import Harness
from chimera.tools.ipython import IPythonTool


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _FakeTool:
    """Stand-in for an existing default tool (e.g. read/write/bash)."""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_agent(*, with_loop: bool = True) -> Any:
    """Build a bare-bones agent stub with mutable ``tools`` and ``loop``."""
    agent = MagicMock()
    agent.tools = [_FakeTool("read"), _FakeTool("write"), _FakeTool("bash")]
    agent.provider = MagicMock()
    if with_loop:
        # Use the real ReAct + LoopConfig so we exercise the actual wire.
        agent.loop = ReAct(max_steps=50, config=LoopConfig(yolo_mode=True))
    else:
        agent.loop = None
    # Make ``run`` deterministic so the harness collects a clean result.
    agent_result = MagicMock()
    agent_result.output = "ok"
    agent_result.cost = 0.0
    agent_result.steps = 1
    agent.run = MagicMock(return_value=agent_result)
    return agent


# --------------------------------------------------------------------------- #
# prepare_agent — direct unit tests                                           #
# --------------------------------------------------------------------------- #


class TestPrepareAgent:
    def test_appends_ipython_tool_when_enabled(self) -> None:
        bench = SWEBenchVerified(ipython=True, condense_every_n_steps=0)
        agent = _make_agent()

        bench.prepare_agent(agent)

        names = [t.name for t in agent.tools]
        assert "ipython" in names
        # Existing tools survive.
        assert {"read", "write", "bash"}.issubset(names)
        ipython_tool = next(t for t in agent.tools if t.name == "ipython")
        assert isinstance(ipython_tool, IPythonTool)

    def test_does_not_duplicate_ipython_tool(self) -> None:
        bench = SWEBenchVerified(ipython=True, condense_every_n_steps=0)
        agent = _make_agent()

        bench.prepare_agent(agent)
        bench.prepare_agent(agent)

        ipython_tools = [t for t in agent.tools if t.name == "ipython"]
        assert len(ipython_tools) == 1

    def test_skips_ipython_when_disabled(self) -> None:
        bench = SWEBenchVerified(ipython=False, condense_every_n_steps=0)
        agent = _make_agent()

        bench.prepare_agent(agent)

        assert "ipython" not in [t.name for t in agent.tools]

    def test_wires_condensation_onto_loop_config(self) -> None:
        bench = SWEBenchVerified(condense_every_n_steps=25)
        agent = _make_agent()

        bench.prepare_agent(agent)

        cfg = agent.loop.config
        assert isinstance(cfg.condensation, SummaryCompaction)
        assert cfg.condense_every_n_steps == 25
        # Provider should have been threaded through to the compaction.
        assert cfg.condensation._provider is agent.provider

    def test_skips_condensation_when_zero(self) -> None:
        bench = SWEBenchVerified(condense_every_n_steps=0)
        agent = _make_agent()

        bench.prepare_agent(agent)

        cfg = agent.loop.config
        assert cfg.condensation is None
        assert cfg.condense_every_n_steps is None

    def test_does_not_overwrite_existing_condensation(self) -> None:
        bench = SWEBenchVerified(condense_every_n_steps=25)
        agent = _make_agent()
        # Caller pre-installed their own compaction strategy.
        custom = SummaryCompaction(provider=None, keep_first=5, keep_last=5)
        agent.loop.config.condensation = custom
        agent.loop.config.condense_every_n_steps = 7

        bench.prepare_agent(agent)

        assert agent.loop.config.condensation is custom
        assert agent.loop.config.condense_every_n_steps == 7

    def test_bumps_max_steps_to_500(self) -> None:
        bench = SWEBenchVerified()  # max_steps=500 by default
        agent = _make_agent()
        assert agent.loop.max_steps == 50  # baseline

        bench.prepare_agent(agent)

        assert agent.loop.max_steps == 500

    def test_does_not_lower_max_steps(self) -> None:
        bench = SWEBenchVerified(max_steps=500)
        agent = _make_agent()
        agent.loop.max_steps = 1000  # caller wanted more headroom

        bench.prepare_agent(agent)

        assert agent.loop.max_steps == 1000

    def test_safe_when_loop_missing(self) -> None:
        bench = SWEBenchVerified()
        agent = _make_agent(with_loop=False)

        # Must not raise even though there is no loop to configure.
        bench.prepare_agent(agent)

        # IPython tool still gets attached because that path doesn't
        # need a loop.
        assert "ipython" in [t.name for t in agent.tools]


# --------------------------------------------------------------------------- #
# Harness — calls prepare_agent before running tasks                          #
# --------------------------------------------------------------------------- #


class TestHarnessIntegration:
    def test_harness_calls_prepare_agent_for_verified(self) -> None:
        bench = SWEBenchVerified()
        # Inject one synthetic task so harness.run() does at least one
        # iteration and we observe a complete run.
        from chimera.eval.benchmarks.swe_bench import SWEBenchInstance

        bench.add_instance(
            SWEBenchInstance(
                instance_id="t1",
                repo="org/repo",
                base_commit="abc",
                problem_statement="Fix something.",
            )
        )
        agent = _make_agent()

        harness = Harness(bench, agent)
        result = harness.run()

        # IPython tool wired BEFORE the agent ran.
        assert "ipython" in [t.name for t in agent.tools]
        # Condensation wired BEFORE the agent ran.
        assert isinstance(agent.loop.config.condensation, SummaryCompaction)
        assert agent.loop.config.condense_every_n_steps == 25
        # max_steps bumped.
        assert agent.loop.max_steps == 500
        # And the run actually executed.
        assert result.total == 1
        assert agent.run.call_count == 1

    def test_harness_no_op_when_benchmark_lacks_prepare_agent(self) -> None:
        """Benchmarks without ``prepare_agent`` are unaffected."""

        class _Plain:
            def name(self) -> str:
                return "plain"

            def tasks(self) -> list[dict[str, Any]]:
                return [{"id": "x", "prompt": "hi"}]

            def evaluate(
                self, task: dict[str, Any], output: str, env: Any
            ) -> bool:
                return True

        agent = _make_agent()
        before_tools = [t.name for t in agent.tools]
        before_max = agent.loop.max_steps
        before_cond = agent.loop.config.condensation

        harness = Harness(_Plain(), agent)
        harness.run()

        # Untouched.
        assert [t.name for t in agent.tools] == before_tools
        assert agent.loop.max_steps == before_max
        assert agent.loop.config.condensation is before_cond


# --------------------------------------------------------------------------- #
# for_verified() recommended config still works after wiring                  #
# --------------------------------------------------------------------------- #


class TestForVerifiedRecommendedConfig:
    def test_for_verified_drives_full_wiring(self) -> None:
        cfg = SWEBenchConfig.for_verified()
        assert cfg.ipython is True
        assert cfg.condense_every_n_steps == 25
        assert cfg.max_steps == 500

        bench = SWEBenchVerified(
            max_steps=cfg.max_steps,
            ipython=cfg.ipython,
            condense_every_n_steps=cfg.condense_every_n_steps,
        )
        agent = _make_agent()
        bench.prepare_agent(agent)

        assert "ipython" in [t.name for t in agent.tools]
        assert isinstance(agent.loop.config.condensation, SummaryCompaction)
        assert agent.loop.config.condense_every_n_steps == 25
        assert agent.loop.max_steps == 500


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
