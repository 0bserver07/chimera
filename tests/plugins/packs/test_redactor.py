"""Redactor policy pack: wire and transcript scrubbing.

The loop cases run through the real assembled stack with a recording
provider, pinning the two-sided contract: the provider-request scrub is
ephemeral (the wire is clean, the durable conversation keeps originals)
while the tool-result scrub is durable (the transcript records the
scrubbed output).
"""
from __future__ import annotations

import re

import pytest

from chimera.core.interception import (
    ProviderRequest,
    intercept_provider_request,
    intercept_tool_result,
)
from chimera.core.tool import BaseTool
from chimera.plugins.manager import PluginManager
from chimera.plugins.packs import RedactorPlugin
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.providers.faux import FauxProvider
from chimera.testing import create_assembled_harness
from chimera.types import Message, ToolCall, ToolResult

SECRET = "sk-livekey12345678"


@pytest.fixture(autouse=True)
def _clean_registry():
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


class RecordingFaux(FauxProvider):
    """Faux provider that records every payload it is asked to complete."""

    def __init__(self, script):
        super().__init__(script)
        self.payloads: list[list[tuple[str, str]]] = []

    def complete(self, messages, tools=None, **kwargs):
        self.payloads.append([(m.role, m.content or "") for m in messages])
        return super().complete(messages, tools=tools, **kwargs)


class LeakTool(BaseTool):
    name = "leak"
    description = "Returns output containing a secret"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, args, env):
        return ToolResult(output=f"connected using {SECRET} just fine")


def test_tool_result_scrub_is_durable_through_the_real_loop(tmp_path):
    """The secret a tool leaks never enters the transcript."""
    PluginManager().load_plugin(RedactorPlugin())
    provider = RecordingFaux([
        {"tool_calls": [{"name": "leak", "arguments": {}}]},
        {"text": "done"},
    ])
    harness = create_assembled_harness(
        workspace=tmp_path, provider=provider, tools=[LeakTool()],
    )
    run = harness.run("connect to the service")

    leak_results = [r for tc, r in run.tool_results if tc and tc.name == "leak"]
    assert leak_results and SECRET not in leak_results[0].output
    assert "[redacted]" in leak_results[0].output
    # Durable: the terminal conversation carries the scrubbed output only.
    tool_msgs = [m for m in run.messages if getattr(m, "role", None) == "tool"]
    assert tool_msgs and all(SECRET not in (m.content or "") for m in tool_msgs)


def test_provider_request_scrub_is_ephemeral_through_the_real_loop(tmp_path):
    """A secret in the user prompt never reaches the wire, but the durable
    conversation keeps the original (the seam's documented scope)."""
    PluginManager().load_plugin(RedactorPlugin())
    provider = RecordingFaux([{"text": "authenticated"}])
    harness = create_assembled_harness(
        workspace=tmp_path, provider=provider, tools=[LeakTool()],
    )
    run = harness.run(f"authenticate with {SECRET} please")

    assert provider.payloads  # the call happened
    for payload in provider.payloads:
        assert all(SECRET not in content for _, content in payload)
    assert any(
        "[redacted]" in content for _, content in provider.payloads[0]
    )
    # Ephemeral: the durable conversation still holds the original.
    user_msgs = [m for m in run.messages if getattr(m, "role", None) == "user"]
    assert any(SECRET in (m.content or "") for m in user_msgs)


# ---------------------------------------------------------------------------
# Envelope details (unit, through the real seam runners)
# ---------------------------------------------------------------------------


def test_named_headers_replaced_wholesale_case_insensitively():
    pack = RedactorPlugin()
    req = ProviderRequest(
        model="m",
        messages=[],
        headers={
            "authorization": "Bearer something-live",
            "X-Trace": f"trace {SECRET}",
            "X-Keep": "plain",
        },
    )
    out, block = intercept_provider_request(pack.interceptors().provider_request, req)

    assert block is None
    assert out.headers == {
        "authorization": "[redacted]",       # named header: wholesale, any case
        "X-Trace": "trace [redacted]",       # other headers: pattern-scrubbed
        "X-Keep": "plain",
    }


def test_tool_call_arguments_inside_messages_are_scrubbed():
    pack = RedactorPlugin()
    original = Message.assistant(
        "running it",
        tool_calls=[ToolCall(
            id="tc1", name="bash",
            arguments={"cmd": f"curl -H 'X-Key: {SECRET}'", "depth": 2,
                       "flags": [f"--token={SECRET}", "--verbose"]},
        )],
    )
    req = ProviderRequest(model="m", messages=[original])
    out, _ = intercept_provider_request(pack.interceptors().provider_request, req)

    args = out.messages[0].tool_calls[0].arguments
    assert args["cmd"] == "curl -H 'X-Key: [redacted]'"
    assert args["flags"] == ["--token=[redacted]", "--verbose"]
    assert args["depth"] == 2
    # The original message object is untouched (rewrite, not mutation).
    assert SECRET in original.tool_calls[0].arguments["cmd"]


def test_clean_request_passes_through_untouched():
    pack = RedactorPlugin()
    req = ProviderRequest(
        model="m",
        messages=[Message.user("nothing secret here")],
        headers={"X-Keep": "plain"},
    )
    out, _ = intercept_provider_request(pack.interceptors().provider_request, req)
    assert out is req  # nothing matched, nothing named: no rewrite at all


def test_error_text_is_scrubbed_too():
    pack = RedactorPlugin()
    tc = ToolCall(id="t1", name="bash", arguments={})
    out = intercept_tool_result(
        pack.interceptors().tool_result,
        tc,
        ToolResult(output="", error=f"auth failed for {SECRET}"),
    )
    assert out.error == "auth failed for [redacted]"


def test_custom_pattern_and_replacement():
    pack = RedactorPlugin(pattern=r"acme-[0-9]{4}", replacement="<hidden>")
    tc = ToolCall(id="t1", name="bash", arguments={})
    out = intercept_tool_result(
        pack.interceptors().tool_result,
        tc,
        ToolResult(output=f"acme-1234 and {SECRET}"),
    )
    assert out.output == f"<hidden> and {SECRET}"  # only the configured pattern


def test_invalid_pattern_fails_at_construction():
    with pytest.raises(re.error):
        RedactorPlugin(pattern="(")


def test_unload_withdraws_both_scrubs():
    manager = PluginManager()
    manager.load_plugin(RedactorPlugin())
    assert len(PluginExtensionRegistry.get_interceptors("provider_request")) == 1
    assert len(PluginExtensionRegistry.get_interceptors("tool_result")) == 1
    manager.unload("redactor")
    assert PluginExtensionRegistry.get_interceptors("provider_request") == []
    assert PluginExtensionRegistry.get_interceptors("tool_result") == []
