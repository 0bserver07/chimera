"""Tests for :class:`chimera.providers.faux.FauxProvider` — no network.

The faux provider is a deterministic, scripted stand-in for a real LLM backend.
These tests prove:

* Scripts play in order (text, tool-call, thinking, and error steps).
* Both exhaustion policies behave (``"final"`` terminal text vs ``"repeat"``).
* Usage and cost accounting are deterministic and add up.
* Tool-call steps yield well-formed ``Response.tool_calls`` a real ReAct loop
  can execute end-to-end (driven against a real ``Agent`` + a trivial tool).
* Error steps surface as a provider error, from both ``complete`` and ``stream``.
* The base ``stream``/``async_complete`` defaults work over faux output.
* Self-registration wires ``create_provider(provider_type="faux")`` once imported.

Nothing here touches the network or requires an API key.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.providers.factory import create_provider
from chimera.providers.faux import FauxProvider, FauxProviderError
from chimera.providers.registry import get_provider_factory
from chimera.types import Message, ToolResult


# --------------------------------------------------------------------------- #
# A trivial tool so scripted tool calls actually execute in a real loop.
# --------------------------------------------------------------------------- #
class PingTool(BaseTool):
    name = "ping"
    description = "Returns pong."
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(output="pong")


def _user(text: str) -> list[Message]:
    return [Message.user(text)]


# --------------------------------------------------------------------------- #
# Script plays in order
# --------------------------------------------------------------------------- #
def test_plays_steps_in_order() -> None:
    provider = FauxProvider([
        {"text": "first"},
        {"text": "second"},
        {"text": "third"},
    ])
    assert provider.complete(_user("q")).content == "first"
    assert provider.complete(_user("q")).content == "second"
    assert provider.complete(_user("q")).content == "third"


def test_string_shorthand_script() -> None:
    provider = FauxProvider("just this")
    assert provider.complete(_user("q")).content == "just this"


def test_single_dict_script() -> None:
    provider = FauxProvider({"text": "solo"})
    assert provider.complete(_user("q")).content == "solo"


def test_tool_call_step_shape() -> None:
    provider = FauxProvider([
        {"text": "calling", "tool_calls": [{"name": "ping", "arguments": {"x": 1}}]},
    ])
    resp = provider.complete(_user("q"))
    assert isinstance(resp, Response)
    assert resp.has_tool_calls
    assert resp.content == "calling"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "ping"
    assert tc.arguments == {"x": 1}


def test_tool_call_ids_are_deterministic_and_monotonic() -> None:
    provider = FauxProvider([
        {"tool_calls": [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {}}]},
        {"tool_calls": [{"name": "c", "arguments": {}}]},
    ])
    r1 = provider.complete(_user("q"))
    r2 = provider.complete(_user("q"))
    assert [tc.id for tc in r1.tool_calls] == ["faux-tc-0", "faux-tc-1"]
    assert [tc.id for tc in r2.tool_calls] == ["faux-tc-2"]


def test_thinking_step_records_thinking_tokens() -> None:
    provider = FauxProvider([{"thinking": "x" * 16, "text": "answer"}])
    resp = provider.complete(_user("q"))
    assert resp.content == "answer"  # thinking is not merged into content
    assert resp.usage["thinking_tokens"] == 4  # 16 // 4


# --------------------------------------------------------------------------- #
# Exhaustion behaviors
# --------------------------------------------------------------------------- #
def test_exhaustion_final_default_is_empty() -> None:
    provider = FauxProvider([{"text": "only"}])
    assert provider.complete(_user("q")).content == "only"
    # Exhausted: default final text is empty, no tool calls -> loop-stopping.
    tail = provider.complete(_user("q"))
    assert tail.content == ""
    assert tail.tool_calls == []
    assert provider.exhausted is True


def test_exhaustion_final_custom_text() -> None:
    provider = FauxProvider([{"text": "only"}], final_text="DONE")
    provider.complete(_user("q"))
    assert provider.complete(_user("q")).content == "DONE"
    assert provider.complete(_user("q")).content == "DONE"


def test_exhaustion_repeat_replays_last_step() -> None:
    provider = FauxProvider(
        [{"text": "a"}, {"text": "b"}],
        on_exhausted="repeat",
    )
    assert provider.complete(_user("q")).content == "a"
    assert provider.complete(_user("q")).content == "b"
    # Past the end: last step repeats forever.
    assert provider.complete(_user("q")).content == "b"
    assert provider.complete(_user("q")).content == "b"


def test_remaining_steps_and_reset() -> None:
    provider = FauxProvider([{"text": "a"}, {"text": "b"}])
    assert provider.remaining_steps == 2
    provider.complete(_user("q"))
    assert provider.remaining_steps == 1
    provider.reset()
    assert provider.remaining_steps == 2
    assert provider.call_count == 0
    assert provider.total_cost == 0.0
    assert provider.complete(_user("q")).content == "a"


def test_invalid_on_exhausted_rejected() -> None:
    with pytest.raises(ValueError, match="on_exhausted"):
        FauxProvider([{"text": "x"}], on_exhausted="bogus")


# --------------------------------------------------------------------------- #
# Usage / cost accounting
# --------------------------------------------------------------------------- #
def test_usage_is_derived_from_text_length() -> None:
    provider = FauxProvider([{"text": "y" * 12}])
    resp = provider.complete([Message.user("x" * 8)])
    assert resp.usage["input_tokens"] == 2  # 8 // 4
    assert resp.usage["output_tokens"] == 3  # 12 // 4


def test_usage_includes_tool_call_serialization() -> None:
    # Output tokens count the produced text plus serialized tool calls, so a
    # tool-only step is not free.
    provider = FauxProvider([{"tool_calls": [{"name": "ping", "arguments": {}}]}])
    resp = provider.complete(_user("q"))
    # tool_blob = "ping" + "{}" = 6 chars -> 1 token; no text.
    assert resp.usage["output_tokens"] == 1


def test_explicit_usage_override_wins() -> None:
    provider = FauxProvider([
        {"text": "hi", "usage": {"input_tokens": 999, "output_tokens": 7}},
    ])
    resp = provider.complete(_user("q"))
    assert resp.usage["input_tokens"] == 999
    assert resp.usage["output_tokens"] == 7


def test_cost_accumulates_per_call() -> None:
    provider = FauxProvider(
        [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        cost_per_call=0.002,
    )
    for _ in range(3):
        provider.complete(_user("q"))
    assert provider.call_count == 3
    assert provider.total_cost == pytest.approx(0.006)


def test_token_totals_accumulate() -> None:
    provider = FauxProvider([{"text": "y" * 12}, {"text": "y" * 8}])
    provider.complete([Message.user("x" * 8)])  # in 2, out 3
    provider.complete([Message.user("x" * 4)])  # in 1, out 2
    assert provider.total_input_tokens == 3
    assert provider.total_output_tokens == 5
    assert provider.total_tokens == 8


# --------------------------------------------------------------------------- #
# Error steps
# --------------------------------------------------------------------------- #
def test_error_step_raises() -> None:
    provider = FauxProvider([{"error": "boom"}])
    with pytest.raises(FauxProviderError, match="boom"):
        provider.complete(_user("q"))


def test_error_step_does_not_perturb_accounting() -> None:
    provider = FauxProvider([{"text": "ok"}, {"error": "boom"}])
    provider.complete(_user("q"))
    with pytest.raises(FauxProviderError):
        provider.complete(_user("q"))
    # The failed call is not counted.
    assert provider.call_count == 1


def test_error_surfaces_through_stream() -> None:
    provider = FauxProvider([{"error": "stream-boom"}])
    with pytest.raises(FauxProviderError, match="stream-boom"):
        list(provider.stream(_user("q")))


# --------------------------------------------------------------------------- #
# Streaming (base default over faux output)
# --------------------------------------------------------------------------- #
def test_stream_emits_text_tool_and_done() -> None:
    provider = FauxProvider([
        {"text": "hello", "tool_calls": [{"name": "ping", "arguments": {}}]},
    ])
    events = list(provider.stream(_user("q")))
    types = [e.type for e in events]
    assert "text_delta" in types
    assert "tool_call_start" in types
    assert types[-1] == "done"
    text = next(e for e in events if e.type == "text_delta")
    assert text.content == "hello"
    done = events[-1]
    assert done.usage is not None
    assert "output_tokens" in done.usage


def test_async_complete_bridges_via_base() -> None:
    provider = FauxProvider([{"text": "async-answer"}])
    resp = asyncio.run(provider.async_complete(_user("q")))
    assert resp.content == "async-answer"


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #
def test_delay_sec_adds_latency() -> None:
    provider = FauxProvider([{"text": "slow"}], delay_sec=0.05)
    start = time.monotonic()
    provider.complete(_user("q"))
    assert time.monotonic() - start >= 0.05


# --------------------------------------------------------------------------- #
# Provider ABC surface
# --------------------------------------------------------------------------- #
def test_provider_properties() -> None:
    provider = FauxProvider(
        [{"text": "x"}],
        model="glm-5.2",
        context_window=1_000_000,
        supports_tools=False,
    )
    assert provider.model_name == "glm-5.2"
    assert provider.context_window == 1_000_000
    assert provider.supports_tool_use is False


# --------------------------------------------------------------------------- #
# coding_script helper
# --------------------------------------------------------------------------- #
def test_coding_script_shape() -> None:
    provider = FauxProvider.coding_script("return 42", tool_rounds=2, tool_name="ping")
    r1 = provider.complete(_user("q"))
    r2 = provider.complete(_user("q"))
    r3 = provider.complete(_user("q"))
    assert r1.has_tool_calls and r1.tool_calls[0].name == "ping"
    assert r2.has_tool_calls
    # Final turn: fenced answer, no tool calls.
    assert not r3.has_tool_calls
    assert "```" in r3.content
    assert "return 42" in r3.content


def test_coding_script_zero_rounds() -> None:
    provider = FauxProvider.coding_script("x = 1", tool_rounds=0)
    resp = provider.complete(_user("q"))
    assert not resp.has_tool_calls
    assert "x = 1" in resp.content


# --------------------------------------------------------------------------- #
# End-to-end: drive a real Agent loop on faux
# --------------------------------------------------------------------------- #
def test_end_to_end_agent_run_returns_scripted_answer() -> None:
    tool = PingTool()
    provider = FauxProvider.coding_script("42", tool_rounds=1, tool_name="ping")
    agent = Agent(provider=provider, tools=[tool], loop=ReAct(max_steps=5))

    result = agent.run("solve it", env=None)

    assert result.success is True
    assert "42" in result.output
    # The scripted tool round actually executed against the real tool.
    assert tool.calls == 1
    assert result.tool_calls_total == 1
    # Two completions: one tool round + one final answer.
    assert provider.call_count == 2


def test_end_to_end_repeat_tool_loop_hits_step_ceiling() -> None:
    # A repeat-mode script that always calls a tool runs until the loop's own
    # max_steps ceiling — proving the loop, not the provider, bounds it.
    tool = PingTool()
    provider = FauxProvider(
        [{"text": "again", "tool_calls": [{"name": "ping", "arguments": {}}]}],
        on_exhausted="repeat",
    )
    agent = Agent(provider=provider, tools=[tool], loop=ReAct(max_steps=3))

    agent.run("loop forever", env=None)

    assert tool.calls == 3  # bounded by max_steps, not the script


# --------------------------------------------------------------------------- #
# Self-registration
# --------------------------------------------------------------------------- #
def test_self_registered_after_import() -> None:
    # Importing chimera.providers.faux (done at module top) fires registration.
    assert get_provider_factory("faux") is not None


def test_create_provider_with_explicit_type() -> None:
    provider = create_provider(provider_type="faux", model="faux")
    assert isinstance(provider, FauxProvider)
    # Empty script + default on_exhausted="final" -> a benign empty completion.
    assert provider.complete(_user("q")).content == ""


def test_create_provider_passes_script_kwarg() -> None:
    provider = create_provider(
        provider_type="faux",
        model="faux",
        script=[{"text": "wired"}],
    )
    assert isinstance(provider, FauxProvider)
    assert provider.complete(_user("q")).content == "wired"
