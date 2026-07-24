---
title: "Interception Seams"
description: "Typed, decision-capable hooks that block, mutate, and rewrite at the four load-bearing points of an agent turn — provider request, tool call, tool result, and outgoing context — through public LoopConfig configuration."
---

Chimera's event bus is **observational**: subscribers watch, they cannot
change what happens. Interception seams are the **decision** counterpart —
small synchronous callables that can *block*, *mutate*, or *rewrite* a value
at the four load-bearing points of a turn. They make sub-agents, plan gates,
payload redaction, and tool policy implementable as user-space plugins
instead of core features.

Everything is configured through public config: a typed
`Interceptors` dataclass hanging off `LoopConfig` (and threaded through
`CodingAgent` / `AgentDriver` for the assembled stack).

```python
from chimera.core import Interceptors, InterceptDecision, LoopConfig
```

## The four seams

| Seam | Fires | Sees | `replace(...)` takes | `block(reason)` means | On exception |
| --- | --- | --- | --- | --- | --- |
| `context` | before each provider call | the message list about to be sent (`list[Message]`) | a new `list[Message]` | the provider call does not happen; the run ends with the reason | fail-open |
| `provider_request` | before each provider call, after `context` | the full envelope: `ProviderRequest(model, messages, tools, kwargs, headers)` | a new `ProviderRequest` | the provider call does not happen; the run ends with the reason | fail-open |
| `tool_call` | before each tool executes | the proposed `ToolCall` | a new `ToolCall` (the original `id` is always preserved) | the call is denied with the reason — it surfaces in the conversation like a permission denial | **fail-closed** |
| `tool_result` | after a tool executes, before its result enters the conversation | `(ToolCall, ToolResult)` | a new `ToolResult` | the output is withheld — the model sees a placeholder naming the reason | fail-open |

Each interceptor is a plain synchronous callable returning an
`InterceptDecision` (or `None`, treated as allow):

```python
InterceptDecision.allow()            # pass through unchanged
InterceptDecision.replace(value)     # substitute (seam-specific type)
InterceptDecision.block("reason")    # stop, with a human-readable reason
```

Signatures per seam:

```python
def on_context(messages: list[Message]) -> InterceptDecision | None: ...
def on_request(req: ProviderRequest) -> InterceptDecision | None: ...
def on_tool_call(tc: ToolCall) -> InterceptDecision | None: ...
def on_tool_result(tc: ToolCall, result: ToolResult) -> InterceptDecision | None: ...

config = LoopConfig(interceptors=Interceptors(
    context=[on_context],
    provider_request=[on_request],
    tool_call=[on_tool_call],
    tool_result=[on_tool_result],
))
```

`interceptors=None` (the default) — or an empty `Interceptors()` — leaves
loop behavior byte-identical; this is pinned by a test.

## Ordering guarantees

- **Within a seam**: interceptors run in list order. The **first block
  wins** and stops the chain. **Replacements chain** — each later
  interceptor sees the previous replacement.
- **`tool_call` runs before the permission check** (and before PreToolUse
  hooks). Rationale: every downstream security decision must evaluate the
  *interceptor-effective* call. If interceptors ran after the permission
  check, a replacement could rewrite arguments the policy never saw,
  turning mutation into a permission bypass. Running first also means a
  block short-circuits cheaply — no ASK prompt fires for a call that was
  never going to run. A blocked call surfaces exactly like a denial:
  `"Blocked by interceptor: <reason>"` in the conversation, an error
  `ToolResult`, and an observational event.
- **Before a provider call**: middleware `before_model` (durable) →
  `context` seam (ephemeral) → `provider_request` seam → the wire.
  The `context` rewrite shapes only what is sent for that call; the
  durable `Context` object is untouched. Durable history mutation stays
  with compaction strategies and `LoopMiddleware`.
- **`tool_result` runs on executed tools only** — synthetic denial
  messages never pass through it — and before truncation, hooks, and
  events, so every downstream consumer sees the effective result.

## Failure policy (per seam)

- `tool_call` is **fail-closed**: an interceptor that raises blocks the
  call with `"interceptor error: ..."`. It is a gate; a crashing gate must
  not wave calls through.
