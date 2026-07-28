"""Context assembly for system prompts.

Builds a multi-layer :class:`SystemPrompt` by gathering project
context, tool descriptions, environment details, and git status.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from chimera.core.system_prompt import SystemPrompt, SystemPromptBuilder
from chimera.core.tool import BaseTool
from chimera.config.paths import project_state_dir


class ContextAssembler:
    """Assembles a layered system prompt from project context.

    Args:
        project_dir: Root directory of the project.
        tools: List of tools available to the agent.
        model: Model identifier string.
    """

    def __init__(
        self,
        project_dir: Path,
        tools: list[BaseTool],
        model: str,
    ) -> None:
        self._project_dir = project_dir
        self._tools = tools
        self._model = model

    async def assemble(
        self,
        agent_definition: Any = None,
        user_append: str | None = None,
    ) -> SystemPrompt:
        """Assemble the full system prompt.

        Args:
            agent_definition: Either a plain string, an object with a
                ``system_prompt`` attribute (e.g. :class:`AgentDefinition`),
                or ``None`` to use the default prompt.
            user_append: Optional extra text appended as the last layer.

        Layers:
            1. Base prompt (agent definition or default)
            2. Tool descriptions (non-deferred tools)
            3. Environment details (cacheable=False)
            4. Project context (CHIMERA.md / CLAUDE.md)
            5. Git status (cacheable=False)
            6. User append (cacheable=False)
        """
        builder = SystemPromptBuilder()

        # Layer 1: base prompt
        try:
            if agent_definition is None:
                base = self._default_prompt()
            elif isinstance(agent_definition, str):
                base = agent_definition
            elif hasattr(agent_definition, "system_prompt"):
                sp = agent_definition.system_prompt
                base = sp if sp is not None else self._default_prompt()
            else:
                base = self._default_prompt()
        except Exception:
            base = self._default_prompt()
        builder.add_layer("base", base, cacheable=True)

        # Layer 2: tool descriptions
        tool_desc = self._build_tool_descriptions()
        if tool_desc:
            builder.add_layer("tools", tool_desc, cacheable=True)

        # Layer 3: environment details
        env_details = self._build_env_details()
        builder.add_layer("environment", env_details, cacheable=False)

        # Layer 4: project context
        project_ctx = self._load_project_context()
        if project_ctx:
            builder.add_layer("project_context", project_ctx, cacheable=True)

        # Layer 5: git status
        git_status = await self._get_git_status()
        if git_status:
            builder.add_layer("git_status", git_status, cacheable=False)

        # Layer 6: user append
        if user_append:
            builder.add_layer("user_append", user_append, cacheable=False)

        return builder.build()

    def _default_prompt(self) -> str:
        """Return the default system prompt."""
        return (
            "You are an expert software engineer. You help users understand "
            "and modify codebases. You use tools to read, search, and edit "
            "files. Always explain your reasoning."
        )

    def _build_tool_descriptions(self) -> str:
        """Build a text block describing available tools."""
        if not self._tools:
            return ""
        lines = ["# Available Tools", ""]
        for tool in self._tools:
            lines.append(f"- **{tool.name}**: {tool.description}")
        return "\n".join(lines)

    def _build_env_details(self) -> str:
        """Build environment details string."""
        now = datetime.now().strftime("%Y-%m-%d")
        return "\n".join([
            "# Environment",
            f"- Model: {self._model}",
            f"- Working directory: {self._project_dir}",
            f"- Platform: {sys.platform}",
            f"- Date: {now}",
        ])

    def _load_project_context(self) -> str | None:
        """Load project context from CHIMERA.md, CLAUDE.md, or .chimera/instructions.md."""
        candidates = [
            self._project_dir / "CHIMERA.md",
            self._project_dir / "CLAUDE.md",
            project_state_dir(self._project_dir) / "instructions.md",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except Exception:
                    continue
        return None

    async def _get_git_status(self) -> str | None:
        """Get git status via subprocess. Returns None on failure."""
        try:
            # Get current branch
            branch_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(self._project_dir), "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            branch_stdout, _ = await branch_proc.communicate()
            if branch_proc.returncode != 0:
                return None
            branch = branch_stdout.decode("utf-8", errors="replace").strip()

            # Get working tree status
            status_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(self._project_dir), "status", "--short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            status_stdout, _ = await status_proc.communicate()
            if status_proc.returncode != 0:
                return None
            status = status_stdout.decode("utf-8", errors="replace").strip()

            # Get recent commits
            log_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(self._project_dir), "log", "--oneline", "-n", "5",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log_stdout, _ = await log_proc.communicate()
            if log_proc.returncode != 0:
                return None
            log = log_stdout.decode("utf-8", errors="replace").strip()

            output = f"Current branch: {branch}\n\nStatus:\n{status}\n\nRecent commits:\n{log}"
            if len(output) > 2000:
                output = output[:2000]
            return output
        except Exception:
            return None
