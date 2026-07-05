# chimera/tools/submit.py
"""Structured final-answer submission tool.

Ported mechanism (pi ``examples/extensions/structured-output.ts``,
``terminate:true``): instead of scraping the agent's final answer out of
free-text assistant prose, give the agent a tool it *calls* with its answer.
The answer becomes a structured tool argument that a grader can read
deterministically — no fence-matching, no "last assistant message" heuristic.

This is an eval-moat feature: multi-step agents whose terminal message is
commentary (a "done" note, lint output) rather than the artifact previously
scored 0% on answer-graded benchmarks even with the ``FINAL_ANSWER_CONTRACT``
prompt suffix. A tool the agent calls with its answer removes the scrape.

The tool performs no external action; its value is the recorded argument. The
adapter (:mod:`chimera.eval.coding_agent_adapter`) reads
``metadata["final_answer"]`` off the tool call / result and prefers it over
streamed text. See :func:`chimera.eval.coding_agent_adapter.aggregate_events`.
"""
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

#: Canonical tool name. The adapter matches ``tool_use`` events by this name.
SUBMIT_TOOL_NAME = "submit"


class SubmitTool(BaseTool):
    """Submit the final answer for the current task.

    The agent calls this once, when the task is complete, with its final
    answer as the ``answer`` argument. The answer is recorded verbatim in the
    result metadata so a benchmark grader can read it deterministically rather
    than inferring it from the last assistant message.
    """

    name = SUBMIT_TOOL_NAME
    description = (
        "Submit your final answer for the current task. Call this exactly once, "
        "when you are done, with the complete final answer (e.g. the full "
        "solution code, or the requested value) as the 'answer' argument. "
        "Put the answer here verbatim — do not summarize it. Calling this "
        "signals the task is complete."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": (
                    "The complete, final answer to the task, verbatim. For a "
                    "coding task this is the full solution (code), not a "
                    "description of it."
                ),
            },
        },
        "required": ["answer"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        answer = args.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return ToolResult(
                output="",
                error="submit requires a non-empty 'answer' string.",
            )
        return ToolResult(
            output="Final answer recorded. The task is complete; you may stop.",
            metadata={"final_answer": answer},
        )
