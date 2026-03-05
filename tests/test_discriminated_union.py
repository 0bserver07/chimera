"""Tests for chimera.config.union — DiscriminatedUnion base class."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from chimera.config.union import DiscriminatedUnion


# ---------------------------------------------------------------------------
# Test hierarchy: Environments
# ---------------------------------------------------------------------------

class _TestEnv(DiscriminatedUnion):
    """Test environment hierarchy with its own registry."""
    _registry: dict[str, type] = {}


class _LocalTestEnv(_TestEnv):
    type_name = "local"

    def __init__(self, working_dir: str = ".", **kwargs: object) -> None:
        self.working_dir = working_dir


class _DockerTestEnv(_TestEnv):
    type_name = "docker"

    def __init__(self, image: str = "python:3.12", working_dir: str = "/workspace", **kwargs: object) -> None:
        self.image = image
        self.working_dir = working_dir


# ---------------------------------------------------------------------------
# Test hierarchy: Strategies (separate registry)
# ---------------------------------------------------------------------------

class _TestStrategy(DiscriminatedUnion):
    _registry: dict[str, type] = {}


class _TestConvergenceStrategy(_TestStrategy):
    type_name = "test_convergence"

    def __init__(self, max_iterations: int = 10, **kwargs: object) -> None:
        self.max_iterations = max_iterations


class _TreeSearchStrategy(_TestStrategy):
    type_name = "tree_search"

    def __init__(self, width: int = 3, **kwargs: object) -> None:
        self.width = width


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFromConfig:
    def test_local_env(self) -> None:
        env = _TestEnv.from_config({"type": "local", "working_dir": "/tmp"})
        assert isinstance(env, _LocalTestEnv)
        assert env.working_dir == "/tmp"

    def test_docker_env(self) -> None:
        env = _TestEnv.from_config({"type": "docker", "image": "node:20"})
        assert isinstance(env, _DockerTestEnv)
        assert env.image == "node:20"
        assert env.working_dir == "/workspace"

    def test_strategy_separate_registry(self) -> None:
        strat = _TestStrategy.from_config({"type": "test_convergence", "max_iterations": 5})
        assert isinstance(strat, _TestConvergenceStrategy)
        assert strat.max_iterations == 5

    def test_registries_are_isolated(self) -> None:
        assert "local" not in _TestStrategy._registry
        assert "test_convergence" not in _TestEnv._registry

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ValueError, match="must have a 'type' field"):
            _TestEnv.from_config({"working_dir": "/tmp"})

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown type 'banana'"):
            _TestEnv.from_config({"type": "banana"})

    def test_passthrough_instance(self) -> None:
        env = _LocalTestEnv(working_dir="/x")
        result = _TestEnv.from_config(env)
        assert result is env


class TestToConfig:
    def test_round_trip_local(self) -> None:
        env = _LocalTestEnv(working_dir="/app")
        config = env.to_config()
        assert config == {"type": "local", "working_dir": "/app"}

    def test_round_trip_docker(self) -> None:
        env = _DockerTestEnv(image="node:20", working_dir="/code")
        config = env.to_config()
        assert config["type"] == "docker"
        assert config["image"] == "node:20"
        assert config["working_dir"] == "/code"

    def test_round_trip_reconstruct(self) -> None:
        original = _DockerTestEnv(image="rust:latest")
        config = original.to_config()
        restored = _TestEnv.from_config(config)
        assert isinstance(restored, _DockerTestEnv)
        assert restored.image == "rust:latest"


class TestAvailableTypes:
    def test_env_types(self) -> None:
        types = _TestEnv.available_types()
        assert "local" in types
        assert "docker" in types

    def test_strategy_types(self) -> None:
        types = _TestStrategy.available_types()
        assert "test_convergence" in types
        assert "tree_search" in types


class TestNestedUnion:
    def test_nested_to_config(self) -> None:
        class _Outer(DiscriminatedUnion):
            _registry: dict[str, type] = {}

        class _Inner(DiscriminatedUnion):
            _registry: dict[str, type] = {}

        class _InnerA(_Inner):
            type_name = "a"
            def __init__(self, val: int = 1) -> None:
                self.val = val

        class _OuterX(_Outer):
            type_name = "x"
            def __init__(self, inner: _Inner | None = None) -> None:
                self.inner = inner or _InnerA()

        obj = _OuterX(inner=_InnerA(val=42))
        config = obj.to_config()
        assert config["type"] == "x"
        assert config["inner"] == {"type": "a", "val": 42}


class TestChimeraConfig:
    def test_from_json_file(self) -> None:
        from chimera.config.config_file import ChimeraConfig

        config_data = {
            "environment": {"type": "local", "working_dir": "/tmp"},
            "training": {
                "strategy": {"type": "test_convergence", "max_iterations": 5}
            },
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config = ChimeraConfig.from_file(f.name)

        assert config.data["environment"]["type"] == "local"

    def test_simple_yaml_parser(self) -> None:
        from chimera.config.config_file import _parse_simple_yaml

        yaml_text = """
environment:
  type: docker
  image: python:3.12
training:
  strategy:
    type: test_convergence
    max_iterations: 10
"""
        result = _parse_simple_yaml(yaml_text)
        assert result["environment"]["type"] == "docker"
        assert result["environment"]["image"] == "python:3.12"
        assert result["training"]["strategy"]["type"] == "test_convergence"
        assert result["training"]["strategy"]["max_iterations"] == 10

    def test_simple_yaml_list(self) -> None:
        from chimera.config.config_file import _parse_simple_yaml

        yaml_text = "tools: [bash, read_file, write_file]"
        result = _parse_simple_yaml(yaml_text)
        assert result["tools"] == ["bash", "read_file", "write_file"]
