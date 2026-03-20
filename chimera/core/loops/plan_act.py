"""Two-phase Plan/Act loop: read-only planning, then full execution.

Inspired by `Cline's Plan/Act mode <https://github.com/cline/cline>`_.
In Cline, the user manually toggles between Plan mode (read-only exploration
and conversation) and Act mode (full tool access).  This loop automates the
transition: the agent first explores with read-only tools and produces a
plan, then executes the plan with full tool access.

Unlike :class:`~chimera.core.loops.plan_execute.PlanAndExecute` which runs
in a single context and simply prompts "now execute", this loop creates
**separate contexts** for each phase.  The plan phase is strictly limited
to read-only tools, preventing accidental mutations during exploration.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig

# Tools that are safe for read-only planning.
# These tools only observe the codebase and never modify it.
READ_ONLY_TOOLS = {
    "read_file",
    "search",
    "list_files",
    "repo_map",
    "think",
    "read_image",
}


class PlanActLoop:
    """Two-phase loop: plan (read-only), then act (full tools).

    Phase 1 (Plan): Agent explores the codebase with read-only tools.
    It outputs a step-by-step plan of what to do.

    Phase 2 (Act): Agent executes the plan with full tool access.
    The plan output is prepended to the act phase context.

    Args:
        plan_steps: Maximum number of ReAct steps for the plan phase.
        act_steps: Maximum number of ReAct steps for the act phase.
        read_only_tools: Set of tool names allowed during the plan phase.
            Defaults to :data:`READ_ONLY_TOOLS`.
        config: Optional :class:`~chimera.core.loop_config.LoopConfig` for
            permissions, events, detection, etc.

    Attributes:
        plan_output: The text output from the plan phase, available after
            :meth:`run` completes.

    Example:
        ```python
        from chimera.core.loops.plan_act import PlanActLoop

        loop = PlanActLoop(plan_steps=10, act_steps=25)
        result = loop.run(provider, tools, context, env)
        print(loop.plan_output)  # The plan generated in phase 1
        ```
    """

    def __init__(
        self,
        plan_steps: int = 10,
        act_steps: int = 25,
        read_only_tools: set[str] | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self._plan_steps = plan_steps
        self._act_steps = act_steps
        self._read_only = read_only_tools or READ_ONLY_TOOLS
        self.config = config
        self.plan_output: str = ""

    def _build_plan_context(self, context: Context) -> Context:
        """Build the plan-phase context from the original context.

        Copies the system prompt and modifies the first user message to
        instruct the agent to explore and plan without making changes.

        Args:
            context: The original context with the user's task.

        Returns:
            A new :class:`Context` for the plan phase.
        """
        plan_context = Context(system=context.system)
        if context.messages:
            original_task = context.messages[0].content
            plan_context.add(Message.user(
                f"{original_task}\n\n"
                f"PLANNING PHASE: Explore the codebase using read-only tools. "
                f"Do NOT make any changes yet. Output a step-by-step plan "
                f"for how you will accomplish this task."
            ))
        return plan_context

    def _build_act_context(self, context: Context) -> Context:
        """Build the act-phase context from the original context and plan.

        Copies the system prompt and creates a new user message containing
        both the original task and the plan output from phase 1.

        Args:
            context: The original context with the user's task.

        Returns:
            A new :class:`Context` for the act phase.
        """
        act_context = Context(system=context.system)
        if context.messages:
            original_task = context.messages[0].content
            act_context.add(Message.user(
                f"{original_task}\n\n"
                f"EXECUTION PHASE: Here is your plan from the planning phase:\n\n"
                f"{self.plan_output}\n\n"
                f"Now execute this plan. You have full tool access."
            ))
        return act_context

    def _filter_read_only(self, tools: list[BaseTool]) -> list[BaseTool]:
        """Filter tools to only include read-only ones.

        Args:
            tools: Full tool list.

        Returns:
            Subset of tools whose names are in :attr:`_read_only`.
        """
        return [t for t in tools if t.name in self._read_only]

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run plan phase, then act phase.

        Args:
            provider: LLM provider for completions.
            tools: Full list of tools (filtered for plan phase).
            context: Conversation context with the user's task.
            env: Execution environment.

        Returns:
            Combined :class:`~chimera.types.AgentResult` spanning both phases.
        """
        # Phase 1: Plan (read-only tools only)
        plan_tools = self._filter_read_only(tools)
        plan_context = self._build_plan_context(context)

        plan_loop = ReAct(max_steps=self._plan_steps, config=self.config)
        plan_result = plan_loop.run(provider, plan_tools, plan_context, env)
        self.plan_output = plan_result.output

        # Phase 2: Act (full tools, plan as context)
        act_context = self._build_act_context(context)

        act_loop = ReAct(max_steps=self._act_steps, config=self.config)
        act_result = act_loop.run(provider, tools, act_context, env)

        # Combined result
        return AgentResult(
            output=act_result.output,
            steps=plan_result.steps + act_result.steps,
            tool_calls_total=(
                plan_result.tool_calls_total + act_result.tool_calls_total
            ),
            cost=plan_result.cost + act_result.cost,
            success=act_result.success,
            error=act_result.error,
        )

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Yield steps from both plan and act phases.

        The plan phase runs first (via :func:`drain_steps`) to produce
        the plan text, then act-phase steps are yielded one at a time.

        Args:
            provider: LLM provider for completions.
            tools: Full list of tools.
            context: Conversation context with the user's task.
            env: Execution environment.

        Returns:
            Combined :class:`~chimera.types.AgentResult` (via generator
            return value).
        """
        # Run plan phase to completion first
        plan_tools = self._filter_read_only(tools)
        plan_context = self._build_plan_context(context)
        plan_loop = ReAct(max_steps=self._plan_steps, config=self.config)
        plan_result = plan_loop.run(provider, plan_tools, plan_context, env)
        self.plan_output = plan_result.output

        # Then yield act-phase steps
        act_context = self._build_act_context(context)
        act_loop = ReAct(max_steps=self._act_steps, config=self.config)
        act_result: AgentResult = yield from act_loop.iter_steps(
            provider, tools, act_context, env,
        )

        # Return combined result
        return AgentResult(
            output=act_result.output,
            steps=plan_result.steps + act_result.steps,
            tool_calls_total=(
                plan_result.tool_calls_total + act_result.tool_calls_total
            ),
            cost=plan_result.cost + act_result.cost,
            success=act_result.success,
            error=act_result.error,
        )
