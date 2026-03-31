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

import time
from collections.abc import AsyncGenerator
from typing import Any

from chimera.core.abort import AbortSignal
from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
from chimera.core.loop_state import QuerySource
from chimera.core.streaming_executor import StreamingToolExecutor
from chimera.core.tool import BaseTool
from chimera.providers.cost import calculate_cost
from chimera.types import Message, ToolCall

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
        system_prompt: str,
        max_turns: int = 100,
        abort_signal: AbortSignal | None = None,
        query_source: QuerySource = QuerySource.FOREGROUND,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Run the agent loop, yielding :class:`LoopEvent` instances.

        Args:
            messages: Initial conversation messages.
            tools: Available tools the model may invoke.
            provider: LLM provider with an ``async_complete`` method.
            system_prompt: System prompt prepended to the conversation.
            max_turns: Maximum number of LLM calls before forcing a stop.
            abort_signal: Optional signal to cooperatively cancel the loop.
            query_source: Categorises the caller (foreground / background / fork).

        Yields:
            :class:`LoopEvent` instances for each significant loop step.
        """
        start_time = time.time()
        turn = 0
        total_usage: dict[str, int] = {}
        total_cost = 0.0

        # Build working copy of messages (system prompt + caller messages)
        working_messages: list[Message] = list(messages)

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
                        turn_count=turn,
                    ),
                    turn=turn,
                )
                return

            # ----- Check max turns -----
            if turn >= max_turns:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason="max_turns",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=turn,
                    ),
                    turn=turn,
                )
                return

            # ----- Phase A: Stream start -----
            yield LoopEvent(
                type=LoopEventType.stream_start,
                data=None,
                turn=turn,
            )

            # ----- Phase B: Call provider -----
            api_messages = [Message.system(system_prompt)] + working_messages
            response = await provider.async_complete(
                api_messages, tools=tool_schemas,
            )

            # Accumulate usage / cost
            _merge_usage(total_usage, response.usage)
            total_cost += calculate_cost(
                getattr(provider, "model_name", "unknown"),
                response.usage,
            )

            turn += 1

            yield LoopEvent(
                type=LoopEventType.assistant,
                data=response,
                turn=turn,
            )

            # ----- Phase C: No tool calls -> completed -----
            if not response.tool_calls:
                yield LoopEvent(
                    type=LoopEventType.result,
                    data=LoopResult(
                        reason="completed",
                        messages=working_messages,
                        usage=total_usage,
                        cost_usd=total_cost,
                        duration_ms=(time.time() - start_time) * 1000,
                        turn_count=turn,
                    ),
                    turn=turn,
                )
                return

            # ----- Phase D: Execute tools -----
            executor = StreamingToolExecutor(tools)
            for tc in response.tool_calls:
                await executor.submit(tc)
            results = await executor.collect()

            # Yield individual tool results
            for tc, result in results:
                yield LoopEvent(
                    type=LoopEventType.tool_result,
                    data=(tc, result),
                    turn=turn,
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
                        turn_count=turn,
                    ),
                    turn=turn,
                )
                return


def _merge_usage(total: dict[str, int], new: dict[str, int]) -> None:
    """Accumulate token usage counters into *total* in place."""
    for key, value in new.items():
        total[key] = total.get(key, 0) + value
