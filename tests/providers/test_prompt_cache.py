"""Unified prompt-caching knob on AnthropicProvider.

Covers the provider-agnostic ``cache="none"|"short"|"long"`` convention and its
Anthropic implementation:

* ``"none"`` (default) adds NO ``cache_control`` anywhere — zero behavior change.
* ``"short"`` marks the system prompt, the last tool, and the last message with
  a 5-minute ephemeral marker (the standard agentic-loop pattern).
* ``"long"`` uses the 1-hour TTL form.
* the deprecated ``enable_cache=True`` flag aliases ``cache="short"``.
* ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` from a response
  are parsed into ``Response.usage`` (sync + streaming paths).

All tests use a FAKE anthropic client — no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("anthropic")

from chimera.providers.anthropic import AnthropicProvider
from chimera.types import Message

EPHEMERAL = {"type": "ephemeral"}
EPHEMERAL_1H = {"type": "ephemeral", "ttl": "1h"}


@pytest.fixture
def _patch_client(monkeypatch):
    """Patch the Anthropic client constructor so no real API key is needed."""
    monkeypatch.setattr(
        "chimera.providers.anthropic.anthropic.Anthropic",
        lambda **kw: MagicMock(),
    )


def _sys_user():
    return [Message.system("You are a careful assistant."), Message.user("Hi there")]


# -- cache="none" (default) : no markers anywhere --------------------------------

def test_cache_defaults_to_none(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4")
    assert provider._cache == "none"


def test_cache_none_adds_no_cache_control_anywhere(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4")  # default: none
    tools = [{"name": "read", "description": "Read", "input_schema": {}}]
    kwargs = provider._prepare_request(_sys_user(), tools=tools)

    # system stays a plain string, last message stays a plain string,
    # last tool has no cache_control.
    assert kwargs["system"] == "You are a careful assistant."
    assert isinstance(kwargs["messages"][-1]["content"], str)
    assert "cache_control" not in kwargs["tools"][0]


def test_cache_none_leaves_tool_result_block_untouched(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4")
    msgs = [Message.user("run it"), Message.tool(call_id="c1", content="output")]
    kwargs = provider._prepare_request(msgs)
    assert "cache_control" not in kwargs["messages"][-1]["content"][-1]


# -- cache="short" : ephemeral markers on system + last tool + last message ------

def test_cache_short_marks_system_and_last_message(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="short")
    kwargs = provider._prepare_request(_sys_user())

    assert kwargs["system"][0]["cache_control"] == EPHEMERAL
    assert kwargs["system"][0]["text"] == "You are a careful assistant."

    last = kwargs["messages"][-1]
    assert isinstance(last["content"], list)
    assert last["content"][-1]["cache_control"] == EPHEMERAL
    assert last["content"][-1]["text"] == "Hi there"


def test_cache_short_marks_last_tool_only(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="short")
    tools = [
        {"name": "read", "description": "Read", "input_schema": {}},
        {"name": "write", "description": "Write", "input_schema": {}},
    ]
    kwargs = provider._prepare_request([Message.user("hi")], tools=tools)
    assert "cache_control" not in kwargs["tools"][0]
    assert kwargs["tools"][1]["cache_control"] == EPHEMERAL


def test_cache_short_marks_last_block_of_tool_result(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="short")
    msgs = [
        Message.system("sys"),
        Message.user("run it"),
        Message.tool(call_id="call_1", content="tool output"),
    ]
    kwargs = provider._prepare_request(msgs)
    last = kwargs["messages"][-1]
    # a tool message renders as a user message carrying a tool_result block
    assert last["role"] == "user"
    block = last["content"][-1]
    assert block["type"] == "tool_result"
    assert block["cache_control"] == EPHEMERAL


def test_cache_short_does_not_mutate_caller_tools(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="short")
    tools = [{"name": "read", "description": "Read", "input_schema": {}}]
    provider._prepare_request([Message.user("hi")], tools=tools)
    # the caller's tool dict must be left clean
    assert "cache_control" not in tools[0]


# -- cache="long" : 1-hour TTL form ----------------------------------------------

def test_cache_long_uses_1h_ttl_on_system_and_message(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="long")
    kwargs = provider._prepare_request(_sys_user())
    assert kwargs["system"][0]["cache_control"] == EPHEMERAL_1H
    assert kwargs["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL_1H


def test_cache_long_uses_1h_ttl_on_last_tool(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="long")
    tools = [{"name": "read", "description": "Read", "input_schema": {}}]
    kwargs = provider._prepare_request([Message.user("hi")], tools=tools)
    assert kwargs["tools"][0]["cache_control"] == EPHEMERAL_1H


# -- deprecated enable_cache alias ----------------------------------------------

def test_enable_cache_alias_maps_to_short(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", enable_cache=True)
    assert provider._cache == "short"
    kwargs = provider._prepare_request(_sys_user())
    assert kwargs["system"][0]["cache_control"] == EPHEMERAL
    assert kwargs["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL


def test_explicit_cache_wins_over_enable_cache(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="long", enable_cache=True)
    assert provider._cache == "long"
    kwargs = provider._prepare_request(_sys_user())
    assert kwargs["system"][0]["cache_control"] == EPHEMERAL_1H


def test_enable_cache_false_default_is_none(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", enable_cache=False)
    assert provider._cache == "none"
    kwargs = provider._prepare_request(_sys_user())
    assert kwargs["system"] == "You are a careful assistant."


# -- validation -----------------------------------------------------------------

def test_invalid_cache_value_raises(_patch_client):
    with pytest.raises(ValueError, match="cache must be one of"):
        AnthropicProvider(model="claude-sonnet-4", cache="forever")


# -- back-compat: instances that only carry _enable_cache (via __new__) ----------

def test_cache_control_falls_back_to_enable_cache_when_cache_absent():
    # Mirrors tests/providers/test_provider_anthropic_stream.py, which builds the
    # provider via __new__ and sets only _enable_cache (never _cache).
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._enable_cache = False
    assert provider._cache_control_block() is None

    provider._enable_cache = True
    assert provider._cache_control_block() == EPHEMERAL


# -- response usage parses cache tokens (sync + streaming) -----------------------

class _FakeUsage:
    def __init__(self, *, cache_creation=None, cache_read=None):
        self.input_tokens = 1000
        self.output_tokens = 50
        if cache_creation is not None:
            self.cache_creation_input_tokens = cache_creation
        if cache_read is not None:
            self.cache_read_input_tokens = cache_read


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, usage):
        self.content = [_FakeBlock("ok")]
        self.usage = usage


def test_complete_parses_cache_tokens_and_sends_markers(_patch_client):
    provider = AnthropicProvider(model="claude-sonnet-4", cache="short")
    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_FakeUsage(cache_creation=800, cache_read=200))

    provider._client.messages.create = _create

    resp = provider.complete(_sys_user())

    # cache_control reached the wire on the system prompt
    assert isinstance(captured["system"], list)
    assert captured["system"][0]["cache_control"] == EPHEMERAL
    # response cache tokens are surfaced in Response.usage
    assert resp.usage["cache_creation_input_tokens"] == 800
    assert resp.usage["cache_read_input_tokens"] == 200


def test_stream_final_usage_includes_cache_tokens():
    final = MagicMock()
    final.usage.input_tokens = 1000
    final.usage.output_tokens = 50
    final.usage.cache_creation_input_tokens = 800
    final.usage.cache_read_input_tokens = 200
    usage = AnthropicProvider._usage_from_final(final)
    assert usage["cache_creation_input_tokens"] == 800
    assert usage["cache_read_input_tokens"] == 200
