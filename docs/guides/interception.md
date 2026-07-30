---
title: "Interception Seams & Policy Packs"
description: "Typed, decision-capable hooks that block, mutate, and rewrite at the four load-bearing points of an agent turn — carried by plugins onto every assembled agent, and proven by the bundled policy packs."
---

Chimera's event bus is **observational**: subscribers watch, they cannot
change what happens. Interception seams are the **decision** counterpart —
small synchronous callables that can *block*, *mutate*, or *rewrite* a value
at the four load-bearing points of a turn. Sub-agents, plan gates, payload
redaction, and tool policy are plugin territory here, not core features:
plugins carry interceptor chains onto every assembled agent, and Chimera
ships three bundled policy packs (`chimera.plugins.packs`) that do exactly
those things through the seams — worked, tested through the real loop, and
loadable by name. Batteries on, as everywhere else in the platform.

Everything is configured through public config: a typed
`Interceptors` dataclass hanging off `LoopConfig` (and threaded through
`CodingAgent` / `AgentDriver` / `chimera.AgentSession` for the assembled
stack), plus a plugin registry that merges chains in for you.

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
- **Across sources — plugins before host**: when chains come from more
  than one place, the merge order is defined below.

## Plugins carry interceptors

A plugin does not need the host to thread anything: it registers its
chains, and every assembled agent (`CodingAgent`, `AgentDriver`,
`chimera.AgentSession`) picks them up on its next turn.

```python
from chimera.core.interception import InterceptDecision
from chimera.plugins import BasePlugin, PluginExtensionRegistry, PluginManager

class NoDeletePlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "no-delete"

    def register_interceptors(self, registry) -> None:
        PluginExtensionRegistry.register_interceptor("tool_call", self._gate)

    def deactivate(self) -> None:
        PluginExtensionRegistry.unregister_interceptor("tool_call", self._gate)

    def _gate(self, tc):
        if "rm -rf" in str(tc.arguments):
            return InterceptDecision.block("no-delete: recursive removal is gated")
        return None

PluginManager().load_plugin(NoDeletePlugin())
# Done. Every assembled agent in this process now enforces the gate —
# no LoopConfig, no constructor kwargs, no host code beyond the load.
```

The registry surface mirrors the other plugin registries:
`register_interceptor(seam, fn)` and `unregister_interceptor(seam, fn)`
(call the latter from `deactivate()` so unloading a plugin withdraws its
chains), `get_interceptors(seam)`, and `get_all_interceptors()` — with
seam names validated against the four seams, so a typo raises instead of
registering a chain that can never fire. When a plugin loads through
`PluginManager`, activation-time registrations are additionally
attributed to the plugin instance: an `activate()` that raises after
registering part of its chains is rolled back (a failed load leaves the
registry exactly as it was), and `unload` withdraws by owner anything
`deactivate()` forgot — a chain can never outlive its plugin.

### The merge contract (pinned by test)

Per seam, the effective chain on an assembled agent is:

1. **plugin-registered interceptors first**, in registration order
   (load order across plugins, list order within one);
2. **host-supplied interceptors last** — the `interceptors=` passed to
   `CodingAgent` / `AgentDriver` / `AgentSession`.

The ordinary chain semantics then apply, which gives the host final say:
the host's interceptors see the *plugin-effective* value (every plugin
replacement has already happened), and a `block` from either side is
terminal — nothing can un-block, so a host block can never be undone by a
plugin, and a plugin gate fires before the host chain is even consulted.

Guarantees, all pinned by tests:

- No plugins registered and no host chains → the loop receives `None`,
  byte-identical to today.
- Host chains only → the host's `Interceptors` object passes through
  untouched (the same object, not a copy).
- The merge is read at the start of every turn, so a plugin loaded — or
  unloaded — between turns simply takes effect on the next one.

`merge_interceptors(*bundles)` in `chimera.core.interception` is the
underlying pure function, for anyone composing bundles by hand.

## The bundled policy packs

Three shipped, importable, tested plugins under `chimera.plugins.packs` —
loadable by instance or by entry-point name:

```python
from chimera.plugins import PluginManager
from chimera.plugins.packs import PlanGatePlugin, RedactorPlugin

manager = PluginManager()
manager.load_plugin(RedactorPlugin(pattern=r"acme-[0-9a-f]{32}"))
manager.load("plan-gate")        # entry-point name, default configuration
```

