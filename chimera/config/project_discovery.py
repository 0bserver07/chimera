"""Auto-discover and load project configuration from CHIMERA.md or CLAUDE.md.

Walks up the directory tree to find config files. Parses markdown with YAML
frontmatter for: model preferences, tool permissions, custom instructions,
environment settings.

Inspired by Claude Code's CLAUDE.md auto-loading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Config filenames to search for, in priority order
CONFIG_FILENAMES = [
    "CHIMERA.md",
    "CLAUDE.md",
    ".chimera.md",
    ".claude.md",
]


@dataclass
class ProjectInstructions:
    """Parsed project-level instructions.

    Args:
        model: Preferred model name.
        tools: Tool permission overrides (allow/deny lists).
        instructions: Custom system prompt additions.
        environment: Environment configuration.
        raw_markdown: The full markdown content.
        source_path: Path to the config file.
    """

    model: str | None = None
    tools: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    raw_markdown: str = ""
    source_path: str = ""

    @property
    def has_instructions(self) -> bool:
        """Whether any instructions were found."""
        return bool(self.instructions or self.model or self.tools)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and body from markdown."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return {}, content

    frontmatter_text = match.group(1)
    body = match.group(2)

    if yaml is not None:
        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
        except Exception:
            frontmatter = {}
    else:
        # Minimal YAML parsing without pyyaml — handle key: value lines
        frontmatter = {}
        for line in frontmatter_text.strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                frontmatter[key.strip()] = value.strip()

    return frontmatter, body


def discover_config(start_dir: str | Path) -> ProjectInstructions | None:
    """Walk up from *start_dir* to find a project config file.

    Args:
        start_dir: Directory to start searching from.

    Returns:
        Parsed ProjectInstructions, or None if no config file found.
    """
    current = Path(start_dir).resolve()

    while True:
        for filename in CONFIG_FILENAMES:
            config_path = current / filename
            if config_path.is_file():
                return _parse_config_file(config_path)

        parent = current.parent
        if parent == current:
            break  # Reached filesystem root
        current = parent

    return None


def _parse_config_file(path: Path) -> ProjectInstructions:
    """Parse a project config file."""
    content = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(content)

    return ProjectInstructions(
        model=frontmatter.get("model"),
        tools=frontmatter.get("tools", {}),
        instructions=body.strip(),
        environment=frontmatter.get("environment", {}),
        raw_markdown=content,
        source_path=str(path),
    )


def discover_all_configs(start_dir: str | Path) -> list[ProjectInstructions]:
    """Find ALL config files from *start_dir* up to root (for inheritance).

    Returns configs in order: most specific (deepest) first.
    """
    configs: list[ProjectInstructions] = []
    current = Path(start_dir).resolve()

    while True:
        for filename in CONFIG_FILENAMES:
            config_path = current / filename
            if config_path.is_file():
                configs.append(_parse_config_file(config_path))

        parent = current.parent
        if parent == current:
            break
        current = parent

    return configs
