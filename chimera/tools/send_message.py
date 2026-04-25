"""SendMessage tool — direct teammate-to-teammate messaging.

Gated by the experimental agent-teams flag. Refuses to run unless
``CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`` is set in the environment.
"""
from __future__ import annotations

from typing import Any

from chimera.cli.agent_teams import ENV_FLAG, Team, TeamMailbox, is_enabled
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

__all__ = ["SendMessageTool"]


class SendMessageTool(BaseTool):
    """Send a direct message to another teammate's mailbox."""

    name = "send_message"
    description = (
        "Send a direct message to another teammate in the active agent team. "
        "Requires CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1."
    )
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient agent_id (or member name in team config).",
            },
            "content": {
                "type": "string",
                "description": "Message body.",
            },
        },
        "required": ["to", "content"],
    }
    is_concurrency_safe = True
    is_read_only = False

    def __init__(self, team_name: str, sender_id: str) -> None:
        self.team_name = team_name
        self.sender_id = sender_id

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if not is_enabled():
            return ToolResult(
                output="",
                error=f"send_message disabled: set {ENV_FLAG}=1 to enable agent teams.",
            )
        to = args.get("to", "").strip()
        content = args.get("content", "")
        if not to:
            return ToolResult(output="", error="'to' is required")
        if not isinstance(content, str):
            return ToolResult(output="", error="'content' must be a string")
        team = Team(self.team_name)
        if not team.exists():
            return ToolResult(output="", error=f"team '{self.team_name}' does not exist")
        mailbox = TeamMailbox(team, to)
        mailbox.send(self.sender_id, content)
        return ToolResult(
            output=f"delivered to {to}",
            metadata={"team": self.team_name, "to": to, "from": self.sender_id},
        )
