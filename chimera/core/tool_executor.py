"""Shared tool execution logic used by all loop variants."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import Message, PendingApproval, ToolCall, ToolResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig
    from chimera.hooks.hook_types import HookOutput

__all__ = [
    "execute_tool_calls",
    "execute_tool_calls_incremental",
    "async_execute_tool_calls_incremental",
    "ToolExecutionResult",
]

# Tools that modify files on disk — used for ghost commit snapshots.
_FILE_MODIFYING_TOOLS = frozenset({"write_file", "edit_file", "replace_in_file"})
_FILE_READING_TOOLS = frozenset({"read_file"})


def _fire_pre_tool_use_hook(
    tc: ToolCall, config: LoopConfig | None,
) -> HookOutput | None:
    """Fire the PreToolUse hook chain for *tc* synchronously.

    Returns the merged :class:`HookOutput`, or ``None`` if no emitter is
    configured. Safely runs the async emitter even when called from a
    running event loop by spinning a one-shot worker thread.
    """
    if config is None or config.hook_emitter is None:
        return None
    if not getattr(config.hook_emitter, "active", True):
        return None

    from collections.abc import Coroutine
    from typing import Any as _Any

    from chimera.hooks.events import HookEvent

    emitter = config.hook_emitter

    def coro_factory() -> Coroutine[_Any, _Any, "HookOutput | None"]:
        return emitter.emit(
            HookEvent.PRE_TOOL_USE,
            tool_name=tc.name,
            tool_input=dict(tc.arguments),
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        # No running loop — safe to use asyncio.run.
        return asyncio.run(coro_factory())

    # Already inside a loop. Run the coroutine on a worker thread with a
    # fresh event loop so we don't deadlock the caller's loop.
    import threading

    result_box: dict[str, HookOutput | None] = {"out": None}

    def _runner() -> None:
        new_loop = asyncio.new_event_loop()
        try:
            result_box["out"] = new_loop.run_until_complete(coro_factory())
        finally:
            new_loop.close()

    th = threading.Thread(target=_runner, daemon=True)
    th.start()
    th.join()
    return result_box["out"]


def _apply_pre_tool_use_hook(
    tc: ToolCall, config: LoopConfig | None,
) -> tuple[ToolCall, HookOutput | None, str | None]:
    """Run PreToolUse hooks and return (effective_tc, hook_output, denial).

    - ``effective_tc`` has merged ``updated_input`` if the hook supplied any.
    - ``denial`` is non-None when the hook denies the call. Caller should
      handle it (raise PermissionDenied or record a denial result).
    - ``hook_output.permission_decision == "allow"`` overrides a default
      DENY at the caller's discretion (caller must inspect).
    """
    out = _fire_pre_tool_use_hook(tc, config)
    if out is None:
        return tc, None, None

    effective_tc = tc
    if out.updated_input is not None:
        merged = dict(tc.arguments)
        merged.update(out.updated_input)  # hook keys override originals
        effective_tc = ToolCall(id=tc.id, name=tc.name, arguments=merged)
        if config is not None and config.event_bus is not None:
            from chimera.events.types import HookUpdatedInputEvent

            config.event_bus.publish(
                HookUpdatedInputEvent(
                    tool_name=tc.name,
                    call_id=tc.id,
                    original=dict(tc.arguments),
                    updated=merged,
                ),
            )

    denial: str | None = None
    if out.permission_decision == "deny" or not out.continue_execution:
        denial = (
            out.permission_decision_reason
            or out.reason
            or out.stop_reason
            or "Blocked by hook"
        )
    return effective_tc, out, denial


class PermissionDenied(Exception):
    """Raised when a tool call is denied by the permission policy."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Permission denied for tool: {tool_name}")


class PermissionAsk(Exception):
    """Raised when a tool call requires user approval."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Permission required for tool: {tool_name}")


class LoopBreak(Exception):
    """Raised when the loop detector triggers a break."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        super().__init__(f"Loop detected: {pattern}")


