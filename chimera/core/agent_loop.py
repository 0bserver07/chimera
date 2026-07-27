"""AgentLoop: core async-generator loop that drives tool-augmented LLM agents.

Integrates the leaf modules from Phase 1 (T1-T7):

- :mod:`chimera.core.loop_events` — event types yielded by the loop
- :mod:`chimera.core.abort` — cooperative cancellation
- :mod:`chimera.core.loop_state` — per-turn bookkeeping
- :mod:`chimera.core.streaming_executor` — concurrent tool execution

The loop is an **async generator**: callers iterate over it with
``async for event in loop.run(...):`` and receive :class:`LoopEvent`
instances describing what happened on each step.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from chimera.core.abort import AbortSignal
from chimera.core.content_replacement import ContentReplacementState
from chimera.core.file_state_cache import FileStateCache
from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
from chimera.core.loop_state import LoopState, QuerySource, RETRY_POLICIES
from chimera.core.message_queue import SteeringMessageQueue
from chimera.core.recovery import ErrorRecovery, WithheldError
from chimera.core.streaming_executor import StreamingToolExecutor
from chimera.core.system_prompt import SystemPrompt
from chimera.core.tool import BaseTool
from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.rules import PermissionBehavior
from chimera.providers.cost import calculate_cost
from chimera.sessions.transcript import TranscriptStorage
from chimera.types import Message, ToolCall, ToolResult

if TYPE_CHECKING:
    from chimera.core.budget import BudgetEnforcer
    from chimera.core.compaction_integration import CompactionIntegration
    from chimera.core.interception import Interceptors
    from chimera.hooks.executor import HookExecutor
    from chimera.hooks.hook_types import HookMatcher

__all__ = ["AgentLoop"]


class AgentLoop:
    """Core agent loop that alternates between LLM calls and tool execution.

    Usage::

        loop = AgentLoop()
        async for event in loop.run(messages, tools=tools, provider=provider,
                                     system_prompt="You are helpful."):
            handle(event)
    """

    async def run(
        self,
        messages: list[Message],
        *,
        tools: list[BaseTool],
        provider: Any,
        system_prompt: str | SystemPrompt,
        max_turns: int | None = 100,
        abort_signal: AbortSignal | None = None,
        query_source: QuerySource = QuerySource.FOREGROUND,
        hook_executor: HookExecutor | None = None,
        hook_matchers: list[HookMatcher] | None = None,
        permission_checker: PermissionChecker | None = None,
        permission_context: PermissionContext | None = None,
        approval_handler: Any = None,
        content_replacement: ContentReplacementState | None = None,
        transcript: TranscriptStorage | None = None,
        file_state_cache: FileStateCache | None = None,
        compaction: CompactionIntegration | None = None,
        stream: bool = False,
        message_queue: SteeringMessageQueue | None = None,
        enable_action_nudge: bool = True,
        enable_auto_continue: bool = True,
        env: Any = None,
        loop_detector: Any = None,
        interceptors: "Interceptors | None" = None,
        budget_enforcer: "BudgetEnforcer | None" = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Run the agent loop, yielding :class:`LoopEvent` instances.

        Args:
            messages: Initial conversation messages.
            tools: Available tools the model may invoke.
            provider: LLM provider with an ``async_complete`` method.
            system_prompt: System prompt prepended to the conversation.
                Accepts a plain string or a :class:`SystemPrompt` object.
            max_turns: Maximum number of LLM calls before forcing a stop.
                ``None`` runs unlimited — until the task completes, the loop
                detector trips, or abort fires.
            abort_signal: Optional signal to cooperatively cancel the loop.
            query_source: Categorises the caller (foreground / background / fork).
            hook_executor: Optional hook executor for lifecycle hooks.
            hook_matchers: Optional list of hook matchers to apply.
            permission_checker: Optional permission checker for tool calls.
            permission_context: Context snapshot for permission checks.
            approval_handler: Optional interactive handler for ASK decisions
                (#171). Called with a wire
                :class:`~chimera.wire.types.ApprovalRequest`; may return an
                :class:`~chimera.wire.types.ApprovalResponse` directly or an
                awaitable of one. While an awaitable handler is pending it is
                raced against *abort_signal*, so cancelling the turn never
                deadlocks on an unanswered prompt. ``None`` keeps the legacy
                behavior: ASK decisions skip the tool with a
                "user approval needed" error result.
            content_replacement: Optional state tracker for large-result persistence.
            transcript: Optional transcript storage for recording messages.
            file_state_cache: Optional LRU file-state cache for tools.
            compaction: Optional compaction integration for context management.
            stream: If ``True`` and provider has ``async_stream()``, use
                streaming to get text deltas and yield ``assistant_chunk``
                events.  Falls back to ``async_complete`` otherwise.
            interceptors: Optional decision-capable seams
                (:class:`~chimera.core.interception.Interceptors`) applied
                at the same four points as the ``LoopConfig``-driven loops:
                context rewrite + provider request before each model call,
                tool_call before execution (runs before the permission
                check), tool_result before it enters the conversation.  A
                blocked provider call ends the run with reason
                ``"interceptor_blocked: ..."``; a blocked tool call
                surfaces as a denial-with-reason tool result.  This loop
                has no event bus, so decisions surface through those
                results rather than :class:`InterceptorEvent`.  ``None``
                (default) leaves behavior unchanged.
            budget_enforcer: Optional :class:`~chimera.core.budget.BudgetEnforcer`
                (reused across turns by a caller that owns it, e.g. a TUI lane).
                Each completed provider call and tool call is recorded against
                it, and the wall clock is re-checked at every turn boundary; the
                first crossed cap ends the run with reason
                ``"budget_exhausted:<dimension>"`` (``cost`` / ``llm_calls`` /
                ``wall_clock`` / ``tool_calls``), the unit that tipped it allowed
                to finish. The caller is responsible for ``start()`` / ``pause()``
                around the turn so wall-clock accrues only active time. ``None``
                (default) leaves behavior byte-identical.

        Yields:
            :class:`LoopEvent` instances for each significant loop step.
        """
        start_time = time.time()
        total_usage: dict[str, int] = {}
        total_cost = 0.0

        def _budget_result() -> LoopEvent:
            """Build the terminal event for a tripped budget (reads live locals)."""
            dimension = (
                budget_enforcer.exhausted_dimension if budget_enforcer else None
            ) or "budget"
            return LoopEvent(
                type=LoopEventType.result,
                data=LoopResult(
                    reason=f"budget_exhausted:{dimension}",
                    messages=working_messages,
                    usage=total_usage,
                    cost_usd=total_cost,
                    duration_ms=(time.time() - start_time) * 1000,
                    turn_count=state.turn_count,
                ),
                turn=state.turn_count,
            )

        # ----- Fire SESSION_START hook -----
        if hook_executor is not None and hook_matchers is not None:
            from chimera.hooks.events import HookEvent
            from chimera.hooks.hook_types import HookInput

            session_start_input = HookInput(
                event=HookEvent.SESSION_START,
                session_id="",
            )
            await hook_executor.execute(
                HookEvent.SESSION_START, session_start_input, hook_matchers,
                abort_signal,
            )

        # Resolve system prompt to a string (CG-3)
        prompt_str: str = (
            system_prompt.to_string()
            if isinstance(system_prompt, SystemPrompt)
            else system_prompt
        )

        # Build working copy of messages (system prompt + caller messages)
        working_messages: list[Message] = list(messages)

        # Initialise LoopState for structured bookkeeping (CG-1)
        state = LoopState(
            messages=list(working_messages),
            turn_count=0,
        )

        # Initialise ErrorRecovery (CG-1)
        recovery = ErrorRecovery()

        # Store query_source on state metadata (available for retry decisions)
        _retry_policy = RETRY_POLICIES.get(query_source)

        # Build tool schemas once
        tool_schemas = [t.to_anthropic_schema() for t in tools] if tools else None

        # --- #131/#132 enforcement state ---
        _nudge_count = 0
        _any_tools_called = False
        _files_edited: list[str] = []

        _EDIT_TOOL_NAMES = frozenset({
            "edit_file", "write_file", "replace_in_file", "apply_patch", "multi_edit",
        })
        _has_edit_tools = any(t.name in _EDIT_TOOL_NAMES for t in tools) if tools else False

        while True:
            # ----- Check the budget before calling the model -----
            # Re-checks the wall clock (which advances without records) and
            # catches a cumulative cap already spent by a previous turn, so a
            # lane that exhausted its budget stops the moment its next turn
            # begins. Takes precedence over the abort check below.
            if budget_enforcer is not None:
                budget_enforcer.check()
                if budget_enforcer.exhausted:
                    yield _budget_result()
                    return

            # ----- Check abort before calling the model -----
            if abort_signal is not None and abort_signal.aborted:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason=f"aborted_{abort_signal.reason or 'unknown'}",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=state.turn_count,
                    ),
                    turn=state.turn_count,
                )
                return

            # ----- Check max turns (None = unlimited) -----
            if max_turns is not None and state.turn_count >= max_turns:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason="max_turns",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=state.turn_count,
                    ),
                    turn=state.turn_count,
                )
                return

            # ----- Compaction: auto-compact if needed -----
            # Budget tracks the provider's real context window so a 1M-token
            # model isn't compacted as if it only had 100K. Falls back to 100K
            # when the provider advertises no window.
            if compaction is not None:
                ctx_window = getattr(provider, "context_window", 100_000) or 100_000
                working_messages, compacted = await compaction.auto_compact_if_needed(
                    working_messages, token_budget=ctx_window,
                )
                if compacted:
                    yield LoopEvent(
                        type=LoopEventType.compact_boundary,
                        data="auto_compact",
                        turn=state.turn_count,
                    )

            # ----- Phase A: Stream start -----
            yield LoopEvent(
                type=LoopEventType.stream_start,
                data=None,
                turn=state.turn_count,
            )

            # ----- Phase B: Call provider -----
            api_messages = [Message.system(prompt_str)] + working_messages

            # -- Interception seams: context rewrite + provider request --
            # Ephemeral (per call); no-op when interceptors is None. A block
            # ends the run with an "interceptor_blocked" result reason.
            from chimera.core.interception import apply_pre_provider_seams

            _seams = apply_pre_provider_seams(
                interceptors, provider, api_messages, tool_schemas,
            )
            if _seams.block_reason is not None:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason=f"interceptor_blocked: {_seams.block_reason}",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=state.turn_count,
                    ),
                    turn=state.turn_count,
                )
                return
            api_messages = _seams.messages
            call_tool_schemas = _seams.tools

            try:
                # Streaming path: use async_stream if available and requested
                if stream and hasattr(provider, "async_stream"):
                    from chimera.providers.base import Response as _Response

                    accumulated_content = ""
                    accumulated_tool_calls: list[ToolCall] = []
                    accumulated_usage: dict[str, int] = {}

                    async for stream_event in provider.async_stream(
                        api_messages, tools=call_tool_schemas, **_seams.kwargs,
                    ):
                        if stream_event.type == "text_delta":
                            accumulated_content += stream_event.content
                            yield LoopEvent(
                                type=LoopEventType.assistant_chunk,
                                data=stream_event.content,
                                turn=state.turn_count,
                            )
                        elif stream_event.type == "thinking_delta":
                            # Reasoning text: forwarded for display only — never
                            # accumulated into the assistant message content.
                            yield LoopEvent(
                                type=LoopEventType.thinking_chunk,
                                data=stream_event.content,
                                turn=state.turn_count,
                            )
                        elif stream_event.type == "tool_call_start" and stream_event.tool_call is not None:
                            accumulated_tool_calls.append(stream_event.tool_call)
                        elif stream_event.type == "tool_call_complete" and stream_event.tool_call is not None:
                            # Replace partial tool call with complete one
                            accumulated_tool_calls = [
                                stream_event.tool_call if tc.id == stream_event.tool_call.id else tc
                                for tc in accumulated_tool_calls
                            ]
                        elif stream_event.type == "done":
                            if stream_event.usage is not None:
                                accumulated_usage = stream_event.usage

                    response = _Response(
                        content=accumulated_content,
                        tool_calls=accumulated_tool_calls,
                        usage=accumulated_usage,
                    )
                else:
                    # Non-streaming path: use async_complete
                    response = await provider.async_complete(
                        api_messages, tools=call_tool_schemas, **_seams.kwargs,
                    )
            except Exception as exc:
                # Attempt error recovery (CG-1)
                error_type = _classify_error(exc)
                if error_type is not None:
                    # Try reactive compaction first for prompt_too_long
                    if error_type == "prompt_too_long" and compaction is not None:
                        compacted_msgs = await compaction.reactive_compact(working_messages)
                        if compacted_msgs is not None:
                            working_messages = compacted_msgs
                            continue  # Retry with compacted messages

                    withheld = WithheldError(
                        type=error_type,
                        original_error=exc,
                    )
                    recovery_result = await recovery.attempt_recovery(state, withheld)
                    if recovery_result.should_continue:
                        continue
                # Unrecoverable — re-raise
                raise
            finally:
                # Header injection is per-call: restore even on a raise.
                if _seams.header_snapshot is not None:
                    provider.request_headers = _seams.header_snapshot

            # Accumulate usage / cost
            _merge_usage(total_usage, response.usage)
            _step_cost = calculate_cost(
                getattr(provider, "model_name", "unknown"),
                response.usage,
            )
            total_cost += _step_cost
            # Record this completed provider call against the budget (its cost is
            # the loop's own priced figure, so no provider wrapper is needed).
            if budget_enforcer is not None:
                budget_enforcer.record_llm_call(cost=_step_cost)

            # Record assistant message in transcript (CG-4)
            if transcript is not None:
                assistant_transcript_msg = Message.assistant(
                    response.content,
                    tool_calls=response.tool_calls,
                )
                await transcript.record(assistant_transcript_msg)

            yield LoopEvent(
                type=LoopEventType.assistant,
                data=response,
                turn=state.turn_count,
            )

            # ----- Budget: a cost/LLM cap tripped by THIS call ends the turn -----
            # The tipping call finished and its response is shown; the tools it
            # requested are not started (the (N+1)th unit never runs).
            if budget_enforcer is not None and budget_enforcer.exhausted:
                yield _budget_result()
                return

            # Surface each tool call before execution so a TUI can render the
            # call (name + args) ahead of its result.
            for _tc in response.tool_calls:
                yield LoopEvent(
                    type=LoopEventType.tool_use,
                    data=_tc,
                    turn=state.turn_count,
                )

            # ----- Phase C: No tool calls -> completed -----
            if not response.tool_calls:
                # Fire STOP hook before completing
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.hook_types import HookInput, HookOutput

                    stop_input = HookInput(
                        event=HookEvent.STOP,
                        session_id="",
                    )
                    try:
                        stop_result = await hook_executor.execute(
                            HookEvent.STOP, stop_input, hook_matchers, abort_signal,
                        )
                    except Exception as stop_exc:
                        # Fire STOP_FAILURE on error
                        stop_failure_input = HookInput(
                            event=HookEvent.STOP_FAILURE,
                            session_id="",
                            tool_error=str(stop_exc),
                        )
                        await hook_executor.execute(
                            HookEvent.STOP_FAILURE, stop_failure_input,
                            hook_matchers, abort_signal,
                        )
                        stop_result = HookOutput()

                    if not stop_result.continue_execution:
                        # Inject the stop_reason as a user message and continue
                        reason_text = stop_result.stop_reason or "Hook prevented stop"
                        working_messages.append(
                            Message.assistant(response.content),
                        )
                        working_messages.append(
                            Message.user(reason_text),
                        )
                        # Advance state for the non-tool turn
                        state = LoopState(
                            messages=list(working_messages),
                            turn_count=state.turn_count + 1,
                            max_output_tokens_recovery_count=state.max_output_tokens_recovery_count,
                            has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                            max_output_tokens_override=state.max_output_tokens_override,
                            transition_reason=state.transition_reason,
                        )
                        continue

                # ----- Follow-up injection: prevent early stop -----
                if message_queue is not None and message_queue.has_follow_up():
                    follow_ups = message_queue.drain_follow_up()
                    working_messages.append(
                        Message.assistant(response.content),
                    )
                    working_messages.extend(follow_ups)
                    state = LoopState(
                        messages=list(working_messages),
                        turn_count=state.turn_count + 1,
                        max_output_tokens_recovery_count=state.max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                        max_output_tokens_override=state.max_output_tokens_override,
                        transition_reason=state.transition_reason,
                    )
                    continue

                # ----- #131: Action nudge — text-only with no tool use -----
                if (
                    enable_action_nudge
                    and _has_edit_tools  # only nudge when edit tools are available
                    and not _any_tools_called
                    and _nudge_count < 2
                ):
                    _nudge_count += 1
                    assistant_msg = Message.assistant(response.content)
                    working_messages.append(assistant_msg)
                    working_messages.append(Message.user(
                        "You described what to do but didn't use any tools to make changes. "
                        "Please use the appropriate tools (edit_file, write_file, bash, etc.) "
                        "to implement the actual changes."
                    ))
                    state = LoopState(
                        messages=list(working_messages),
                        turn_count=state.turn_count + 1,
                        max_output_tokens_recovery_count=state.max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                        max_output_tokens_override=state.max_output_tokens_override,
                        transition_reason=state.transition_reason,
                    )
                    continue

                # ----- #132: Auto-continue when no edits made -----
                if (
                    enable_auto_continue
                    and _has_edit_tools  # only auto-continue when edit tools are available
                    and not _files_edited
                    and _any_tools_called
                    and (max_turns is None or state.turn_count < max_turns - 5)
                    and _nudge_count < 2
                ):
                    _nudge_count += 1
                    assistant_msg = Message.assistant(response.content)
                    working_messages.append(assistant_msg)
                    working_messages.append(Message.user(
                        "You haven't made any file changes yet. "
                        "Please use edit_file or write_file to implement the changes needed."
                    ))
                    state = LoopState(
                        messages=list(working_messages),
                        turn_count=state.turn_count + 1,
                        max_output_tokens_recovery_count=state.max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                        max_output_tokens_override=state.max_output_tokens_override,
                        transition_reason=state.transition_reason,
                    )
                    continue

                # Fire SESSION_END hook before completing
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.hook_types import HookInput

                    session_end_input = HookInput(
                        event=HookEvent.SESSION_END,
                        session_id="",
                    )
                    await hook_executor.execute(
                        HookEvent.SESSION_END, session_end_input,
                        hook_matchers, abort_signal,
                    )

                # Persist the terminal assistant answer. Every other exit path
                # (hook-stop, follow-up, nudge) appends it; the completed path
                # must too, or the model's final reply is dropped from history —
                # invisible to save/resume (the reply is only emitted as an
                # event + recorded in the transcript, not the message list).
                working_messages.append(Message.assistant(response.content))

                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason="completed",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=state.turn_count + 1,
                    ),
                    turn=state.turn_count + 1,
                )
                return

            # ----- Phase D: Execute tools (with hook and permission integration) -----
            # Process tool calls with PRE_TOOL_USE / POST_TOOL_USE hooks
            tool_call_results: list[tuple[ToolCall, ToolResult]] = []

            # Separate tool calls into those that need hooks (sequential)
            # vs those that can be batched (CG-8)
            hook_blocked: set[str] = set()  # tc.id -> blocked by hook
            effective_args_map: dict[str, dict[str, Any]] = {}  # tc.id -> possibly modified args
            effective_tc_map: dict[str, ToolCall] = {}  # tc.id -> interceptor-effective call

            for tc in response.tool_calls:
                # --- Interception seam: tool_call (block / mutate) ---
                # Runs BEFORE the permission check and hooks so every
                # downstream security decision evaluates the
                # interceptor-effective call (fail-closed; see
                # chimera/core/interception.py).
                if interceptors is not None and interceptors.tool_call:
                    from chimera.core.interception import intercept_tool_call

                    tc, _intercept_block = intercept_tool_call(
                        interceptors.tool_call, tc,
                    )
                    if _intercept_block is not None:
                        tool_call_results.append(
                            (tc, ToolResult(
                                output="",
                                error=f"Blocked by interceptor: {_intercept_block}",
                            )),
                        )
                        hook_blocked.add(tc.id)
                        continue

                effective_args = dict(tc.arguments)
                skip_tool = False

                # --- Permission check (CG-2) ---
                if permission_checker is not None and permission_context is not None:
                    tool_obj = _find_tool(tools, tc.name)
                    if tool_obj is not None:
                        decision = await permission_checker.check(
                            tool_obj, effective_args, permission_context,
                        )
                        if decision.behavior == PermissionBehavior.DENY:
                            tool_call_results.append(
                                (tc, ToolResult(
                                    output="",
                                    error=f"Permission denied: {decision.message}",
                                )),
                            )
                            skip_tool = True
                        elif decision.behavior == PermissionBehavior.ASK:
                            if approval_handler is not None:
                                # Interactive seam (#171): route the ASK to the
                                # frontend and wait for the user's decision.
                                approved, verdict = await _resolve_approval(
                                    approval_handler, tc, effective_args, abort_signal,
                                )
                                if not approved:
                                    tool_call_results.append(
                                        (tc, ToolResult(
                                            output="",
                                            error=f"Permission denied: {verdict}",
                                        )),
                                    )
                                    skip_tool = True
                                # approved: fall through and execute unchanged
                            else:
                                # No interactive handler — skip with "permission required"
                                tool_call_results.append(
                                    (tc, ToolResult(
                                        output="",
                                        error="Permission required: user approval needed",
                                    )),
                                )
                                skip_tool = True
                        # ALLOW: proceed; apply updated_input if provided
                        elif decision.updated_input is not None:
                            effective_args = decision.updated_input

                # --- PRE_TOOL_USE hook ---
                if not skip_tool and hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.hook_types import HookInput

                    pre_input = HookInput(
                        event=HookEvent.PRE_TOOL_USE,
                        session_id="",
                        tool_name=tc.name,
                        tool_input=dict(tc.arguments),
                    )
                    pre_result = await hook_executor.execute(
                        HookEvent.PRE_TOOL_USE, pre_input, hook_matchers, abort_signal,
                    )
                    if not pre_result.continue_execution:
                        # Tool blocked by hook — produce a denial result
                        denial_msg = pre_result.reason or pre_result.stop_reason or "Blocked by hook"
                        tool_call_results.append(
                            (tc, ToolResult(output=f"Tool blocked: {denial_msg}")),
                        )
                        skip_tool = True
                    elif pre_result.updated_input is not None:
                        effective_args = pre_result.updated_input

                if skip_tool:
                    hook_blocked.add(tc.id)
                else:
                    effective_args_map[tc.id] = effective_args
                    effective_tc_map[tc.id] = tc

            # CG-8: Create ONE StreamingToolExecutor for ALL non-blocked tool calls
            non_blocked_tcs = [
                tc for tc in response.tool_calls if tc.id not in hook_blocked
            ]

            if non_blocked_tcs:
                executor = StreamingToolExecutor(tools, env=env)
                for tc in non_blocked_tcs:
                    # Dispatch the interceptor-effective call (name may have
                    # been replaced) with the hook-effective arguments.
                    base_tc = effective_tc_map.get(tc.id, tc)
                    modified_tc = ToolCall(
                        id=tc.id,
                        name=base_tc.name,
                        arguments=effective_args_map.get(tc.id, dict(base_tc.arguments)),
                    )
                    await executor.submit(modified_tc)

                if abort_signal is not None:
                    collect_task = asyncio.ensure_future(executor.collect())

                    async def _wait_for_abort() -> None:
                        while not abort_signal.aborted:
                            await asyncio.sleep(0.01)

                    abort_waiter = asyncio.ensure_future(_wait_for_abort())
                    done_set, _ = await asyncio.wait(
                        {collect_task, abort_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if abort_waiter in done_set:
                        collect_task.cancel()
                        try:
                            await collect_task
                        except asyncio.CancelledError:
                            pass
                        exec_results = await executor.discard()
                    else:
                        abort_waiter.cancel()
                        try:
                            await abort_waiter
                        except asyncio.CancelledError:
                            pass
                        exec_results = collect_task.result()
                else:
                    exec_results = await executor.collect()

                # --- Interception seam: tool_result (patch / withhold) ---
                # Fail-open; applied before content replacement, post hooks,
                # and the conversation append so downstream consumers see
                # only the effective result.
                if interceptors is not None and interceptors.tool_result:
                    from chimera.core.interception import intercept_tool_result

                    exec_results = [
                        (stc, intercept_tool_result(
                            interceptors.tool_result, stc, sresult,
                        ))
                        for stc, sresult in exec_results
                    ]

                for stc, sresult in exec_results:
                    # CG-4: Content replacement check
                    if content_replacement is not None:
                        result_text = sresult.output if sresult.success else (sresult.error or "")
                        tool_obj = _find_tool(tools, stc.name)
                        tool_max = getattr(tool_obj, "max_result_size_chars", None) if tool_obj else None
                        if content_replacement.should_persist(stc.id, len(result_text), tool_max):
                            # Record the decision (actual persistence is a higher-level concern)
                            preview = result_text[:content_replacement.preview_size_bytes]
                            content_replacement.record_decision(
                                stc.id,
                                persisted_path=f"<persisted:{stc.id}>",
                                preview=preview,
                                original_size=len(result_text),
                            )

                    # Record the completed tool call against the budget (the
                    # normalized cross-loop unit; blocked/denied calls that never
                    # executed are deliberately not counted).
                    if budget_enforcer is not None:
                        budget_enforcer.record_tool_call(stc.name)

                    tool_call_results.append((stc, sresult))

                # --- POST_TOOL_USE / POST_TOOL_USE_FAILURE hook ---
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.hook_types import HookInput

                    for stc, sresult in exec_results:
                        if sresult.success:
                            post_event = HookEvent.POST_TOOL_USE
                        else:
                            post_event = HookEvent.POST_TOOL_USE_FAILURE

                        post_input = HookInput(
                            event=post_event,
                            session_id="",
                            tool_name=stc.name,
                            tool_input=dict(stc.arguments),
                            tool_output=sresult.output if sresult.success else None,
                            tool_error=sresult.error,
                        )
                        await hook_executor.execute(
                            post_event, post_input, hook_matchers,
                            abort_signal,
                        )

                        # Fire NOTIFICATION if tool result has an error
                        # (notifications surface tool-level issues)
                        if sresult.error:
                            notif_input = HookInput(
                                event=HookEvent.NOTIFICATION,
                                session_id="",
                                tool_name=stc.name,
                                tool_error=sresult.error,
                            )
                            await hook_executor.execute(
                                HookEvent.NOTIFICATION, notif_input,
                                hook_matchers, abort_signal,
                            )

            results = tool_call_results

            # --- #131/#132: Track tool usage and edits ---
            _any_tools_called = True
            for tc, result in results:
                if tc.name in _EDIT_TOOL_NAMES and result.success:
                    path = (
                        tc.arguments.get("file_path")
                        or tc.arguments.get("path")
                        or tc.arguments.get("file", "")
                    )
                    if path:
                        _files_edited.append(path)

            # Yield individual tool results
            for tc, result in results:
                yield LoopEvent(
                    type=LoopEventType.tool_result,
                    data=(tc, result),
                    turn=state.turn_count,
                )

            # Build tool-result messages and append to conversation
            assistant_msg = Message.assistant(
                response.content, tool_calls=response.tool_calls,
            )
            tool_result_messages = [
                Message.tool(tc.id, result.output if result.success else (result.error or ""))
                for tc, result in results
            ]
            working_messages.append(assistant_msg)
            working_messages.extend(tool_result_messages)

            # Loop-detector safety net: stop if the agent repeats the same tool
            # call (a stuck loop — matters most when max_turns is unlimited).
            if loop_detector is not None:
                for _tc, _r in results:
                    loop_detector.record(_tc.name, _tc.arguments)
                if loop_detector.check() is not None:
                    yield LoopEvent(
                        type=LoopEventType.result,
                        data=LoopResult(
                            reason="loop_detected",
                            messages=working_messages,
                            usage=total_usage,
                            cost_usd=total_cost,
                            duration_ms=(time.time() - start_time) * 1000,
                            turn_count=state.turn_count,
                        ),
                        turn=state.turn_count,
                    )
                    return

            # --- #133: Error context injection ---
            for tc, result in results:
                if not result.success:
                    error_msg = result.error or result.output or ""
                    if tc.name in ("edit_file", "write_file", "replace_in_file"):
                        working_messages.append(Message.user(
                            f"The {tc.name} tool failed: {error_msg[:500]}\n"
                            f"Please re-read the file to see its actual content, then try again with the correct text."
                        ))
                    elif tc.name == "bash":
                        working_messages.append(Message.user(
                            f"The bash command failed: {error_msg[:500]}\n"
                            f"Please diagnose the issue before retrying."
                        ))

            # Record tool result messages in transcript (CG-4)
            if transcript is not None:
                for trm in tool_result_messages:
                    await transcript.record(trm)

            # Advance LoopState via next_turn (CG-1)
            state = state.next_turn(assistant_msg, tool_result_messages)

            # ----- Budget: a tool-call or wall-clock cap tripped this turn -----
            # Checked after the turn's tool results are shown and before the
            # abort check, so a budget stop reports as budget_exhausted rather
            # than a generic abort.
            if budget_enforcer is not None and budget_enforcer.exhausted:
                yield _budget_result()
                return

            # ----- Check abort after tool execution -----
            if abort_signal is not None and abort_signal.aborted:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason=f"aborted_{abort_signal.reason or 'unknown'}",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=state.turn_count,
                    ),
                    turn=state.turn_count,
                )
                return

            # ----- Steering injection: inject messages between tool turns -----
            if message_queue is not None and message_queue.has_steering():
                steering_msgs = message_queue.drain_steering()
                working_messages.extend(steering_msgs)


