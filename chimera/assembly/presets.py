"""Named presets for assembling different agent configurations."""
from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = ["AssemblyConfig", "PRESETS", "DEPRECATED_PRESET_ALIASES"]


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


# Map deprecated preset keys -> canonical replacement key.
# Both keys remain in PRESETS for one release; consumers should migrate to
# the canonical key. The CLI/runtime emits a DeprecationWarning when a
# deprecated key is used.
DEPRECATED_PRESET_ALIASES: dict[str, str] = {
    "claude_code": "coding_agent",
}


PRESETS: dict[str, AssemblyConfig] = {
    "coding_agent": AssemblyConfig(
        name="coding_agent",
        description="Full-featured coding agent (canonical preset)",
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
    "kimi": AssemblyConfig(
        name="kimi",
        description="Action-first agent (Kimi style) — KISS, iterate on failures",
        tool_set="coding",
        permissions=True,
        hooks=False,
        transcripts=True,
        max_turns=50,
    ),
    "swebench": AssemblyConfig(
        name="swebench",
        description="Benchmark agent optimized for SWE-bench — minimal edits, root cause focus",
        tool_set="coding",
        permissions=False,
        hooks=False,
        transcripts=False,
        content_replacement=False,
        compaction=False,
        streaming=False,
        max_turns=30,
    ),
}

# Register deprecated aliases pointing at the canonical preset's config.
# WHY: callers using the legacy key get an identical Agent (same tools, same
# prompt, same flags) — the alias is structural, not just textual. Keeping
# `name` as the legacy key preserves prompt lookup behavior in
# `system_prompts.PRESET_PROMPTS`, which still contains the legacy key.
for _legacy_key, _canonical_key in DEPRECATED_PRESET_ALIASES.items():
    if _legacy_key not in PRESETS and _canonical_key in PRESETS:
        PRESETS[_legacy_key] = replace(PRESETS[_canonical_key], name=_legacy_key)
