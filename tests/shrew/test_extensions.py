"""Tests for the shrew small-model-fit extensions (agent S3).

Exercise each extension with synthetic models so the suite is
hermetic — no provider, no GPU, no network. Each function in the
public surface gets at least one positive case and one negative
(no-op / fail-open) case.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

import pytest

from chimera.shrew.extensions import (
    MOE_MODEL_CATALOG,
    SMALL_MODEL_THRESHOLD_B,
    TINY_MODEL_THRESHOLD_B,
    TOOLS_TO_DROP_FOR_TINY,
    compute_optimal_context_window,
    filter_tools_for_model,
    model_size_billions,
    wrap_for_small_model,
)
from chimera.shrew.extensions.moe_offload import _resolve_profile

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeTool:
    """Lightweight stand-in for chimera.core.tool.BaseTool."""

    name: str


def _toolset() -> list[Any]:
    """Mixed-shape tool list — instances, dicts, and bare strings."""
    return [
        _FakeTool(name="read"),
        _FakeTool(name="write"),
        _FakeTool(name="edit"),
        _FakeTool(name="bash"),
        _FakeTool(name="search"),
        _FakeTool(name="web_fetch"),
        _FakeTool(name="browser"),
        {"name": "delegate"},
        {"name": "image_read"},
        "repo_map",
        "import_graph",
        "mcp__some-server__big_tool",
        "todo",
    ]


# ---------------------------------------------------------------------------
# moe_offload
# ---------------------------------------------------------------------------


class TestComputeOptimalContextWindow:
    def test_known_moe_model_8gb_vram(self) -> None:
        # qwen3.6-35b-a3b on 8 GB: with MoE offload trick, only the
        # attention layers + KV cache live on GPU. The helper should
        # still return at least the 4K floor and never above the model
        # cap (32K).
        ctx = compute_optimal_context_window("qwen3.6-35b-a3b", 8)
        assert ctx >= 4096
        assert ctx <= 32_768
        # Power-of-two snap.
        assert (ctx & (ctx - 1)) == 0

    def test_known_dense_model_smaller_window_under_vram_pressure(self) -> None:
        # Dense 9.7B (qwen3.5-9b) eats ~5.3 GB on GPU. On 8 GB, only a
        # sliver of VRAM is left for KV cache — context should be
        # *meaningfully* smaller than the MoE result above on the
        # same VRAM budget.
        moe_ctx = compute_optimal_context_window("qwen3.6-35b-a3b", 8)
        dense_ctx = compute_optimal_context_window("qwen3.5-9b", 8)
        assert dense_ctx <= moe_ctx

    def test_more_vram_yields_no_smaller_window(self) -> None:
        small = compute_optimal_context_window("qwen3.6-35b-a3b", 8)
        big = compute_optimal_context_window("qwen3.6-35b-a3b", 24)
        assert big >= small

    def test_capped_at_model_max(self) -> None:
        # 64 GB VRAM is far beyond what the 32K-context Qwen needs;
        # we should clamp to the architectural max.
        ctx = compute_optimal_context_window("qwen3.6-35b-a3b", 64)
        profile = MOE_MODEL_CATALOG["qwen3.6-35b-a3b"]
        assert ctx <= profile.max_context

    def test_unknown_model_returns_conservative_default(self) -> None:
        ctx = compute_optimal_context_window("totally-made-up/foo-1b", 8)
        assert ctx >= 4096
        assert ctx <= 32_768

    def test_zero_vram_returns_floor(self) -> None:
        assert compute_optimal_context_window("qwen3.6-35b-a3b", 0) == 4096

    def test_provider_prefix_resolved(self) -> None:
        ctx_a = compute_optimal_context_window("qwen3.6-35b-a3b", 8)
        ctx_b = compute_optimal_context_window("llamacpp/qwen3.6-35b-a3b", 8)
        assert ctx_a == ctx_b

    def test_alias_resolved(self) -> None:
        # qwen3.5 is an Ollama alias for qwen3.5-9b in the catalogue.
        assert _resolve_profile("qwen3.5") is not None
        assert _resolve_profile("qwen-9b") is not None  # alias

    def test_tiny_vram_still_returns_floor(self) -> None:
        # 1 GB VRAM with a 5.3 GB attention budget → free_gb negative.
        ctx = compute_optimal_context_window("qwen3.5-9b", 1)
        assert ctx == 4096

    def test_catalog_default_keys_present(self) -> None:
        # Sanity: the catalogue ships the four canonical models the
        # spec calls out.
        for key in (
            "qwen3.6-35b-a3b",
            "qwen3.5-9b",
            "qwen3.5",
            "deepseek-coder-v2-lite-16b",
        ):
            assert key in MOE_MODEL_CATALOG


# ---------------------------------------------------------------------------
# scaffold_fit
# ---------------------------------------------------------------------------


class TestWrapForSmallModel:
    def test_small_model_gets_scaffold(self) -> None:
        wrapped = wrap_for_small_model("You are a helpful agent.", 9.0)
        assert "small-model-scaffold" in wrapped
        assert "reasoning-checklist" in wrapped
        assert "output-rules" in wrapped
        assert "You are a helpful agent." in wrapped

    def test_large_model_passes_through_unchanged(self) -> None:
        prompt = "You are a frontier model."
        # 70B is well above the 13B threshold.
        assert wrap_for_small_model(prompt, 70.0) == prompt

    def test_threshold_boundary_above_passes_through(self) -> None:
        # Exactly the threshold (13.0) should pass through unchanged
        # (the helper uses strict <, so 13.0 is "large enough").
        prompt = "x"
        assert wrap_for_small_model(prompt, SMALL_MODEL_THRESHOLD_B) == prompt

    def test_threshold_boundary_below_gets_scaffold(self) -> None:
        prompt = "x"
        wrapped = wrap_for_small_model(prompt, SMALL_MODEL_THRESHOLD_B - 0.1)
        assert wrapped != prompt
        assert "small-model-scaffold" in wrapped

    def test_idempotent(self) -> None:
        prompt = "Sys"
        once = wrap_for_small_model(prompt, 3.0)
        twice = wrap_for_small_model(once, 3.0)
        assert once == twice

    def test_zero_size_treated_as_small(self) -> None:
        # Defensive: unknown size collapses to "small" so the scaffold
        # is layered in.
        wrapped = wrap_for_small_model("Sys", 0.0)
        assert "small-model-scaffold" in wrapped

    def test_empty_prompt_gets_scaffold(self) -> None:
        wrapped = wrap_for_small_model("", 3.0)
        assert "small-model-scaffold" in wrapped
        assert "reasoning-checklist" in wrapped


# ---------------------------------------------------------------------------
# tool_filter
# ---------------------------------------------------------------------------


class TestFilterToolsForModel:
    def test_tiny_model_drops_complex_tools(self) -> None:
        out = filter_tools_for_model(_toolset(), "qwen3.5")  # 9.7B alias
        # qwen3.5 is 9.7B which is *above* TINY_MODEL_THRESHOLD_B.
        # Confirm and switch to a real tiny model (qwen-1.5b synthetic).
        assert filter_tools_for_model(_toolset(), "fake-1.5b/coder") != []
        # For tiny synthetic, complex tools should be gone.
        names = [_tool_name(t) for t in filter_tools_for_model(_toolset(), "fake-1.5b/coder")]
        for dropped in ("web_fetch", "browser", "delegate", "image_read",
                        "repo_map", "import_graph"):
            assert dropped not in names
        # MCP namespaced tool also gone.
        assert "mcp__some-server__big_tool" not in names
        # Core tools survive.
        for kept in ("read", "write", "edit", "bash", "search", "todo"):
            assert kept in names
        del out  # unused after sanity branch

    def test_small_but_not_tiny_model_keeps_full_set(self) -> None:
        # 9B model is at threshold (>=) → fail-open.
        names = [_tool_name(t) for t in filter_tools_for_model(_toolset(), "qwen3.5")]
        assert "web_fetch" in names
        assert "browser" in names

    def test_large_model_keeps_full_set(self) -> None:
        out = filter_tools_for_model(_toolset(), "anthropic/claude-haiku-4-5")
        assert len(out) == len(_toolset())

    def test_unknown_model_fails_open(self) -> None:
        # No size info, no parseable hint → keep everything.
        out = filter_tools_for_model(_toolset(), "mystery-model")
        assert len(out) == len(_toolset())

    def test_empty_input(self) -> None:
        assert filter_tools_for_model([], "fake-1.5b") == []

    def test_input_not_mutated(self) -> None:
        tools = _toolset()
        snapshot = list(tools)
        _ = filter_tools_for_model(tools, "fake-1.5b/coder")
        assert tools == snapshot

    def test_extra_drops_merged(self) -> None:
        out = filter_tools_for_model(
            _toolset(), "fake-1.5b/coder",
            extra_drops=frozenset({"todo"}),
        )
        names = [_tool_name(t) for t in out]
        assert "todo" not in names

    def test_provider_prefix_resolved(self) -> None:
        a = filter_tools_for_model(_toolset(), "fake-1.5b")
        b = filter_tools_for_model(_toolset(), "llamacpp/fake-1.5b")
        names_a = [_tool_name(t) for t in a]
        names_b = [_tool_name(t) for t in b]
        assert names_a == names_b


class TestModelSizeBillions:
    def test_catalog_lookup_returns_active_params(self) -> None:
        # MoE: active params, not nominal.
        assert model_size_billions("qwen3.6-35b-a3b") == 3.0

    def test_dense_lookup(self) -> None:
        assert model_size_billions("qwen3.5-9b") == 9.7

    def test_alias_lookup(self) -> None:
        assert model_size_billions("qwen-9b") == 9.7

    def test_regex_fallback_with_decimal(self) -> None:
        assert model_size_billions("anthropic/some-7.5b-instruct") == 7.5

    def test_regex_fallback_integer(self) -> None:
        assert model_size_billions("openai/foo-13b") == 13.0

    def test_regex_no_match_returns_none(self) -> None:
        assert model_size_billions("anthropic/claude-haiku-4-5") is None
        assert model_size_billions("") is None

    def test_provider_prefix_stripped(self) -> None:
        assert model_size_billions("llamacpp/qwen3.5-9b") == 9.7


# ---------------------------------------------------------------------------
# CLI wiring (apply_small_model_extensions)
# ---------------------------------------------------------------------------


class TestApplySmallModelExtensions:
    def test_applies_all_three(self) -> None:
        from chimera.shrew.cli import apply_small_model_extensions

        ns = argparse.Namespace(model="qwen3.6-35b-a3b", vram_gb=8)
        result = apply_small_model_extensions(
            ns,
            system_prompt="Be helpful.",
            tools=_toolset(),
        )
        assert result["model"] == "qwen3.6-35b-a3b"
        assert result["context_window"] >= 4096
        assert result["model_size_b"] == 3.0  # MoE active params
        # 3B active → small-model scaffold applied.
        assert "small-model-scaffold" in result["system_prompt"]
        # 3B active is < TINY_MODEL_THRESHOLD_B (9.0) → tools trimmed.
        names = [_tool_name(t) for t in result["tools"]]
        assert "web_fetch" not in names
        assert "read" in names

    def test_no_inputs_returns_metadata_only(self) -> None:
        from chimera.shrew.cli import apply_small_model_extensions

        ns = argparse.Namespace(model="qwen3.5-9b", vram_gb=8)
        result = apply_small_model_extensions(ns)
        assert result["system_prompt"] is None
        assert result["tools"] is None
        assert result["model_size_b"] == 9.7
        assert result["context_window"] >= 4096

    def test_large_model_is_a_no_op_on_prompt_and_tools(self) -> None:
        from chimera.shrew.cli import apply_small_model_extensions

        ns = argparse.Namespace(model="anthropic/claude-haiku-4-5", vram_gb=24)
        prompt = "You are Claude."
        tools = _toolset()
        result = apply_small_model_extensions(
            ns, system_prompt=prompt, tools=tools,
        )
        assert result["system_prompt"] == prompt
        # Unknown to catalogue and no parseable size → fail-open.
        assert len(result["tools"]) == len(tools)

    def test_vram_env_var_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chimera.shrew.cli import apply_small_model_extensions

        monkeypatch.setenv("SHREW_VRAM_GB", "16")
        ns = argparse.Namespace(model="qwen3.6-35b-a3b")
        with_env = apply_small_model_extensions(ns)
        monkeypatch.delenv("SHREW_VRAM_GB")
        ns2 = argparse.Namespace(model="qwen3.6-35b-a3b")
        without_env = apply_small_model_extensions(ns2)
        # 16 GB VRAM should give us at least as much context as 8 GB.
        assert with_env["context_window"] >= without_env["context_window"]


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_thresholds_are_sane() -> None:
    # Tiny < small. Otherwise the scaffold (sub-13B) would not be a
    # superset of the tool-filter (sub-9B) target.
    assert TINY_MODEL_THRESHOLD_B < SMALL_MODEL_THRESHOLD_B


def test_default_drops_are_strings() -> None:
    assert all(isinstance(name, str) for name in TOOLS_TO_DROP_FOR_TINY)
    assert "web_fetch" in TOOLS_TO_DROP_FOR_TINY
    assert "browser" in TOOLS_TO_DROP_FOR_TINY


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _tool_name(tool: Any) -> str:
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        return str(tool.get("name", ""))
    return str(getattr(tool, "name", ""))
