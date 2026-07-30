"""Interception seams through the three strategy loops — real loop objects.

Pins the per-loop coverage table in ``docs/guides/interception.md``:

- every claimed-supported seam (``context`` / ``provider_request`` /
  ``tool_call`` / ``tool_result``) has an enforcement test through the real
  ``PlanAndExecute`` / ``Reflexion`` / ``TreeOfThought`` loop;
- the one claimed-unsupported site — ``TreeOfThought``'s internal
  candidate-evaluation call — has a test proving the seams are inert there;
- the no-interceptors configuration is byte-identical for all three loops
  (``config=None`` vs a config carrying no / empty ``Interceptors``).

Providers are scripted; nothing in the loops is mocked.
"""
from __future__ import annotations

import pytest

from chimera.core.context import Context
from chimera.core.interception import (
    InterceptDecision,
    Interceptors,
    ProviderRequest,
)
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.providers.base import Response
from chimera.types import Message, ToolCall, ToolResult
from tests.core.test_interception import (
    DangerousTool,
    EchoTool,
    HeaderedProvider,
    RecordingProvider,
    _yolo,
)

# ---------------------------------------------------------------------------
# Scenario plumbing
# ---------------------------------------------------------------------------


def _tc_resp(name: str = "echo", arguments: dict | None = None) -> Response:
    return Response(
        content="calling a tool",
        tool_calls=[ToolCall(id="tc1", name=name, arguments=arguments or {"message": "hello"})],
        usage={},
    )


def _text(content: str = "done") -> Response:
    return Response(content=content, tool_calls=[], usage={})


def _make_loop(name: str, config):
    if name == "plan-execute":
        return PlanAndExecute(max_steps=6, config=config)
    if name == "reflexion":
        return Reflexion(max_steps=6, config=config)
    return TreeOfThought(max_steps=6, n_candidates=2, config=config)


def _tool_script(name: str, tool_resp: Response) -> list[Response]:
    """A script that reaches exactly one tool call, then finishes."""
    if name == "tot":
        # Two candidate calls per step (n_candidates=2); step 2's identical
        # text candidates skip the evaluator and end the run.
        return [tool_resp, tool_resp, _text(), _text()]
    return [tool_resp, _text()]


def _text_script(name: str) -> list[Response]:
    """A script for a tool-less run that ends on its first step."""
    if name == "tot":
        return [_text(), _text()]  # identical candidates: no evaluator call
    return [_text()]


LOOP_NAMES = ["plan-execute", "reflexion", "tot"]


def _fresh_context() -> Context:
    context = Context(system="You are helpful")
    context.add(Message.user("go"))
    return context


# ---------------------------------------------------------------------------
# tool_call seam: a plan-gate-style block is enforced (per loop)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_tool_call_block_enforced(name):
    """A tool_call block stops execution and surfaces as a denial-with-reason."""
    tool = DangerousTool()

    def gate(call: ToolCall):
        if call.name == "dangerous_delete":
            return InterceptDecision.block("writes are gated until a plan exists")
        return None

    provider = RecordingProvider(_tool_script(name, _tc_resp(name="dangerous_delete", arguments={})))
    loop = _make_loop(name, _yolo(interceptors=Interceptors(tool_call=[gate])))
    context = _fresh_context()

    result = loop.run(provider, [tool], context, None)

    assert tool.executed is False
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any(
        "Blocked by interceptor: writes are gated until a plan exists" in m.content
        for m in tool_msgs
    )
    assert result.success is True  # the run continues past the denial


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_tool_call_block_control_without_interceptors(name):
    """Falsifiability control: the identical run with no interceptors executes."""
    tool = DangerousTool()
    provider = RecordingProvider(_tool_script(name, _tc_resp(name="dangerous_delete", arguments={})))
    loop = _make_loop(name, None)
    result = loop.run(provider, [tool], _fresh_context(), None)

    assert tool.executed is True
    assert result.success is True


# ---------------------------------------------------------------------------
# tool_result seam: a patch lands in the conversation (per loop)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_tool_result_patched_before_entering_context(name):
    def patch(call: ToolCall, result: ToolResult):
        return InterceptDecision.replace(
            ToolResult(output=result.output.replace("hello", "[REDACTED]"))
        )

    provider = RecordingProvider(_tool_script(name, _tc_resp()))
    loop = _make_loop(name, _yolo(interceptors=Interceptors(tool_result=[patch])))
    context = _fresh_context()

    loop.run(provider, [EchoTool()], context, None)

    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].content == "Echo: [REDACTED]"


# ---------------------------------------------------------------------------
# context seam: ephemeral rewrite + block (per loop)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_context_rewrite_shapes_wire_but_not_durable_context(name):
    def redact(messages):
        return InterceptDecision.replace([
            Message(role=m.role, content=m.content.replace("SECRETVALUE", "***"),
                    tool_calls=m.tool_calls, call_id=m.call_id)
            for m in messages
        ])

    provider = RecordingProvider(_text_script(name))
    loop = _make_loop(name, _yolo(interceptors=Interceptors(context=[redact])))
    context = Context(system="You are helpful")
    context.add(Message.user("the token is SECRETVALUE"))

    result = loop.run(provider, [], context, None)

    assert result.success is True
    assert provider.calls, "the provider was called"
    for call in provider.calls:
        assert all("SECRETVALUE" not in content for _role, content in call["messages"])
        assert any("***" in content for _role, content in call["messages"])
    # Durable context untouched (the rewrite is ephemeral).
    assert any("SECRETVALUE" in m.content for m in context.messages)


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_context_block_ends_run_before_provider_call(name):
    provider = RecordingProvider(_text_script(name))
    loop = _make_loop(
        name,
        _yolo(interceptors=Interceptors(
            context=[lambda messages: InterceptDecision.block("context policy says no")],
        )),
    )

    result = loop.run(provider, [], _fresh_context(), None)

    assert provider.calls == []  # the provider was never reached
    assert result.success is False
    assert result.error == "Blocked by interceptor: context policy says no"
    assert result.output == "Blocked by interceptor: context policy says no"


