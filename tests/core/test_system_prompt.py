"""Tests for chimera.core.system_prompt — Phase 5."""
from __future__ import annotations

from chimera.core.system_prompt import SystemPromptBuilder


class TestSystemPromptBuilder:
    """SystemPromptBuilder should assemble layers with fluent API."""

    def test_build_layers(self):
        prompt = (
            SystemPromptBuilder()
            .add_layer("base", "You are an assistant.")
            .add_layer("tools", "Available tools: read, write.")
            .build()
        )
        assert len(prompt.layers) == 2
        assert "You are an assistant." in prompt.to_string()
        assert "Available tools" in prompt.to_string()

    def test_cache_prefix_excludes_non_cacheable(self):
        prompt = (
            SystemPromptBuilder()
            .add_layer("base", "System instructions.", cacheable=True)
            .add_layer("env", "cwd=/tmp", cacheable=False)
            .add_layer("rules", "Follow rules.", cacheable=True)
            .build()
        )
        prefix = prompt.cache_prefix()
        assert "System instructions." in prefix
        assert "Follow rules." in prefix
        assert "cwd=/tmp" not in prefix

    def test_to_api_messages(self):
        prompt = (
            SystemPromptBuilder()
            .add_layer("base", "System.", cacheable=True)
            .add_layer("env", "env info", cacheable=False)
            .add_layer("rules", "Rules.", cacheable=True)
            .build()
        )
        msgs = prompt.to_api_messages()
        assert len(msgs) == 3
        # First cacheable layer (base) should have cache_control
        assert msgs[0]["text"] == "System."
        assert "cache_control" in msgs[0]
        # Non-cacheable layer (env) should NOT have cache_control
        assert msgs[1]["text"] == "env info"
        assert "cache_control" not in msgs[1]
        # Last cacheable layer (rules) is the last one overall — no cache_control
        # (spec: cacheable layers get cache_control EXCEPT the last one)
        assert msgs[2]["text"] == "Rules."
        assert "cache_control" not in msgs[2]
