from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import (
    LoopBreak,
    execute_tool_calls,
    execute_tool_calls_incremental,
)
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.providers.cost import calculate_cost
from chimera.types import AgentResult, Message, StepResult, ToolResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig


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

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(
                context.to_messages(), tools=schemas if schemas else None,
            )
            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost
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

        # Max steps
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

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion, auto-denying ASK permissions."""
        return drain_steps(self.iter_steps(provider, tools, context, env))


    async def async_run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion using async provider calls.

        Tool execution remains synchronous.  Any pending ASK permissions
        are auto-denied (same as :meth:`run`).
        """
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0

        for _ in range(self.max_steps):
            steps += 1
            response = await provider.async_complete(
                context.to_messages(), tools=schemas if schemas else None,
            )
            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost
            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            try:
                exec_result = execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )

            total_tool_calls += exec_result.executed

            if exec_result.pending is not None:
                # Auto-deny in async_run (same as run/drain_steps)
                exec_result.pending.deny("Auto-denied by async_run")
                context.add(
                    Message.tool(
                        exec_result.pending.tool_call.id,
                        exec_result.pending.denial_message,
                    ),
                )

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
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