def _merge_usage(total: dict[str, int], new: dict[str, int]) -> None:
    """Accumulate token usage counters into *total* in place."""
    for key, value in new.items():
        total[key] = total.get(key, 0) + value


def _find_tool(tools: list[BaseTool], name: str) -> BaseTool | None:
    """Look up a tool by name."""
    for t in tools:
        if t.name == name:
            return t
    return None


async def _resolve_approval(
    handler: Any,
    tc: ToolCall,
    args: dict[str, Any],
    abort_signal: AbortSignal | None,
) -> tuple[bool, str]:
    """Ask *handler* to decide an ASK permission for one tool call (#171).

    The handler receives a wire :class:`~chimera.wire.types.ApprovalRequest`
    and may answer synchronously or with an awaitable. An awaitable answer is
    raced against *abort_signal* with the same cooperative-poll idiom the
    tool-execution phase uses, so cancelling the turn while a prompt is open
    resolves to a denial instead of deadlocking. A handler exception is a
    denial, never a crash.

    Args:
        handler: Callable ``(ApprovalRequest) -> ApprovalResponse | awaitable``.
        tc: The tool call awaiting approval.
        args: Effective (hook-merged) tool arguments.
        abort_signal: The turn's abort signal, if any.

    Returns:
        ``(approved, reason)`` — *reason* is the user's feedback when given,
        else a generic verdict string.
    """
    from chimera.wire.types import ApprovalRequest

    request = ApprovalRequest(
        request_id=tc.id, tool_name=tc.name, tool_args=dict(args),
    )
    try:
        outcome = handler(request)
        if inspect.isawaitable(outcome):
            handler_task = asyncio.ensure_future(outcome)
            if abort_signal is not None:

                async def _wait_for_abort() -> None:
                    while not abort_signal.aborted:
                        await asyncio.sleep(0.05)

                abort_waiter = asyncio.ensure_future(_wait_for_abort())
                done_set, _ = await asyncio.wait(
                    {handler_task, abort_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if handler_task not in done_set:
                    handler_task.cancel()
                    try:
                        await handler_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    return False, "turn aborted while awaiting approval"
                abort_waiter.cancel()
                try:
                    await abort_waiter
                except asyncio.CancelledError:
                    pass
                response = handler_task.result()
            else:
                response = await handler_task
        else:
            response = outcome
    except Exception as exc:  # noqa: BLE001 - a broken prompt must not kill the turn
        return False, f"approval handler error: {exc}"
    if response is None:
        return False, "user denied"
    approved = bool(getattr(response, "approved", False))
    reason = str(getattr(response, "reason", "") or "")
    if approved:
        return True, reason or "approved by user"
    return False, reason or "denied by user"


def _classify_error(exc: Exception) -> str | None:
    """Map a provider exception to an error type string for recovery.

    Returns ``None`` if the error is not recoverable.
    """
    msg = str(exc).lower()
    if "max_output_tokens" in msg or "max_tokens" in msg:
        return "max_output_tokens"
    if "prompt_too_long" in msg or "context_length" in msg:
        return "prompt_too_long"
    return None
