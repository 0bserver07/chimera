# chimera/tools/dmail.py
"""D-Mail tool: context rewind to checkpoint with a summary message.

Inspired by Steins;Gate-themed context management (as seen in Kimi CLI).
The agent can create checkpoints at any point during a conversation, then
later "send a D-Mail" to rewind the context to that checkpoint with a
condensed summary of what was learned — allowing the agent to continue
from that earlier point without repeating work.
"""
from __future__ import annotations

from typing import Any

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import Message, ToolResult


class DMailTool(BaseTool):
    """Send a message to your past self at a checkpoint, rewinding context.

    The tool manages its own checkpoint-to-message-index mapping on top of
    a :class:`~chimera.core.context.Context` instance.  Checkpoints are
    lightweight markers (just a message index), not filesystem snapshots.
    """

    name = "dmail"
    description = (
        "Send a message to your past self at a checkpoint, rewinding the conversation "
        "context to that point. Use this when context is cluttered with irrelevant "
        "information. Your message should summarize what you learned so your past self "
        "can continue without repeating work."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "checkpoint_id": {
                "type": "integer",
                "description": "The checkpoint number to rewind to (shown in context as CHECKPOINT N)",
            },
            "message": {
                "type": "string",
                "description": "Message to your past self with everything useful you learned",
            },
        },
        "required": ["checkpoint_id", "message"],
    }

    def __init__(self, context: Context) -> None:
        self._context = context
        self._checkpoints: dict[int, int] = {}  # checkpoint_id -> message_index
        self._next_id: int = 0

    def create_checkpoint(self) -> int:
        """Record a checkpoint at the current context position.

        Returns:
            The checkpoint ID (starting from 0, incrementing).
        """
        cp_id = self._next_id
        self._checkpoints[cp_id] = len(self._context.messages)
        self._next_id += 1
        return cp_id

    @property
    def checkpoint_count(self) -> int:
        """Return the number of checkpoints created so far."""
        return self._next_id

    def execute(self, args: dict[str, Any], env: Environment | None = None) -> ToolResult:
        """Send a D-Mail, rewinding context to a checkpoint.

        Args:
            args: Must contain ``checkpoint_id`` (int) and ``message`` (str).
            env: Unused — operates on the in-memory Context.

        Returns:
            ToolResult confirming the rewind, or an error if the checkpoint
            does not exist.
        """
        cp_id = args["checkpoint_id"]
        message = args["message"]

        if cp_id not in self._checkpoints:
            return ToolResult(
                output="",
                error=f"No checkpoint with ID {cp_id}. Available: {sorted(self._checkpoints.keys())}",
            )

        msg_index = self._checkpoints[cp_id]

        # Truncate context to checkpoint
        self._context.messages = self._context.messages[:msg_index]

        # Remove checkpoints after this one
        self._checkpoints = {k: v for k, v in self._checkpoints.items() if k <= cp_id}

        # Append dmail as user message
        self._context.add(Message.user(f"[D-Mail from future self] {message}"))

        return ToolResult(
            output=f"D-Mail sent. Context rewound to checkpoint {cp_id}.",
            metadata={"checkpoint_id": cp_id, "messages_removed": len(self._context.messages) - msg_index},
        )