- `context`, `provider_request`, and `tool_result` are **fail-open**: a
  raising interceptor is skipped, the last good value proceeds, and the
  error is reported observationally. These seams shape data; a buggy
  formatter should degrade to a no-op, not kill the run mid-turn. A
  guarantee that must never fail open belongs in an explicit `block()`
  decision — honored on every seam — not in an exception path.

A replacement of the wrong type counts as an interceptor error and follows
the same per-seam policy.

## Observability

Every block / replace / error decision emits an `InterceptorEvent`
(`type="interceptor"`) on the loop's event bus, carrying `seam`,
`decision` (`"blocked"` / `"replaced"` / `"error"`), `reason`,
`tool_name`, `call_id`, and the interceptor's name — so a TUI or audit
trail can show "tool X blocked by interceptor: reason" without sitting on
the decision path. On the `AgentLoop` path (which has no event bus),
decisions surface through the denial text and the run-result reason
(`"interceptor_blocked: ..."`).

## Worked example 1: payload + header redaction

Scrub a marker from every outgoing message and redact the
`Authorization` header before the request leaves the process:

```python
from chimera.core import Interceptors, InterceptDecision, LoopConfig, ProviderRequest
from chimera.types import Message

MARKER = "TOPSECRET-42"

def scrub_context(messages):
    return InterceptDecision.replace([
        Message(role=m.role, content=m.content.replace(MARKER, "[scrubbed]"),
                tool_calls=m.tool_calls, call_id=m.call_id)
        for m in messages
    ])

def redact_header(req: ProviderRequest):
    if req.headers is None:          # transport has no header surface
        return InterceptDecision.allow()
    headers = {**req.headers, "Authorization": "[redacted]"}
    return InterceptDecision.replace(ProviderRequest(
        model=req.model, messages=req.messages, tools=req.tools,
        kwargs=req.kwargs, headers=headers,
    ))

config = LoopConfig(interceptors=Interceptors(
    context=[scrub_context],
    provider_request=[redact_header],
))
```

Header scope (honest): `ProviderRequest.headers` is populated only for
providers exposing a `request_headers` property —
`OpenAICompatibleProvider` today. Replaced headers apply **for that call
only** and the originals are restored afterwards, even if the request
raises. Providers whose headers live inside an SDK client (e.g. the
Anthropic provider's client-level `default_headers`) see `headers=None`;
replacing them is a no-op there.

## Worked example 2: tool-gating policy

Block any tool matching a name pattern, surfaced as a denial with a
reason — the model sees why and can route around it:

```python
from chimera.core import Interceptors, InterceptDecision

def gate_tools(tc):
    if tc.name.startswith("dangerous_"):
        return InterceptDecision.block(f"tool {tc.name} is gated by policy")
    return InterceptDecision.allow()

interceptors = Interceptors(tool_call=[gate_tools])
```

On the assembled stack, the same object threads through without touching
core:

```python
from chimera.assembly.driver import AgentDriver

driver = AgentDriver(model="glm-5.2", interceptors=interceptors)
```

`CodingAgent(..., interceptors=...)` accepts it directly as well. A plugin
is just an object that builds an `Interceptors` instance (see
`RedactionPolicyPlugin` in `tests/core/test_interception.py` for a
four-seam example verified through the real loop); the embedder passes it
to `LoopConfig` or the assembled constructors.

## Scope and contract notes

- **Sync-only.** Interceptors are plain callables; coroutines are not
  awaited (the seams also run inside synchronous executors). Do fast,
  in-memory work — the same posture as `PermissionPolicy.evaluate`.
- **Coverage.** The `tool_call` / `tool_result` seams fire in every loop
  that funnels tools through the shared executors: `ReAct` (sync and
  async), `PlanAndExecute`, `Reflexion`, `TreeOfThought`, and `AgentLoop`
  (the `chimera code` path). The `context` / `provider_request` seams
  fire at the `ReAct` and `AgentLoop` provider-call sites; strategy loops
  own their provider calls and are not yet covered there.
- **`kwargs` passthrough.** `ProviderRequest.kwargs` is passed verbatim
  to the provider's `complete` / `stream` call (e.g.
  `{"temperature": 0.7}`); keys must be accepted by the provider's
  signature.
- **Resumed approvals.** Tool calls re-executed after an interactive
  approval resume run with the executor's config detached (the existing
  approval contract) and skip all config-driven checks, interceptors
  included.
