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
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from chimera.core.abort import AbortSignal
from chimera.core.content_replacement import ContentReplacementState
from chimera.core.file_state_cache import FileStateCache
from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
from chimera.core.loop_state import LoopState, QuerySource, RetryPolicy, RETRY_POLICIES
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
    from chimera.hooks.executor import HookExecutor
    from chimera.hooks.types import HookMatcher

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
        max_turns: int = 100,
        abort_signal: AbortSignal | None = None,
        query_source: QuerySource = QuerySource.FOREGROUND,
        hook_executor: HookExecutor | None = None,
        hook_matchers: list[HookMatcher] | None = None,
        permission_checker: PermissionChecker | None = None,
        permission_context: PermissionContext | None = None,
        content_replacement: ContentReplacementState | None = None,
        transcript: TranscriptStorage | None = None,
        file_state_cache: FileStateCache | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Run the agent loop, yielding :class:`LoopEvent` instances.

        Args:
            messages: Initial conversation messages.
            tools: Available tools the model may invoke.
            provider: LLM provider with an ``async_complete`` method.
            system_prompt: System prompt prepended to the conversation.
                Accepts a plain string or a :class:`SystemPrompt` object.
            max_turns: Maximum number of LLM calls before forcing a stop.
            abort_signal: Optional signal to cooperatively cancel the loop.
            query_source: Categorises the caller (foreground / background / fork).
            hook_executor: Optional hook executor for lifecycle hooks.
            hook_matchers: Optional list of hook matchers to apply.
            permission_checker: Optional permission checker for tool calls.
            permission_context: Context snapshot for permission checks.
            content_replacement: Optional state tracker for large-result persistence.
            transcript: Optional transcript storage for recording messages.
            file_state_cache: Optional LRU file-state cache for tools.

        Yields:
            :class:`LoopEvent` instances for each significant loop step.
        """
        start_time = time.time()
        total_usage: dict[str, int] = {}
        total_cost = 0.0

        # ----- Fire SESSION_START hook -----
        if hook_executor is not None and hook_matchers is not None:
            from chimera.hooks.events import HookEvent
            from chimera.hooks.types import HookInput

            session_start_input = HookInput(
                event=HookEvent.SESSION_START.value,
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

        while True:
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

            # ----- Check max turns -----
            if state.turn_count >= max_turns:
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

            # ----- Phase A: Stream start -----
            yield LoopEvent(
                type=LoopEventType.stream_start,
                data=None,
                turn=state.turn_count,
            )

            # ----- Phase B: Call provider -----
            api_messages = [Message.system(prompt_str)] + working_messages

            try:
                response = await provider.async_complete(
                    api_messages, tools=tool_schemas,
                )
            except Exception as exc:
                # Attempt error recovery (CG-1)
                error_type = _classify_error(exc)
                if error_type is not None:
                    withheld = WithheldError(
                        type=error_type,
                        original_error=exc,
                    )
                    result = await recovery.attempt_recovery(state, withheld)
                    if result.should_continue:
                        continue
                # Unrecoverable — re-raise
                raise

            # Accumulate usage / cost
            _merge_usage(total_usage, response.usage)
            total_cost += calculate_cost(
                getattr(provider, "model_name", "unknown"),
                response.usage,
            )

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

            # ----- Phase C: No tool calls -> completed -----
            if not response.tool_calls:
                # Fire STOP hook before completing
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.types import HookInput

                    stop_input = HookInput(
                        event=HookEvent.STOP.value,
                        session_id="",
                    )
                    stop_result = await hook_executor.execute(
                        HookEvent.STOP, stop_input, hook_matchers, abort_signal,
                    )
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

                # Fire SESSION_END hook before completing
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.types import HookInput

                    session_end_input = HookInput(
                        event=HookEvent.SESSION_END.value,
                        session_id="",
                    )
                    await hook_executor.execute(
                        HookEvent.SESSION_END, session_end_input,
                        hook_matchers, abort_signal,
                    )

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
            effective_args_map: dict[str, dict] = {}  # tc.id -> possibly modified args

            for tc in response.tool_calls:
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
                    from chimera.hooks.types import HookInput

                    pre_input = HookInput(
                        event=HookEvent.PRE_TOOL_USE.value,
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

            # CG-8: Create ONE StreamingToolExecutor for ALL non-blocked tool calls
            non_blocked_tcs = [
                tc for tc in response.tool_calls if tc.id not in hook_blocked
            ]

            if non_blocked_tcs:
                executor = StreamingToolExecutor(tools)
                for tc in non_blocked_tcs:
                    modified_tc = ToolCall(
                        id=tc.id,
                        name=tc.name,
                        arguments=effective_args_map.get(tc.id, dict(tc.arguments)),
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

                    tool_call_results.append((stc, sresult))

                # --- POST_TOOL_USE / POST_TOOL_USE_FAILURE hook ---
                if hook_executor is not None and hook_matchers is not None:
                    from chimera.hooks.events import HookEvent
                    from chimera.hooks.types import HookInput

                    for stc, sresult in exec_results:
                        if sresult.success:
                            post_event = HookEvent.POST_TOOL_USE
                        else:
                            post_event = HookEvent.POST_TOOL_USE_FAILURE

                        post_input = HookInput(
                            event=post_event.value,
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

            results = tool_call_results

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

            # Record tool result messages in transcript (CG-4)
            if transcript is not None:
                for trm in tool_result_messages:
                    await transcript.record(trm)

            # Advance LoopState via next_turn (CG-1)
            state = state.next_turn(assistant_msg, tool_result_messages)

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
