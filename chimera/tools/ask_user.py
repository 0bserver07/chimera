# chimera/tools/ask_user.py
from __future__ import annotations

from typing import Any, Callable

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class AskUserTool(BaseTool):
    """Pause the agent loop and ask the user a question.

    Accepts an optional *callback* for programmatic use.  When no callback
    is provided the tool falls back to reading from ``stdin``.
    """

    name = "ask_user"
    description = (
        "Ask the user a question when you need clarification or a decision."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of choices for the user to pick from",
            },
        },
        "required": ["question"],
    }

    def __init__(
        self,
        callback: Callable[[str, list[str] | None], str] | None = None,
    ) -> None:
        self._callback = callback

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        question = args["question"]
        choices: list[str] | None = args.get("choices")

        if self._callback:
            answer = self._callback(question, choices)
        else:
            # Default: stdin
            if choices:
                prompt = (
                    f"{question}\n"
                    + "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(choices))
                    + "\n> "
                )
            else:
                prompt = f"{question}\n> "
            answer = input(prompt)

        return ToolResult(output=answer)
