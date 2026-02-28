# chimera/streaming/loop.py
"""StreamingReAct -- ReAct loop with streaming support."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Iterator

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.providers.cost import calculate_cost
from chimera.streaming.base import StreamHandler
from chimera.types import AgentResult, Message, ToolCall

__all__ = ["StreamingReAct"]


class StreamingReAct:
    """ReAct loop that streams LLM output through a :class:`StreamHandler`.

    When the provider exposes a ``stream()`` method the loop consumes
    :class:`StreamEvent` objects incrementally, forwarding them to the
    handler and accumulating them into a complete :class:`Response`.

    If the provider does **not** have a ``stream()`` method the loop
    falls back to the regular ``complete()`` path so it can be used as
    a drop-in replacement for :class:`ReAct`.
    """

    def __init__(
        self,
        max_steps: int = 50,
        handler: StreamHandler | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.handler = handler

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        # Provider ABC now always has stream() (default wraps complete()).
        can_stream = True

        for _ in range(self.max_steps):
            steps += 1

            if self.handler:
                self.handler.on_step_start(steps)

            msgs = context.to_messages()
            tool_schemas = schemas if schemas else None

            if can_stream:
                events = provider.stream(  # type: ignore[union-attr]
                    msgs, tools=tool_schemas,
                )
                response = self._accumulate_stream(events, self.handler)
            else:
                response = provider.complete(msgs, tools=tool_schemas)
                # Still emit text to the handler for non-streaming providers.
                if self.handler and response.content:
                    self.handler.on_text(response.content)

            total_cost += calculate_cost(provider.model_name, response.usage)
            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if self.handler:
                self.handler.on_step_end(steps)

            if not response.has_tool_calls:
                if self.handler:
                    self.handler.on_done()
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            for tc in response.tool_calls:
                total_tool_calls += 1

                if self.handler:
                    self.handler.on_tool_start(tc.name, tc.id)

                tool = tool_map.get(tc.name)
                if tool is None:
                    error_msg = f"Error: unknown tool {tc.name}"
                    context.add(Message.tool(tc.id, error_msg))
                    if self.handler:
                        self.handler.on_tool_end(tc.id, error_msg)
                    continue

                result = tool.execute(tc.arguments, env)
                if result.success:
                    content = result.output
                else:
                    content = f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

                if self.handler:
                    self.handler.on_tool_end(tc.id, content)

        if self.handler:
            self.handler.on_done()
        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )

    async def async_run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Async version of :meth:`run` using async provider streaming."""
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0

        for _ in range(self.max_steps):
            steps += 1

            if self.handler:
                self.handler.on_step_start(steps)

            msgs = context.to_messages()
            tool_schemas = schemas if schemas else None

            response = await self._async_accumulate_stream(
                provider.async_stream(msgs, tools=tool_schemas), self.handler,
            )

            total_cost += calculate_cost(provider.model_name, response.usage)
            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if self.handler:
                self.handler.on_step_end(steps)

            if not response.has_tool_calls:
                if self.handler:
                    self.handler.on_done()
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            for tc in response.tool_calls:
                total_tool_calls += 1
                if self.handler:
                    self.handler.on_tool_start(tc.name, tc.id)
                tool = tool_map.get(tc.name)
                if tool is None:
                    error_msg = f"Error: unknown tool {tc.name}"
                    context.add(Message.tool(tc.id, error_msg))
                    if self.handler:
                        self.handler.on_tool_end(tc.id, error_msg)
                    continue
                result = tool.execute(tc.arguments, env)
                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))
                if self.handler:
                    self.handler.on_tool_end(tc.id, content)

        if self.handler:
            self.handler.on_done()
        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _accumulate_stream(
        events: Iterator[StreamEvent],
        handler: StreamHandler | None,
    ) -> Response:
        """Consume an iterator of stream events into a single Response.

        While iterating, each event is forwarded to *handler* (if given)
        via :meth:`StreamHandler.handle_event`.
        """
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool_call: ToolCall | None = None
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        for event in events:
            if handler:
                handler.handle_event(event)

            if event.type == "text_delta":
                content_parts.append(event.content)
            elif event.type == "tool_call_start":
                current_tool_call = event.tool_call
            elif event.type == "tool_call_delta":
                pass
            elif event.type == "tool_call_complete":
                # Fully-parsed tool call from the provider.
                if event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                current_tool_call = None
            elif event.type == "done":
                # Flush any tool call that wasn't explicitly completed
                # (e.g. from the default Provider.stream() wrapper).
                if current_tool_call is not None:
                    tool_calls.append(current_tool_call)
                    current_tool_call = None
                if event.tool_call and event.tool_call not in tool_calls:
                    tool_calls.append(event.tool_call)
                if event.usage:
                    usage = event.usage

        # Safety: flush if the stream ended without done/complete
        if current_tool_call is not None:
            tool_calls.append(current_tool_call)

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    @staticmethod
    async def _async_accumulate_stream(
        events: AsyncIterator[StreamEvent],
        handler: StreamHandler | None,
    ) -> Response:
        """Async version of :meth:`_accumulate_stream`."""
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool_call: ToolCall | None = None
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        async for event in events:
            if handler:
                handler.handle_event(event)

            if event.type == "text_delta":
                content_parts.append(event.content)
            elif event.type == "tool_call_start":
                current_tool_call = event.tool_call
            elif event.type == "tool_call_delta":
                pass
            elif event.type == "tool_call_complete":
                if event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                current_tool_call = None
            elif event.type == "done":
                if current_tool_call is not None:
                    tool_calls.append(current_tool_call)
                    current_tool_call = None
                if event.tool_call and event.tool_call not in tool_calls:
                    tool_calls.append(event.tool_call)
                if event.usage:
                    usage = event.usage

        if current_tool_call is not None:
            tool_calls.append(current_tool_call)

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )
