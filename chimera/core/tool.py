"""Tool abstraction for giving agents the ability to take actions.

Provides :class:`BaseTool`, the abstract base class for all tools, and the
:func:`tool` decorator for quickly turning a plain function into a tool
instance.

Example:
    ```python
    from chimera.core.tool import tool

    @tool(
        name="greet",
        description="Say hello.",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}},
    )
    def greet(args, env):
        return {"output": f"Hello, {args['name']}!"}
    ```
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from chimera.env.base import Environment
from chimera.types import ToolResult


class BaseTool(ABC):
    """Base class for all tools an agent can invoke.

    Subclass this and implement :meth:`execute` to create a custom tool.
    Set the class-level attributes *name*, *description*, and *parameters*
    (a JSON Schema dict) to describe the tool to the model.

    Attributes:
        name: Unique tool name exposed to the model.
        description: Human-readable description used in the tool schema.
        parameters: JSON Schema defining accepted arguments.
        requires_approval: If ``True``, the framework will request user
            confirmation before executing this tool.
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    requires_approval: bool = False

    @abstractmethod
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the tool with the given arguments.

        Args:
            args: Dictionary of arguments conforming to :attr:`parameters`.
            env: The active execution environment, or ``None`` for
                environment-independent tools.

        Returns:
            A :class:`~chimera.types.ToolResult` containing the tool's
            output (or error information).
        """

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Convert to Anthropic tool use schema."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class _FunctionTool(BaseTool):
    """Tool created from a function via decorator."""

    def __init__(
        self,
        func: Callable[..., ToolResult],
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self._func = func
        self.name = name
        self.description = description
        self.parameters = parameters

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return self._func(args, env)


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[Callable[..., ToolResult]], _FunctionTool]:
    """Decorator to create a :class:`BaseTool` from a plain function.

    The decorated function must accept ``(args: dict, env: Environment | None)``
    and return a :class:`~chimera.types.ToolResult`.

    Args:
        name: Unique tool name exposed to the model.
        description: Human-readable description for the tool schema.
        parameters: JSON Schema dict describing the expected arguments.

    Returns:
        A decorator that wraps the target function in a
        :class:`_FunctionTool` instance.

    Example:
        ```python
        @tool("add", "Add two numbers.", {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
        })
        def add(args, env):
            return {"output": args["a"] + args["b"]}
        ```
    """
    def decorator(func: Callable[..., ToolResult]) -> _FunctionTool:
        return _FunctionTool(func, name, description, parameters)
    return decorator
