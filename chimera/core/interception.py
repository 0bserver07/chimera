"""Typed, decision-capable interception seams for the agent loop.

Interceptors are the loop's *decision* seams: small synchronous callables
that can **block**, **mutate**, or **rewrite** a value at the four
load-bearing points of a turn, configured through
:attr:`~chimera.core.loop_config.LoopConfig.interceptors`:

- ``provider_request`` — the full request envelope (model, messages, tools,
  kwargs, headers) before it is sent to the provider.
- ``tool_call`` — a tool call before execution (block it, or mutate args).
- ``tool_result`` — a tool result before it enters the conversation
  (patch or withhold the output).
- ``context`` — the message list about to be sent to the provider
  (ephemeral rewrite; the durable :class:`~chimera.core.context.Context`
  is untouched).

This is deliberately distinct from :mod:`chimera.events`: the
:class:`~chimera.events.base.EventBus` is **observational** — subscribers
watch, they cannot change what happens.  Interceptors **decide** — their
return value determines what the loop does next.  Every block / replace /
error decision is *also* reported observationally through an
:class:`~chimera.events.types.InterceptorEvent` so UIs and audit trails
can show what happened.

Contract
--------
Each interceptor is a plain synchronous callable returning an
:class:`InterceptDecision` (or ``None``, treated as allow).  Interceptors
run **in list order**; the first ``block`` wins and stops the chain;
``replace`` decisions chain (each later interceptor sees the previous
replacement).  Coroutines are *not* awaited — these seams also run inside
synchronous executors, so the contract is sync-only by design.  Do fast,
in-memory work here (the same posture as
:meth:`~chimera.permissions.base.PermissionPolicy.evaluate`).

Ordering vs. permissions (tool_call seam)
-----------------------------------------
``tool_call`` interceptors run **before** the PreToolUse hook and
**before** the permission policy.  Rationale: every downstream security
decision must evaluate the *interceptor-effective* call — if interceptors
ran after the permission check, a replacement could rewrite arguments the
policy never saw, turning mutation into a permission bypass.  Running
first means a block short-circuits cheaply (no ASK prompt for a call that
was never going to run) and a mutation is still fully vetted by hooks,
permissions, and discipline guards.

Failure policy (per seam)
-------------------------
- ``tool_call`` — **fail-closed**: an interceptor that raises blocks the
  call with reason ``"interceptor error: ..."``.  This seam is a gate; a
  crashing gate must not wave calls through.
- ``provider_request`` / ``tool_result`` / ``context`` — **fail-open**:
  an interceptor that raises is skipped, the last good value proceeds,
  and the error is reported via :class:`InterceptorEvent`.  These seams
  shape data; a buggy formatter should degrade to a no-op, not kill the
  run mid-turn.  A guarantee that must never fail open belongs in an
  explicit ``block()`` decision (honored on every seam), not in an
  exception path.

A replacement of the wrong type counts as an interceptor error and
follows the same per-seam policy.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.types import Message, ToolCall, ToolResult

if TYPE_CHECKING:
    from chimera.events.base import EventBus

__all__ = [
    "ContextInterceptor",
    "InterceptDecision",
    "Interceptors",
    "PreProviderOutcome",
    "ProviderRequest",
    "ProviderRequestInterceptor",
    "ToolCallInterceptor",
    "ToolResultInterceptor",
    "apply_pre_provider_seams",
    "intercept_context",
    "intercept_provider_request",
    "intercept_tool_call",
    "intercept_tool_result",
    "merge_interceptors",
]


@dataclass(frozen=True)
class InterceptDecision:
    """The outcome of one interceptor: allow, replace the value, or block.

    Construct via the three factories — never by hand — so the ``kind``
    field stays one of ``"allow"`` / ``"replace"`` / ``"block"``:

    - :meth:`allow` — pass the value through unchanged.
    - :meth:`replace` — substitute a new value; later interceptors in the
      chain see the replacement.
    - :meth:`block` — stop with a human-readable reason.  On the
      ``tool_call`` seam this surfaces as a denial-with-reason; on the
      ``provider_request`` / ``context`` seams it aborts the provider
      call; on the ``tool_result`` seam it withholds the output (the
      model sees a placeholder naming the reason instead).

    Attributes:
        kind: Decision discriminator (``"allow"`` | ``"replace"`` |
            ``"block"``).
        value: Replacement payload for ``kind="replace"``; type is
            seam-specific (see the ``*Interceptor`` aliases).
        reason: Human-readable reason for ``kind="block"``.
    """

    kind: str
    value: Any = None
    reason: str = ""

    @staticmethod
    def allow() -> "InterceptDecision":
        """Pass the value through unchanged."""
        return InterceptDecision(kind="allow")

    @staticmethod
    def replace(value: Any) -> "InterceptDecision":
        """Substitute *value* for the intercepted value (chains in order)."""
        return InterceptDecision(kind="replace", value=value)

    @staticmethod
    def block(reason: str) -> "InterceptDecision":
        """Stop the chain with *reason* (first block wins)."""
        return InterceptDecision(kind="block", reason=reason)


@dataclass
class ProviderRequest:
    """The request envelope a ``provider_request`` interceptor sees.

    Attributes:
        model: The provider's model identifier (informational — replacing
            it does not swap the provider).
        messages: The messages about to be sent (already past the
            ``context`` seam).
        tools: Tool schemas offered to the model, or ``None``.
        kwargs: Extra keyword arguments passed verbatim to the provider's
            ``complete`` / ``stream`` call (e.g. ``temperature``).  Keys
            must be accepted by the provider's signature.
        headers: Per-request HTTP headers when the provider transport
            supports header injection (currently providers exposing a
            ``request_headers`` property, e.g.
            :class:`~chimera.providers.compatible.OpenAICompatibleProvider`);
            ``None`` when headers are not reachable for this provider —
            replacing them is then a no-op.
    """

    model: str
    messages: list[Message]
    tools: list[dict[str, Any]] | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] | None = None


#: ``(request) -> decision`` — replace value must be a :class:`ProviderRequest`.
ProviderRequestInterceptor = Callable[[ProviderRequest], "InterceptDecision | None"]
#: ``(tool_call) -> decision`` — replace value must be a
#: :class:`~chimera.types.ToolCall`; the original ``id`` is always preserved.
ToolCallInterceptor = Callable[[ToolCall], "InterceptDecision | None"]
#: ``(tool_call, tool_result) -> decision`` — replace value must be a
#: :class:`~chimera.types.ToolResult`.
ToolResultInterceptor = Callable[[ToolCall, ToolResult], "InterceptDecision | None"]
#: ``(messages) -> decision`` — replace value must be a ``list[Message]``.
ContextInterceptor = Callable[[list[Message]], "InterceptDecision | None"]


@dataclass
class Interceptors:
    """Ordered interceptor chains for the four loop seams.

    Hangs off :attr:`chimera.core.loop_config.LoopConfig.interceptors`;
    ``None`` (or an all-empty instance) leaves loop behavior byte-identical.

    Attributes:
        provider_request: Chain for the request envelope before each
            provider call.
        tool_call: Chain for each tool call before execution (runs before
            hooks and the permission check — see module docstring).
        tool_result: Chain for each executed tool's result before it
            enters the conversation.
        context: Chain for the message list before each provider call
            (ephemeral — the durable context is untouched).
    """

    provider_request: list[ProviderRequestInterceptor] = field(default_factory=list)
    tool_call: list[ToolCallInterceptor] = field(default_factory=list)
    tool_result: list[ToolResultInterceptor] = field(default_factory=list)
    context: list[ContextInterceptor] = field(default_factory=list)


def _bundle_is_empty(bundle: "Interceptors") -> bool:
    """True when *bundle* carries no interceptor on any seam."""
    return not (
        bundle.provider_request
        or bundle.tool_call
        or bundle.tool_result
        or bundle.context
    )


def merge_interceptors(*bundles: "Interceptors | None") -> "Interceptors | None":
    """Merge interceptor bundles into one chain per seam, in argument order.

    The composition seam for hosts that accept interceptors from more than
    one source — e.g. chains registered by loaded plugins plus the host's
    own configuration.  Pass the bundles in the order their chains should
    run: with ``merge_interceptors(plugin_bundle, host_bundle)`` every
    plugin interceptor runs before every host interceptor on each seam,
    and the ordinary chain contract does the rest — first ``block`` wins
    (a block from either side is terminal; nothing can un-block),
    ``replace`` decisions chain (the later bundle sees the earlier
    bundle's replacements), and the per-seam failure policy is unchanged.

    Identity guarantees (pinned by test):

    - Every input ``None`` or empty → ``None``, so the no-interceptors
      configuration stays byte-identical.
    - Exactly one non-empty input → that exact object, unmodified — an
      existing single-source configuration passes through untouched when
      nothing else contributes.
    - Otherwise → a new :class:`Interceptors`; the inputs are never
      mutated.

    Args:
        *bundles: Interceptor bundles in the order their chains should
            run.  ``None`` and all-empty entries are skipped.

    Returns:
        The merged bundle, or ``None`` when no input carries any
        interceptor.
    """
    contributing = [
        b for b in bundles if b is not None and not _bundle_is_empty(b)
    ]
    if not contributing:
        return None
    if len(contributing) == 1:
        return contributing[0]
    return Interceptors(
        provider_request=[fn for b in contributing for fn in b.provider_request],
        tool_call=[fn for b in contributing for fn in b.tool_call],
        tool_result=[fn for b in contributing for fn in b.tool_result],
        context=[fn for b in contributing for fn in b.context],
    )


def _name_of(fn: Callable[..., Any]) -> str:
    """Best-effort display name for an interceptor callable."""
    return getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)


def _emit(
    event_bus: "EventBus | None",
    *,
    seam: str,
    decision: str,
    reason: str = "",
    tool_name: str = "",
    call_id: str = "",
    interceptor: str = "",
) -> None:
    """Publish an observational :class:`InterceptorEvent`, never raising."""
    if event_bus is None:
        return
    try:
        from chimera.events.types import InterceptorEvent

        event_bus.publish(
            InterceptorEvent(
                seam=seam,
                decision=decision,
                reason=reason,
                tool_name=tool_name,
                call_id=call_id,
                interceptor=interceptor,
            )
        )
    except Exception:
        # Observation must never break the decision path.
        pass


def _run_chain(
    interceptors: Sequence[Callable[..., "InterceptDecision | None"]],
    value: Any,
    *,
    seam: str,
    expected_type: type | tuple[type, ...],
    fail_open: bool,
    event_bus: "EventBus | None",
    leading_args: tuple[Any, ...] = (),
    tool_name: str = "",
    call_id: str = "",
) -> tuple[Any, str | None]:
    """Run one seam's interceptor chain over *value*.

    Args:
        interceptors: Callables run in list order.
        value: The initial value; ``replace`` decisions substitute it.
        seam: Seam name for events (``"tool_call"``, ...).
        expected_type: Required type of a replacement value.
        fail_open: ``True`` → a raising interceptor is skipped;
            ``False`` → a raising interceptor blocks (fail-closed).
        event_bus: Optional bus for observational events.
        leading_args: Fixed positional args passed before *value*
            (e.g. the originating :class:`~chimera.types.ToolCall` on the
            ``tool_result`` seam).
        tool_name: Tool name for event payloads (tool seams).
        call_id: Tool call id for event payloads (tool seams).

    Returns:
        ``(final_value, block_reason)`` — *block_reason* is ``None``
        unless a block won (first block wins; on fail-closed seams an
        interceptor error is a block).
    """
    for fn in interceptors:
        name = _name_of(fn)
        try:
            decision = fn(*leading_args, value)
        except Exception as exc:
            _emit(
                event_bus, seam=seam, decision="error",
                reason=f"{name}: {exc}", tool_name=tool_name, call_id=call_id,
                interceptor=name,
            )
            if fail_open:
                continue
            return value, f"interceptor error: {exc}"
        if decision is None or decision.kind == "allow":
            continue
        if decision.kind == "block":
            _emit(
                event_bus, seam=seam, decision="blocked",
                reason=decision.reason, tool_name=tool_name, call_id=call_id,
                interceptor=name,
            )
            return value, decision.reason or "blocked by interceptor"
        if decision.kind == "replace":
            if not isinstance(decision.value, expected_type):
                bad = (
                    f"{name}: replace value must be "
                    f"{getattr(expected_type, '__name__', expected_type)}, "
                    f"got {type(decision.value).__name__}"
                )
                _emit(
                    event_bus, seam=seam, decision="error", reason=bad,
                    tool_name=tool_name, call_id=call_id, interceptor=name,
                )
                if fail_open:
                    continue
                return value, f"interceptor error: {bad}"
            value = decision.value
            _emit(
                event_bus, seam=seam, decision="replaced",
                tool_name=tool_name, call_id=call_id, interceptor=name,
            )
            continue
        # Unknown kind — treat as an interceptor error under the seam policy.
        _emit(
            event_bus, seam=seam, decision="error",
            reason=f"{name}: unknown decision kind {decision.kind!r}",
            tool_name=tool_name, call_id=call_id, interceptor=name,
        )
        if not fail_open:
            return value, f"interceptor error: unknown decision kind {decision.kind!r}"
    return value, None


def intercept_tool_call(
    interceptors: Sequence[ToolCallInterceptor],
    tc: ToolCall,
    *,
    event_bus: "EventBus | None" = None,
) -> tuple[ToolCall, str | None]:
    """Run the ``tool_call`` seam (fail-closed) over *tc*.

    The original call ``id`` is always preserved on replacements — it is
    load-bearing for matching results back to calls.  Replacements may
    change the tool ``name`` and ``arguments``.

    Args:
        interceptors: The chain, in order.
        tc: The tool call the model proposed.
        event_bus: Optional bus for observational events.

    Returns:
        ``(effective_call, block_reason)``; *block_reason* non-``None``
        means the call must not execute and should surface as a
        denial-with-reason.
    """
    effective, block_reason = _run_chain(
        interceptors, tc,
        seam="tool_call", expected_type=ToolCall, fail_open=False,
        event_bus=event_bus, tool_name=tc.name, call_id=tc.id,
    )
    if effective is not tc and effective.id != tc.id:
        effective = ToolCall(id=tc.id, name=effective.name, arguments=effective.arguments)
    return effective, block_reason


def intercept_tool_result(
    interceptors: Sequence[ToolResultInterceptor],
    tc: ToolCall,
    result: ToolResult,
    *,
    event_bus: "EventBus | None" = None,
) -> ToolResult:
    """Run the ``tool_result`` seam (fail-open) over *result*.

    A ``block(reason)`` decision withholds the output: the returned
    result carries a placeholder naming the reason, so the model learns
    the result was withheld rather than that the tool failed.

    Args:
        interceptors: The chain, in order.
        tc: The originating tool call (context for the interceptor).
        result: The result the tool produced.
        event_bus: Optional bus for observational events.

    Returns:
        The effective :class:`~chimera.types.ToolResult` to feed into the
        conversation.
    """
    effective, block_reason = _run_chain(
        interceptors, result,
        seam="tool_result", expected_type=ToolResult, fail_open=True,
        event_bus=event_bus, leading_args=(tc,),
        tool_name=tc.name, call_id=tc.id,
    )
    if block_reason is not None:
        return ToolResult(
            output=f"[tool result withheld by interceptor: {block_reason}]",
        )
    if isinstance(effective, ToolResult):
        return effective
    return result  # unreachable in practice: _run_chain enforces the type


def intercept_context(
    interceptors: Sequence[ContextInterceptor],
    messages: list[Message],
    *,
    event_bus: "EventBus | None" = None,
) -> tuple[list[Message], str | None]:
    """Run the ``context`` seam (fail-open) over *messages*.

    The rewrite is ephemeral: it shapes what is sent to the provider for
    this call only; the durable :class:`~chimera.core.context.Context` is
    untouched.  (Durable mutation belongs to compaction strategies and
    :class:`~chimera.core.middleware.LoopMiddleware`.)

    Args:
        interceptors: The chain, in order.
        messages: The message list about to be sent.
        event_bus: Optional bus for observational events.

    Returns:
        ``(effective_messages, block_reason)``; *block_reason*
        non-``None`` means the provider call must not happen.
    """
    return _run_chain(
        interceptors, messages,
        seam="context", expected_type=list, fail_open=True,
        event_bus=event_bus,
    )


def intercept_provider_request(
    interceptors: Sequence[ProviderRequestInterceptor],
    request: ProviderRequest,
    *,
    event_bus: "EventBus | None" = None,
) -> tuple[ProviderRequest, str | None]:
    """Run the ``provider_request`` seam (fail-open) over *request*.

    Args:
        interceptors: The chain, in order.
        request: The request envelope about to be sent.
        event_bus: Optional bus for observational events.

    Returns:
        ``(effective_request, block_reason)``; *block_reason*
        non-``None`` means the provider call must not happen.
    """
    return _run_chain(
        interceptors, request,
        seam="provider_request", expected_type=ProviderRequest, fail_open=True,
        event_bus=event_bus,
    )


@dataclass
class PreProviderOutcome:
    """Combined outcome of the ``context`` + ``provider_request`` seams.

    Produced by :func:`apply_pre_provider_seams` so every provider-call
    site applies the two pre-call seams identically.

    Attributes:
        messages: The effective messages to send.
        tools: The effective tool schemas (or ``None``).
        kwargs: Extra provider-call kwargs (passed verbatim).
        block_reason: Non-``None`` when a block won — the provider call
            must not happen.
        header_snapshot: The provider's original headers when an
            interceptor changed them — the caller must restore these
            (``provider.request_headers = header_snapshot``) after the
            call, in a ``finally`` block, so header mutation is per-call.
    """

    messages: list[Message]
    tools: list[dict[str, Any]] | None
    kwargs: dict[str, Any] = field(default_factory=dict)
    block_reason: str | None = None
    header_snapshot: dict[str, str] | None = None


def apply_pre_provider_seams(
    interceptors: "Interceptors | None",
    provider: Any,
    messages: list[Message],
    tools: list[dict[str, Any]] | None,
    *,
    event_bus: "EventBus | None" = None,
) -> PreProviderOutcome:
    """Apply the ``context`` then ``provider_request`` seams for one call.

    Header handling: when the provider exposes a ``request_headers``
    property (a ``dict[str, str]``), the request envelope carries a copy;
    if an interceptor replaces the headers, they are applied to the
    provider for this call and the original snapshot is returned for the
    caller to restore in a ``finally``.  Providers without
    ``request_headers`` see ``headers=None`` (documented as unreachable).

    Args:
        interceptors: The configured chains, or ``None`` (no-op).
        provider: The provider about to be called.
        messages: The message list about to be sent.
        tools: The tool schemas about to be offered.
        event_bus: Optional bus for observational events.

    Returns:
        A :class:`PreProviderOutcome`; when *interceptors* is ``None`` or
        both chains are empty, it echoes the inputs untouched.
    """
    outcome = PreProviderOutcome(messages=messages, tools=tools)
    if interceptors is None:
        return outcome
    if not interceptors.context and not interceptors.provider_request:
        return outcome

    if interceptors.context:
        outcome.messages, outcome.block_reason = intercept_context(
            interceptors.context, outcome.messages, event_bus=event_bus,
        )
        if outcome.block_reason is not None:
            return outcome

    if interceptors.provider_request:
        raw_headers = getattr(provider, "request_headers", None)
        request = ProviderRequest(
            model=str(getattr(provider, "model_name", "")),
            messages=outcome.messages,
            tools=outcome.tools,
            kwargs=dict(outcome.kwargs),
            headers=dict(raw_headers) if isinstance(raw_headers, dict) else None,
        )
        request, outcome.block_reason = intercept_provider_request(
            interceptors.provider_request, request, event_bus=event_bus,
        )
        if outcome.block_reason is not None:
            return outcome
        outcome.messages = request.messages
        outcome.tools = request.tools
        outcome.kwargs = dict(request.kwargs)
        if (
            isinstance(raw_headers, dict)
            and request.headers is not None
            and request.headers != raw_headers
        ):
            try:
                provider.request_headers = dict(request.headers)
                outcome.header_snapshot = dict(raw_headers)
            except AttributeError:
                # Read-only header surface — scope honestly: no injection.
                pass

    return outcome
