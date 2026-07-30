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

import sys
import uuid
import warnings
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.config.paths import chimera_home, store_path

__all__ = ["CodingAgent", "LOOP_POSTURES"]

# Sentinel: distinguishes "caller did not specify max_turns" (use the preset's
# value) from "caller explicitly passed None" (unlimited — run until done).
_USE_CONFIG_MAX_TURNS: Any = object()

# Per-lane "loop posture" (§13.3): a system-prompt augmentation that changes how
# the agent approaches a task. Applied *within* AgentLoop — only AgentLoop emits
# the LoopEvents the TUI renders, so the reasoning loop itself is not swapped;
# the posture shapes behaviour through the prompt. Lets a multiplexer cohort race
# e.g. plan-first vs act-first on the same model + preset.
LOOP_POSTURES: dict[str, str] = {
    "plan": (
        "\n\n## Working posture: plan-first\n"
        "Before making any edits, write a short numbered plan (2-4 steps) for how "
        "you will solve the task, then carry it out. Revise the plan if you learn "
        "it is wrong."
    ),
    "tdd": (
        "\n\n## Working posture: test-first\n"
        "Work test-first: identify or write a failing test that captures the goal, "
        "make it pass with the smallest change, and run the tests before finishing."
    ),
}


def _warn_if_deprecated_preset(preset: str) -> None:
    """Emit a one-line DeprecationWarning if `preset` is a deprecated alias.

    Always raises a Python DeprecationWarning (so tests/pytest can capture it),
    but only echoes a human-readable note to stderr when stderr is a TTY —
    JSON pipelines and machine consumers stay silent.
    """
    from chimera.assembly.presets import DEPRECATED_PRESET_ALIASES

    canonical = DEPRECATED_PRESET_ALIASES.get(preset)
    if canonical is None:
        return
    msg = f"preset {preset!r} is deprecated; use {canonical!r} instead"
    warnings.warn(msg, DeprecationWarning, stacklevel=2)
    if sys.stderr.isatty():
        print(f"DeprecationWarning: {msg}", file=sys.stderr)


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
        preset: str = "coding_agent",
        *,
        provider: Any = None,
        permission_callback: Any = None,
        tools_override: list[Any] | None = None,
        extra_tools: list[Any] | None = None,
        max_turns: Any = _USE_CONFIG_MAX_TURNS,
        enable_nudges: bool = True,
        loop: str | None = None,
        interceptors: Any = None,
        budget: Any = None,
    ) -> None:
        from chimera.assembly.presets import PRESETS
        from chimera.assembly.system_prompts import CODING_AGENT_PROMPT, PRESET_PROMPTS
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

        # Surface a deprecation warning on legacy preset keys.
        _warn_if_deprecated_preset(preset)

        # Load config
        config = PRESETS.get(preset, PRESETS["coding_agent"])
        self._config = config
        self._max_turns: int | None = (
            config.max_turns if max_turns is _USE_CONFIG_MAX_TURNS else max_turns
        )
        self._project_dir = Path(project_dir or ".").resolve()

        # Conversation history persisted across run() calls so a REPL/TUI keeps
        # context between turns; steering queue enables mid-turn injection.
        from chimera.core.message_queue import SteeringMessageQueue

        self._history: list[Any] = []
        self._message_queue = SteeringMessageQueue()
        # Autonomous nudges (action / keep-going) help -p and benchmark runs but
        # make interactive Q&A ramble ("you didn't use any tools"); a REPL/TUI
        # turns them off via enable_nudges=False.
        self._enable_nudges = enable_nudges

        # Interception seams (T4, additive): a
        # chimera.core.interception.Interceptors instance threaded into
        # AgentLoop.run so embedders can block/mutate provider requests,
        # tool calls, tool results, and the outgoing context without
        # touching core. None (default) = unchanged behavior. At run()
        # time these host chains are merged with interceptors registered
        # by loaded plugins (_effective_interceptors: plugin chains first,
        # host chains last). The merged chains reach BOTH run paths — the
        # default AgentLoop and strategy-loop lanes (plan-execute /
        # reflexion / tot via loop_adapter); per-loop seam coverage is
        # tabled in docs/guides/interception.md.
        self._interceptors = interceptors

        # Hot-swap seam (/resync): the skills prompt section starts empty —
        # byte-identical prompts until the first resync_resources() binds the
        # discovered SKILL.md catalog; run() appends it to every subsequent
        # turn's assembled prompt. The busy flag makes a resync refuse
        # cleanly instead of racing a streaming turn, and the optional plugin
        # manager is what /resync hot-swaps (None = plugins not scanned).
        self._skills_prompt_section: str = ""
        self._turn_active: bool = False
        self._plugin_manager: Any = None

        # Uniform run budget (T12, additive): a
        # chimera.core.budget.BudgetSpec whose caps (cost / steps=llm_calls /
        # wall-clock / tool_calls) end a run cleanly with a
        # ``budget_exhausted:<dimension>`` reason. ONE enforcer is created here
        # and reused across run() calls, so cost and step counts accumulate over
        # the agent's whole life (a lane's successive turns), and wall-clock
        # measures active time via start()/pause() around each turn. Enforcement
        # rides the default AgentLoop path; strategy-loop lanes are not covered
        # yet (the loop_adapter seam now carries interceptors; threading the
        # budget enforcer through it is still open). None / an all-None spec
        # = unchanged.
        self._budget = budget if budget is not None and budget.is_set else None
        self._budget_enforcer: Any = None
        if self._budget is not None:
            from chimera.core.budget import BudgetEnforcer

            self._budget_enforcer = BudgetEnforcer(self._budget)

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

        # Prompt selection — use preset-specific prompt, fall back to tool-set prompt
        tool_factory = {
            "coding": coding_tools,
            "minimal": minimal_tools,
            "explore": explore_tools,
        }
        # Try preset name first (e.g., "swebench", "kimi"), then tool_set
        self._system_prompt_text = (
            PRESET_PROMPTS.get(config.name)
            or PRESET_PROMPTS.get(config.tool_set, CODING_AGENT_PROMPT)
        )
        # Per-lane loop posture (§13.3): shape behaviour via a prompt suffix.
        self._loop = loop
        if loop and loop in LOOP_POSTURES:
            self._system_prompt_text = str(self._system_prompt_text) + LOOP_POSTURES[loop]

        # Tools (must be built before the spawner, which needs them)
        if tools_override:
            self.tools = tools_override
        else:
            self.tools = tool_factory.get(config.tool_set, coding_tools)(
                file_cache=self._file_cache,
                task_manager=self._task_manager,
                command_registry=self._command_registry,
                workdir=str(self._project_dir),
            )

        # Additive tools (e.g. the eval ``submit`` tool) appended without
        # disturbing preset tool sets. Default None → zero behavior change.
        if extra_tools:
            self.tools = [*self.tools, *extra_tools]

        # Agent spawner (for sub-agents and forked skills)
        self._spawner: Any = None
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
            t_spawner = getattr(t, "_spawner", "missing")
            if t_spawner is None and self._spawner is not None:
                t._spawner = self._spawner  # type: ignore[attr-defined]

        # Permissions (if enabled)
        self._permission_checker = None
        self._permission_context = None
        # Interactive approval seam (#171): the callback is stored and passed
        # to AgentLoop as ``approval_handler`` so ASK decisions reach a real
        # user (e.g. the TUI's ApprovalBroker). When None, the loaded context
        # is swapped to BYPASS below — the legacy non-interactive posture in
        # which nothing ever ASKs (effectively auto-approve).
        self._permission_callback = permission_callback
        if config.permissions:
            try:
                from chimera.permissions.checker import PermissionChecker
                from chimera.permissions.loader import PermissionRuleLoader

                self._permission_checker = PermissionChecker()
                loader = PermissionRuleLoader(
                    project_dir=str(self._project_dir),
                    user_dir=str(chimera_home().parent),  # loaders append .chimera themselves
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
        self._hook_matchers: list[Any] | None = None
        if config.hooks:
            try:
                from chimera.hooks.executor import HookExecutor
                from chimera.hooks.loader import HookLoader

                self._hook_executor = HookExecutor()
                HookLoader(
                    project_dir=str(self._project_dir),
                    user_dir=str(chimera_home().parent),  # loaders append .chimera themselves
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
                transcript_dir = store_path("project-sessions", self._project_dir)
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

    def _effective_interceptors(self) -> Any:
        """Merge plugin-registered interceptor chains with this host's own.

        Read at the top of every :meth:`run`, so a plugin loaded (or
        unloaded) between turns simply takes effect on the next turn.

        Ordering contract (pinned by
        ``tests/assembly/test_plugin_interceptors.py``): per seam,
        interceptors registered by loaded plugins run first, in
        registration order; the host's ``interceptors=`` chains run last.
        The host therefore sees the plugin-effective value and has final
        say on replacement; a ``block`` from either side is terminal —
        nothing can un-block, so a host block can never be undone by a
        plugin. With no plugin registrations the host's configuration
        object passes through untouched, and ``None`` stays ``None``
        (byte-identical behavior, pinned).

        Returns:
            The merged :class:`~chimera.core.interception.Interceptors`,
            or ``None`` when neither plugins nor the host contribute any.
        """
        from chimera.core.interception import merge_interceptors
        from chimera.plugins.registry import PluginExtensionRegistry

        return merge_interceptors(
            PluginExtensionRegistry.get_all_interceptors(),
            getattr(self, "_interceptors", None),
        )

    async def run(self, task: str) -> AsyncGenerator[LoopEvent, None]:
        """Run the agent on a task, yielding LoopEvents.

        Marks the agent busy for the duration (the ``/resync`` hot-swap seam
        refuses while a turn is active), then delegates to :meth:`_run_turn`.
        """
        self._turn_active = True
        try:
            async for event in self._run_turn(task):
                yield event
        finally:
            self._turn_active = False

    async def _run_turn(self, task: str) -> AsyncGenerator[LoopEvent, None]:
        """The actual turn body (see :meth:`run`)."""
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
        # Hot-swap seam: the skills prompt section is "" until the first
        # /resync binds a discovered catalog, so pre-resync prompts are
        # byte-identical to before the seam existed. Because assembly runs
        # here on every turn, a resync reaches the NEXT turn of the current
        # conversation — no restart, no new session.
        system_prompt = await assembler.assemble(
            user_append=str(self._system_prompt_text)
            + (getattr(self, "_skills_prompt_section", "") or ""),
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

        from chimera.types import Message

        # Real loop swap (§13.3): when the lane selects a genuinely different
        # reasoning loop (plan-execute / reflexion / tot), bridge its steps to
        # LoopEvents so the TUI still renders it. Postures (plan/tdd) and the
        # default stay on AgentLoop below.
        from chimera.assembly.loop_adapter import adapt_loop, is_real_loop

        _loop_name = getattr(self, "_loop", None)
        if _loop_name and is_real_loop(_loop_name):
            from chimera.env.local import LocalEnvironment

            _sp = (
                system_prompt.to_string()
                if hasattr(system_prompt, "to_string") else str(system_prompt)
            )
            _seed = list(getattr(self, "_history", None) or []) + [Message.user(task)]
            _max = int(getattr(self, "_max_turns", None) or self._config.max_turns or 50)
            async for event in adapt_loop(
                _loop_name,
                provider=self.provider,
                tools=self.tools,
                system_prompt=_sp,
                messages=_seed,
                env=LocalEnvironment(str(self._project_dir)),
                max_steps=_max,
                abort_signal=self._abort_signal,
                # The SAME merged plugin+host chains the AgentLoop path gets —
                # one merge site, so a policy pack gates strategy-loop lanes too.
                interceptors=self._effective_interceptors(),
            ):
                if event.type == LoopEventType.result and getattr(event.data, "messages", None):
                    self._history = list(event.data.messages)
                yield event
            return

        # Run the loop with snapshot tracking
        from chimera.core.agent_loop import AgentLoop

        # Tools that modify files on disk
        _FILE_TOOLS = {"write_file", "edit_file", "bash", "replace_in_file"}

        from chimera.detection.exact import ExactRepeatDetector
        from chimera.env.local import LocalEnvironment

        _tool_env = LocalEnvironment(str(self._project_dir))
        _loop_detector = ExactRepeatDetector(threshold=5)

        loop = AgentLoop()
        turn_modified: list[str] = []
        last_turn = 0

        # Budget (T12): resume the lane's wall clock for this turn and bank it
        # on exit, so idle time between turns never counts against a wall-clock
        # cap. The enforcer is None (and this is a no-op) when no budget is set.
        _enforcer = getattr(self, "_budget_enforcer", None)
        if _enforcer is not None:
            _enforcer.start()
        try:
            async for event in loop.run(
                messages=list(getattr(self, "_history", None) or []) + [Message.user(task)],
                tools=self.tools,
                provider=self.provider,
                system_prompt=system_prompt,
                max_turns=getattr(self, "_max_turns", self._config.max_turns),
                abort_signal=self._abort_signal,
                permission_checker=self._permission_checker,
                permission_context=self._permission_context,
                approval_handler=getattr(self, "_permission_callback", None),
                hook_executor=self._hook_executor,
                hook_matchers=self._hook_matchers,
                transcript=self._transcript,
                content_replacement=self._content_replacement,
                compaction=self._compaction,
                stream=self._config.streaming,
                message_queue=getattr(self, "_message_queue", None),
                enable_action_nudge=getattr(self, "_enable_nudges", True),
                enable_auto_continue=getattr(self, "_enable_nudges", True),
                env=_tool_env,
                loop_detector=_loop_detector,
                interceptors=self._effective_interceptors(),
                budget_enforcer=_enforcer,
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

                # Persist the full conversation so the next run() has context.
                if event.type == LoopEventType.result:
                    _res = event.data
                    if getattr(_res, "messages", None):
                        self._history = list(_res.messages)

                yield event
        finally:
            if _enforcer is not None:
                _enforcer.pause()

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

    def steer(self, text: str) -> None:
        """Inject a steering message, delivered between tool turns mid-run."""
        from chimera.types import Message

        self._message_queue.add_steering(Message.user(text))

    def queue_follow_up(self, text: str) -> None:
        """Queue a message delivered after the agent would otherwise stop."""
        from chimera.types import Message

        self._message_queue.add_follow_up(Message.user(text))

    def clear_history(self) -> None:
        """Forget the conversation so the next run() starts fresh."""
        self._history = []
        self._message_queue.clear()

    def load_history(self, messages: list[Any]) -> None:
        """Seed the conversation from a saved history (for session resume).

        The next :meth:`run` continues from these messages, so a restored
        cohort lane picks up where it left off.
        """
        self._history = list(messages)

    @property
    def history(self) -> list[Any]:
        """The accumulated conversation messages across run() calls."""
        return self._history

    @property
    def budget(self) -> Any:
        """The agent's :class:`~chimera.core.budget.BudgetSpec`, or ``None``."""
        return self._budget

    @property
    def budget_tally(self) -> Any:
        """Live budget counters (cost / llm_calls / elapsed), or ``None``.

        The enforcer's mutable :class:`~chimera.core.budget.BudgetTally` — read
        by a status display for a live consumption meter. ``None`` when no
        budget is set.
        """
        enforcer = getattr(self, "_budget_enforcer", None)
        return enforcer.tally if enforcer is not None else None

    def set_budget(self, budget: Any) -> None:
        """Set or clear the run budget mid-session (e.g. the TUI ``/budget``).

        Consumption already recorded is preserved (the new enforcer keeps the
        prior tally), so a raised cap keeps counting from where it was and a
        tightened one can trip on the next turn. ``None`` or an all-``None`` spec
        clears the budget. Takes effect on the next :meth:`run` — an in-flight
        turn keeps the enforcer it started with.

        Args:
            budget: A :class:`~chimera.core.budget.BudgetSpec`, or ``None``.
        """
        self._budget = budget if budget is not None and budget.is_set else None
        if self._budget is None:
            self._budget_enforcer = None
            return
        from chimera.core.budget import BudgetEnforcer

        old = self._budget_enforcer
        enforcer = BudgetEnforcer(self._budget)
        if old is not None:
            enforcer.tally = old.tally  # keep consumption spent under the old cap
        self._budget_enforcer = enforcer

    # -- hot-swap seam (/resync) ----------------------------------------
    @property
    def plugin_manager(self) -> Any:
        """The attached :class:`~chimera.plugins.manager.PluginManager`, or ``None``."""
        return self._plugin_manager

    def attach_plugin_manager(self, manager: Any) -> None:
        """Attach the plugin manager :meth:`resync_resources` hot-swaps.

        Attachment alone binds nothing — the next :meth:`resync_resources`
        call is what syncs the manager's plugin-contributed tools and
        interceptors into this agent. ``None`` detaches (plugins are then
        skipped by resync; anything previously bound stays bound until a
        resync with a manager runs).

        Args:
            manager: A :class:`~chimera.plugins.manager.PluginManager`
                (or ``None`` to detach).
        """
        self._plugin_manager = manager

    def resync_resources(self) -> Any:
        """Hot-swap plugins / skills / agent definitions from disk, live.

        Re-discovers each catalog and rebinds it into this running agent via
        :func:`chimera.assembly.resync.resync_agent`: plugin hot-swap (with
        per-plugin failure isolation), plugin tool + interceptor rebind, the
        refreshed skills prompt catalog (reaches the next turn — the prompt
        is reassembled every turn), the flat skill-command registry, and the
        agent-definition catalog.

        Returns:
            The :class:`~chimera.assembly.resync.ResyncReport`; refused (and
            nothing rebound) while a turn is active.
        """
        from chimera.assembly.resync import resync_agent

        return resync_agent(
            self,
            workdir=self._project_dir,
            plugin_manager=self._plugin_manager,
        )