def execute_tool_calls(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
    context: Context,
    env: Environment | None,
    config: LoopConfig | None,
) -> int:
    """Execute tool calls with optional config hooks.

    Returns the number of tool calls executed.
    May emit events, check permissions, and detect loops via *config*.
    """
    count = 0
    for tc in tool_calls:
        count += 1

        # -- Cancellation check --
        if config and config.cancellation:
            config.cancellation.check()

        # -- PreToolUse hook (may mutate input, deny, or override perms) --
        tc, hook_out, hook_denial = _apply_pre_tool_use_hook(tc, config)
        if hook_denial is not None:
            raise PermissionDenied(tc.name)

        # -- Permission check (skipped iff hook returned permissionDecision) --
        hook_decision = hook_out.permission_decision if hook_out else None
        if config and config.permissions and hook_decision not in {"allow", "deny"}:
            from chimera.permissions.base import PermissionAction

            action = config.permissions.evaluate(tc.name, tc.arguments)
            if config.event_bus:
                from chimera.events.types import PermissionEvent

                config.event_bus.publish(
                    PermissionEvent(
                        tool_name=tc.name,
                        action=action.value,
                        granted=action != PermissionAction.DENY,
                        call_id=tc.id,
                    )
                )
            if action == PermissionAction.DENY:
                context.add(Message.tool(tc.id, f"Permission denied for {tc.name}"))
                continue
            if action == PermissionAction.ASK and hook_decision != "allow":
                raise PermissionAsk(tc.name)

        # -- Discipline guards --
        if config and config.discipline:
            for _guard in config.discipline:
                _g_ctx = {"file_path": tc.arguments.get("path", ""), "arguments": tc.arguments}
                _g_result = _guard.check(tc.name, _g_ctx)
                if not _g_result.allowed:
                    if _g_result.severity == "block":
                        from chimera.discipline.guard import DisciplineViolation
                        raise DisciplineViolation(_guard.name, _g_result.reason)

        # -- Event: tool call --
        if config and config.event_bus:
            from chimera.events.types import ToolCallEvent

            config.event_bus.publish(
                ToolCallEvent(tool_name=tc.name, arguments=tc.arguments, call_id=tc.id)
            )

        # -- Resolve tool --
        tool = tool_map.get(tc.name)
        if tool is None:
            context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
            continue

        # -- Ghost commit: snapshot before file-modifying tools --
        if config and config.ghost_commits and tc.name in _FILE_MODIFYING_TOOLS:
            path = tc.arguments.get("path", "")
            if path:
                config.ghost_commits.snapshot(f"{tc.name}: {path}", [path])

        # -- Cancellation: bind token to cancellable tools --
        if config and config.cancellation:
            from chimera.core.cancellation import CancellableTool
            if isinstance(tool, CancellableTool):
                tool.bind_cancellation(config.cancellation)

        # -- Execute --
        result = tool.execute(tc.arguments, env)
        content = result.output if result.success else f"Error: {result.error}\n{result.output}"

        # -- Truncation --
        if result.success:
            from chimera.core.truncation import truncate_output

            trunc_cfg = config.truncation if config else None
            content = truncate_output(content, trunc_cfg)

        context.add(Message.tool(tc.id, content))

        # -- Audit log --
        if config and config.audit_log:
            config.audit_log.record(
                tool_name=tc.name, arguments=tc.arguments, decision="allowed"
            )

        # -- Checkpoint --
        if config and config.checkpoint_manager and result.success:
            config.checkpoint_manager.create(description=f"After {tc.name}")

        # -- Event: tool result --
        if config and config.event_bus:
            from chimera.events.types import ErrorEvent, ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=result.success,
                    tool_metadata=result.metadata,
                )
            )
            if not result.success:
                # Additive error.occurred emission for the event-sourcing
                # subsystem; classic subscribers ignore unknown events.
                config.event_bus.publish(
                    ErrorEvent(
                        error=str(result.error or "tool failed"),
                        recoverable=True,
                    ),
                )

        # -- Feedback tracker: learn from errors --
        if config and config.feedback_tracker:
            try:
                from chimera.events.types import ToolResultEvent as _TRE
                config.feedback_tracker.on_tool_result(_TRE(
                    call_id=tc.id, output=content,
                    success=result.success, tool_metadata=result.metadata,
                ))
            except Exception:
                pass  # Learning is best-effort, never blocks execution

        # -- File tracking --
        if config and config.file_tracker:
            if tc.name in _FILE_READING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_read(path)
            elif tc.name in _FILE_MODIFYING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_modified(path)

        # -- Loop detection --
        if config and config.detector:
            from chimera.detection.actions import OnDetect

            det_result = config.detector.record_and_check(tc.name, tc.arguments)
            if det_result is not None:
                if config.event_bus:
                    from chimera.events.types import LoopDetectedEvent

                    config.event_bus.publish(
                        LoopDetectedEvent(pattern=det_result.pattern)
                    )
                if config.detector.on_detect == OnDetect.BREAK:
                    raise LoopBreak(det_result.pattern)
                if config.detector.on_detect == OnDetect.ASK:
                    raise PermissionAsk(f"loop_detected:{tc.name}")

    return count


