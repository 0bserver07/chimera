from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from chimera.env.base import Environment
from chimera.types import ToolResult


class BaseTool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    requires_approval: bool = False

    @abstractmethod
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the tool with given arguments."""

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
    """Decorator to create a tool from a function."""
    def decorator(func: Callable[..., ToolResult]) -> _FunctionTool:
        return _FunctionTool(func, name, description, parameters)
    return decorator
