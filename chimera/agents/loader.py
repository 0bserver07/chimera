"""Agent loading: discover and register presets + custom agents.

Provides:
- ``create_default_registry()`` — registry with built-in presets
- ``load_custom_agents()`` — bulk-load ``.md`` files into a registry
- ``FileAgentDef`` — dataclass for file-based agent definitions
- ``AgentLoader`` — multi-source agent discovery with priority resolution
- ``AgentFactory`` — create Agent instances from file-based definitions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.agents.config import AgentConfig, _parse_frontmatter
from chimera.agents.registry import AgentRegistry

if TYPE_CHECKING:
    from chimera.config.skills import SkillRegistry
    from chimera.core.agent import Agent
    from chimera.core.tool import BaseTool
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


# ---------------------------------------------------------------------------
# File-based agent definitions (Spec 10)
# ---------------------------------------------------------------------------

@dataclass
class FileAgentDef:
    """Agent definition loaded from a Markdown file.

    Args:
        name: Agent name.
        description: Short description.
        system_prompt: Full system prompt (markdown body).
        model: Model override (e.g. ``claude-sonnet-4-6``).
        tools: List of tool names to include.
        loop: Loop type name (e.g. ``react``).
        max_iterations: Maximum loop iterations.
        triggers: Keyword triggers for agent selection.
        skills: Skill names to inject into the system prompt.
        source: Where this definition was loaded from.
        file_path: Path to the source file.
    """

    name: str
    description: str = ""
    system_prompt: str = ""
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    loop: str = "react"
    max_iterations: int = 50
    triggers: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    source: str = ""
    file_path: str = ""

    @classmethod
    def from_file(cls, path: Path, source: str = "project") -> FileAgentDef:
        """Parse agent definition from Markdown with YAML frontmatter.

        Args:
            path: Path to the ``.md`` file.
            source: Source label (``project``, ``user``, ``builtin``, ``plugin``).

        Returns:
            A FileAgentDef parsed from the file.
        """
        content = path.read_text()
        if not content.startswith("---"):
            return cls(
                name=path.stem,
                system_prompt=content.strip(),
                source=source,
                file_path=str(path),
            )

        parts = content.split("---", 2)
        if len(parts) < 3:
            return cls(
                name=path.stem,
                system_prompt=content.strip(),
                source=source,
                file_path=str(path),
            )

        meta = _parse_frontmatter(parts[1])
        body = parts[2].strip()

        name = str(meta.get("name", path.stem))
        tools_raw = meta.get("tools", [])
        tools = list(tools_raw) if isinstance(tools_raw, list) else []
        triggers_raw = meta.get("triggers", [])
        triggers = list(triggers_raw) if isinstance(triggers_raw, list) else []
        skills_raw = meta.get("skills", [])
        skills = list(skills_raw) if isinstance(skills_raw, list) else []
        max_iter_raw = meta.get("max_iterations", meta.get("max_steps", "50"))
        max_iterations = int(max_iter_raw) if isinstance(max_iter_raw, str) else 50
        model_raw = meta.get("model")
        model = str(model_raw) if model_raw and not isinstance(model_raw, list) else None

        return cls(
            name=name,
            description=str(meta.get("description", "")),
            system_prompt=body,
            model=model,
            tools=tools,
            loop=str(meta.get("loop", "react")),
            max_iterations=max_iterations,
            triggers=triggers,
            skills=skills,
            source=source,
            file_path=str(path),
        )


class AgentLoader:
    """Loads and resolves agent definitions from multiple sources.

    Priority (last loaded wins per name):
    1. Built-in (lowest)
    2. User (``~/.chimera/agents/``)
    3. Project (``.chimera/agents/`` in project root)
    Plugin and programmatic agents override via the AgentRegistry.

    Example:
        ```python
        loader = AgentLoader(project_root="/path/to/project")
        agent_def = loader.get("code-reviewer")
        ```
    """

    PROJECT_DIR = ".chimera/agents"
    USER_DIR = "~/.chimera/agents"

    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._agents: dict[str, FileAgentDef] = {}

    def load_all(self) -> dict[str, FileAgentDef]:
        """Load agents from all sources with priority resolution.

        Returns:
            Dictionary of agent name to FileAgentDef.
        """
        self._load_from_dir(self._builtin_dir(), "builtin")
        self._load_from_dir(Path(self.USER_DIR).expanduser(), "user")
        self._load_from_dir(self.project_root / self.PROJECT_DIR, "project")
        return dict(self._agents)

    def get(self, name: str) -> FileAgentDef | None:
        """Look up an agent definition by name.

        Args:
            name: Agent name.

        Returns:
            FileAgentDef if found, None otherwise.
        """
        if not self._agents:
            self.load_all()
        return self._agents.get(name)

    def list_agents(self) -> list[FileAgentDef]:
        """Return all loaded agent definitions.

        Returns:
            List of FileAgentDef instances.
        """
        if not self._agents:
            self.load_all()
        return list(self._agents.values())

    def find_by_trigger(self, keyword: str) -> list[FileAgentDef]:
        """Find agents whose triggers match a keyword.

        Args:
            keyword: Keyword to match (case-insensitive).

        Returns:
            List of matching FileAgentDef instances.
        """
        keyword = keyword.lower()
        return [
            agent for agent in self.list_agents()
            if any(keyword in t.lower() for t in agent.triggers)
        ]

    def _load_from_dir(self, directory: Path | None, source: str) -> None:
        if not directory or not directory.exists():
            return
        for path in sorted(directory.glob("*.md")):
            try:
                agent_def = FileAgentDef.from_file(path, source=source)
                self._agents[agent_def.name] = agent_def
            except Exception:
                pass

    def _builtin_dir(self) -> Path | None:
        try:
            import importlib.resources
            candidate = importlib.resources.files("chimera") / "builtin_agents"
            if hasattr(candidate, "is_dir") and candidate.is_dir():
                return Path(str(candidate))
        except Exception:
            pass
        return None


class AgentFactory:
    """Creates Agent instances from FileAgentDef.

    Args:
        provider: The LLM provider to use.
        tool_registry: Mapping of tool names to BaseTool instances.
        skill_registry: Optional SkillRegistry for skill injection.

    Example:
        ```python
        factory = AgentFactory(
            provider=provider,
            tool_registry={"bash": bash_tool, "read_file": read_tool},
        )
        agent = factory.create(agent_def)
        ```
    """

    def __init__(
        self,
        provider: Provider,
        tool_registry: dict[str, BaseTool],
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry

    def create(self, agent_def: FileAgentDef) -> Agent:
        """Create an Agent from a file-based definition.

        Args:
            agent_def: The file-based agent definition.

        Returns:
            A configured Agent instance.
        """
        from chimera.core.agent import Agent
        from chimera.core.prompt import Prompt

        tools = []
        for tool_name in agent_def.tools:
            tool = self.tool_registry.get(tool_name)
            if tool:
                tools.append(tool)

        system_prompt = agent_def.system_prompt
        if agent_def.skills and self.skill_registry:
            skill_context = self._resolve_skills(agent_def.skills)
            if skill_context:
                system_prompt = f"{system_prompt}\n\n{skill_context}"

        loop_cls = self._resolve_loop(agent_def.loop)
        loop_instance = loop_cls(max_steps=agent_def.max_iterations)

        prompt = Prompt.from_string(system_prompt)

        return Agent(
            provider=self.provider,
            tools=tools,
            loop=loop_instance,
            prompt=prompt,
            name=agent_def.name,
        )

    def _resolve_skills(self, skill_names: list[str]) -> str:
        sections = []
        for name in skill_names:
            skill = self.skill_registry.get(name)  # type: ignore[union-attr]
            if skill:
                sections.append(f"## Skill: {skill.name}\n{skill.content}")
        return "\n\n".join(sections)

    def _resolve_loop(self, loop_name: str) -> type:
        from chimera.agents.config import _import_object, _LOOP_REGISTRY
        dotted = _LOOP_REGISTRY.get(loop_name)
        if dotted:
            return _import_object(dotted)  # type: ignore[return-value]
        from chimera.core.loop import ReAct
        return ReAct
