from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.interception import intercepted_complete
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

    def __init__(
        self,
        max_steps: int = 50,
        n_candidates: int = 3,
        config: LoopConfig | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.n_candidates = n_candidates
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
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None
        interceptors = self.config.interceptors if self.config else None

        for _ in range(self.max_steps):
            steps += 1
            step_cost = 0.0

            # Generate N candidate responses
            candidates: list[str] = []
            candidate_tool_calls = []
            for _ in range(self.n_candidates):
                # Interception seams: context rewrite + provider request run
                # per candidate call (each sends the conversation), via the
                # shared strategy-loop enforcement site. The envelope carries
                # temperature=0.7 so envelope interceptors see — and may
                # replace — what is actually sent.
                response, blocked = intercepted_complete(
                    interceptors, provider, context.to_messages(),
                    schemas if schemas else None, event_bus=event_bus,
                    temperature=0.7,
                )
                if blocked is not None:
                    blocked_msg = f"Blocked by interceptor: {blocked}"
                    yield StepResult(
                        message=Message.assistant(blocked_msg),
                        tool_calls=[],
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    return AgentResult(
                        output=blocked_msg,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error=blocked_msg,
                    )
                c = calculate_cost(provider.model_name, response.usage)
                step_cost += c
                total_cost += c
                candidates.append(response.content)
                candidate_tool_calls.append(response.tool_calls)

            # If any candidate has tool calls, use the first one
            tool_call_idx = None
            for i, tcs in enumerate(candidate_tool_calls):
                if tcs:
                    tool_call_idx = i
                    break

            if tool_call_idx is not None:
                chosen_content = candidates[tool_call_idx]
                chosen_tool_calls = candidate_tool_calls[tool_call_idx]
                context.add(Message.assistant(chosen_content, tool_calls=chosen_tool_calls))

                try:
                    exec_result = execute_tool_calls_incremental(
                        chosen_tool_calls, tool_map, context, env, self.config,
                    )
                except LoopBreak:
                    yield StepResult(
                        message=Message.assistant(chosen_content),
                        tool_calls=chosen_tool_calls,
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    return AgentResult(
                        output=chosen_content,
                        steps=steps,
                        tool_calls_total=total_tool_calls + len(chosen_tool_calls),
                        cost=total_cost,
                        success=False,
                        error="Loop detected",
                    )

                total_tool_calls += exec_result.executed

                if exec_result.pending is not None:
                    step = StepResult(
                        message=Message.assistant(chosen_content),
                        tool_calls=chosen_tool_calls,
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
                                output=chosen_content, steps=steps,
                                tool_calls_total=total_tool_calls, cost=total_cost,
                                success=False, error="Loop detected",
                            )
                        total_tool_calls += extra.executed
                    else:
                        context.add(Message.tool(pa.tool_call.id, pa.denial_message))
                else:
                    yield StepResult(
                        message=Message.assistant(chosen_content),
                        tool_calls=chosen_tool_calls,
                        tool_results=exec_result.results,
                        done=False,
                        step=steps,
                        cost=step_cost,
                    )
                continue

            # No tool calls: evaluate candidates and pick the best
            if len(set(candidates)) == 1:
                best_content = candidates[0]
            else:
                candidate_text = "\n".join(
                    f"Candidate {i + 1}: {c}" for i, c in enumerate(candidates)
                )
                eval_prompt = self.EVALUATE_PROMPT.format(n=len(candidates))
                eval_context = Context(system="You are an evaluator.")
                eval_context.add(Message.user(f"{candidate_text}\n\n{eval_prompt}"))
                # Honest scope (documented in docs/guides/interception.md,
                # pinned inert by test): this internal candidate-evaluation
                # call is NOT run through the interception seams. It sends a
                # synthetic evaluator prompt, not the conversation — context
                # interceptors are written against the conversation (e.g. a
                # watcher counting user messages) and would misread it.
                eval_response = provider.complete(eval_context.to_messages())
                eval_cost = calculate_cost(provider.model_name, eval_response.usage)
                step_cost += eval_cost
                total_cost += eval_cost

                try:
                    choice = int(eval_response.content.strip()) - 1
                    if 0 <= choice < len(candidates):
                        best_content = candidates[choice]
                    else:
                        best_content = candidates[0]
                except (ValueError, IndexError):
                    best_content = candidates[0]

            context.add(Message.assistant(best_content))
            yield StepResult(
                message=Message.assistant(best_content),
                tool_calls=[],
                done=True,
                step=steps,
                cost=step_cost,
            )
            return AgentResult(
                output=best_content,
                steps=steps,
                tool_calls_total=total_tool_calls,
                cost=total_cost,
                success=True,
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
