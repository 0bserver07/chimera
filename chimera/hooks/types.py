"""Hook type definitions — inputs, outputs, and hook descriptors."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HookInput:
    """Data passed to a hook when it fires."""

    event: Any  # HookEvent enum or str
    session_id: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    tool_error: str | None = None
    user_prompt: str | None = None
    messages: list[Any] | None = None

    def to_json(self) -> str:
        """Serialize this input to a JSON string."""
        # Serialize enum to its string value if needed
        event_val = self.event.value if hasattr(self.event, "value") else self.event
        return json.dumps(
            {
                "event": event_val,
                "session_id": self.session_id,
                "tool_name": self.tool_name,
                "tool_input": self.tool_input,
                "tool_output": self.tool_output,
                "tool_error": self.tool_error,
                "user_prompt": self.user_prompt,
                "messages": self.messages,
            }
        )


@dataclass
class HookOutput:
    """Result returned by a hook execution."""

    continue_execution: bool = True
    suppress_output: bool = False
    stop_reason: str | None = None
    decision: str | None = None
    reason: str | None = None
    system_message: str | None = None
    additional_context: str | None = None
    updated_input: dict[str, Any] | None = None
    retry: bool = False


# ---------------------------------------------------------------------------
# Hook descriptors
# ---------------------------------------------------------------------------


@dataclass
class CommandHook:
    """A hook that executes a shell command."""

    command: str
    type: str = field(default="command", init=False)
    timeout: int = 60


@dataclass
class PromptHook:
    """A hook that sends a prompt to an LLM for evaluation."""

    prompt: str
    type: str = field(default="prompt", init=False)
    timeout: int = 30


@dataclass
class FunctionHook:
    """A hook that calls a Python function."""

    callback: Callable[..., Any]
    type: str = field(default="function", init=False)
    id: str | None = None
    timeout: int = 5
    error_message: str = "Hook check failed"


@dataclass
class HookMatcher:
    """Associates a matcher pattern with a list of hooks."""

    hooks: list[CommandHook | PromptHook | FunctionHook]
    matcher: str | None = None
    source: str = "user"
    plugin_name: str | None = None


# Type alias for the three hook kinds.
Hook = CommandHook | PromptHook | FunctionHook
