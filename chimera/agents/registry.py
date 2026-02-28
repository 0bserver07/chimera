"""Agent registry: collect and look up AgentConfig instances by name."""
from __future__ import annotations

from pathlib import Path

from chimera.agents.config import AgentConfig

__all__ = ["AgentRegistry"]


class AgentRegistry:
    """In-memory registry of named :class:`AgentConfig` instances.

    Configs can be registered programmatically or bulk-loaded from a
    directory of ``.md`` files with YAML frontmatter.
    """

    def __init__(self) -> None:
        self._configs: dict[str, AgentConfig] = {}

    # -- Mutation -------------------------------------------------------------

    def register(self, config: AgentConfig) -> None:
        """Add (or overwrite) a config keyed by ``config.name``."""
        self._configs[config.name] = config

    # -- Lookup ---------------------------------------------------------------

    def get(self, name: str) -> AgentConfig | None:
        """Return the config for *name*, or ``None`` if not found."""
        return self._configs.get(name)

    def list(self) -> list[str]:
        """Return all registered config names in insertion order."""
        return list(self._configs.keys())

    # -- Bulk loading ---------------------------------------------------------

    def load_directory(self, path: str) -> None:
        """Load every ``.md`` file in *path* as an :class:`AgentConfig`.

        Files are processed in sorted order for deterministic results.
        Non-existent or non-directory paths are silently ignored.
        """
        dir_path = Path(path)
        if not dir_path.is_dir():
            return
        for md_file in sorted(dir_path.glob("*.md")):
            config = AgentConfig.from_markdown(str(md_file))
            self.register(config)
