"""Agent configuration: dataclass + YAML-frontmatter markdown loader."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["AgentConfig"]

# ---------------------------------------------------------------------------
# Mapping from tool name -> tool singleton import path
# ---------------------------------------------------------------------------
_TOOL_REGISTRY: dict[str, str] = {
    "read_file": "chimera.tools:read_file",
    "write_file": "chimera.tools:write_file",
    "edit_file": "chimera.tools:edit_file",
    "bash": "chimera.tools:bash",
    "search": "chimera.tools:search",
    "list_files": "chimera.tools:list_files",
    "test": "chimera.tools:test",
    "web_fetch": "chimera.tools:web_fetch",
    "git": "chimera.tools:git",
    "replace_in_file": "chimera.tools:replace_in_file",
    "verify": "chimera.tools:verify",
    "repo_map": "chimera.tools.repo_map:RepoMapTool",
}

# ---------------------------------------------------------------------------
# Loop name -> class import path
# ---------------------------------------------------------------------------
_LOOP_REGISTRY: dict[str, str] = {
    "react": "chimera.core.loop:ReAct",
    "plan_execute": "chimera.core.loops.plan_execute:PlanAndExecute",
    "reflexion": "chimera.core.loops.reflexion:Reflexion",
}

# ---------------------------------------------------------------------------
# Permission preset name -> class import path
# ---------------------------------------------------------------------------
_PERMISSION_REGISTRY: dict[str, str] = {
    "auto_approve": "chimera.permissions.presets:AutoApprove",
    "always_deny": "chimera.permissions.presets:AlwaysDeny",
    "read_only": "chimera.permissions.presets:ReadOnly",
    "interactive": "chimera.permissions.presets:Interactive",
}


def _import_object(dotted: str) -> object:
    """Import 'module.path:AttributeName' and return the attribute."""
    module_path, attr_name = dotted.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, attr_name)


def _resolve_tool(name: str) -> object:
    """Resolve a tool name to a BaseTool instance."""
    dotted = _TOOL_REGISTRY.get(name)
    if dotted is None:
        raise ValueError(f"Unknown tool name: {name!r}")
    obj = _import_object(dotted)
    # Some entries point to classes (RepoMapTool), others to instances
    if isinstance(obj, type):
        return obj()
    return obj


# ---------------------------------------------------------------------------
# Minimal YAML frontmatter parser (no pyyaml dependency)
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    """Parse a minimal YAML-like frontmatter block.

    Handles:
      key: value              -> str
      key: [a, b, c]          -> list[str]
      key: 42                 -> str (caller converts)
    """
    result: dict[str, str | list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1)
        raw_value = match.group(2).strip()
        # List in [a, b, c] format
        if raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1]
            items = [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
            result[key] = items
        else:
            # Strip surrounding quotes
            result[key] = raw_value.strip("\"'")
    return result


# ---------------------------------------------------------------------------
# AgentConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Declarative configuration for building an Agent."""

    name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    permissions: str = "auto_approve"
    loop: str = "react"
    max_steps: int = 50
    model: str | None = None
    triggers: list[str] = field(default_factory=list)

    # -- Factory methods -----------------------------------------------------

    @classmethod
    def from_markdown(cls, path: str) -> AgentConfig:
        """Parse a ``.md`` file with YAML frontmatter.

        Expected format::

            ---
            name: my-agent
            description: A custom agent
            tools: [read_file, bash]
            permissions: interactive
            loop: react
            max_steps: 30
            ---
            You are a custom agent that...

        The body after the second ``---`` is used as the *system_prompt*.
        """
        text = Path(path).read_text()
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(
                f"Markdown file {path!r} must contain YAML frontmatter "
                "delimited by --- markers"
            )
        frontmatter_text = parts[1]
        body = parts[2].strip()

        fm = _parse_frontmatter(frontmatter_text)

        name = str(fm.get("name", Path(path).stem))
        description = str(fm.get("description", ""))
        tools_raw = fm.get("tools", [])
        tools = list(tools_raw) if isinstance(tools_raw, list) else []
        permissions = str(fm.get("permissions", "auto_approve"))
        loop = str(fm.get("loop", "react"))
        max_steps_raw = fm.get("max_steps", "50")
        max_steps = int(max_steps_raw) if isinstance(max_steps_raw, str) else 50
        model = fm.get("model")
        if isinstance(model, list):
            model = None

        return cls(
            name=name,
            description=description,
            system_prompt=body,
            tools=tools,
            permissions=permissions,
            loop=loop,
            max_steps=max_steps,
            model=str(model) if model else None,
        )

    # -- Build ---------------------------------------------------------------

    def build(self, provider: Provider, env: Environment | None = None) -> Agent:
        """Construct a fully-wired :class:`~chimera.core.agent.Agent`."""
        from chimera.core.agent import Agent
        from chimera.core.prompt import Prompt
        from chimera.core.tool import BaseTool

        # Resolve tools by name
        resolved_tools: list[BaseTool] = []
        for tool_name in self.tools:
            resolved_tools.append(_resolve_tool(tool_name))  # type: ignore[arg-type]

        # Resolve loop
        loop_dotted = _LOOP_REGISTRY.get(self.loop)
        if loop_dotted is None:
            raise ValueError(f"Unknown loop: {self.loop!r}")
        loop_cls = _import_object(loop_dotted)
        loop_instance = loop_cls(max_steps=self.max_steps)  # type: ignore[operator]

        # Build prompt
        prompt = Prompt.from_string(self.system_prompt)

        return Agent(
            provider=provider,
            tools=resolved_tools,
            loop=loop_instance,
            prompt=prompt,
            name=self.name,
        )
