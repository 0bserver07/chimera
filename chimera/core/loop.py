from __future__ import annotations

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.providers.cost import calculate_cost
from chimera.types import AgentResult, Message


class ReAct:
    """ReAct loop: Reason -> Act (tool call) -> Observe (tool result) -> repeat.

    Iterates until the provider returns a response with no tool calls,
    or max_steps is reached.
    """

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
        total_cost = 0.0

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(context.to_messages(), tools=schemas if schemas else None)
            total_cost += calculate_cost(provider.model_name, response.usage)
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            for tc in response.tool_calls:
                total_tool_calls += 1
                tool = tool_map.get(tc.name)
                if tool is None:
                    context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                    continue
                result = tool.execute(tc.arguments, env)
                if result.success:
                    content = result.output
                else:
                    content = f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )
