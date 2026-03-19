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

__all__ = [
    "execute_tool_calls",
    "execute_tool_calls_incremental",
    "async_execute_tool_calls_incremental",
    "ToolExecutionResult",
]

# Tools that modify files on disk — used for ghost commit snapshots.
_FILE_MODIFYING_TOOLS = frozenset({"write_file", "edit_file", "replace_in_file"})
_FILE_READING_TOOLS = frozenset({"read_file"})


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
                    )
                )
            if action == PermissionAction.DENY:
                context.add(Message.tool(tc.id, f"Permission denied for {tc.name}"))
                continue
            if action == PermissionAction.ASK:
                raise PermissionAsk(tc.name)

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
            from chimera.events.types import ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=result.success,
                    tool_metadata=result.metadata,
                )
            )

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
            from chimera.events.types import ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=tr.success,
                    tool_metadata=tr.metadata,
                )
            )

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
    Results are ordered to match *tool_calls* order.

    :exc:`LoopBreak` is still raised (callers must handle it).
    """
    result = ToolExecutionResult()

    # Phase 1: pre-process — permission checks, tool resolution (sequential)
    approved: list[tuple[int, ToolCall, BaseTool]] = []

    for i, tc in enumerate(tool_calls):
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
                result.remaining = list(tool_calls[i + 1 :])
                return result

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

        approved.append((i, tc, tool))

    if not approved:
        return result

    # Phase 2: execute all approved tools concurrently
    async def _run(tc: ToolCall, t: BaseTool) -> ToolResult:
        try:
            return await t.async_execute(tc.arguments, env)
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    tool_results = await asyncio.gather(
        *[_run(tc, t) for _, tc, t in approved]
    )

    # Phase 3: post-process — add to context, emit events, detect loops
    for (_, tc, _tool), tr in zip(approved, tool_results):
        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"

        # -- Truncation --
        if tr.success:
            from chimera.core.truncation import truncate_output

            trunc_cfg = config.truncation if config else None
            content = truncate_output(content, trunc_cfg)

        context.add(Message.tool(tc.id, content))
        result.results.append(tr)
        result.executed += 1

        # -- Event: tool result --
        if config and config.event_bus:
            from chimera.events.types import ToolResultEvent

            config.event_bus.publish(
                ToolResultEvent(
                    call_id=tc.id,
                    output=content,
                    success=tr.success,
                    tool_metadata=tr.metadata,
                )
            )

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
