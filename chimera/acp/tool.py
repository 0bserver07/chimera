"""ExternalAgentTool — wraps an ACP agent as a Chimera tool."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, TYPE_CHECKING

from chimera.acp.client import ACPClient
from chimera.acp.types import ACPSessionConfig
from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment


class ExternalAgentTool(BaseTool):
    """Wraps an external ACP agent as a Chimera tool.

    Args:
        config: ACP session configuration.
        agent_name: Name for this tool (exposed to the model).
    """

    description = "Delegate a task to an external AI agent"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task to delegate to the external agent",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        config: ACPSessionConfig,
        agent_name: str = "external_agent",
    ) -> None:
        self.name = agent_name
        self.config = config
        self._client: ACPClient | None = None

    def setup(self) -> None:
        """Start the ACP client subprocess."""
        self._client = ACPClient(self.config)
        self._client.start()

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute by sending the task to the external agent.

        Args:
            args: Must contain a ``"task"`` key with the task description.
            env: Unused — the external agent manages its own environment.

        Returns:
            A :class:`ToolResult` with the agent's response text and metadata.
        """
        if not self._client:
            self.setup()
        assert self._client is not None

        task = args.get("task", "")
        response = self._client.send_message(task)
        return ToolResult(
            output=response.text,
            metadata={
                "thoughts": response.thoughts,
                "tool_calls": [asdict(tc) for tc in response.tool_calls],
                "cost": response.cost,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
        )

    def cleanup(self) -> None:
        """Stop the ACP client subprocess."""
        if self._client:
            self._client.stop()
            self._client = None
