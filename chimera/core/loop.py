from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Iterator, TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import (
    LoopBreak,
    async_execute_tool_calls_incremental,
    execute_tool_calls,
    execute_tool_calls_incremental,
)
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.providers.cost import calculate_cost
from chimera.providers.cost_tracker import CostLimitExceeded
from chimera.types import AgentResult, Message, StepResult, ToolCall, ToolResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig
    from chimera.streaming.base import StreamHandler


class ReAct:
    """ReAct loop: Reason -> Act (tool call) -> Observe (tool result) -> repeat.

    Iterates until the provider returns a response with no tool calls,
    or max_steps is reached.
    """

    def __init__(
        self,
        max_steps: int = 50,
        config: LoopConfig | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.config = config

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Yield one :class:`StepResult` per LLM turn.

        When a permission check returns ASK, the yielded step carries a
        :attr:`~StepResult.pending_approval`.  The consumer must call
        :meth:`~PendingApproval.approve` or :meth:`~PendingApproval.deny`
        before iterating further.

        The :class:`AgentResult` is the generator return value (accessible
        via ``StopIteration.value``).
        """
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None
        handler: StreamHandler | None = self.config.handler if self.config else None

        for _ in range(self.max_steps):
            steps += 1

            if handler:
                handler.on_step_start(steps)
                events = provider.stream(
                    context.to_messages(), tools=schemas if schemas else None,
                )
                response = self._accumulate_stream(events, handler)
            else:
                response = provider.complete(
                    context.to_messages(), tools=schemas if schemas else None,
                )

            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost

            # Cost tracking
            if self.config and self.config.cost_tracker:
                try:
                    self.config.cost_tracker.record(step_cost, model=provider.model_name)
                except CostLimitExceeded:
                    if handler:
                        handler.on_done()
                    yield StepResult(
                        message=Message.assistant(response.content),
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    return AgentResult(
                        output=response.content,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Cost limit exceeded",
                    )

            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=[],
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            # Execute tool calls incrementally (pauses on ASK)
            try:
                exec_result = execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )

            total_tool_calls += exec_result.executed

            # Emit tool events to handler
            if handler:
                for tc_idx, tc in enumerate(response.tool_calls):
                    handler.on_tool_start(tc.name, tc.id)
                    if tc_idx < len(exec_result.results):
                        tr = exec_result.results[tc_idx]
                        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
                        handler.on_tool_end(tc.id, content[:500])

            if exec_result.pending is not None:
                # Pause: yield step with pending approval
                step = StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                    pending_approval=exec_result.pending,
                )
                yield step

                # Consumer must have called approve() or deny() by now
                pa = exec_result.pending
                if pa.approved:
                    # Execute the approved call + remaining
                    remaining = [pa.tool_call] + exec_result.remaining
                    try:
                        extra = execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        return AgentResult(
                            output=response.content,
                            steps=steps,
                            tool_calls_total=total_tool_calls,
                            cost=total_cost,
                            success=False,
                            error="Loop detected",
                        )
                    total_tool_calls += extra.executed
                else:
                    # Denied — add denial message to context
                    context.add(
                        Message.tool(pa.tool_call.id, pa.denial_message),
                    )
            else:
                # All tool calls executed normally
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                )

            if handler:
                handler.on_step_end(steps)

        # Max steps
        if handler:
            handler.on_done()
        yield StepResult(
            message=Message.assistant("Max steps reached"),
            tool_calls=[],
            done=True,
            step=steps,
            cost=0.0,
        )
        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )

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

        # Safety: flush if the stream ended without done/complete
        if current_tool_call is not None:
            tool_calls.append(current_tool_call)

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion, auto-denying ASK permissions."""
        return drain_steps(self.iter_steps(provider, tools, context, env))


    async def async_iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AsyncGenerator[StepResult, None]:
        """Async version of :meth:`iter_steps`.

        Uses ``async_complete`` for LLM calls and
        ``async_execute_tool_calls_incremental`` for concurrent tool
        execution.

        The :class:`AgentResult` is stored on ``self._async_result``
        because async generators cannot use ``return <value>``.
        Use :func:`async_drain_steps` to consume and retrieve it.
        """
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None

        for _ in range(self.max_steps):
            steps += 1

            response = await provider.async_complete(
                context.to_messages(), tools=schemas if schemas else None,
            )

            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost

            # Cost tracking
            if self.config and self.config.cost_tracker:
                try:
                    self.config.cost_tracker.record(step_cost, model=provider.model_name)
                except CostLimitExceeded:
                    yield StepResult(
                        message=Message.assistant(response.content),
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    self._async_result = AgentResult(
                        output=response.content,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Cost limit exceeded",
                    )
                    return

            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=[],
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )
                return

            # Execute tool calls concurrently
            try:
                exec_result = await async_execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )
                return

            total_tool_calls += exec_result.executed

            if exec_result.pending is not None:
                step = StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                    pending_approval=exec_result.pending,
                )
                yield step

                pa = exec_result.pending
                if pa.approved:
                    remaining = [pa.tool_call] + exec_result.remaining
                    try:
                        extra = await async_execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        self._async_result = AgentResult(
                            output=response.content,
                            steps=steps,
                            tool_calls_total=total_tool_calls,
                            cost=total_cost,
                            success=False,
                            error="Loop detected",
                        )
                        return
                    total_tool_calls += extra.executed
                else:
                    context.add(
                        Message.tool(pa.tool_call.id, pa.denial_message),
                    )
            else:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                )

        # Max steps
        yield StepResult(
            message=Message.assistant("Max steps reached"),
            tool_calls=[],
            done=True,
            step=steps,
            cost=0.0,
        )
        self._async_result = AgentResult(
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
        """Run the loop to completion using async provider calls.

        Uses :meth:`async_iter_steps` internally.  Any pending ASK
        permissions are auto-denied (same as :meth:`run`).
        """
        return await async_drain_steps(
            self.async_iter_steps(provider, tools, context, env)
        )


def drain_steps(
    gen: Generator[StepResult, None, AgentResult],
) -> AgentResult:
    """Consume an ``iter_steps`` generator to completion.

    Any :attr:`~StepResult.pending_approval` is automatically denied.
    Returns the :class:`AgentResult` from the generator.
    """
    result: AgentResult | None = None
    try:
        while True:
            step = next(gen)
            if step.pending_approval:
                step.pending_approval.deny("Auto-denied by drain_steps")
    except StopIteration as e:
        result = e.value
    if result is None:
        # Generator ended without returning; shouldn't happen, but be safe
        result = AgentResult(
            output="", steps=0, tool_calls_total=0, cost=0.0, success=False,
            error="Generator ended unexpectedly",
        )
    return result


async def async_drain_steps(
    gen: AsyncGenerator[StepResult, None],
) -> AgentResult:
    """Consume an ``async_iter_steps`` generator to completion.

    Any :attr:`~StepResult.pending_approval` is automatically denied.
    Retrieves the :class:`AgentResult` from the ``_async_result``
    attribute set by :meth:`ReAct.async_iter_steps`.
    """
    owner: ReAct | None = None
    # Access the underlying ReAct instance from the generator
    if hasattr(gen, "ag_frame") and gen.ag_frame is not None:
        local_vars = gen.ag_frame.f_locals
        owner = local_vars.get("self")

    async for step in gen:
        if step.pending_approval:
            step.pending_approval.deny("Auto-denied by async_drain_steps")

    if owner is not None and hasattr(owner, "_async_result"):
        return owner._async_result

    return AgentResult(
        output="", steps=0, tool_calls_total=0, cost=0.0, success=False,
        error="Generator ended unexpectedly",
    )