Every pack also exposes `interceptors()` for host-side use without the
plugin system: `CodingAgent(interceptors=pack.interceptors())`.

### plan-gate

Blocks write / edit / shell tool calls (`write_file`, `edit_file`,
`replace_in_file`, `apply_patch`, `bash` by default) until the agent has
recorded a plan, and tells the model exactly how to unblock itself. The
honest heuristic: "a plan exists" means the model has **issued** a call to
a planning tool (`think` or `todo` by default) since the most recent user
message — issuing is enough (the gate opens on the `tool_call` seam,
before execution, so it works even where no planning tool is installed);
the pack does not read the plan or judge its quality. A `context`-seam
watcher re-arms the gate on every new user message, including mid-run
steering. Gate state is **per conversation**: the seams carry no session
id, so the pack keys state by execution lane — the (thread, asyncio
task) pair the loop runs on — and re-derives each lane's truth from the
message list itself before every provider call. Concurrent agents in one
process (multiplexer lanes on one event loop, REPL turns on worker
threads, strategy-loop bridge threads, sequential thread reuse) get
independent gates; a lane with no recorded state fails closed (armed) —
pinned in both failure directions through real concurrent loops. Limits:
a host that hand-interleaves two conversations' streams inside a single
asyncio task shares a lane for at most one step (no shipped runner does
this); a compaction that rewrites away this turn's planning call re-arms
the gate (fails closed, never open); and tool names match exactly as the
loop dispatches them — namespaced variants need explicit configuration.

### redactor

Scrubs a configurable secret pattern (`pattern=`, `replacement=`) from
the provider request — message contents, `TextContent` blocks (a
multimodal message duplicates its text into one), tool-call arguments
riding them, and request headers — on the `provider_request` seam, and
from tool outputs and error text on the `tool_result` seam. Headers
named in `headers=` (`Authorization` by default, case-insensitive) are
replaced wholesale. The wire scrub is **ephemeral** (the durable
conversation keeps its originals); the tool-result scrub is **durable**
(the transcript records the scrubbed output) — both sides are pinned
through the real loop. Limits: header redaction reaches only providers
exposing a `request_headers` surface; text only — exactly what passes
through unscrubbed is `ImageContent` blocks (bytes and media type),
content-block types other than `TextContent`, and `ToolResult.metadata`;
both seams are fail-open by the seam contract, so pair it with the
events-side `RedactionMiddleware` for defense in depth. An invalid
pattern raises at construction.

### delegate-spawner

Sub-agents as plugin policy: rewrites matching tool calls (the `spawn_`
prefix and/or exact `names=`) into calls to the `delegate` tool on the
`tool_call` seam — same call id, one `task` argument taken from the
original call's `task`/`prompt` or rendered from its arguments — so the
loop dispatches a sub-agent instead, with core untouched. Limit: the
host's tool set must actually include a delegate tool
(`chimera.tools.delegate.DelegateTool`, shipped outside the default
interactive set — add it via `extra_tools=`); without one the rewritten
call surfaces as a loud `Unknown tool` error, never a silent drop.

### Hot-swap

Hot-swap — editing a pack's source and swapping the new policy into a
live process — is arriving. The pieces are in place (`PluginManager.reload`
re-imports and re-activates a plugin from fresh source, and the per-turn
merge picks up whatever is registered), and the end-to-end story will
ship once it is pinned the way everything above is.

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

## Worked example 1: payload + header redaction, inline

The redactor pack does this off the shelf; here is the same policy
written by hand, for when you want a one-off without a plugin:

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

## Worked example 2: tool-gating policy, inline

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
core — and composes with whatever loaded plugins registered (plugin
chains first, yours last):

```python
from chimera.assembly.driver import AgentDriver

driver = AgentDriver(model="glm-5.2", interceptors=interceptors)
```

`CodingAgent(..., interceptors=...)` and `chimera.AgentSession` accept it
the same way. When the policy is worth keeping, promote it into a plugin
(see "Plugins carry interceptors" above) and it rides along with every
agent the process assembles — the plan-gate pack began as exactly this
kind of gate.

## Per-loop coverage

The loops with provider-call sites of their own — `ReAct`, `AgentLoop`
(the `chimera code` default), and the three swappable strategy loops —
seam by seam. A "yes" cell is enforced through the real loop by a test;
the one scoped cell and the resumed-approval carve-out below are *also*
pinned by tests proving the uncovered calls are genuinely inert — a
claimed gap here is documented and tested, never assumed.

