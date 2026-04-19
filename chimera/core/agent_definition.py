"""AgentDefinition: declarative agent configuration loaded from YAML/JSON.

Provides :class:`AgentDefinition` for describing an agent's name, model,
tools, and system prompt, and :class:`AgentDefinitionLoader` for discovering
and loading definitions from the filesystem.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

__all__ = ["AgentDefinition", "AgentDefinitionLoader"]

_YAML_EXTENSIONS = {".yaml", ".yml"}
_JSON_EXTENSIONS = {".json"}
_SUPPORTED_EXTENSIONS = _YAML_EXTENSIONS | _JSON_EXTENSIONS


@dataclass
class AgentDefinition:
    """Declarative description of an agent.

    Attributes:
        name: Unique agent name (used as a lookup key).
        description: Human-readable description of what the agent does.
        model: Model identifier to use, or ``None`` to use the default.
        tools: List of tool names the agent may use, or ``None`` for all.
        system_prompt: Custom system prompt, or ``None`` for the default.
    """

    name: str
    description: str
    model: str | None = None
    tools: list[str] | None = None
    system_prompt: str | None = None

    @classmethod
    def from_file(cls, path: Path) -> AgentDefinition:
        """Load an agent definition from a YAML or JSON file.

        Args:
            path: Path to the definition file.

        Returns:
            A populated :class:`AgentDefinition`.

        Raises:
            ValueError: If the file extension is not supported.
        """
        suffix = path.suffix.lower()

        if suffix in _YAML_EXTENSIONS:
            with open(path) as f:
                data: dict[str, Any] = yaml.safe_load(f)
        elif suffix in _JSON_EXTENSIONS:
            with open(path) as f:
                data = json.load(f)
        else:
            raise ValueError(
                f"Unsupported file extension '{suffix}'. "
                f"Expected one of: {sorted(_SUPPORTED_EXTENSIONS)}"
            )

        return cls(
            name=data["name"],
            description=data["description"],
            model=data.get("model"),
            tools=data.get("tools"),
            system_prompt=data.get("system_prompt"),
        )


@dataclass
class AgentDefinitionLoader:
    """Discovers and loads :class:`AgentDefinition` instances from directories.

    Attributes:
        search_paths: Directories to scan for agent definition files.
    """

    search_paths: list[Path] = field(default_factory=list)

    def load_all(self) -> dict[str, AgentDefinition]:
        """Load all agent definitions found in :attr:`search_paths`.

        Returns:
            Dictionary mapping agent name to :class:`AgentDefinition`.
            Files with unsupported extensions are silently skipped.
            Non-existent directories are silently skipped.
        """
        result: dict[str, AgentDefinition] = {}

        for search_path in self.search_paths:
            if not search_path.is_dir():
                continue
            for file_path in sorted(search_path.iterdir()):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                    continue
                defn = AgentDefinition.from_file(file_path)
                result[defn.name] = defn

        return result

    def get(self, name: str) -> AgentDefinition | None:
        """Look up a single agent definition by name.

        Loads all definitions and returns the one matching *name*,
        or ``None`` if not found.
        """
        all_defs = self.load_all()
        return all_defs.get(name)
