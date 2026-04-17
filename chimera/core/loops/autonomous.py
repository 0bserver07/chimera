"""AutonomousLoop: long-running agent loop with goal decomposition and replanning.

Decomposes a high-level goal into numbered sub-tasks, executes each via an
inner ReAct loop, and replans on failure.  Enforces global limits on total
steps, cost, and wall-clock time.

Example::

    from chimera.core.loops.autonomous import AutonomousLoop

    loop = AutonomousLoop(max_steps_per_task=15, max_total_steps=100)
    result = loop.run(provider, tools, context, env)
"""
from __future__ import annotations

import re
import time
from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.providers.cost import calculate_cost
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig


def _parse_plan(text: str) -> list[str]:
    """Extract numbered steps from an LLM-generated plan.

    Recognises patterns like ``1. Do something`` or ``1) Do something``.
    Falls back to non-empty lines if no numbered pattern is found.

    Args:
        text: Raw plan text from the provider.

    Returns:
        List of step descriptions (strings), in order.
    """
    # Match lines starting with a number followed by . or )
    pattern = re.compile(r"^\s*\d+[.)]\s*(.+)", re.MULTILINE)
    matches = pattern.findall(text)
    if matches:
        return [m.strip() for m in matches]
    # Fallback: non-empty, non-whitespace lines
    return [line.strip() for line in text.splitlines() if line.strip()]