| Seam | `ReAct` (sync + async) | `AgentLoop` (`chimera code` default) | `PlanAndExecute` | `Reflexion` | `TreeOfThought` |
| --- | --- | --- | --- | --- | --- |
| `context` | yes | yes | yes | yes | conversation calls only¹ |
| `provider_request` | yes | yes | yes | yes | conversation calls only¹ |
| `tool_call` | yes | yes | yes | yes | yes |
| `tool_result` | yes | yes | yes | yes | yes |

¹ `TreeOfThought` makes two kinds of provider calls. Its
candidate-generation calls send the conversation and run both
pre-provider seams — the envelope carries the candidates'
`temperature=0.7`, so an envelope interceptor decides over what is
actually sent. Its **internal candidate-evaluation call** sends a
synthetic evaluator prompt instead of the conversation and is **not**
intercepted: conversation-shaped context interceptors (e.g. a watcher
that re-arms a gate on each new user message) would misread it. Pinned
inert by test.

The strategy loops route every conversation provider call through one
shared enforcement site (`intercepted_complete` in
`chimera.core.interception`), so a pre-provider block ends the run with
`"Blocked by interceptor: <reason>"` in all three, exactly as in `ReAct`.

Pins: `tests/core/test_interception.py` (`ReAct`, `AgentLoop`),
`tests/core/test_loops_interception.py` (the three strategy loops — every
supported cell, the inert-evaluator pin, and a byte-identical
no-interceptors pin per loop), `tests/assembly/test_loop_adapter.py` and
`tests/assembly/test_plugin_interceptors.py` (the assembled lanes below).

The remaining loop classes compose the loops above rather than adding
provider-call sites of their own. `PlanActLoop` runs its two phases on
inner `ReAct` loops built with the same config, so its conversation calls
carry the `ReAct` column; `AutonomousLoop` executes each sub-task on an
inner `ReAct` with the same config, while its internal planning and
replanning calls send synthetic prompts and are not intercepted — the
same class of internal call as `TreeOfThought`'s evaluator. `RetryLoop`
and `LintFeedbackLoop` wrap a caller-supplied inner loop (default: a
config-less `ReAct`), so their coverage is exactly their inner loop's —
construct the inner loop with the config that carries your chains.

### Strategy-loop lanes

The assembled path reaches the strategy loops through
`chimera/assembly/loop_adapter.py` — a lane's `:plan-execute` /
`:reflexion` / `:tot`, and `CodingAgent(loop=...)`. Lanes receive the
**same merged plugin+host chains** as the default `AgentLoop` path:
`CodingAgent._effective_interceptors()` is the one merge site, and the
adapter never merges. Loading a policy pack therefore gates every lane
regardless of which reasoning loop it runs. Two adapter facts, both
pinned: with no interceptors the loop is built config-free
(byte-identical to before the seam existed), and the config that carries
the chains carries *only* the chains — the lanes' documented
no-permission-checks posture (see the TUI guide) is unchanged. Lanes run
without an event bus, so interceptor decisions surface through denial
text and run results there, as on the `AgentLoop` path.

## Scope and contract notes

- **Sync-only.** Interceptors are plain callables; coroutines are not
  awaited (the seams also run inside synchronous executors). Do fast,
  in-memory work — the same posture as `PermissionPolicy.evaluate`.
- **Coverage.** See the per-loop coverage table above: the shared tool
  executors carry the tool seams into every loop, the per-loop
  provider-call sites carry the pre-provider seams, and the
  plugin-registry merge rides the whole assembled path — the default
  `AgentLoop` and the strategy-loop lanes alike.
- **`kwargs` passthrough.** `ProviderRequest.kwargs` is passed verbatim
  to the provider's `complete` / `stream` call (e.g.
  `{"temperature": 0.7}`); keys must be accepted by the provider's
  signature. On the strategy loops the envelope is seeded with the extra
  arguments the call already carries (`TreeOfThought`'s candidate
  `temperature=0.7`), so replacing `kwargs` replaces what is actually
  sent.
- **Resumed approvals.** Tool calls re-executed after an interactive
  approval resume run with the executor's config detached (the existing
  approval contract) and skip all config-driven checks, interceptors
  included. This carve-out is shared by `ReAct` and the three strategy
  loops.
