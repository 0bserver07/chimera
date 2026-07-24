"""Tests for chimera.core.interception — the typed decision seams.

Covers the decision type, chain mechanics (composition order, first block
wins, replacement chaining, per-seam failure policy), the four seams wired
through the real ReAct loop via PUBLIC LoopConfig only, the byte-identical
None-config pin, the interceptors-before-permissions ordering pin, the
AgentLoop (assembled path) wiring, and the proof plugin exercising all
four seams at once.
"""
from __future__ import annotations

import pytest

from chimera.core.context import Context
from chimera.core.interception import (
    InterceptDecision,
    Interceptors,
    ProviderRequest,
    intercept_context,
    intercept_provider_request,
    intercept_tool_call,
    intercept_tool_result,
)
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.events.base import EventBus
from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingProvider(Provider):
    """Scripted provider that records exactly what each call received."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None, **kwargs):
        self.calls.append({
            "messages": [(m.role, m.content) for m in messages],
            "tools": tools,
            "temperature": temperature,
            "kwargs": dict(kwargs),
        })
        if self._call_count >= len(self._responses):
            return Response(content="(exhausted)", tool_calls=[], usage={})
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "recording-mock"


class HeaderedProvider(RecordingProvider):
    """Recording provider with the ``request_headers`` injection surface."""

    def __init__(self, responses, headers=None):
        super().__init__(responses)
        self._headers = dict(headers or {})
        self.headers_seen: list[dict] = []

    @property
    def request_headers(self) -> dict:
        return dict(self._headers)

    @request_headers.setter
    def request_headers(self, value: dict) -> None:
        self._headers = dict(value)

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None, **kwargs):
        self.headers_seen.append(dict(self._headers))
        return super().complete(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, **kwargs,
        )


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args, env):
        return ToolResult(output=f"Echo: {args['message']}")


class DangerousTool(BaseTool):
    name = "dangerous_delete"
    description = "Should never run in these tests"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self) -> None:
        self.executed = False

    def execute(self, args, env):
        self.executed = True
        return ToolResult(output="deleted everything")


class RecordingPolicy(PermissionPolicy):
    """Allow-all policy that records every evaluation it is asked for."""

    def __init__(self) -> None:
        self.evaluations: list[tuple[str, dict]] = []

    def evaluate(self, tool_name, args):
        self.evaluations.append((tool_name, dict(args)))
        return PermissionAction.ALLOW


def _collect_events(bus: EventBus) -> list:
    seen: list = []
    bus.subscribe("interceptor", seen.append)
    return seen


def _yolo(**kwargs) -> LoopConfig:
    """LoopConfig without the default Interactive permission policy."""
    return LoopConfig(yolo_mode=True, **kwargs)


# ---------------------------------------------------------------------------
# Decision type
# ---------------------------------------------------------------------------


def test_decision_factories():
    allow = InterceptDecision.allow()
    assert allow.kind == "allow"

    rep = InterceptDecision.replace({"x": 1})
    assert rep.kind == "replace"
    assert rep.value == {"x": 1}

    blk = InterceptDecision.block("nope")
    assert blk.kind == "block"
    assert blk.reason == "nope"


# ---------------------------------------------------------------------------
# Chain mechanics
# ---------------------------------------------------------------------------


def test_composition_order_and_replacement_chaining():
    """Interceptors run in list order; each sees the previous replacement."""
    tc = ToolCall(id="t1", name="echo", arguments={"message": "start"})

    def first(call):
        assert call.arguments["message"] == "start"
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments={"message": "start+a"})
        )

    def second(call):
        assert call.arguments["message"] == "start+a"  # sees first's replacement
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments={"message": "start+a+b"})
        )

    effective, block = intercept_tool_call([first, second], tc)
    assert block is None
    assert effective.arguments["message"] == "start+a+b"


def test_first_block_wins():
    """The first block stops the chain; later interceptors never run."""
    tc = ToolCall(id="t1", name="echo", arguments={})
    ran: list[str] = []

    def blocker_one(call):
        ran.append("one")
        return InterceptDecision.block("first reason")

    def blocker_two(call):
        ran.append("two")
        return InterceptDecision.block("second reason")

    _, block = intercept_tool_call([blocker_one, blocker_two], tc)
    assert block == "first reason"
    assert ran == ["one"]


def test_none_return_treated_as_allow():
    tc = ToolCall(id="t1", name="echo", arguments={"message": "hi"})
    effective, block = intercept_tool_call([lambda call: None], tc)
    assert block is None
    assert effective is tc


def test_tool_call_fail_closed_on_exception():
    """tool_call is a gate: a raising interceptor blocks the call."""
    tc = ToolCall(id="t1", name="echo", arguments={})

    def broken(call):
        raise RuntimeError("plugin bug")

    _, block = intercept_tool_call([broken], tc)
    assert block is not None
    assert "interceptor error" in block
    assert "plugin bug" in block


def test_tool_call_fail_closed_on_wrong_replacement_type():
    tc = ToolCall(id="t1", name="echo", arguments={})
    _, block = intercept_tool_call([lambda call: InterceptDecision.replace("junk")], tc)
    assert block is not None
    assert "interceptor error" in block


def test_tool_call_replacement_preserves_id():
    """The call id is load-bearing; replacements cannot change it."""
    tc = ToolCall(id="original-id", name="echo", arguments={})

    def rename(call):
        return InterceptDecision.replace(
            ToolCall(id="hijacked-id", name="other_tool", arguments={"k": 1})
        )

    effective, block = intercept_tool_call([rename], tc)
    assert block is None
    assert effective.id == "original-id"
    assert effective.name == "other_tool"


def test_tool_result_fail_open_on_exception():
    """Mutating seams fail open: a raising interceptor is skipped."""
    tc = ToolCall(id="t1", name="echo", arguments={})
    tr = ToolResult(output="original")

    def broken(call, result):
        raise RuntimeError("boom")

    def patcher(call, result):
        return InterceptDecision.replace(ToolResult(output=result.output + "+patched"))

    out = intercept_tool_result([broken, patcher], tc, tr)
    assert out.output == "original+patched"  # broken skipped, chain continued


def test_tool_result_block_withholds_output():
    tc = ToolCall(id="t1", name="echo", arguments={})
    tr = ToolResult(output="the secret sauce")

    out = intercept_tool_result(
        [lambda call, result: InterceptDecision.block("contains secrets")], tc, tr,
    )
    assert out.success is True
    assert "withheld by interceptor" in out.output
    assert "contains secrets" in out.output
    assert "secret sauce" not in out.output


def test_context_fail_open_on_exception():
    msgs = [Message.user("hi")]

    def broken(messages):
        raise ValueError("nope")

    out, block = intercept_context([broken], msgs)
    assert block is None
    assert out is msgs


def test_provider_request_fail_open_on_wrong_type():
    req = ProviderRequest(model="m", messages=[Message.user("hi")])
    out, block = intercept_provider_request(
        [lambda r: InterceptDecision.replace("not a request")], req,
    )
    assert block is None
    assert out is req


def test_error_events_emitted_on_failure():
    """Interceptor errors are observable on the bus (both policies)."""
    bus = EventBus()
    seen = _collect_events(bus)
    tc = ToolCall(id="t1", name="echo", arguments={})

    def broken(call):
        raise RuntimeError("bug")

    intercept_tool_call([broken], tc, event_bus=bus)
    intercept_tool_result(
        [lambda call, result: (_ for _ in ()).throw(RuntimeError("bug2"))],
        tc, ToolResult(output="x"), event_bus=bus,
    )
    decisions = [(e.seam, e.decision) for e in seen]
    assert ("tool_call", "error") in decisions
    assert ("tool_result", "error") in decisions


# ---------------------------------------------------------------------------
# None-config pin: byte-identical behavior
# ---------------------------------------------------------------------------


def _run_scenario(config: LoopConfig):
    provider = RecordingProvider([
        Response(
            content="calling",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hello"})],
            usage={"input_tokens": 10, "output_tokens": 5},
        ),
        Response(content="done", tool_calls=[], usage={"input_tokens": 12, "output_tokens": 3}),
    ])
    context = Context(system="You are a test.")
    context.add(Message.user("go"))
    result = ReAct(max_steps=5, config=config).run(provider, [EchoTool()], context, env=None)
    return provider.calls, [(m.role, m.content) for m in context.messages], result


def test_none_and_empty_interceptors_byte_identical():
    """No interceptors (None) and empty Interceptors() leave everything
    the provider saw, the context transcript, and the result identical."""
    calls_none, ctx_none, res_none = _run_scenario(_yolo())
    calls_empty, ctx_empty, res_empty = _run_scenario(_yolo(interceptors=Interceptors()))

    assert calls_none == calls_empty
    assert ctx_none == ctx_empty
    assert repr(res_none) == repr(res_empty)


def test_empty_interceptors_emit_no_events():
    bus = EventBus()
    seen = _collect_events(bus)
    _run_scenario(_yolo(event_bus=bus, secrets_redaction=False, interceptors=Interceptors()))
    assert seen == []


# ---------------------------------------------------------------------------
# Ordering vs permissions (pin)
# ---------------------------------------------------------------------------


def test_permission_policy_sees_interceptor_mutated_args():
    """Interceptors run BEFORE the permission check, so the policy
    evaluates the arguments that will actually execute."""
    policy = RecordingPolicy()

    def mutate(call):
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments={"message": "MUTATED"})
        )

    config = LoopConfig(
        permissions=policy,
        interceptors=Interceptors(tool_call=[mutate]),
    )
    provider = RecordingProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "original"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ])
    context = Context()
    context.add(Message.user("go"))
    ReAct(max_steps=5, config=config).run(provider, [EchoTool()], context, env=None)

    assert policy.evaluations == [("echo", {"message": "MUTATED"})]
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any("Echo: MUTATED" in m.content for m in tool_msgs)


def test_interceptor_block_short_circuits_permission_check():
    """A blocked call never reaches the permission policy and surfaces
    as a denial-with-reason."""
    policy = RecordingPolicy()
    bus = EventBus()
    seen = _collect_events(bus)

    config = LoopConfig(
        permissions=policy,
        event_bus=bus,
        secrets_redaction=False,
        interceptors=Interceptors(
            tool_call=[lambda call: InterceptDecision.block("policy says no")],
        ),
    )
    provider = RecordingProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "x"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ])
    context = Context()
    context.add(Message.user("go"))
    result = ReAct(max_steps=5, config=config).run(provider, [EchoTool()], context, env=None)

    assert policy.evaluations == []  # never consulted
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any("Blocked by interceptor: policy says no" in m.content for m in tool_msgs)
    assert result.success is True  # the run continues past the denial
    blocked = [e for e in seen if e.decision == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].seam == "tool_call"
    assert blocked[0].tool_name == "echo"
    assert blocked[0].reason == "policy says no"


# ---------------------------------------------------------------------------
# Seams through the real loop (public config only)
# ---------------------------------------------------------------------------


def test_tool_result_patched_before_entering_context():
    def patch(call, result):
        return InterceptDecision.replace(
            ToolResult(output=result.output.replace("hello", "[REDACTED]"))
        )

    config = _yolo(interceptors=Interceptors(tool_result=[patch]))
    provider = RecordingProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hello"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ])
    context = Context()
    context.add(Message.user("go"))
    ReAct(max_steps=5, config=config).run(provider, [EchoTool()], context, env=None)

    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].content == "Echo: [REDACTED]"


def test_context_rewrite_is_ephemeral():
    """The provider sees the rewritten list; the durable Context does not."""
    def redact(messages):
        return InterceptDecision.replace([
            Message(role=m.role, content=m.content.replace("SECRETVALUE", "***"),
                    tool_calls=m.tool_calls, call_id=m.call_id)
            for m in messages
        ])

    config = _yolo(interceptors=Interceptors(context=[redact]))
    provider = RecordingProvider([
        Response(content="done", tool_calls=[], usage={}),
    ])
    context = Context()
    context.add(Message.user("the password is SECRETVALUE"))
    ReAct(max_steps=5, config=config).run(provider, [], context, env=None)

    sent = provider.calls[0]["messages"]
    assert any("***" in content for _, content in sent)
    assert all("SECRETVALUE" not in content for _, content in sent)
    # Durable context is untouched.
    assert any("SECRETVALUE" in m.content for m in context.messages)


def test_provider_request_block_ends_run_with_reason():
    config = _yolo(interceptors=Interceptors(
        provider_request=[lambda req: InterceptDecision.block("no requests today")],
    ))
    provider = RecordingProvider([Response(content="never", tool_calls=[], usage={})])
    context = Context()
    context.add(Message.user("go"))
    result = ReAct(max_steps=5, config=config).run(provider, [], context, env=None)

    assert result.success is False
    assert result.error == "Blocked by interceptor: no requests today"
    assert provider.calls == []  # the request never went out


def test_provider_request_header_redaction_applies_and_restores():
    original_headers = {"Authorization": "Bearer sk-live", "X-Extra": "keep"}

    def redact_header(req):
        assert req.headers is not None
        new_headers = dict(req.headers)
        new_headers["Authorization"] = "[redacted]"
        return InterceptDecision.replace(ProviderRequest(
            model=req.model, messages=req.messages, tools=req.tools,
            kwargs=req.kwargs, headers=new_headers,
        ))

    config = _yolo(interceptors=Interceptors(provider_request=[redact_header]))
    provider = HeaderedProvider(
        [Response(content="done", tool_calls=[], usage={})],
        headers=original_headers,
    )
    context = Context()
    context.add(Message.user("go"))
    ReAct(max_steps=5, config=config).run(provider, [], context, env=None)

    # The call itself saw the redacted header...
    assert provider.headers_seen == [
        {"Authorization": "[redacted]", "X-Extra": "keep"},
    ]
    # ...and the provider's headers were restored afterwards (per-call).
    assert provider.request_headers == original_headers


def test_provider_request_headers_none_when_unsupported():
    """Providers without request_headers see headers=None (honest scope)."""
    seen_headers: list = []

    def observe(req):
        seen_headers.append(req.headers)
        return InterceptDecision.allow()

    config = _yolo(interceptors=Interceptors(provider_request=[observe]))
    provider = RecordingProvider([Response(content="done", tool_calls=[], usage={})])
    context = Context()
    context.add(Message.user("go"))
    ReAct(max_steps=5, config=config).run(provider, [], context, env=None)
    assert seen_headers == [None]


def test_provider_request_kwargs_passthrough():
    def set_temperature(req):
        return InterceptDecision.replace(ProviderRequest(
            model=req.model, messages=req.messages, tools=req.tools,
            kwargs={"temperature": 0.7}, headers=req.headers,
        ))

    config = _yolo(interceptors=Interceptors(provider_request=[set_temperature]))
    provider = RecordingProvider([Response(content="done", tool_calls=[], usage={})])
    context = Context()
    context.add(Message.user("go"))
    ReAct(max_steps=5, config=config).run(provider, [], context, env=None)
    assert provider.calls[0]["temperature"] == 0.7


# ---------------------------------------------------------------------------
# The proof plugin: all four seams through public config only
# ---------------------------------------------------------------------------


class RedactionPolicyPlugin:
    """A user-space plugin wiring all four seams — no core changes.

    - context: scrubs the marker string from every outgoing message.
    - provider_request: redacts the Authorization header.
    - tool_call: blocks any tool whose name starts with ``dangerous_``.
    - tool_result: stamps every tool output with a policy marker.
    """

    MARKER = "TOPSECRET-42"

    def interceptors(self) -> Interceptors:
        return Interceptors(
            context=[self._scrub_context],
            provider_request=[self._redact_header],
            tool_call=[self._gate_tools],
            tool_result=[self._stamp_result],
        )

    def _scrub_context(self, messages):
        return InterceptDecision.replace([
            Message(role=m.role, content=m.content.replace(self.MARKER, "[scrubbed]"),
                    tool_calls=m.tool_calls, call_id=m.call_id)
            for m in messages
        ])

    def _redact_header(self, req):
        if req.headers is None:
            return InterceptDecision.allow()
        headers = dict(req.headers)
        if "Authorization" in headers:
            headers["Authorization"] = "[redacted]"
        return InterceptDecision.replace(ProviderRequest(
            model=req.model, messages=req.messages, tools=req.tools,
            kwargs=req.kwargs, headers=headers,
        ))

    def _gate_tools(self, call):
        if call.name.startswith("dangerous_"):
            return InterceptDecision.block(f"tool {call.name} is gated by policy")
        return InterceptDecision.allow()

    def _stamp_result(self, call, result):
        if not result.success:
            return InterceptDecision.allow()
        return InterceptDecision.replace(
            ToolResult(output=result.output + " [policy-checked]")
        )


def test_proof_plugin_all_four_seams_through_public_config():
    plugin = RedactionPolicyPlugin()
    bus = EventBus()
    seen = _collect_events(bus)
    config = _yolo(
        event_bus=bus, secrets_redaction=False,
        interceptors=plugin.interceptors(),
    )

    dangerous = DangerousTool()
    provider = HeaderedProvider(
        [
            Response(
                content="acting",
                tool_calls=[
                    ToolCall(id="tc1", name="dangerous_delete", arguments={}),
                    ToolCall(id="tc2", name="echo", arguments={"message": "ok"}),
                ],
                usage={},
            ),
            Response(content="all done", tool_calls=[], usage={}),
        ],
        headers={"Authorization": "Bearer sk-live", "X-Title": "chimera"},
    )
    context = Context(system="You are a test.")
    context.add(Message.user(f"the launch code is {plugin.MARKER}"))

    result = ReAct(max_steps=5, config=config).run(
        provider, [EchoTool(), dangerous], context, env=None,
    )
    assert result.success is True

    # (d) context rewrite: the marker never reached the provider...
    for call in provider.calls:
        assert all(plugin.MARKER not in content for _, content in call["messages"])
    assert any("[scrubbed]" in content for _, content in provider.calls[0]["messages"])
    # ...but the durable context still holds the original.
    assert any(plugin.MARKER in m.content for m in context.messages)

    # (a) provider request: header redacted on the wire, restored after.
    assert all(h["Authorization"] == "[redacted]" for h in provider.headers_seen)
    assert provider.request_headers["Authorization"] == "Bearer sk-live"

    # (b) tool call: the dangerous tool never executed; denial-with-reason
    # entered the conversation.
    assert dangerous.executed is False
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any(
        "Blocked by interceptor: tool dangerous_delete is gated by policy" in m.content
        for m in tool_msgs
    )

    # (c) tool result: the echo output was patched before entering context.
    assert any("Echo: ok [policy-checked]" in m.content for m in tool_msgs)

    # Observational events recorded the decisions.
    decisions = {(e.seam, e.decision) for e in seen}
    assert ("tool_call", "blocked") in decisions
    assert ("tool_result", "replaced") in decisions
    assert ("context", "replaced") in decisions
    assert ("provider_request", "replaced") in decisions


# ---------------------------------------------------------------------------
# Async paths: async_iter_steps + the async tool executor
# ---------------------------------------------------------------------------


class AsyncRecordingProvider(RecordingProvider):
    async def async_complete(self, messages, tools=None, **kwargs):
        return self.complete(messages, tools=tools, **kwargs)


@pytest.mark.asyncio
async def test_async_loop_honors_tool_seams():
    from chimera.core.loop import async_drain_steps

    def gate(call):
        if call.name == "dangerous_delete":
            return InterceptDecision.block("gated")
        return InterceptDecision.allow()

    def stamp(call, result):
        return InterceptDecision.replace(ToolResult(output=result.output + "!"))

    config = _yolo(interceptors=Interceptors(tool_call=[gate], tool_result=[stamp]))
    dangerous = DangerousTool()
    provider = AsyncRecordingProvider([
        Response(
            content="",
            tool_calls=[
                ToolCall(id="tc1", name="dangerous_delete", arguments={}),
                ToolCall(id="tc2", name="echo", arguments={"message": "hi"}),
            ],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ])
    context = Context()
    context.add(Message.user("go"))
    loop = ReAct(max_steps=5, config=config)
    result = await async_drain_steps(
        loop.async_iter_steps(provider, [EchoTool(), dangerous], context, env=None)
    )

    assert result.success is True
    assert dangerous.executed is False
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any("Blocked by interceptor: gated" in m.content for m in tool_msgs)
    assert any(m.content == "Echo: hi!" for m in tool_msgs)


@pytest.mark.asyncio
async def test_async_loop_provider_request_block():
    config = _yolo(interceptors=Interceptors(
        provider_request=[lambda req: InterceptDecision.block("halt")],
    ))
    provider = AsyncRecordingProvider([Response(content="x", tool_calls=[], usage={})])
    context = Context()
    context.add(Message.user("go"))
    loop = ReAct(max_steps=5, config=config)

    from chimera.core.loop import async_drain_steps
    result = await async_drain_steps(
        loop.async_iter_steps(provider, [], context, env=None)
    )
    assert result.success is False
    assert result.error == "Blocked by interceptor: halt"
    assert provider.calls == []


# ---------------------------------------------------------------------------
# Assembled path: AgentLoop + CodingAgent/AgentDriver additive kwarg
# ---------------------------------------------------------------------------


class AgentLoopProvider:
    """Minimal duck-typed provider for AgentLoop (async_complete only)."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.model_name = "mock"
        self.calls: list[list] = []

    async def async_complete(self, messages, tools=None, **kwargs):
        self.calls.append([(m.role, m.content) for m in messages])
        return next(self._responses)


