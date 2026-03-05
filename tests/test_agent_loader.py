"""Tests for agent loading and registry."""
from __future__ import annotations

import os
import tempfile

import pytest

from chimera.agents.config import AgentConfig
from chimera.agents.loader import create_default_registry, load_custom_agents
from chimera.agents.registry import AgentRegistry


class TestDefaultRegistry:
    def test_has_presets(self):
        registry = create_default_registry()
        names = registry.list()
        assert "build" in names
        assert "explore" in names
        assert "general" in names
        assert "plan" in names
        assert "review" in names

    def test_preset_count(self):
        registry = create_default_registry()
        assert len(registry.list()) == 5

    def test_get_build(self):
        registry = create_default_registry()
        config = registry.get("build")
        assert config is not None
        assert config.name == "build"
        assert len(config.tools) > 0

    def test_get_nonexistent(self):
        registry = create_default_registry()
        assert registry.get("nonexistent") is None


class TestCustomAgentLoading:
    def test_load_from_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "custom.md")
            with open(md, "w") as f:
                f.write("---\nname: custom-agent\ndescription: A test agent\ntools: [read_file, bash]\n---\nYou are a custom agent.\n")

            registry = AgentRegistry()
            loaded = load_custom_agents(registry, tmp)
            assert "custom-agent" in loaded
            config = registry.get("custom-agent")
            assert config is not None
            assert config.tools == ["read_file", "bash"]

    def test_load_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = AgentRegistry()
            loaded = load_custom_agents(registry, tmp)
            assert loaded == []

    def test_load_nonexistent_dir(self):
        registry = AgentRegistry()
        loaded = load_custom_agents(registry, "/nonexistent/path")
        assert loaded == []

    def test_load_skips_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Bad markdown (no frontmatter)
            with open(os.path.join(tmp, "bad.md"), "w") as f:
                f.write("No frontmatter here")
            # Good one
            with open(os.path.join(tmp, "good.md"), "w") as f:
                f.write("---\nname: good\ndescription: works\ntools: [bash]\n---\nGood agent.\n")

            registry = AgentRegistry()
            loaded = load_custom_agents(registry, tmp)
            assert "good" in loaded
            assert len(loaded) == 1

    def test_multiple_custom_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["alpha", "beta", "gamma"]:
                with open(os.path.join(tmp, f"{name}.md"), "w") as f:
                    f.write(f"---\nname: {name}\ndescription: Agent {name}\ntools: [bash]\n---\nAgent {name}.\n")

            registry = AgentRegistry()
            loaded = load_custom_agents(registry, tmp)
            assert len(loaded) == 3
            assert set(loaded) == {"alpha", "beta", "gamma"}
