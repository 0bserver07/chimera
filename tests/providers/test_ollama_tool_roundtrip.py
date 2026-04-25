"""Contract tests for OllamaProvider 2-turn tool round trip (M0).

Verifies the tool-result message shape sent on turn 2:
    {"role": "tool", "tool_name": "<name>", "content": "<stringified>"}
No OpenAI-style `tool_call_id` field is permitted (Ollama uses tool_name).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.ollama import OllamaProvider
from chimera.types import Message, ToolCall


# Turn-1 response: assistant emits a single tool_call to list_files.
TURN1_BODY: dict[str, Any] = {
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": "list_files", "arguments": {"path": "."}}}
        ],
    },
    "eval_count": 12,
    "prompt_eval_count": 50,
    "done": True,
}

# Turn-2 response: assistant returns final text after seeing the tool result.
TURN2_BODY: dict[str, Any] = {
    "message": {
        "role": "assistant",
        "content": "Found 3 files: README.md, chimera/, tests/.",
    },
    "eval_count": 18,
    "prompt_eval_count": 80,
    "done": True,
}


class _SeqResponse:
    """Mimics httpx.Response for httpx.post(...)."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


@pytest.fixture()
def captured_post() -> Any:
    """Patch httpx.post to return queued responses; capture call args."""
    calls: list[dict[str, Any]] = []
    bodies = [TURN1_BODY, TURN2_BODY]

    def _post(url: str, **kw: Any) -> _SeqResponse:
        calls.append({"url": url, "json": kw.get("json")})
        return _SeqResponse(bodies.pop(0) if bodies else TURN2_BODY)

    with patch("chimera.providers.ollama.httpx") as mock_httpx:
        mock_httpx.post.side_effect = _post
        # In case implementation switched to AsyncClient for complete(), too:
        mock_httpx.AsyncClient = MagicMock()
        yield calls


def test_two_turn_tool_roundtrip_sends_tool_name_field(captured_post: list[dict[str, Any]]) -> None:
    prov = OllamaProvider(model="kimi-k2.6:cloud")

    # Turn 1: user asks; provider returns a tool call.
    turn1 = prov.complete([Message.user("List files in cwd")])
    assert turn1.has_tool_calls
    tc = turn1.tool_calls[0]
    assert tc.name == "list_files"

    # Build conversation for turn 2: original user, assistant(tool_calls), tool result.
    convo = [
        Message.user("List files in cwd"),
        Message.assistant("", tool_calls=[ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)]),
        Message(role="tool", content="README.md\nchimera/\ntests/", call_id=tc.id),
    ]
    turn2 = prov.complete(convo)
    assert "Found" in turn2.content

    # Two POSTs to /api/chat were issued.
    assert len(captured_post) == 2
    for c in captured_post:
        assert c["url"].endswith("/api/chat")

    # Inspect turn-2 outgoing messages array for the tool-result shape.
    sent_msgs = (captured_post[1]["json"] or {}).get("messages") or []
    tool_msgs = [m for m in sent_msgs if m.get("role") == "tool"]
    assert tool_msgs, f"no role=tool message in {sent_msgs}"
    tm = tool_msgs[0]
    assert tm.get("tool_name") == "list_files", f"missing tool_name in {tm}"
    assert isinstance(tm.get("content"), str) and tm["content"].startswith("README.md")
    # Ollama protocol: no tool_call_id on tool messages.
    assert "tool_call_id" not in tm
