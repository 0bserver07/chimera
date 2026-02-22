from __future__ import annotations

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message


class PlanAndExecute:
    """Two-phase loop: first ask the LLM for a plan, then execute it step by step.

    Phase 1: Generate a plan (no tool calls expected).
              After the plan is generated, a follow-up prompt asks the model
              to begin executing the plan.
    Phase 2: Execute the plan using tools (standard ReAct-style).
    """

    EXECUTE_PROMPT = "Now execute the plan you just created, step by step."

    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps

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
        plan_generated = False

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(
                context.to_messages(),
                tools=schemas if schemas else None,
            )
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                # Phase 1: First text-only response is the plan.
                # Inject a follow-up prompt to start execution.
                if not plan_generated and tools:
                    plan_generated = True
                    context.add(Message.user(self.EXECUTE_PROMPT))
                    continue
                # Phase 2: Text-only response after plan means we're done.
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=0.0,
                    success=True,
                )

            plan_generated = True
            # Execute tool calls
            for tc in response.tool_calls:
                total_tool_calls += 1
                tool = tool_map.get(tc.name)
                if tool is None:
                    context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                    continue
                result = tool.execute(tc.arguments, env)
                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=0.0,
            success=False,
            error="Max steps reached",
        )
