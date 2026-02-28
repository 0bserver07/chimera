from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import drain_steps
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import (
    LoopBreak,
    execute_tool_calls_incremental,
)
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.providers.cost import calculate_cost
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig


class PlanAndExecute:
    """Two-phase loop: first ask the LLM for a plan, then execute it step by step.

    Phase 1: Generate a plan (no tool calls expected).
              After the plan is generated, a follow-up prompt asks the model
              to begin executing the plan.
    Phase 2: Execute the plan using tools (standard ReAct-style).
    """

    EXECUTE_PROMPT = "Now execute the plan you just created, step by step."

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
        """Yield one :class:`StepResult` per LLM turn."""
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        plan_generated = False
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(
                context.to_messages(),
                tools=schemas if schemas else None,
            )
            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                if not plan_generated and tools:
                    plan_generated = True
                    context.add(Message.user(self.EXECUTE_PROMPT))
                    if event_bus:
                        from chimera.events.types import StepEvent
                        event_bus.publish(StepEvent(step_number=steps, content=response.content))
                    yield StepResult(
                        message=Message.assistant(response.content),
                        tool_calls=[],
                        done=False,
                        step=steps,
                        cost=step_cost,
                    )
                    continue
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

            plan_generated = True

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
                        extra = execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        return AgentResult(
                            output=response.content, steps=steps,
                            tool_calls_total=total_tool_calls, cost=total_cost,
                            success=False, error="Loop detected",
                        )
                    total_tool_calls += extra.executed
                else:
                    context.add(Message.tool(pa.tool_call.id, pa.denial_message))
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
        return drain_steps(self.iter_steps(provider, tools, context, env))
