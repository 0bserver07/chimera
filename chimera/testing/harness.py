"""Hermetic agent-loop test harness: scripted turns through the REAL loop.

The harness wires the deterministic :class:`~chimera.providers.faux.FauxProvider`
(zero network, zero cost), real tools rooted in a throwaway workspace, and the
real :class:`~chimera.core.agent_loop.AgentLoop` into one object that runs a
turn and hands back everything a regression test wants to inspect: the ordered
:class:`~chimera.core.loop_events.LoopEvent` stream, the tool calls that
actually executed, the files they touched, usage/cost accounting, and the
terminal reason. Nothing in the loop is mocked — only the model is scripted.

Two entry points:

* :func:`create_harness` → :class:`AgentHarness`: the core path — drives
  ``AgentLoop`` directly, exactly as the assembled agent does.
* :func:`create_assembled_harness` → :class:`DriverHarness`: the assembled
  path — drives :class:`~chimera.assembly.driver.AgentDriver` /
  :class:`~chimera.assembly.coding_agent.CodingAgent`, so preset wiring
  (prompts, tool sets, streaming, nudges) is part of the run.

Example:
    ```python
    from chimera.testing import create_harness

    harness = create_harness(
        turns=[
            {"text": "writing", "tool_calls": [
                {"name": "write_file",
                 "arguments": {"path": "hello.txt", "content": "hi"}},
            ]},
            {"text": "done"},
        ],
        workspace=tmp_path,
    )
    run = harness.run("create hello.txt")
    assert run.reason == "completed"
    assert run.files_created == ["hello.txt"]
    ```

Contract (repo rule, non-negotiable): this harness exists for **regression
locks and unit-level loop behavior** — fast, offline, deterministic. It
*complements* and never replaces live validation: a feature is not "done"
until verified against a real LLM.
"""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.providers.faux import FauxProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from chimera.core.loop_events import LoopResult
    from chimera.core.tool import BaseTool
    from chimera.providers.faux import Script, ScriptStep
    from chimera.types import Message, ToolCall, ToolResult

__all__ = [
    "AgentHarness",
    "DriverHarness",
    "HarnessRun",
    "create_assembled_harness",
    "create_harness",
    "default_test_tools",
]

#: Top-level workspace entries excluded from file-diff bookkeeping — agent
#: infrastructure (snapshots, transcripts) rather than task output.
_DIFF_IGNORED_TOPLEVEL = frozenset({".chimera", ".git"})


def default_test_tools(workspace: str | Path) -> list[BaseTool]:
    """Real file/shell tools rooted at *workspace* for hermetic runs.

    Returns fresh instances of the built-in ``bash``, ``read_file``,
    ``write_file``, ``edit_file``, and ``list_files`` tools with their
    operations pinned to *workspace* — scripted tool calls therefore execute
    for real, but only inside the throwaway directory.

    Args:
        workspace: Directory the tools operate in.

    Returns:
        A list of tool instances ready for the loop.
    """
    from chimera.core.operations import (
        LocalBashOps,
        LocalReadOps,
        LocalSearchOps,
        LocalWriteOps,
    )
    from chimera.tools.bash import BashTool
    from chimera.tools.edit import EditFileTool
    from chimera.tools.list_files import ListFilesTool
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool

    cwd = str(workspace)
    read_ops = LocalReadOps(cwd=cwd)
    write_ops = LocalWriteOps(cwd=cwd)
    bash_ops = LocalBashOps(cwd=cwd)
    search_ops = LocalSearchOps(cwd=cwd)
    return [
        BashTool(ops=bash_ops),
        ReadFileTool(ops=read_ops),
        WriteFileTool(read_ops=read_ops, write_ops=write_ops),
        EditFileTool(read_ops=read_ops, write_ops=write_ops),
        ListFilesTool(ops=search_ops),
    ]


