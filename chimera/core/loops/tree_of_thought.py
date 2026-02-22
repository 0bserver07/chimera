from __future__ import annotations

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message


class TreeOfThought:
    """Simplified Tree-of-Thought loop.

    At each step, generates N candidate responses, evaluates them by asking the
    model to pick the best one, then continues from the best candidate.
    Falls back to standard ReAct-style execution for tool calls.
    """

    EVALUATE_PROMPT = (
        "You generated {n} candidate responses. Evaluate each one and pick the best. "
        "Respond with ONLY the number of the best candidate (1-indexed)."
    )

    def __init__(self, max_steps: int = 50, n_candidates: int = 3) -> None:
        self.max_steps = max_steps
        self.n_candidates = n_candidates

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

        for _ in range(self.max_steps):
            steps += 1

            # Generate N candidate responses
            candidates: list[str] = []
            candidate_tool_calls = []
            for _ in range(self.n_candidates):
                response = provider.complete(
                    context.to_messages(),
                    tools=schemas if schemas else None,
                    temperature=0.7,
                )
                candidates.append(response.content)
                candidate_tool_calls.append(response.tool_calls)

            # If any candidate has tool calls, use the first one with tool calls
            # (simplified: real ToT would evaluate which tool call path is best)
            tool_call_idx = None
            for i, tcs in enumerate(candidate_tool_calls):
                if tcs:
                    tool_call_idx = i
                    break

            if tool_call_idx is not None:
                # Execute tool calls from the chosen candidate
                chosen_content = candidates[tool_call_idx]
                chosen_tool_calls = candidate_tool_calls[tool_call_idx]
                context.add(Message.assistant(chosen_content, tool_calls=chosen_tool_calls))

                for tc in chosen_tool_calls:
                    total_tool_calls += 1
                    tool = tool_map.get(tc.name)
                    if tool is None:
                        context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                        continue
                    result = tool.execute(tc.arguments, env)
                    content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                    context.add(Message.tool(tc.id, content))
                continue

            # No tool calls in any candidate: evaluate and pick the best
            if len(set(candidates)) == 1:
                # All candidates are the same, just use it
                best_content = candidates[0]
            else:
                # Ask the model to evaluate
                candidate_text = "\n".join(
                    f"Candidate {i + 1}: {c}" for i, c in enumerate(candidates)
                )
                eval_prompt = self.EVALUATE_PROMPT.format(n=len(candidates))
                eval_context = Context(system="You are an evaluator.")
                eval_context.add(Message.user(f"{candidate_text}\n\n{eval_prompt}"))
                eval_response = provider.complete(eval_context.to_messages())

                # Parse the selection
                try:
                    choice = int(eval_response.content.strip()) - 1
                    if 0 <= choice < len(candidates):
                        best_content = candidates[choice]
                    else:
                        best_content = candidates[0]
                except (ValueError, IndexError):
                    best_content = candidates[0]

            context.add(Message.assistant(best_content))
            return AgentResult(
                output=best_content,
                steps=steps,
                tool_calls_total=total_tool_calls,
                cost=0.0,
                success=True,
            )

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=0.0,
            success=False,
            error="Max steps reached",
        )
