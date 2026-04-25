"""Tests for the SWE-bench Verified adapter (issue #84).

These tests cover variant config, max-step plumbing, IPython tool
plumbing, and the LLM-condensation trigger. The dataset is mocked /
synthesized — no Docker, no live LLM, no upstream dataset is required.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.eval.benchmarks.swe_bench import SWEBenchInstance
from chimera.eval.benchmarks.swe_bench_verified import (
    DEFAULT_CONDENSE_EVERY_N_STEPS,
    DEFAULT_LITE_MAX_STEPS,
    DEFAULT_VERIFIED_MAX_STEPS,
    SWEBenchConfig,
    SWEBenchVerified,
)


@pytest.fixture
def verified_dataset() -> Any:
    """Synthesise a tiny Verified-shape dataset (JSONL)."""
    rows = [
        {
            "instance_id": "django__django__1",
            "repo": "django/django",
            "base_commit": "abc1",
            "problem_statement": "Fix the timezone bug",
            "test_patch": "diff --git a/tests/test_tz.py b/tests/test_tz.py\n",
        },
        {
            "instance_id": "sympy__sympy__1",
            "repo": "sympy/sympy",
            "base_commit": "def2",
            "problem_statement": "Make Matrix.inv stable",
        },
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False,
    ) as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        path = f.name
    yield path
    os.unlink(path)


# --------------------------------------------------------------------------- #
# Config dataclass                                                            #
# --------------------------------------------------------------------------- #


class TestSWEBenchConfig:
    def test_default_is_verified_500(self) -> None:
        cfg = SWEBenchConfig()
        assert cfg.variant == "verified"
        assert cfg.max_steps == DEFAULT_VERIFIED_MAX_STEPS == 500
        assert cfg.ipython is True
        assert cfg.condense_every_n_steps == DEFAULT_CONDENSE_EVERY_N_STEPS

    def test_for_lite_uses_100_steps_no_ipython(self) -> None:
        cfg = SWEBenchConfig.for_lite()
        assert cfg.variant == "lite"
        assert cfg.max_steps == DEFAULT_LITE_MAX_STEPS == 100
        assert cfg.ipython is False
        assert cfg.condense_every_n_steps == 0

    def test_for_verified_uses_500_steps_with_ipython(self) -> None:
        cfg = SWEBenchConfig.for_verified()
        assert cfg.variant == "verified"
        assert cfg.max_steps == 500
        assert cfg.ipython is True

    def test_overrides_propagate(self) -> None:
        cfg = SWEBenchConfig.for_verified(max_steps=200, ipython=False)
        assert cfg.max_steps == 200
        assert cfg.ipython is False

    def test_invalid_variant_rejected(self) -> None:
        with pytest.raises(ValueError, match="variant"):
            SWEBenchConfig(variant="full")

    def test_zero_max_steps_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_steps"):
            SWEBenchConfig(max_steps=0)

    def test_negative_condense_rejected(self) -> None:
        with pytest.raises(ValueError, match="condense_every_n_steps"):
            SWEBenchConfig(condense_every_n_steps=-1)


# --------------------------------------------------------------------------- #
# Adapter — loading + naming                                                  #
# --------------------------------------------------------------------------- #


class TestSWEBenchVerifiedAdapter:
    def test_name_is_verified(self) -> None:
        bench = SWEBenchVerified()
        assert bench.name() == "swe-bench-verified"

    def test_loads_jsonl(self, verified_dataset: str) -> None:
        bench = SWEBenchVerified(dataset_path=verified_dataset)
        tasks = bench.tasks()
        assert len(tasks) == 2
        assert tasks[0]["id"] == "django__django__1"
        assert tasks[0]["repo"] == "django/django"

    def test_skips_cleanly_without_dataset(self) -> None:
        # Mirrors the pattern used by the other scaffolded adapters:
        # no dataset means an empty task list, not an exception.
        bench = SWEBenchVerified()
        assert bench.tasks() == []

    def test_missing_dataset_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            SWEBenchVerified(dataset_path="/nope/does-not-exist.jsonl")

    def test_limit_is_respected(self, verified_dataset: str) -> None:
        bench = SWEBenchVerified(dataset_path=verified_dataset, limit=1)
        assert len(bench.tasks()) == 1

    def test_inherits_from_swe_bench(self) -> None:
        # It should be substitutable wherever SWEBench is accepted.
        from chimera.eval.benchmarks.swe_bench import SWEBench

        bench = SWEBenchVerified()
        assert isinstance(bench, SWEBench)

    def test_can_add_instance_programmatically(self) -> None:
        bench = SWEBenchVerified()
        bench.add_instance(
            SWEBenchInstance(
                instance_id="x",
                repo="r/r",
                base_commit="c",
                problem_statement="desc",
            )
        )
        assert len(bench.tasks()) == 1


# --------------------------------------------------------------------------- #
# Max-step plumbing                                                           #
# --------------------------------------------------------------------------- #


class TestMaxSteps:
    def test_default_is_500(self) -> None:
        bench = SWEBenchVerified()
        assert bench.max_steps == 500

    def test_max_steps_override(self) -> None:
        bench = SWEBenchVerified(max_steps=750)
        assert bench.max_steps == 750
        assert bench.config.max_steps == 750

    def test_lite_default_is_100(self) -> None:
        cfg = SWEBenchConfig.for_lite()
        assert cfg.max_steps == 100


# --------------------------------------------------------------------------- #
# IPython tool plumbing                                                       #
# --------------------------------------------------------------------------- #


class TestIPythonPlumbing:
    def test_ipython_enabled_by_default(self) -> None:
        bench = SWEBenchVerified()
        assert bench.ipython_enabled is True

    def test_ipython_can_be_disabled(self) -> None:
        bench = SWEBenchVerified(ipython=False)
        assert bench.ipython_enabled is False
        assert bench.build_ipython_tool() is None

    def test_build_ipython_tool_returns_tool(self) -> None:
        bench = SWEBenchVerified(ipython=True)
        tool = bench.build_ipython_tool()
        assert tool is not None
        # Sanity-check the schema looks like a real BaseTool.
        assert tool.name == "ipython"
        assert "code" in tool.parameters["properties"]
        assert "code" in tool.parameters.get("required", [])


# --------------------------------------------------------------------------- #
# LLM condensation trigger                                                    #
# --------------------------------------------------------------------------- #


class TestCondensation:
    def test_should_condense_fires_on_multiples(self) -> None:
        bench = SWEBenchVerified(condense_every_n_steps=10)
        # Step 0 never condenses (no history yet).
        assert bench.should_condense(0) is False
        assert bench.should_condense(5) is False
        assert bench.should_condense(10) is True
        assert bench.should_condense(20) is True
        assert bench.should_condense(21) is False

    def test_zero_disables_condensation(self) -> None:
        bench = SWEBenchVerified(condense_every_n_steps=0)
        assert bench.should_condense(10) is False
        assert bench.should_condense(100) is False
        assert bench.build_condensation() is None

    def test_build_condensation_returns_summary(self) -> None:
        from chimera.compaction.summary import SummaryCompaction

        bench = SWEBenchVerified(condense_every_n_steps=25)
        comp = bench.build_condensation()
        assert isinstance(comp, SummaryCompaction)
        assert comp.keep_first == 2
        assert comp.keep_last == 20

    def test_build_condensation_passes_provider(self) -> None:
        from chimera.compaction.summary import SummaryCompaction

        bench = SWEBenchVerified(condense_every_n_steps=25)
        provider = MagicMock()
        comp = bench.build_condensation(provider=provider)
        assert isinstance(comp, SummaryCompaction)
        # The provider is held for actual summarisation, not eagerly
        # invoked at construction.
        assert provider.complete.call_count == 0