class AsyncEchoTool(EchoTool):
    is_concurrency_safe = True

    async def async_execute(self, args, env):
        return self.execute(args, env)


class AsyncDangerousTool(DangerousTool):
    is_concurrency_safe = True

    async def async_execute(self, args, env):
        return self.execute(args, env)


@pytest.mark.asyncio
async def test_agent_loop_honors_all_seams():
    from chimera.core.agent_loop import AgentLoop
    from chimera.core.loop_events import LoopEventType

    plugin = RedactionPolicyPlugin()
    dangerous = AsyncDangerousTool()
    provider = AgentLoopProvider([
        Response(
            content="",
            tool_calls=[
                ToolCall(id="tc1", name="dangerous_delete", arguments={}),
                ToolCall(id="tc2", name="echo", arguments={"message": "ok"}),
            ],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ])

    events = []
    async for ev in AgentLoop().run(
        [Message.user(f"code {plugin.MARKER}")],
        tools=[AsyncEchoTool(), dangerous],
        provider=provider,
        system_prompt="test",
        max_turns=5,
        interceptors=plugin.interceptors(),
    ):
        events.append(ev)

    # (d) context scrubbed on the wire.
    for call in provider.calls:
        assert all(plugin.MARKER not in content for _, content in call)

    # (b) blocked tool surfaced as denial-with-reason; never executed.
    assert dangerous.executed is False
    tool_results = [ev.data for ev in events if ev.type == LoopEventType.tool_result]
    blocked = [r for tc, r in tool_results if tc.name == "dangerous_delete"]
    assert blocked and "Blocked by interceptor" in (blocked[0].error or "")

    # (c) echo result patched.
    patched = [r for tc, r in tool_results if tc.name == "echo"]
    assert patched and patched[0].output == "Echo: ok [policy-checked]"


@pytest.mark.asyncio
async def test_agent_loop_provider_request_block_reason():
    from chimera.core.agent_loop import AgentLoop
    from chimera.core.loop_events import LoopEventType

    provider = AgentLoopProvider([Response(content="x", tool_calls=[], usage={})])
    results = []
    async for ev in AgentLoop().run(
        [Message.user("go")],
        tools=[],
        provider=provider,
        system_prompt="test",
        max_turns=3,
        interceptors=Interceptors(
            provider_request=[lambda req: InterceptDecision.block("sealed")],
        ),
    ):
        if ev.type == LoopEventType.result:
            results.append(ev.data)

    assert provider.calls == []
    assert results and results[0].reason == "interceptor_blocked: sealed"


def test_coding_agent_accepts_interceptors_kwarg(tmp_path):
    """The assembled path threads interceptors without touching core."""
    from chimera.assembly.coding_agent import CodingAgent

    ic = Interceptors(tool_call=[lambda call: InterceptDecision.allow()])
    agent = CodingAgent(
        provider=AgentLoopProvider([]),
        project_dir=str(tmp_path),
        interceptors=ic,
    )
    assert agent._interceptors is ic


def test_agent_driver_forwards_interceptors_kwarg(tmp_path):
    from chimera.assembly.driver import AgentDriver

    ic = Interceptors()
    driver = AgentDriver(
        project_dir=str(tmp_path),
        provider=AgentLoopProvider([]),
        interceptors=ic,
    )
    assert driver.agent._interceptors is ic
