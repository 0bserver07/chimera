"""Faithful (de)serialization of conversation history for cohort resume (§13.2).

The existing session serializers keep only ``role`` + ``content`` (snapshots
reconstruct the rest from later events). Resuming a multiplexer lane needs the
*exact* messages — including tool calls and their ids — so the next provider
request stays valid: a ``tool_use`` block must keep its matching ``tool_result``.
This codec round-trips :class:`~chimera.types.Message` faithfully, and imports
only stdlib + ``chimera.types`` (no TUI extra), so it is safe to load anywhere.
"""
from __future__ import annotations

from typing import Any

from chimera.types import ImageContent, Message, TextContent, ToolCall

__all__ = [
    "serialize_history",
    "deserialize_history",
    "message_to_dict",
    "dict_to_message",
]


def _block_to_dict(block: Any) -> dict[str, Any]:
    if getattr(block, "type", "") == "image":
        return {
            "type": "image",
            "data": getattr(block, "data", ""),
            "media_type": getattr(block, "media_type", ""),
        }
    return {"type": "text", "text": getattr(block, "text", "")}


def _dict_to_block(data: dict[str, Any]) -> Any:
    if data.get("type") == "image":
        return ImageContent(data=data.get("data", ""), media_type=data.get("media_type", ""))
    return TextContent(text=data.get("text", ""))


def message_to_dict(message: Message) -> dict[str, Any]:
    """Encode one Message as a JSON-friendly dict, losing nothing load-bearing."""
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.call_id is not None:
        out["call_id"] = message.call_id
    if message.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in message.tool_calls
        ]
    if message.content_blocks:
        out["content_blocks"] = [_block_to_dict(b) for b in message.content_blocks]
    return out


def dict_to_message(data: dict[str, Any]) -> Message:
    """Inverse of :func:`message_to_dict`."""
    return Message(
        role=data.get("role", "user"),
        content=data.get("content", ""),
        tool_calls=[
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("arguments", {}) or {},
            )
            for tc in (data.get("tool_calls") or [])
        ],
        call_id=data.get("call_id"),
        content_blocks=[_dict_to_block(b) for b in (data.get("content_blocks") or [])],
    )


def serialize_history(messages: list[Any]) -> list[dict[str, Any]]:
    """Encode a conversation history for persistence."""
    return [message_to_dict(m) for m in messages]


def deserialize_history(rows: list[dict[str, Any]]) -> list[Message]:
    """Decode a persisted conversation history back into Messages."""
    return [dict_to_message(r) for r in rows]
