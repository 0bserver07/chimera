"""CompiledFunctionTool: expose a :class:`CompiledFunction` as an agent tool."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.function_synthesis.runtime import CompiledFunction


class CompiledFunctionTool(BaseTool):
    """Wraps a loaded compiled function so agents can call it as a tool.

    Uses the function's name/description by default.  Agents call the tool
    with a single ``user_input`` string; the compiled function's output is
    returned verbatim.
    """

    is_concurrency_safe = True
    is_read_only = True

    def __init__(
        self,
        function: CompiledFunction,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        self._function = function
        self.name = name or function.name
        self.description = (
            description or f"Compiled neural function: {function.spec.description}"
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "user_input": {
                    "type": "string",
                    "description": "Input passed to the compiled function.",
                },
            },
            "required": ["user_input"],
        }

    def execute(  # type: ignore[override]
        self,
        args: dict[str, Any] | None = None,
        env: Environment | None = None,
        *,
        user_input: str | None = None,
        **_kwargs: Any,
    ) -> Any:
        """Invoke the compiled function.

        Supports two calling conventions:

        - Framework style: ``execute({"user_input": "..."}, env)`` returning
          a :class:`ToolResult`.
        - Direct style: ``execute(user_input="...")`` returning the raw
          string output (used when the tool is called as a plain callable
          outside the ReAct loop).
        """
        if user_input is not None and args is None:
            # Direct-call style: return the raw string.
            return self._function(user_input)

        if args is None:
            args = {}
        if user_input is None:
            user_input = args.get("user_input") or ""
        assert user_input is not None
        output = self._function(user_input)
        return ToolResult(output=output)
