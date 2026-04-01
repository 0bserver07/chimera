"""Named presets for assembling different agent configurations."""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AssemblyConfig", "PRESETS"]


@dataclass
class AssemblyConfig:
    """Configuration for assembling a coding agent."""

    name: str
    description: str
    tool_set: str = "coding"       # "coding", "minimal", "explore"
    system_prompt: str = ""
    permissions: bool = True
    hooks: bool = True
    transcripts: bool = True
    content_replacement: bool = True
    compaction: bool = True
    streaming: bool = True
    max_turns: int = 100
    model: str | None = None       # Override model


PRESETS: dict[str, AssemblyConfig] = {
    "claude_code": AssemblyConfig(
        name="claude_code",
        description="Full-featured coding agent (Claude Code style)",
        tool_set="coding",
        permissions=True,
        hooks=True,
        transcripts=True,
        content_replacement=True,
        compaction=True,
        streaming=True,
        max_turns=100,
    ),
    "codex": AssemblyConfig(
        name="codex",
        description="Code generation agent (Codex style)",
        tool_set="coding",
        permissions=True,
        hooks=False,
        transcripts=True,
        max_turns=50,
    ),
    "minimal": AssemblyConfig(
        name="minimal",
        description="Minimal agent with basic tools",
        tool_set="minimal",
        permissions=False,
        hooks=False,
        transcripts=False,
        max_turns=20,
    ),
    "explore": AssemblyConfig(
        name="explore",
        description="Read-only exploration agent",
        tool_set="explore",
        permissions=False,
        hooks=False,
        transcripts=False,
        max_turns=30,
    ),
}