class AutonomousLoop:
    """Goal-driven loop with automatic decomposition and replanning.

    Accepts a high-level goal (the first user message in the context),
    asks the provider to decompose it into numbered steps, then executes
    each step using an inner :class:`~chimera.core.loop.ReAct` loop.
    If a step fails, the loop asks the provider to revise the remaining
    plan and retries.

    Args:
        max_steps_per_task: Maximum ReAct steps for each sub-task.
        max_total_steps: Hard cap on cumulative steps across all sub-tasks.
        max_replans: Maximum number of replan attempts before giving up.
        max_cost: Optional cost cap (dollars). ``None`` means no limit.
        max_time_seconds: Optional wall-clock time cap. ``None`` means no
            limit.
        config: Optional :class:`~chimera.core.loop_config.LoopConfig` for
            permissions, events, streaming, etc.
    """

    PLAN_PROMPT = (
        "Break this goal into numbered steps (e.g. 1. ... 2. ... 3. ...). "
        "Each step should be a concrete, actionable sub-task. "
        "Output ONLY the numbered list, nothing else."
    )

    REPLAN_PROMPT = (
        "Step {step_num} failed because: {error}\n\n"
        "Completed steps so far:\n{completed}\n\n"
        "Remaining steps that need revision:\n{remaining}\n\n"
        "Revise the remaining plan. Output ONLY a new numbered list of "
        "steps to complete the goal."
    )

    def __init__(
        self,
        max_steps_per_task: int = 20,
        max_total_steps: int = 200,
        max_replans: int = 3,
        max_cost: float | None = None,
        max_time_seconds: float | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self.max_steps_per_task = max_steps_per_task
        self.max_total_steps = max_total_steps
        self.max_replans = max_replans
        self.max_cost = max_cost
        self.max_time_seconds = max_time_seconds
        self.config = config

        # Public state after run
        self.plan: list[str] = []
        self.completed_steps: list[str] = []
        self.replan_count: int = 0

    def _emit_event(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        """Publish a StepEvent to the event bus if one is configured."""
        event_bus = self.config.event_bus if self.config else None
        if event_bus:
            from chimera.events.types import StepEvent
            event_bus.publish(StepEvent(**kwargs))

    def _create_checkpoint(self, name: str) -> None:
        """Create a checkpoint if a checkpoint manager is configured."""
        if self.config and self.config.checkpoint_manager:
            self.config.checkpoint_manager.create(name=name)

    def _decompose(
        self,
        provider: Provider,
        goal: str,
    ) -> tuple[list[str], float]:
        """Ask the provider to decompose a goal into steps.

        Args:
            provider: LLM provider.
            goal: The high-level goal text.

        Returns:
            Tuple of (list of step descriptions, cost of the planning call).
        """
        plan_context = Context()
        plan_context.add(Message.user(f"{goal}\n\n{self.PLAN_PROMPT}"))
        response = provider.complete(plan_context.to_messages())
        cost = calculate_cost(provider.model_name, response.usage)
        steps = _parse_plan(response.content)
        return steps, cost

    def _replan(
        self,
        provider: Provider,
        goal: str,
        failed_step_num: int,
        error: str,
        completed: list[str],
        remaining: list[str],
    ) -> tuple[list[str], float]:
        """Ask the provider to revise the remaining plan after a failure.

        Args:
            provider: LLM provider.
            goal: The original high-level goal.
            failed_step_num: 1-based index of the step that failed.
            error: Error description from the failed step.
            completed: Steps that succeeded.
            remaining: Steps not yet attempted (including the failed one).

        Returns:
            Tuple of (revised step list, cost of the replan call).
        """
        completed_text = "\n".join(
            f"{i+1}. {s}" for i, s in enumerate(completed)
        ) or "(none)"
        remaining_text = "\n".join(
            f"{i+1}. {s}" for i, s in enumerate(remaining)
        ) or "(none)"

        prompt = self.REPLAN_PROMPT.format(
            step_num=failed_step_num,
            error=error,
            completed=completed_text,
            remaining=remaining_text,
        )

        plan_context = Context()
        plan_context.add(Message.user(f"Original goal: {goal}\n\n{prompt}"))
        response = provider.complete(plan_context.to_messages())
        cost = calculate_cost(provider.model_name, response.usage)
        steps = _parse_plan(response.content)
        return steps, cost

    def _execute_step(
        self,
        provider: Provider,
        tools: list[BaseTool],
        env: Environment | None,
        goal: str,
        step_desc: str,
        step_num: int,
    ) -> AgentResult:
        """Execute a single sub-task using an inner ReAct loop.

        Args:
            provider: LLM provider.
            tools: Available tools.
            env: Execution environment.
            goal: The original high-level goal (for context).
            step_desc: Description of this specific sub-task.
            step_num: 1-based step number (for the user prompt).

        Returns:
            :class:`~chimera.types.AgentResult` from the inner loop.
        """
        step_context = Context()
        step_context.add(Message.user(
            f"You are working on a larger goal: {goal}\n\n"
            f"Current task (step {step_num}): {step_desc}\n\n"
            f"Complete this step. When done, summarize what you accomplished."
        ))

        inner_loop = ReAct(
            max_steps=self.max_steps_per_task,
            config=self.config,
        )
        return inner_loop.run(provider, tools, step_context, env)

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the autonomous loop to completion.

        Matches the :meth:`ReAct.run` signature so loops are
        interchangeable.

        Args:
            provider: LLM provider for completions.
            tools: Available tools.
            context: Conversation context — the first user message is the
                goal.
            env: Execution environment.

        Returns:
            Combined :class:`~chimera.types.AgentResult` across all
            sub-tasks.
        """
        # Extract goal from context
        goal = ""
        for msg in context.messages:
            if msg.role == "user":
                goal = msg.content
                break
        if not goal:
            return AgentResult(
                output="No goal found in context",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=False,
                error="No goal found in context",
            )

        start_time = time.monotonic()
        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        outputs: list[str] = []
        self.completed_steps = []
        self.replan_count = 0

        # Phase 1: Decompose goal
        self.plan, plan_cost = self._decompose(provider, goal)
        total_cost += plan_cost

        if not self.plan:
            return AgentResult(
                output="Failed to decompose goal into steps",
                steps=0,
                tool_calls_total=0,
                cost=total_cost,
                success=False,
                error="Planning produced no steps",
            )

        self._create_checkpoint("plan_complete")
        self._emit_event(step_number=0, content=f"Plan: {self.plan}")

        # Phase 2: Execute each step
        remaining_steps = list(self.plan)
        step_index = 0

        while remaining_steps:
            step_desc = remaining_steps.pop(0)
            step_index += 1

            # Check time limit
            if self.max_time_seconds is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= self.max_time_seconds:
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else "Time limit reached",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Time limit exceeded",
                    )

            # Check cost limit
            if self.max_cost is not None and total_cost >= self.max_cost:
                return AgentResult(
                    output="\n\n".join(outputs) if outputs else "Cost limit reached",
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error="Cost limit exceeded",
                )

            # Check total steps limit
            if total_steps >= self.max_total_steps:
                return AgentResult(
                    output="\n\n".join(outputs) if outputs else "Max total steps reached",
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error="Max total steps reached",
                )

            # Execute step
            self._emit_event(
                step_number=step_index,
                content=f"Executing step {step_index}: {step_desc}",
            )

            result = self._execute_step(
                provider, tools, env, goal, step_desc, step_index,
            )

            total_steps += result.steps
            total_tool_calls += result.tool_calls_total
            total_cost += result.cost

            if result.success:
                self.completed_steps.append(step_desc)
                outputs.append(f"Step {step_index}: {result.output}")
                self._create_checkpoint(f"step_{step_index}_done")
            else:
                # Step failed — attempt replan
                if self.replan_count >= self.max_replans:
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else f"Step {step_index} failed: {result.error}",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error=f"Max replans ({self.max_replans}) exceeded",
                    )

                self.replan_count += 1
                error_desc = result.error or "Unknown error"
                # Include the failed step in remaining for replanning
                replan_remaining = [step_desc] + remaining_steps

                new_steps, replan_cost = self._replan(
                    provider,
                    goal,
                    step_index,
                    error_desc,
                    self.completed_steps,
                    replan_remaining,
                )
                total_cost += replan_cost

                if not new_steps:
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else "Replanning produced no steps",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Replanning produced no steps",
                    )

                remaining_steps = new_steps
                self._create_checkpoint(f"replan_{self.replan_count}")
                self._emit_event(
                    step_number=step_index,
                    content=f"Replanned after step {step_index} failure. New steps: {new_steps}",
                )

        # All steps completed
        combined_output = "\n\n".join(outputs) if outputs else "All steps completed"
        return AgentResult(
            output=combined_output,
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Yield steps from each sub-task execution.

        The planning phase runs synchronously. Then each sub-task's inner
        ReAct loop yields its steps to the caller.

        Args:
            provider: LLM provider for completions.
            tools: Available tools.
            context: Conversation context — first user message is the goal.
            env: Execution environment.

        Returns:
            Combined :class:`~chimera.types.AgentResult` (via generator
            return value).
        """
        # Extract goal
        goal = ""
        for msg in context.messages:
            if msg.role == "user":
                goal = msg.content
                break
        if not goal:
            yield StepResult(
                message=Message.assistant("No goal found in context"),
                done=True,
                step=0,
                cost=0.0,
            )
            return AgentResult(
                output="No goal found in context",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=False,
                error="No goal found in context",
            )

        start_time = time.monotonic()
        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        outputs: list[str] = []
        self.completed_steps = []
        self.replan_count = 0

        # Decompose
        self.plan, plan_cost = self._decompose(provider, goal)
        total_cost += plan_cost

        if not self.plan:
            yield StepResult(
                message=Message.assistant("Failed to decompose goal"),
                done=True,
                step=0,
                cost=plan_cost,
            )
            return AgentResult(
                output="Failed to decompose goal into steps",
                steps=0,
                tool_calls_total=0,
                cost=total_cost,
                success=False,
                error="Planning produced no steps",
            )

        # Emit initial plan event + checkpoint (parity with run())
        self._create_checkpoint("plan_complete")
        self._emit_event(step_number=0, content=f"Plan: {self.plan}")

        remaining_steps = list(self.plan)
        step_index = 0

        while remaining_steps:
            step_desc = remaining_steps.pop(0)
            step_index += 1

            # Limit checks
            if self.max_time_seconds is not None:
                if time.monotonic() - start_time >= self.max_time_seconds:
                    yield StepResult(
                        message=Message.assistant("Time limit reached"),
                        done=True,
                        step=total_steps,
                        cost=0.0,
                    )
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else "Time limit reached",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Time limit exceeded",
                    )

            if total_steps >= self.max_total_steps:
                yield StepResult(
                    message=Message.assistant("Max total steps reached"),
                    done=True,
                    step=total_steps,
                    cost=0.0,
                )
                return AgentResult(
                    output="\n\n".join(outputs) if outputs else "Max total steps reached",
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error="Max total steps reached",
                )

            # Execute sub-task via inner ReAct, yielding its steps
            self._emit_event(
                step_number=step_index,
                content=f"Executing step {step_index}: {step_desc}",
            )

            step_context = Context()
            step_context.add(Message.user(
                f"You are working on a larger goal: {goal}\n\n"
                f"Current task (step {step_index}): {step_desc}\n\n"
                f"Complete this step. When done, summarize what you accomplished."
            ))

            inner_loop = ReAct(
                max_steps=self.max_steps_per_task,
                config=self.config,
            )

            inner_result: AgentResult = yield from inner_loop.iter_steps(
                provider, tools, step_context, env,
            )

            total_steps += inner_result.steps
            total_tool_calls += inner_result.tool_calls_total
            total_cost += inner_result.cost

            if inner_result.success:
                self.completed_steps.append(step_desc)
                outputs.append(f"Step {step_index}: {inner_result.output}")
                self._create_checkpoint(f"step_{step_index}_done")
            else:
                if self.replan_count >= self.max_replans:
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else f"Step {step_index} failed",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error=f"Max replans ({self.max_replans}) exceeded",
                    )

                self.replan_count += 1
                replan_remaining = [step_desc] + remaining_steps
                new_steps, replan_cost = self._replan(
                    provider, goal, step_index,
                    inner_result.error or "Unknown error",
                    self.completed_steps, replan_remaining,
                )
                total_cost += replan_cost

                if not new_steps:
                    return AgentResult(
                        output="\n\n".join(outputs) if outputs else "Replanning failed",
                        steps=total_steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Replanning produced no steps",
                    )

                remaining_steps = new_steps
                self._create_checkpoint(f"replan_{self.replan_count}")
                self._emit_event(
                    step_number=step_index,
                    content=f"Replanned after step {step_index} failure. New steps: {new_steps}",
                )

        combined_output = "\n\n".join(outputs) if outputs else "All steps completed"
        return AgentResult(
            output=combined_output,
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )
