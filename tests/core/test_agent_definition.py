"""Tests for chimera.core.agent_definition — AgentDefinition and AgentDefinitionLoader."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from chimera.core.agent_definition import AgentDefinition, AgentDefinitionLoader


# ---------------------------------------------------------------------------
# Test 1: AgentDefinition creation with all fields
# ---------------------------------------------------------------------------


def test_agent_definition_creation():
    defn = AgentDefinition(
        name="test-agent",
        description="A test agent",
        model="claude-3-opus",
        tools=["bash", "read"],
        system_prompt="You are a helpful agent.",
    )

    assert defn.name == "test-agent"
    assert defn.description == "A test agent"
    assert defn.model == "claude-3-opus"
    assert defn.tools == ["bash", "read"]
    assert defn.system_prompt == "You are a helpful agent."


def test_agent_definition_optional_fields():
    defn = AgentDefinition(
        name="minimal",
        description="Minimal agent",
    )

    assert defn.name == "minimal"
    assert defn.description == "Minimal agent"
    assert defn.model is None
    assert defn.tools is None
    assert defn.system_prompt is None


# ---------------------------------------------------------------------------
# Test 2: from_file with YAML
# ---------------------------------------------------------------------------


def test_from_file_yaml():
    data = {
        "name": "yaml-agent",
        "description": "Loaded from YAML",
        "model": "claude-3-sonnet",
        "tools": ["bash", "grep"],
        "system_prompt": "You search code.",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        path = Path(f.name)

    try:
        defn = AgentDefinition.from_file(path)
        assert defn.name == "yaml-agent"
        assert defn.description == "Loaded from YAML"
        assert defn.model == "claude-3-sonnet"
        assert defn.tools == ["bash", "grep"]
        assert defn.system_prompt == "You search code."
    finally:
        path.unlink()


def test_from_file_yml_extension():
    data = {
        "name": "yml-agent",
        "description": "Loaded from .yml",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        path = Path(f.name)

    try:
        defn = AgentDefinition.from_file(path)
        assert defn.name == "yml-agent"
        assert defn.description == "Loaded from .yml"
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Test 3: from_file with JSON
# ---------------------------------------------------------------------------


def test_from_file_json():
    data = {
        "name": "json-agent",
        "description": "Loaded from JSON",
        "model": "gpt-4",
        "tools": ["write"],
        "system_prompt": "You write code.",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        path = Path(f.name)

    try:
        defn = AgentDefinition.from_file(path)
        assert defn.name == "json-agent"
        assert defn.description == "Loaded from JSON"
        assert defn.model == "gpt-4"
        assert defn.tools == ["write"]
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Test 4: from_file with unsupported extension raises error
# ---------------------------------------------------------------------------


def test_from_file_unsupported_extension():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write("[agent]\nname = 'test'\n")
        f.flush()
        path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="Unsupported"):
            AgentDefinition.from_file(path)
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Test 5: AgentDefinitionLoader.load_all
# ---------------------------------------------------------------------------


def test_loader_load_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)

        # Create two agent definition files
        (d / "agent1.yaml").write_text(yaml.dump({
            "name": "agent-one",
            "description": "First agent",
        }))
        (d / "agent2.json").write_text(json.dumps({
            "name": "agent-two",
            "description": "Second agent",
        }))
        # Non-agent file should be ignored
        (d / "notes.txt").write_text("not an agent")

        loader = AgentDefinitionLoader(search_paths=[d])
        agents = loader.load_all()

        assert "agent-one" in agents
        assert "agent-two" in agents
        assert len(agents) == 2
        assert agents["agent-one"].description == "First agent"
        assert agents["agent-two"].description == "Second agent"


# ---------------------------------------------------------------------------
# Test 6: AgentDefinitionLoader.get
# ---------------------------------------------------------------------------


def test_loader_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)

        (d / "explore.yaml").write_text(yaml.dump({
            "name": "explore",
            "description": "Explore codebase",
        }))

        loader = AgentDefinitionLoader(search_paths=[d])

        result = loader.get("explore")
        assert result is not None
        assert result.name == "explore"

        result_missing = loader.get("nonexistent")
        assert result_missing is None


# ---------------------------------------------------------------------------
# Test 7: AgentDefinitionLoader with multiple search paths
# ---------------------------------------------------------------------------


def test_loader_multiple_search_paths():
    with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
        d1 = Path(dir1)
        d2 = Path(dir2)

        (d1 / "a.yaml").write_text(yaml.dump({
            "name": "from-dir1",
            "description": "Agent from first directory",
        }))
        (d2 / "b.yaml").write_text(yaml.dump({
            "name": "from-dir2",
            "description": "Agent from second directory",
        }))

        loader = AgentDefinitionLoader(search_paths=[d1, d2])
        agents = loader.load_all()

        assert "from-dir1" in agents
        assert "from-dir2" in agents


# ---------------------------------------------------------------------------
# Test 8: AgentDefinitionLoader with empty search paths
# ---------------------------------------------------------------------------


def test_loader_empty_search_paths():
    loader = AgentDefinitionLoader(search_paths=[])
    agents = loader.load_all()
    assert agents == {}


def test_loader_nonexistent_path():
    loader = AgentDefinitionLoader(search_paths=[Path("/nonexistent/path")])
    agents = loader.load_all()
    assert agents == {}
