"""Protocol message types for Agent Client Protocol (ACP)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ACPSessionConfig:
    """Configuration for an ACP subprocess session.

    Attributes:
        command: Command to spawn the ACP server (e.g. ``["npx", "-y", "claude-code-acp"]``).
        args: Additional command-line arguments.
        env: Extra environment variables for the subprocess.
        working_dir: Working directory for the subprocess.
        notification_drain_delay: Seconds to wait for trailing notifications.
    """

    command: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    notification_drain_delay: float = 0.1


@dataclass
class ACPToolCall:
    """A tool call made by the external agent.

    Attributes:
        tool_call_id: Unique identifier for this tool call.
        title: Human-readable title of the tool call.
        tool_kind: Kind/category of tool.
        status: Current status (``"running"``, ``"completed"``, ``"error"``).
        raw_input: Raw input to the tool.
        raw_output: Raw output from the tool.
        is_error: Whether the tool call resulted in an error.
    """

    tool_call_id: str
    title: str
    tool_kind: str
    status: str
    raw_input: str | None = None
    raw_output: str | None = None
    is_error: bool = False


@dataclass
class ACPResponse:
    """Accumulated response from an ACP session.

    Attributes:
        text: Full text response from the external agent.
        thoughts: List of thought/reasoning chunks.
        tool_calls: Tool calls made during the response.
        cost: Total cost of the response.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens used.
    """

    text: str
    thoughts: list[str]
    tool_calls: list[ACPToolCall]
    cost: float
    input_tokens: int
    output_tokens: int