@dataclass
class ToolExecutionResult:
    """Result of :func:`execute_tool_calls_incremental`.

    Attributes:
        executed: Number of tool calls that were actually executed.
        results: Collected :class:`ToolResult` objects.
        pending: If set, execution paused waiting for user approval.
        remaining: Tool calls that were not executed (after the pending one).
    """

    executed: int = 0
    results: list[ToolResult] = field(default_factory=list)
    pending: PendingApproval | None = None
    remaining: list[ToolCall] = field(default_factory=list)


def execute_tool_calls_incremental(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
    context: Context,
    env: Environment | None,
    config: LoopConfig | None,
) -> ToolExecutionResult:
    """Execute tool calls, pausing on ASK permissions instead of raising.

    Unlike :func:`execute_tool_calls`, this returns a
    :class:`ToolExecutionResult` that carries a :class:`PendingApproval`
    when a permission check returns ASK — the caller can inspect the
    pending approval, let the consumer decide, and then resume.

    :exc:`LoopBreak` is still raised (callers must handle it).
    """
    result = ToolExecutionResult()

    for i, tc in enumerate(tool_calls):
        # -- Cancellation check --
        if config and config.cancellation:
            config.cancellation.check()

        # -- Permission check --
        if config and config.permissions:
            from chimera.permissions.base import PermissionAction

            action = config.permissions.evaluate(tc.name, tc.arguments)
            if config.event_bus:
                from chimera.events.types import PermissionEvent

                config.event_bus.publish(
                    PermissionEvent(
                        tool_name=tc.name,
                        action=action.value,
                        granted=action != PermissionAction.DENY,
                        call_id=tc.id,
                    )
                )
            if action == PermissionAction.DENY:
                context.add(Message.tool(tc.id, f"Permission denied for {tc.name}"))
                tr = ToolResult(output=f"Permission denied for {tc.name}")
                result.results.append(tr)
                continue
            if action == PermissionAction.ASK:
                result.pending = PendingApproval(
                    tool_call=tc,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    reason=f"Permission required for tool: {tc.name}",
                )
                result.remaining = list(tool_calls[i + 1:])
                return result

        # -- Discipline guards --
        if config and config.discipline:
            for _guard in config.discipline:
                _g_ctx = {"file_path": tc.arguments.get("path", ""), "arguments": tc.arguments}
                _g_result = _guard.check(tc.name, _g_ctx)
                if not _g_result.allowed:
                    if _g_result.severity == "block":
                        from chimera.discipline.guard import DisciplineViolation
                        raise DisciplineViolation(_guard.name, _g_result.reason)

        # -- Event: tool call --
        if config and config.event_bus:
            from chimera.events.types import ToolCallEvent

            config.event_bus.publish(
                ToolCallEvent(tool_name=tc.name, arguments=tc.arguments, call_id=tc.id)
            )

        # -- Resolve tool --
        tool = tool_map.get(tc.name)
        if tool is None:
            context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
            tr = ToolResult(output="", error=f"Unknown tool {tc.name}")
            result.results.append(tr)
            result.executed += 1
            continue

        # -- Ghost commit: snapshot before file-modifying tools --
        if config and config.ghost_commits and tc.name in _FILE_MODIFYING_TOOLS:
            path = tc.arguments.get("path", "")
            if path:
                config.ghost_commits.snapshot(f"{tc.name}: {path}", [path])

        # -- Cancellation: bind token to cancellable tools --
        if config and config.cancellation:
            from chimera.core.cancellation import CancellableTool
            if isinstance(tool, CancellableTool):
                tool.bind_cancellation(config.cancellation)

        # -- Execute --
        tr = tool.execute(tc.arguments, env)
        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"

        # -- Truncation --
        if tr.success:
            from chimera.core.truncation import truncate_output

            trunc_cfg = config.truncation if config else None
            content = truncate_output(content, trunc_cfg)

        context.add(Message.tool(tc.id, content))
        result.results.append(tr)
        result.executed += 1

        # -- Wire: status update --
        if config and config.wire:
            from chimera.wire.types import StatusUpdate
            config.wire.send(StatusUpdate(
                step=0,  # not known at this level
                metadata={"tool": tc.name, "success": tr.success},
            ))

        # -- Audit log --
        if config and config.audit_log:
            config.audit_log.record(
                tool_name=tc.name, arguments=tc.arguments, decision="allowed"
            )

        # -- Checkpoint --
        if config and config.checkpoint_manager and tr.success:
            config.checkpoint_manager.create(description=f"After {tc.name}")

        # -- Event: tool result --
        if config and config.event_bus:
            from chimera.events.types import ErrorEvent, ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=tr.success,
                    tool_metadata=tr.metadata,
                )
            )
            if not tr.success:
                config.event_bus.publish(
                    ErrorEvent(
                        error=str(tr.error or "tool failed"),
                        recoverable=True,
                    ),
                )

        # -- Feedback tracker: learn from errors --
        if config and config.feedback_tracker:
            try:
                from chimera.events.types import ToolResultEvent as _TRE
                config.feedback_tracker.on_tool_result(_TRE(
                    call_id=tc.id, output=content,
                    success=tr.success, tool_metadata=tr.metadata,
                ))
            except Exception:
                pass  # Learning is best-effort

        # -- File tracking --
        if config and config.file_tracker:
            if tc.name in _FILE_READING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_read(path)
            elif tc.name in _FILE_MODIFYING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_modified(path)

        # -- Loop detection --
        if config and config.detector:
            from chimera.detection.actions import OnDetect

            det_result = config.detector.record_and_check(tc.name, tc.arguments)
            if det_result is not None:
                if config.event_bus:
                    from chimera.events.types import LoopDetectedEvent

                    config.event_bus.publish(
                        LoopDetectedEvent(pattern=det_result.pattern)
                    )
                if config.detector.on_detect == OnDetect.BREAK:
                    raise LoopBreak(det_result.pattern)
                if config.detector.on_detect == OnDetect.ASK:
                    result.pending = PendingApproval(
                        tool_call=tc,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        reason=f"Loop detected: {det_result.pattern}",
                    )
                    result.remaining = list(tool_calls[i + 1:])
                    return result

    return result


