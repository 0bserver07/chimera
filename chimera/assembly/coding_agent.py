"""CodingAgent: fully-assembled coding agent using all chimera layers.

This is the primary entry point for using chimera as a coding agent product.
It wires together all 8 phases of the architecture:

- Core loop (AgentLoop with streaming, error recovery)
- Sub-agent spawning (AgentSpawner with context isolation)
- State management (ContentReplacement, FileStateCache, Transcripts)
- Permissions (PermissionChecker with multi-source rules)
- System prompt (layered construction with git status, memory)
- Hooks (lifecycle events, function/command/prompt hooks)
- Commands (slash commands, skills, SkillTool)
- Production infrastructure (feature flags, analytics, memory)
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType

__all__ = ["CodingAgent"]


class CodingAgent:
    """A fully-assembled coding agent using all chimera layers.

    Usage::

        agent = CodingAgent(model="claude-sonnet-4-20250514")
        async for event in agent.run("Fix the bug in auth.py"):
            print(event)

    Or with a preset::

        agent = CodingAgent.from_preset("codex", model="gpt-4o")
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        project_dir: str | Path | None = None,
        preset: str = "claude_code",
        *,
        provider: Any = None,
        permission_callback: Any = None,
        tools_override: list | None = None,
    ) -> None:
        from chimera.assembly.presets import PRESETS
        from chimera.assembly.system_prompts import (
            CODING_AGENT_PROMPT,
            EXPLORE_PROMPT,
            MINIMAL_PROMPT,
        )
        from chimera.assembly.tool_sets import coding_tools, explore_tools, minimal_tools
        from chimera.core.abort import AbortSignal
        from chimera.core.content_replacement import ContentReplacementState
        from chimera.core.feature_flags import FeatureFlags
        from chimera.core.file_state_cache import FileStateCache
        from chimera.core.memory import PersistentMemory
        from chimera.core.task_manager import TaskManager
        from chimera.commands.input_handler import InputHandler
        from chimera.commands.processor import SlashCommandProcessor
        from chimera.commands.registry import CommandRegistry
        from chimera.providers.factory import create_provider

        # Load config
        config = PRESETS.get(preset, PRESETS["claude_code"])
        self._config = config
        self._project_dir = Path(project_dir or ".").resolve()

        # Feature flags
        FeatureFlags.from_env()

        # Provider — use injected provider or create from model name
        if provider is not None:
            self.provider = provider
        else:
            self.provider = create_provider(model=model)

        # Infrastructure
        self._file_cache = FileStateCache()
        self._task_manager = TaskManager()
        self._abort_signal = AbortSignal()
        self._command_registry = CommandRegistry()

        # Prompt selection
        prompt_map = {
            "coding": CODING_AGENT_PROMPT,
            "minimal": MINIMAL_PROMPT,
            "explore": EXPLORE_PROMPT,
        }
        tool_factory = {
            "coding": coding_tools,
            "minimal": minimal_tools,
            "explore": explore_tools,
        }
        self._system_prompt_text = prompt_map.get(
            config.tool_set, CODING_AGENT_PROMPT,
        )

        # Tools (must be built before the spawner, which needs them)
        if tools_override:
            self.tools = tools_override
        else:
            self.tools = tool_factory.get(config.tool_set, coding_tools)(
                file_cache=self._file_cache,
                task_manager=self._task_manager,
                command_registry=self._command_registry,
            )

        # Agent spawner (for sub-agents and forked skills)
        try:
            from chimera.core.agent_spawner import AgentSpawner

            self._spawner = AgentSpawner(
                provider=self.provider,
                available_tools=self.tools,
                task_manager=self._task_manager,
            )
        except Exception:
            self._spawner = None

        # Wire spawner into tools that need it
        for t in self.tools:
            if hasattr(t, "_spawner") and t._spawner is None and self._spawner is not None:
                t._spawner = self._spawner

        # Permissions (if enabled)
        self._permission_checker = None
        self._permission_context = None
        if config.permissions:
            try:
                from chimera.permissions.checker import PermissionChecker
                from chimera.permissions.loader import PermissionRuleLoader

                self._permission_checker = PermissionChecker()
                loader = PermissionRuleLoader(
                    project_dir=str(self._project_dir),
                    user_dir=str(Path.home() / ".chimera"),
                )
                self._permission_context = loader.load()

                # If no interactive callback, default to BYPASS mode
                # so tools aren't silently blocked in non-interactive use
                if permission_callback is None and self._permission_context is not None:
                    from chimera.permissions.modes import PermissionMode
                    from chimera.permissions.context import PermissionContext
                    # Replace with bypass mode
                    self._permission_context = PermissionContext(
                        mode=PermissionMode.BYPASS,
                        allow_rules=self._permission_context.allow_rules,
                        deny_rules=self._permission_context.deny_rules,
                        ask_rules=self._permission_context.ask_rules,
                        additional_working_dirs=self._permission_context.additional_working_dirs,
                        is_bypass_available=True,
                    )
            except Exception:
                pass

        # Hooks (if enabled)
        self._hook_executor = None
        self._hook_matchers: list | None = None
        if config.hooks:
            try:
                from chimera.hooks.executor import HookExecutor
                from chimera.hooks.loader import HookLoader

                self._hook_executor = HookExecutor()
                HookLoader(
                    project_dir=str(self._project_dir),
                    user_dir=str(Path.home() / ".chimera"),
                )
                self._hook_matchers = []
            except Exception:
                pass

        # Content replacement (if enabled)
        self._content_replacement = (
            ContentReplacementState() if config.content_replacement else None
        )

        # Transcripts (if enabled)
        self._transcript = None
        if config.transcripts:
            try:
                from chimera.sessions.transcript import TranscriptStorage

                session_id = str(uuid.uuid4())[:8]
                transcript_dir = self._project_dir / ".chimera" / "sessions"
                self._transcript = TranscriptStorage(transcript_dir, session_id)
            except Exception:
                pass

        # Compaction (if enabled)
        self._compaction = None
        if config.compaction:
            try:
                from chimera.core.compaction_integration import CompactionIntegration
                from chimera.core.token_estimator import TokenEstimator

                estimator = TokenEstimator()
                self._compaction = CompactionIntegration(estimator=estimator)
            except Exception:
                pass

        # Snapshot manager (undo/revert)
        from chimera.core.snapshot import SnapshotManager

        self._snapshot_manager = SnapshotManager(self._project_dir)

        # Memory
        self._memory = PersistentMemory(self._project_dir)

        # Input handler (slash commands)
        self._input_handler = InputHandler(
            processor=SlashCommandProcessor(self._command_registry),
        )

    @classmethod
    def from_preset(cls, preset: str, **kwargs: Any) -> CodingAgent:
        """Create a CodingAgent from a named preset."""
        return cls(preset=preset, **kwargs)

    async def run(self, task: str) -> AsyncGenerator[LoopEvent, None]:
        """Run the agent on a task, yielding LoopEvents."""
        # Check for slash command
        was_command, output = await self._input_handler.process(task)
        if was_command:
            yield LoopEvent(
                type=LoopEventType.system,
                data=output or "Command executed",
                turn=0,
            )
            return

        # Build system prompt
        from chimera.core.context_assembler import ContextAssembler

        assembler = ContextAssembler(
            project_dir=self._project_dir,
            tools=self.tools,
            model=getattr(self.provider, "model_name", "unknown"),
        )
        system_prompt = await assembler.assemble(
            user_append=self._system_prompt_text,
        )

        # Inject memory
        memory_content = self._memory.load()
        if memory_content:
            from chimera.core.system_prompt import SystemPromptBuilder

            system_prompt = (
                SystemPromptBuilder()
                .add_layer("assembled", system_prompt.to_string())
                .add_layer("memory", memory_content, cacheable=False)
                .build()
            )

        # Run the loop with snapshot tracking
        from chimera.core.agent_loop import AgentLoop
        from chimera.types import Message

        # Tools that modify files on disk
        _FILE_TOOLS = {"write_file", "edit_file", "bash", "replace_in_file"}

        loop = AgentLoop()
        turn_modified: list[str] = []
        last_turn = 0

        async for event in loop.run(
            messages=[Message.user(task)],
            tools=self.tools,
            provider=self.provider,
            system_prompt=system_prompt,
            max_turns=self._config.max_turns,
            abort_signal=self._abort_signal,
            permission_checker=self._permission_checker,
            permission_context=self._permission_context,
            hook_executor=self._hook_executor,
            hook_matchers=self._hook_matchers,
            transcript=self._transcript,
            content_replacement=self._content_replacement,
            compaction=self._compaction,
            stream=self._config.streaming,
        ):
            # Track modified files from tool_result events
            if event.type == LoopEventType.tool_result:
                tc, _result = event.data
                if tc.name in _FILE_TOOLS:
                    file_path = tc.arguments.get("path") or tc.arguments.get("file_path")
                    if file_path and file_path not in turn_modified:
                        turn_modified.append(file_path)

                # When the turn advances, take a snapshot of what changed
                if event.turn > last_turn and turn_modified:
                    await self._snapshot_manager.take(
                        turn=last_turn or 1,
                        modified_files=list(turn_modified),
                    )
                    turn_modified.clear()
                last_turn = event.turn

            yield event

        # Take a final snapshot for any remaining modifications
        if turn_modified:
            await self._snapshot_manager.take(
                turn=last_turn,
                modified_files=turn_modified,
            )

    def abort(self) -> None:
        """Abort the current run."""
        self._abort_signal.abort("User cancelled")

    def reset_abort(self) -> None:
        """Reset abort signal for a new run."""
        from chimera.core.abort import AbortSignal

        self._abort_signal = AbortSignal()
