"""Agent loading: discover and register presets + custom agents."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from chimera.agents.config import AgentConfig
from chimera.agents.registry import AgentRegistry

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.providers.base import Provider

# Preset configs (lazy-loaded to avoid circular imports)
_PRESET_NAMES = ["build", "explore", "general", "plan", "review"]


def create_default_registry() -> AgentRegistry:
    """Create a registry pre-loaded with all built-in presets."""
    registry = AgentRegistry()
    _load_presets(registry)
    return registry


def _load_presets(registry: AgentRegistry) -> None:
    """Load built-in preset configs into the registry."""
    from chimera.agents.presets.build import BUILD_CONFIG
    from chimera.agents.presets.explore import EXPLORE_CONFIG
    from chimera.agents.presets.general import GENERAL_CONFIG
    from chimera.agents.presets.plan import PLAN_CONFIG
    from chimera.agents.presets.review import REVIEW_CONFIG

    for config in [BUILD_CONFIG, EXPLORE_CONFIG, GENERAL_CONFIG, PLAN_CONFIG, REVIEW_CONFIG]:
        registry.register(config)


def load_custom_agents(registry: AgentRegistry, directory: str) -> list[str]:
    """Load custom agent configs from a directory of .md files.

    Args:
        registry: The registry to load configs into.
        directory: Path to a directory containing ``.md`` agent config files.

    Returns:
        List of loaded agent names.
    """
    loaded: list[str] = []
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return loaded
    for md_file in sorted(dir_path.glob("*.md")):
        try:
            config = AgentConfig.from_markdown(str(md_file))
            registry.register(config)
            loaded.append(config.name)
        except Exception:
            continue
    return loaded
