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
    """Result returned by a hook execution.

    Mirrors the harness hook stdout JSON contract used for ecosystem compat.

    Attributes:
        continue_execution: If False, halt the tool dispatch chain.
        suppress_output: If True, suppress hook stdout/stderr from logs.
        stop_reason: Reason for halting (informational).
        decision: Legacy decision field ("allow"/"block").
        reason: Legacy reason text.
        system_message: Message to surface in the agent transcript.
        additional_context: Extra text to append to the tool result.
        updated_input: Mutated tool input (shallow-merged over original).
        retry: Whether the loop should retry the action.
        permission_decision: One of "allow" | "deny" | "ask" | "defer".
            Maps from `hookSpecificOutput.permissionDecision`.
        permission_decision_reason: Human-readable rationale for the
            permission decision. Maps from
            `hookSpecificOutput.permissionDecisionReason`.
    """

    continue_execution: bool = True
    suppress_output: bool = False
    stop_reason: str | None = None
    decision: str | None = None
    reason: str | None = None
    system_message: str | None = None
    additional_context: str | None = None
    updated_input: dict[str, Any] | None = None
    retry: bool = False
    permission_decision: str | None = None
    permission_decision_reason: str | None = None


# ---------------------------------------------------------------------------
# Hook descriptors
# ---------------------------------------------------------------------------


@dataclass
class CommandHook:
    """A hook that executes a shell command.

    Attributes:
        command: Shell command line to execute.
        timeout: Per-hook timeout in seconds (0 = unlimited).
        cwd: Working directory for the subprocess. ``None`` defers to
            the executor's default (typically project root).
        extra_env: Additional environment variables merged on top of
            ``os.environ`` (and the auto-injected ``HOOK_*`` vars).
    """

    command: str
    type: str = field(default="command", init=False)
    timeout: int = 60
    cwd: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class PromptHook:
    """A hook that sends a prompt to an LLM for evaluation."""

    prompt: str
    type: str = field(default="prompt", init=False)
    timeout: int = 30


@dataclass
class FunctionHook:
    """A hook that calls a Python function.

    Attributes:
        callback: The callable to invoke. Its calling convention is
            selected by ``receives_input``.
        type: Type discriminator; always ``"function"``.
        id: Optional stable identifier, used to remove the hook later
            (e.g. the subscription id returned by :meth:`HookEmitter.on`).
        timeout: Per-hook timeout in seconds (``0`` clamps to a tiny value).
        error_message: Human-readable label surfaced in timeout diagnostics.
        receives_input: Selects the callback signature. When ``False``
            (the default, preserving legacy behavior) the callback is
            invoked as ``callback(messages, abort_signal)``. When ``True``
            (as set by :meth:`HookEmitter.on`) it is invoked with the single
            :class:`HookInput` argument so ergonomic subscribers can read
            the full event payload (event, tool name, tool input, ...).
    """

    callback: Callable[..., Any]
    type: str = field(default="function", init=False)
    id: str | None = None
    timeout: int = 5
    error_message: str = "Hook check failed"
    receives_input: bool = False


@dataclass
class HookMatcher:
    """Associates a matcher pattern with a list of hooks.

    The ``events`` field constrains which lifecycle events fire this matcher.
    ``None`` matches every event (legacy behavior). A list of event names
    (e.g. ``["PreToolUse"]``) restricts the matcher to those events.
    Names use the upstream string form (``HookEvent.value``).
    """

    hooks: list[CommandHook | PromptHook | FunctionHook]
    matcher: str | None = None
    source: str = "user"
    plugin_name: str | None = None
    events: list[str] | None = None


# Type alias for the three hook kinds.
Hook = CommandHook | PromptHook | FunctionHook
