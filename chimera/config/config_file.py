"""YAML/JSON configuration file loader using DiscriminatedUnion."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.compaction.base import CompactionStrategy
    from chimera.env.base import Environment
    from chimera.training.strategies.base import Strategy


class ChimeraConfig:
    """Load and resolve a full Chimera configuration from YAML or JSON.

    Example:
        ```python
        config = ChimeraConfig.from_file("chimera.yaml")
        env = config.create_environment()
        strategy = config.create_strategy()
        ```
    """

    @classmethod
    def from_file(cls, path: str | Path) -> ChimeraConfig:
        """Load configuration from a YAML or JSON file.

        Args:
            path: Path to the config file.

        Returns:
            A ChimeraConfig instance.
        """
        path = Path(path)
        text = path.read_text()
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
                data = yaml.safe_load(text)
            except ImportError:
                data = _parse_simple_yaml(text)
        else:
            data = json.load(open(path))
        return cls(data or {})

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def create_environment(self) -> Environment:
        """Create an Environment from the config's ``environment`` section.

        Returns:
            An Environment subclass instance.
        """
        from chimera.env.base import Environment
        env: Environment = Environment.from_config(self.data.get("environment", {"type": "local"}))
        return env

    def create_strategy(self) -> Strategy:
        """Create a Strategy from the config's ``training.strategy`` section.

        Returns:
            A Strategy subclass instance.
        """
        from chimera.training.strategies.base import Strategy
        strategy: Strategy = Strategy.from_config(
            self.data.get("training", {}).get("strategy", {"type": "test_convergence"})
        )
        return strategy

    def create_compaction(self) -> CompactionStrategy | None:
        """Create a CompactionStrategy from the config's ``compaction`` section.

        Returns:
            A CompactionStrategy subclass instance, or None if not configured.
        """
        comp = self.data.get("compaction")
        if comp:
            from chimera.compaction.base import CompactionStrategy
            strategy: CompactionStrategy = CompactionStrategy.from_config(comp)
            return strategy
        return None


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML parser for simple key-value configs.

    Handles nested dicts (by indentation), strings, numbers, lists.
    This is a fallback when PyYAML is not installed.
    """
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        # Pop stack to find parent
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        if ":" not in stripped:
            continue

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        parent = stack[-1][1]

        if not raw_value:
            # Nested dict
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        elif raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1]
            parent[key] = [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
        else:
            # Try number
            raw_value = raw_value.strip("\"'")
            try:
                parent[key] = int(raw_value)
            except ValueError:
                try:
                    parent[key] = float(raw_value)
                except ValueError:
                    parent[key] = raw_value

    return result