async def async_execute_tool_calls_incremental(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
    context: Context,
    env: Environment | None,
    config: "LoopConfig | None",
) -> ToolExecutionResult:
    """Async version of :func:`execute_tool_calls_incremental`.

    Runs permission and detection checks synchronously (in-memory, no I/O).
    Executes approved tool calls concurrently via ``asyncio.gather()``.
    Results are ordered to match *tool_calls* order — denied and
    unknown-tool entries are placed at their original index, with
    approved-tool results filling the remaining slots.

    :exc:`LoopBreak` is still raised (callers must handle it).
    """
    result = ToolExecutionResult()

    # Phase 1: pre-process — permission checks, tool resolution (sequential)
    # ordered_results: slot-aligned to tool_calls so final ordering is preserved.
    ordered_results: list[ToolResult | None] = [None] * len(tool_calls)
    approved: list[tuple[int, ToolCall, BaseTool]] = []

    for i, tc in enumerate(tool_calls):
        # -- Cancellation check --
        if config and config.cancellation:
            config.cancellation.check()

        # -- PreToolUse hook (W5 finishing-touch: mirror sync executor) --
        # Without this, .claude/settings.json hooks declared via the mink CLI
        # parse + thread into LoopConfig but never fire, because mink runs
        # go through this async executor.
        tc, hook_out, hook_denial = _apply_pre_tool_use_hook(tc, config)
        if hook_denial is not None:
            context.add(Message.tool(tc.id, f"Blocked by hook: {hook_denial}"))
            ordered_results[i] = ToolResult(output="", error=f"Blocked by hook: {hook_denial}")
            continue
        hook_decision = hook_out.permission_decision if hook_out else None

        # -- Permission check (skipped iff hook returned permissionDecision) --
        if config and config.permissions and hook_decision not in {"allow", "deny"}:
            from chimera.permissions.base import PermissionAction

            action = config.permissions.evaluate(tc.name, tc.arguments)
            if config.event_bus:
                from chimera.events.types import PermissionEvent

                config.event_bus.publish(
                    PermissionEvent(
                        tool_name=tc.name,
                        action=action.value,
                        granted=action != PermissionAction.DENY,
                        call_id=tc.id,
                    )
                )
            if action == PermissionAction.DENY:
                context.add(Message.tool(tc.id, f"Permission denied for {tc.name}"))
                ordered_results[i] = ToolResult(
                    output=f"Permission denied for {tc.name}",
                )
                continue
            if action == PermissionAction.ASK:
                # Flush what we've ordered so far in their original slots
                for r in ordered_results[:i]:
                    if r is not None:
                        result.results.append(r)
                result.pending = PendingApproval(
                    tool_call=tc,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    reason=f"Permission required for tool: {tc.name}",
                )
                result.remaining = list(tool_calls[i + 1 :])
                return result

        # -- Discipline guards --
        if config and config.discipline:
            for _guard in config.discipline:
                _g_ctx = {"file_path": tc.arguments.get("path", ""), "arguments": tc.arguments}
                _g_result = _guard.check(tc.name, _g_ctx)
                if not _g_result.allowed:
                    if _g_result.severity == "block":
                        from chimera.discipline.guard import DisciplineViolation
                        raise DisciplineViolation(_guard.name, _g_result.reason)

        # -- Event: tool call --
        if config and config.event_bus:
            from chimera.events.types import ToolCallEvent

            config.event_bus.publish(
                ToolCallEvent(tool_name=tc.name, arguments=tc.arguments, call_id=tc.id)
            )

        # -- Resolve tool --
        tool = tool_map.get(tc.name)
        if tool is None:
            context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
            ordered_results[i] = ToolResult(output="", error=f"Unknown tool {tc.name}")
            result.executed += 1
            continue

        # -- Ghost commit: snapshot before file-modifying tools --
        if config and config.ghost_commits and tc.name in _FILE_MODIFYING_TOOLS:
            path = tc.arguments.get("path", "")
            if path:
                config.ghost_commits.snapshot(f"{tc.name}: {path}", [path])

        # -- Cancellation: bind token to cancellable tools --
        if config and config.cancellation:
            from chimera.core.cancellation import CancellableTool
            if isinstance(tool, CancellableTool):
                tool.bind_cancellation(config.cancellation)

        approved.append((i, tc, tool))

    if not approved:
        # Only denial / unknown tools — emit in order, skip empty slots
        for r in ordered_results:
            if r is not None:
                result.results.append(r)
        return result

    # Phase 2: execute all approved tools concurrently.
    # Audit H-4: when ``config.tool_timeout_s`` is set, wrap each dispatch
    # in ``asyncio.wait_for`` so a runaway tool returns a synthetic error
    # result rather than blocking the whole turn. The error message is
    # discoverable by the agent's next reasoning step so it can react
    # (e.g. retry with smaller input) instead of crashing the run.
    timeout_s = config.tool_timeout_s if config is not None else None

    async def _run(tc: ToolCall, t: BaseTool) -> ToolResult:
        try:
            if timeout_s is not None:
                return await asyncio.wait_for(
                    t.async_execute(tc.arguments, env),
                    timeout=timeout_s,
                )
            return await t.async_execute(tc.arguments, env)
        except asyncio.TimeoutError:
            return ToolResult(
                output="",
                error=f"Tool {tc.name} exceeded {timeout_s}s timeout",
            )
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    tool_results = await asyncio.gather(
        *[_run(tc, t) for _, tc, t in approved]
    )

    # Slot approved tool results back into their original indexes so order is preserved.
    for (idx, _tc, _tool), tr in zip(approved, tool_results):
        ordered_results[idx] = tr

    # Phase 3: post-process — add to context, emit events, detect loops
    # Walk tool_calls in order; for approved slots, run the full post-processing.
    approved_index_map = {idx for idx, _, _ in approved}
    for i, tc in enumerate(tool_calls):
        tr_opt = ordered_results[i]
        if tr_opt is None:
            continue
        tr = tr_opt
        if i not in approved_index_map:
            # Denied or unknown — already appended context message; just record result.
            result.results.append(tr)
            continue
        # Proceed to full post-processing for this approved tool call.
        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"

        # -- Truncation --
        if tr.success:
            from chimera.core.truncation import truncate_output

            trunc_cfg = config.truncation if config else None
            content = truncate_output(content, trunc_cfg)

        context.add(Message.tool(tc.id, content))
        result.results.append(tr)
        result.executed += 1

        # -- Wire: status update --
        if config and config.wire:
            from chimera.wire.types import StatusUpdate
            config.wire.send(StatusUpdate(
                step=0,  # not known at this level
                metadata={"tool": tc.name, "success": tr.success},
            ))

        # -- Audit log --
        if config and config.audit_log:
            config.audit_log.record(
                tool_name=tc.name, arguments=tc.arguments, decision="allowed"
            )

        # -- Checkpoint --
        if config and config.checkpoint_manager and tr.success:
            config.checkpoint_manager.create(description=f"After {tc.name}")

        # -- Event: tool result --
        if config and config.event_bus:
            from chimera.events.types import ErrorEvent, ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=tr.success,
                    tool_metadata=tr.metadata,
                )
            )
            if not tr.success:
                config.event_bus.publish(
                    ErrorEvent(
                        error=str(tr.error or "tool failed"),
                        recoverable=True,
                    ),
                )

        # -- Feedback tracker: learn from errors --
        if config and config.feedback_tracker:
            try:
                from chimera.events.types import ToolResultEvent as _TRE
                config.feedback_tracker.on_tool_result(_TRE(
                    call_id=tc.id, output=content,
                    success=tr.success, tool_metadata=tr.metadata,
                ))
            except Exception:
                pass  # Learning is best-effort

        # -- File tracking --
        if config and config.file_tracker:
            if tc.name in _FILE_READING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_read(path)
            elif tc.name in _FILE_MODIFYING_TOOLS:
                path = tc.arguments.get("path", "")
                if path:
                    config.file_tracker.record_modified(path)

        # -- Loop detection --
        if config and config.detector:
            from chimera.detection.actions import OnDetect

            det_result = config.detector.record_and_check(tc.name, tc.arguments)
            if det_result is not None:
                if config.event_bus:
                    from chimera.events.types import LoopDetectedEvent

                    config.event_bus.publish(
                        LoopDetectedEvent(pattern=det_result.pattern)
                    )
                if config.detector.on_detect == OnDetect.BREAK:
                    raise LoopBreak(det_result.pattern)
                if config.detector.on_detect == OnDetect.ASK:
                    result.pending = PendingApproval(
                        tool_call=tc,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        reason=f"Loop detected: {det_result.pattern}",
                    )
                    return result

    return result
