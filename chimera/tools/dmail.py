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
from chimera.core.tool import ContextAwareTool
from chimera.env.base import Environment
from chimera.types import Message, ToolResult


class DMailTool(ContextAwareTool):
    """Send a message to your past self at a checkpoint, rewinding context.

    The tool manages its own checkpoint-to-message-index mapping on top of
    a :class:`~chimera.core.context.Context` instance.  Checkpoints are
    lightweight markers (just a message index), not filesystem snapshots.

    The agent's :class:`~chimera.core.agent.Agent` calls
    :meth:`~chimera.core.tool.ContextAwareTool.bind_context` before the loop
    starts, so the tool always operates on the correct context.
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
            "action": {
                "type": "string",
                "enum": ["checkpoint", "send"],
                "description": "Either 'checkpoint' to save current position, or 'send' to rewind",
            },
            "checkpoint_id": {
                "type": "integer",
                "description": "The checkpoint to rewind to (required for 'send')",
            },
            "message": {
                "type": "string",
                "description": "Message to your past self (required for 'send')",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self._context: Context | None = None  # type: ignore[assignment]  # set by bind_context()
        self._checkpoints: dict[int, int] = {}  # checkpoint_id -> message_index
        self._next_id: int = 0

    def create_checkpoint(self) -> int:
        """Record a checkpoint at the current context position.

        Returns:
            The checkpoint ID (starting from 0, incrementing).

        Raises:
            RuntimeError: If the tool has not been bound to a context.
        """
        if self._context is None:
            raise RuntimeError("DMailTool not bound to a context. Call bind_context() first.")
        cp_id = self._next_id
        self._checkpoints[cp_id] = len(self._context.messages)
        self._next_id += 1
        return cp_id

    @property
    def checkpoint_count(self) -> int:
        """Return the number of checkpoints created so far."""
        return self._next_id

    def execute(self, args: dict[str, Any], env: Environment | None = None) -> ToolResult:
        """Execute a D-Mail action (checkpoint or send).

        Args:
            args: Must contain ``action``. For ``"send"``, must also contain
                ``checkpoint_id`` (int) and ``message`` (str).
            env: Unused -- operates on the in-memory Context.

        Returns:
            ToolResult confirming the action, or an error if the tool is
            unbound or the checkpoint does not exist.
        """
        if self._context is None:
            return ToolResult(output="", error="DMailTool not bound to a context")

        action = args["action"]

        if action == "checkpoint":
            cp_id = self.create_checkpoint()
            return ToolResult(
                output=f"Checkpoint {cp_id} created at message index {self._checkpoints[cp_id]}.",
                metadata={"checkpoint_id": cp_id},
            )

        elif action == "send":
            cp_id_arg = args.get("checkpoint_id")
            message = args.get("message")
            if cp_id_arg is None or message is None:
                return ToolResult(output="", error="'send' requires checkpoint_id and message")
            cp_id = int(cp_id_arg)

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

        return ToolResult(output="", error=f"Unknown action: {action}")