# ---------------------------------------------------------------------------
# provider_request seam: envelope replacement + block (per loop)
# ---------------------------------------------------------------------------


def _set_temperature(req: ProviderRequest):
    kwargs = dict(req.kwargs)
    kwargs["temperature"] = 0.25
    return InterceptDecision.replace(ProviderRequest(
        model=req.model, messages=req.messages, tools=req.tools,
        kwargs=kwargs, headers=req.headers,
    ))


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_provider_request_replacement_reaches_the_wire(name):
    provider = RecordingProvider(_text_script(name))
    loop = _make_loop(
        name, _yolo(interceptors=Interceptors(provider_request=[_set_temperature])),
    )

    result = loop.run(provider, [], _fresh_context(), None)

    assert result.success is True
    assert provider.calls
    for call in provider.calls:
        assert call["temperature"] == 0.25


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_provider_request_block_ends_run_before_provider_call(name):
    provider = RecordingProvider(_text_script(name))
    loop = _make_loop(
        name,
        _yolo(interceptors=Interceptors(
            provider_request=[lambda req: InterceptDecision.block("no wire today")],
        )),
    )

    result = loop.run(provider, [], _fresh_context(), None)

    assert provider.calls == []
    assert result.success is False
    assert result.error == "Blocked by interceptor: no wire today"


def test_header_injection_applies_per_call_and_restores():
    """Header replacement through the shared strategy-loop call site is
    per-call: applied during the request, restored afterwards."""
    def redact_header(req: ProviderRequest):
        if req.headers is None:
            return None
        return InterceptDecision.replace(ProviderRequest(
            model=req.model, messages=req.messages, tools=req.tools,
            kwargs=req.kwargs, headers={**req.headers, "Authorization": "[redacted]"},
        ))

    provider = HeaderedProvider([_text()], headers={"Authorization": "Bearer real"})
    loop = PlanAndExecute(
        max_steps=3,
        config=_yolo(interceptors=Interceptors(provider_request=[redact_header])),
    )
    result = loop.run(provider, [], _fresh_context(), None)

    assert result.success is True
    assert provider.headers_seen == [{"Authorization": "[redacted]"}]  # during the call
    assert provider.request_headers == {"Authorization": "Bearer real"}  # restored after


# ---------------------------------------------------------------------------
# TreeOfThought scoping: envelope honesty + the inert internal evaluator call
# ---------------------------------------------------------------------------


def test_tot_envelope_carries_candidate_temperature():
    """The candidate calls' temperature=0.7 is visible in the envelope, so
    an interceptor decides over what is actually sent."""
    seen_kwargs: list[dict] = []

    def watch(req: ProviderRequest):
        seen_kwargs.append(dict(req.kwargs))
        return None

    provider = RecordingProvider([_text(), _text()])
    loop = TreeOfThought(
        max_steps=3, n_candidates=2,
        config=_yolo(interceptors=Interceptors(provider_request=[watch])),
    )
    loop.run(provider, [], _fresh_context(), None)

    assert seen_kwargs == [{"temperature": 0.7}, {"temperature": 0.7}]


def test_tot_internal_evaluator_call_is_not_intercepted():
    """Claimed-unsupported pin: the candidate-evaluation call bypasses the
    pre-provider seams (documented in docs/guides/interception.md)."""
    seen_context: list[list] = []
    seen_requests: list[list] = []

    def watch_ctx(messages):
        seen_context.append([(m.role, m.content) for m in messages])
        return None

    def watch_req(req: ProviderRequest):
        seen_requests.append([(m.role, m.content) for m in req.messages])
        return None

    provider = RecordingProvider([
        _text("Candidate answer A"),
        _text("Candidate answer B"),
        _text("1"),  # the evaluator's pick
    ])
    loop = TreeOfThought(
        max_steps=3, n_candidates=2,
        config=_yolo(interceptors=Interceptors(
            context=[watch_ctx], provider_request=[watch_req],
        )),
    )
    result = loop.run(provider, [], _fresh_context(), None)

    assert result.success is True
    assert result.output == "Candidate answer A"
    assert len(provider.calls) == 3  # 2 candidate calls + 1 evaluator call
    # The seams fired only for the 2 candidate calls...
    assert len(seen_context) == 2
    assert len(seen_requests) == 2
    # ...and never saw the synthetic evaluator prompt.
    for snapshot in seen_context + seen_requests:
        assert all("evaluator" not in content.lower() for _role, content in snapshot)


# ---------------------------------------------------------------------------
# Byte-identical pin: no interceptors configured = unchanged behavior
# ---------------------------------------------------------------------------


def _run_scenario(name: str, config):
    provider = RecordingProvider(_tool_script(name, _tc_resp()))
    loop = _make_loop(name, config)
    context = _fresh_context()
    result = loop.run(provider, [EchoTool()], context, None)
    transcript = [(m.role, m.content) for m in context.messages]
    return provider.calls, transcript, repr(result)


@pytest.mark.parametrize("name", LOOP_NAMES)
def test_none_config_byte_identical(name):
    """config=None, a config with interceptors=None, and a config with an
    empty Interceptors() leave the provider calls, the transcript, and the
    result identical — the seam plumbing is invisible until configured."""
    runs = [
        _run_scenario(name, None),
        _run_scenario(name, _yolo()),
        _run_scenario(name, _yolo(interceptors=Interceptors())),
    ]
    for other in runs[1:]:
        assert other == runs[0]