def _snapshot_files(root: Path) -> dict[str, str]:
    """Map relative file path → content hash for every file under *root*."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in _DIFF_IGNORED_TOPLEVEL:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        out[str(rel)] = digest
    return out


@dataclass
class HarnessRun:
    """Everything one harness turn produced, ready for assertions.

    Attributes:
        events: Every :class:`LoopEvent` the loop yielded, in order.
        result: The terminal :class:`LoopResult` payload, or ``None`` when the
            run raised before a ``result`` event (e.g. a scripted provider
            error).
        error: The exception the loop surfaced, or ``None`` on a clean exit.
        files_created: Workspace-relative paths created during the run.
        files_modified: Workspace-relative paths whose content changed.
        files_deleted: Workspace-relative paths removed during the run.
    """

    events: list[LoopEvent] = field(default_factory=list)
    result: LoopResult | None = None
    error: BaseException | None = None
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)

    # -- event access ---------------------------------------------------
    def events_of(self, event_type: LoopEventType) -> list[LoopEvent]:
        """All events of *event_type*, in emission order."""
        return [ev for ev in self.events if ev.type == event_type]

    @property
    def event_types(self) -> list[LoopEventType]:
        """The ordered sequence of event types (for shape assertions)."""
        return [ev.type for ev in self.events]

    # -- terminal outcome -----------------------------------------------
    @property
    def reason(self) -> str:
        """Terminal reason: ``completed`` / ``max_turns`` / ``aborted_*`` /
        ``loop_detected`` — or ``"error"`` when the loop raised instead of
        finishing, and ``"no_result"`` when no terminal event arrived."""
        if self.result is not None:
            return str(self.result.reason)
        if self.error is not None:
            return "error"
        return "no_result"

    @property
    def output_text(self) -> str:
        """The last non-empty assistant message content (the "answer")."""
        for ev in reversed(self.events_of(LoopEventType.assistant)):
            content = str(getattr(ev.data, "content", "") or "")
            if content:
                return content
        return ""

    @property
    def messages(self) -> list[Message]:
        """The final conversation from the terminal result (empty if none)."""
        if self.result is None:
            return []
        return list(getattr(self.result, "messages", None) or [])

    # -- tools ----------------------------------------------------------
    @property
    def tool_calls(self) -> list[ToolCall]:
        """Every tool call the model issued, in order."""
        return [ev.data for ev in self.events_of(LoopEventType.tool_use)]

    @property
    def tool_results(self) -> list[tuple[ToolCall | None, ToolResult]]:
        """``(tool_call, result)`` pairs for every executed tool, in order."""
        out: list[tuple[ToolCall | None, ToolResult]] = []
        for ev in self.events_of(LoopEventType.tool_result):
            if isinstance(ev.data, tuple) and len(ev.data) == 2:
                out.append((ev.data[0], ev.data[1]))
            else:
                out.append((None, ev.data))
        return out

    # -- accounting ------------------------------------------------------
    @property
    def usage(self) -> dict[str, Any]:
        """Accumulated token usage from the terminal result (``{}`` if none)."""
        if self.result is None:
            return {}
        return dict(getattr(self.result, "usage", None) or {})

    @property
    def cost_usd(self) -> float:
        """Total priced cost from the terminal result (``0.0`` if none)."""
        if self.result is None:
            return 0.0
        return float(getattr(self.result, "cost_usd", 0.0) or 0.0)

    @property
    def turn_count(self) -> int:
        """LLM turns the loop counted (``0`` when no result arrived)."""
        if self.result is None:
            return 0
        return int(getattr(self.result, "turn_count", 0) or 0)

    # -- streaming -------------------------------------------------------
    @property
    def streamed_text(self) -> str:
        """Concatenated ``assistant_chunk`` text (streaming runs only)."""
        return "".join(str(ev.data) for ev in self.events_of(LoopEventType.assistant_chunk))

    @property
    def thinking_chunks(self) -> list[str]:
        """Scripted reasoning chunks as they streamed (in order)."""
        return [str(ev.data) for ev in self.events_of(LoopEventType.thinking_chunk)]


class _HarnessBase:
    """Shared run-collection machinery for the two harness flavors."""

    workspace: Path
    _owns_workspace: bool
    _history: list[Any]

    def _resolve_workspace(self, workspace: str | Path | None) -> None:
        if workspace is None:
            self.workspace = Path(tempfile.mkdtemp(prefix="chimera-harness-"))
            self._owns_workspace = True
        else:
            self.workspace = Path(workspace)
            self.workspace.mkdir(parents=True, exist_ok=True)
            self._owns_workspace = False

    def cleanup(self) -> None:
        """Remove the workspace **iff** the harness created it itself."""
        if self._owns_workspace and self.workspace.is_dir():
            import shutil

            shutil.rmtree(self.workspace, ignore_errors=True)

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    async def _collect(
        self,
        stream: Any,
        on_event: Callable[[LoopEvent], None] | None,
    ) -> HarnessRun:
        """Drain *stream* into a :class:`HarnessRun` with file bookkeeping.

        ``on_event`` fires synchronously after each event is recorded, while
        the loop generator is suspended — calling :meth:`steer` /
        :meth:`abort` from it is therefore deterministic mid-stream
        injection, not a race.
        """
        before = _snapshot_files(self.workspace)
        run = HarnessRun()
        try:
            async for ev in stream:
                run.events.append(ev)
                if ev.type == LoopEventType.result:
                    run.result = ev.data
                if on_event is not None:
                    on_event(ev)
        except Exception as exc:  # noqa: BLE001 - surfaced on run.error for assertions
            run.error = exc
        after = _snapshot_files(self.workspace)
        run.files_created = sorted(set(after) - set(before))
        run.files_deleted = sorted(set(before) - set(after))
        run.files_modified = sorted(
            path for path, digest in after.items()
            if path in before and before[path] != digest
        )
        return run

    @property
    def history(self) -> list[Any]:
        """Conversation messages carried across :meth:`run` calls."""
        return self._history


class AgentHarness(_HarnessBase):
    """Scripted turns through the real :class:`~chimera.core.agent_loop.AgentLoop`.

    The loop itself is untouched production code; only the provider plays a
    script. Multi-turn: each :meth:`run` call seeds the loop with the
    conversation the previous call ended on (like a REPL turn).

    Args:
        script: :class:`~chimera.providers.faux.FauxProvider` script — the
            scripted completions, in order (dicts with ``text`` /
            ``tool_calls`` / ``thinking`` / ``usage`` / ``error`` keys, a
            single dict, or a bare string).
        tools: Tools available to the loop. Defaults to
            :func:`default_test_tools` rooted at the workspace.
        workspace: Directory tools operate in. Defaults to a fresh temp
            directory owned (and removable via :meth:`cleanup`) by the
            harness; pass pytest's ``tmp_path`` to let pytest manage it.
        provider: Explicit provider instance (e.g. a
            :class:`~chimera.core.budget.BudgetedProvider`-wrapped faux).
            Mutually exclusive with *script*.
        model: Model id reported by the faux provider. Keep the default
            ``"faux"`` for zero-cost runs, or set a priced id (e.g.
            ``"glm-5.2"``) so the loop's cost accounting is non-zero and
            budget paths become testable.
        system_prompt: System prompt for the loop.
        max_turns: Turn ceiling (small by default — scripts are short).
        stream: Drive the provider's streaming surface (``async_stream``);
            enables ``assistant_chunk`` / ``thinking_chunk`` events.
        nudges: Re-enable the action/keep-going nudges. Off by default so a
            script's turns map 1:1 onto loop turns.
        config: Extra keyword arguments forwarded verbatim to
            :meth:`AgentLoop.run` (``permission_checker``, ``transcript``,
            ``loop_detector``, ``approval_handler``, ``hook_executor``, …).

    Raises:
        ValueError: If both *script* and *provider* are given.
    """

    def __init__(
        self,
        script: Script | ScriptStep | str | None = None,
        *,
        tools: list[BaseTool] | None = None,
        workspace: str | Path | None = None,
        provider: Any = None,
        model: str = "faux",
        system_prompt: str = "You are a deterministic test agent.",
        max_turns: int = 8,
        stream: bool = False,
        nudges: bool = False,
        config: dict[str, Any] | None = None,
    ) -> None:
        if script is not None and provider is not None:
            raise ValueError("pass either script= or provider=, not both")
        from chimera.core.message_queue import SteeringMessageQueue

        self._resolve_workspace(workspace)
        self.provider: Any = (
            provider if provider is not None else FauxProvider(script, model=model)
        )
        self.tools: list[BaseTool] = (
            tools if tools is not None else default_test_tools(self.workspace)
        )
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.stream = stream
        self.nudges = nudges
        self.message_queue = SteeringMessageQueue()
        self._loop_kwargs: dict[str, Any] = dict(config or {})
        self._history = []
        self._abort_signal: Any = None

    # -- mid-run controls -----------------------------------------------
    def steer(self, text: str) -> None:
        """Queue a steering message, delivered between tool turns mid-run."""
        from chimera.types import Message as _Message

        self.message_queue.add_steering(_Message.user(text))

    def queue_follow_up(self, text: str) -> None:
        """Queue a message delivered when the agent would otherwise stop."""
        from chimera.types import Message as _Message

        self.message_queue.add_follow_up(_Message.user(text))

    def abort(self, reason: str = "test abort") -> None:
        """Cooperatively cancel the in-flight run (or pre-arm the next one)."""
        if self._abort_signal is not None:
            self._abort_signal.abort(reason)

    # -- driving ---------------------------------------------------------
    async def arun(
        self,
        prompt: str,
        *,
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> HarnessRun:
        """Run one turn through the real loop (async).

        Args:
            prompt: The user message for this turn.
            on_event: Optional callback fired after each event while the loop
                is suspended — the deterministic seam for mid-stream
                :meth:`steer` / :meth:`abort` injection.

        Returns:
            The collected :class:`HarnessRun`.
        """
        from chimera.core.abort import AbortSignal
        from chimera.core.agent_loop import AgentLoop
        from chimera.env.local import LocalEnvironment
        from chimera.types import Message as _Message

        self._abort_signal = AbortSignal()
        messages = list(self._history) + [_Message.user(prompt)]
        loop = AgentLoop()
        stream = loop.run(
            messages=messages,
            tools=self.tools,
            provider=self.provider,
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
            abort_signal=self._abort_signal,
            stream=self.stream,
            message_queue=self.message_queue,
            enable_action_nudge=self.nudges,
            enable_auto_continue=self.nudges,
            env=LocalEnvironment(str(self.workspace)),
            **self._loop_kwargs,
        )
        run = await self._collect(stream, on_event)
        if run.result is not None and run.messages:
            self._history = list(run.messages)
        return run

    def run(
        self,
        prompt: str,
        *,
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> HarnessRun:
        """Synchronous wrapper over :meth:`arun` (one ``asyncio.run`` per turn)."""
        return asyncio.run(self.arun(prompt, on_event=on_event))


class DriverHarness(_HarnessBase):
    """Scripted turns through the assembled AgentDriver / CodingAgent stack.

    Same inspection surface as :class:`AgentHarness`, but the run goes through
    :class:`~chimera.assembly.driver.AgentDriver` →
    :class:`~chimera.assembly.coding_agent.CodingAgent` → ``AgentLoop`` — so
    preset wiring (tool sets, system prompt assembly, streaming posture,
    snapshots, history persistence) is part of what the test exercises.

    Args:
        script: Faux-provider script (see :class:`AgentHarness`).
        workspace: The agent's ``project_dir``. Defaults to a fresh temp dir.
        provider: Explicit provider instance; mutually exclusive with *script*.
        model: Model id for the faux provider.
        preset: Assembly preset (default ``"minimal"`` — no transcripts, no
            permissions, smallest tool set).
        tools: Optional ``tools_override`` for the assembled agent.
        max_turns: Turn ceiling.
        interactive: Forwarded to the driver; the default ``True`` disables
            the autonomous nudges, keeping scripts 1:1 with turns.
        agent_kwargs: Extra keyword arguments forwarded to ``CodingAgent``.

    Raises:
        ValueError: If both *script* and *provider* are given.
    """

    def __init__(
        self,
        script: Script | ScriptStep | str | None = None,
        *,
        workspace: str | Path | None = None,
        provider: Any = None,
        model: str = "faux",
        preset: str = "minimal",
        tools: list[BaseTool] | None = None,
        max_turns: int = 8,
        interactive: bool = True,
        agent_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if script is not None and provider is not None:
            raise ValueError("pass either script= or provider=, not both")
        from chimera.assembly.driver import AgentDriver

        self._resolve_workspace(workspace)
        self.provider = (
            provider if provider is not None else FauxProvider(script, model=model)
        )
        extra: dict[str, Any] = dict(agent_kwargs or {})
        if tools is not None:
            extra["tools_override"] = tools
        self.driver = AgentDriver(
            model=model,
            project_dir=self.workspace,
            preset=preset,
            interactive=interactive,
            provider=self.provider,
            max_turns=max_turns,
            **extra,
        )
        self._history = []

    # -- mid-run controls -----------------------------------------------
    def steer(self, text: str) -> None:
        """Inject a steering message via the driver (mid-run seam)."""
        self.driver.steer(text)

    def queue_follow_up(self, text: str) -> None:
        """Queue a follow-up message via the driver."""
        self.driver.queue_follow_up(text)

    def abort(self, reason: str = "test abort") -> None:
        """Cancel the current turn via the driver."""
        del reason  # the driver's cancel() carries its own reason
        self.driver.cancel()

    # -- driving ---------------------------------------------------------
    async def arun(
        self,
        prompt: str,
        *,
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> HarnessRun:
        """Run one assembled turn, collecting events and file changes."""
        run = await self._collect(self.driver.send(prompt), on_event)
        self._history = list(self.driver.history)
        return run

    def run(
        self,
        prompt: str,
        *,
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> HarnessRun:
        """Synchronous wrapper over :meth:`arun`."""
        return asyncio.run(self.arun(prompt, on_event=on_event))


def create_harness(
    turns: Script | ScriptStep | str | None = None,
    *,
    tools: list[BaseTool] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AgentHarness:
    """One-liner for :class:`AgentHarness` (the core AgentLoop path).

    Args:
        turns: The scripted completions, in order (the faux-provider script).
        tools: Tools for the loop; defaults to real file/shell tools rooted
            at the harness workspace.
        config: Extra ``AgentLoop.run`` keyword arguments.
        **kwargs: Forwarded to :class:`AgentHarness` (``workspace=``,
            ``model=``, ``provider=``, ``stream=``, ``max_turns=``, …).

    Returns:
        A ready-to-run harness.
    """
    return AgentHarness(turns, tools=tools, config=config, **kwargs)


def create_assembled_harness(
    turns: Script | ScriptStep | str | None = None,
    **kwargs: Any,
) -> DriverHarness:
    """One-liner for :class:`DriverHarness` (the assembled agent path).

    Args:
        turns: The scripted completions, in order.
        **kwargs: Forwarded to :class:`DriverHarness` (``workspace=``,
            ``preset=``, ``model=``, ``provider=``, ``max_turns=``, …).

    Returns:
        A ready-to-run assembled-path harness.
    """
    return DriverHarness(turns, **kwargs)
