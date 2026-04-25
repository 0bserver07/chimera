"""Contract tests for OllamaProvider.stream() async generator (M0).

Targets the contract, not implementation: NDJSON over POST /api/chat with
mid-stream tool_calls accumulation, num_ctx + keep_alive defaults, and
think:true gated by model name.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.ollama import OllamaProvider
from chimera.types import Message


# Tool calls land mid-stream per Ollama protocol; final chunk carries done:true.
NDJSON_CHUNKS: list[dict[str, Any]] = [
    {"message": {"role": "assistant", "content": "Hello "}, "done": False},
    {"message": {"role": "assistant", "content": "world"}, "done": False},
    {
        "message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": "."}}}],
        },
        "done": True, "prompt_eval_count": 100, "eval_count": 20,
    },
]


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200

    async def __aenter__(self) -> "_FakeStreamResponse": return self
    async def __aexit__(self, *exc: object) -> None: return None
    def raise_for_status(self) -> None: return None

    async def aiter_lines(self):  # type: ignore[no-untyped-def]
        for line in self._lines:
            yield line


class _CapturingAsyncClient:
    """Captures POST kwargs so tests can inspect the request body."""

    def __init__(self, lines: list[str]) -> None:
        self.lines, self.last_url, self.last_json = lines, None, None

    async def __aenter__(self) -> "_CapturingAsyncClient": return self
    async def __aexit__(self, *exc: object) -> None: return None

    def stream(self, method: str, url: str, **kw: Any) -> _FakeStreamResponse:
        self.last_url, self.last_json = url, kw.get("json")
        return _FakeStreamResponse(self.lines)


async def _run(model: str) -> tuple[list[Any], _CapturingAsyncClient]:
    cap = _CapturingAsyncClient([json.dumps(c) for c in NDJSON_CHUNKS])
    with patch("chimera.providers.ollama.httpx") as mh:
        mh.AsyncClient = MagicMock(return_value=cap)
        prov = OllamaProvider(model=model)
        events: list[Any] = []
        async for ev in prov.async_stream([Message.user("hi")]):  # contract: async gen
            events.append(ev)
    return events, cap


@pytest.mark.asyncio
async def test_stream_emits_text_then_tool_call_in_order() -> None:
    events, _ = await _run("kimi-k2.6:cloud")
    types = [getattr(e, "type", None) for e in events]
    assert "text_delta" in types, types
    first_tc = next((i for i, t in enumerate(types) if t and t.startswith("tool_call_")), None)
    assert first_tc is not None, types
    assert all(i < first_tc for i, t in enumerate(types) if t == "text_delta")

    text = "".join(e.content for e in events if e.type == "text_delta")
    assert "Hello " in text and "world" in text

    starts = [e for e in events if e.type == "tool_call_start"]
    completes = [e for e in events if e.type == "tool_call_complete"]
    assert starts and completes, types
    assert starts[0].tool_call.name == "list_files"
    assert starts[0].tool_call.id  # synthesized non-empty id
    # id must be stable across the start/(delta)/complete lifecycle.
    assert completes[0].tool_call.id == starts[0].tool_call.id
    assert completes[0].tool_call.arguments.get("path") == "."


@pytest.mark.asyncio
async def test_stream_request_body_kimi_includes_think_and_keep_alive() -> None:
    _, cap = await _run("kimi-k2.6:cloud")
    assert cap.last_url and cap.last_url.endswith("/api/chat"), cap.last_url
    assert "/v1/chat/completions" not in cap.last_url
    body = cap.last_json or {}
    assert body.get("keep_alive") == "60m"
    assert body.get("think") is True, "kimi* models must send think:true"
    opts = body.get("options") or {}
    assert isinstance(opts.get("num_ctx"), int) and opts["num_ctx"] > 0


@pytest.mark.asyncio
async def test_stream_request_body_qwen_omits_think() -> None:
    _, cap = await _run("qwen3:32b")
    body = cap.last_json or {}
    assert body.get("think") in (None, False)
    assert body.get("keep_alive") == "60m"
    assert "num_ctx" in (body.get("options") or {})
