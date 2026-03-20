from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class Layer:
    """A single instruction layer."""
    name: str
    content: str
    priority: int = 0     # higher = appears first in prompt
    enabled: bool = True


class InstructionLayer:
    """Compose prompts from multiple layers.

    Instead of a single system prompt string, build it from
    composable layers: base instructions, personality, project
    context, tool guidance, user rules, etc.

    Each layer can be independently enabled/disabled, reordered,
    or replaced.

    Inspired by Codex's personality + instruction + project doc system.

    Usage:
        il = InstructionLayer()
        il.add("base", "You are a coding assistant.", priority=100)
        il.add("personality", "Be concise. No fluff.", priority=90)
        il.add("project", project_rules, priority=50)
        il.add("tools", "Available tools: ...", priority=10)

        prompt = il.render()
    """

    def __init__(self) -> None:
        self._layers: dict[str, Layer] = {}

    def add(self, name: str, content: str, priority: int = 0, enabled: bool = True) -> InstructionLayer:
        """Add or replace a layer. Returns self for chaining."""
        self._layers[name] = Layer(name=name, content=content, priority=priority, enabled=enabled)
        return self

    def remove(self, name: str) -> bool:
        """Remove a layer. Returns True if found."""
        if name in self._layers:
            del self._layers[name]
            return True
        return False

    def enable(self, name: str) -> None:
        """Enable a layer."""
        if name in self._layers:
            self._layers[name].enabled = True

    def disable(self, name: str) -> None:
        """Disable a layer without removing it."""
        if name in self._layers:
            self._layers[name].enabled = False

    def get(self, name: str) -> Layer | None:
        """Get a layer by name."""
        return self._layers.get(name)

    @property
    def layers(self) -> list[Layer]:
        """All layers, sorted by priority (highest first)."""
        return sorted(self._layers.values(), key=lambda layer: layer.priority, reverse=True)

    @property
    def active_layers(self) -> list[Layer]:
        """Enabled layers only, sorted by priority."""
        return [layer for layer in self.layers if layer.enabled]

    def render(self, **variables: str) -> str:
        """Render all active layers into a single prompt string.

        Each layer is separated by a blank line. Variables can be
        substituted using {variable_name} syntax.
        """
        parts = []
        for layer in self.active_layers:
            content = layer.content
            # Simple variable substitution
            for key, value in variables.items():
                content = content.replace(f"{{{key}}}", value)
            parts.append(content)
        return "\n\n".join(parts)

    def to_prompt(self, **variables: str) -> Any:
        """Convert to a Chimera Prompt object."""
        from chimera.core.prompt import Prompt
        return Prompt.from_string(self.render(**variables))

    def add_from_file(self, name: str, path: str, priority: int = 0) -> InstructionLayer:
        """Load a layer from a file."""
        with open(os.path.expanduser(path)) as f:
            content = f.read()
        return self.add(name, content, priority=priority)

    def add_from_directory(self, directory: str, priority_start: int = 50) -> InstructionLayer:
        """Load all .md files from a directory as layers.

        Files are sorted alphabetically. Priority decreases for each file.
        """
        directory = os.path.expanduser(directory)
        if not os.path.isdir(directory):
            return self

        files = sorted(f for f in os.listdir(directory) if f.endswith('.md'))
        for i, fname in enumerate(files):
            path = os.path.join(directory, fname)
            name = fname.replace('.md', '')
            self.add_from_file(name, path, priority=priority_start - i)
        return self

    # --- Presets ---

    @classmethod
    def coding_agent(cls, project_context: str = "") -> InstructionLayer:
        """Preset for a general coding agent."""
        il = cls()
        il.add("base", (
            "You are an expert coding agent. You can read files, write files, "
            "edit files, run shell commands, search code, run tests, and use git."
        ), priority=100)
        il.add("guidelines", (
            "Guidelines:\n"
            "- Read existing code before modifying it\n"
            "- Run tests after making changes\n"
            "- Be concise in explanations, thorough in code\n"
            "- Use the think tool to plan complex tasks before acting"
        ), priority=90)
        if project_context:
            il.add("project", f"# Project Context\n{project_context}", priority=50)
        return il

    @classmethod
    def reviewer(cls) -> InstructionLayer:
        """Preset for a code reviewer."""
        il = cls()
        il.add("base", (
            "You are a code reviewer. Analyze the code for bugs, security issues, "
            "performance problems, and style violations. Be specific and actionable."
        ), priority=100)
        il.add("format", (
            "For each issue found, provide:\n"
            "1. File and line number\n"
            "2. Severity (critical/warning/info)\n"
            "3. Description\n"
            "4. Suggested fix"
        ), priority=90)
        return il
