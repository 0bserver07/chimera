---
title: "Hooks and Extensions"
description: "In-process extension seams — subscribe to lifecycle events, veto tool calls, hot-reload plugins, and register your own provider and auth backend."
---

Chimera exposes several in-process seams for extending the agent without
forking it: a lifecycle hook emitter you can subscribe to, plugin hot-reload,
and runtime registries for providers and auth backends. None of them require a
restart or a code change to the core.

---

## Subscribing to lifecycle events

`HookEmitter` (in `chimera/hooks/emitter.py`) is the ergonomic, in-process
registration surface for the hook lifecycle. Call `.on(event, callback)` to
subscribe and `.off(subscription_id)` to unsubscribe:

```python
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent

emitter = HookEmitter()

def log_tool(hook_input):
    print(f"tool starting: {hook_input.tool_name}")

sub_id = emitter.on(HookEvent.PRE_TOOL_USE, log_tool)
# ... later ...
emitter.off(sub_id)   # returns True if the subscription was found
```

The callback receives the full `HookInput` for that emission, so it can read
`tool_name`, `tool_input`, `tool_output`, `tool_error`, `user_prompt`, and
`messages` directly. It may be synchronous or `async`. If the emitter has no
executor yet, one is created lazily, so `HookEmitter().on(...)` works out of
the box. Exceptions raised by a callback are swallowed by the executor, so a
bad subscriber never breaks the loop.

`.on()` accepts two keyword-only options:

- `matcher` — an fnmatch pattern constraining which `tool_name` values fire the
  callback (`None`, the default, matches all).
- `timeout` — per-callback timeout in seconds (default `5`).

---

## The event catalog

Events are the `HookEvent` enum. The pairs that bracket each turn and each tool
call are the ones you will reach for most:

| Event | Fires |
|---|---|
| `PRE_TURN` / `POST_TURN` | Immediately before / after each model call. |
| `PRE_TOOL_USE` / `POST_TOOL_USE` | Before a tool runs / after it succeeds. |
| `POST_TOOL_USE_FAILURE` | After a tool raises. |
| `SESSION_START` / `SESSION_END` | Session lifecycle boundaries. |
| `USER_PROMPT_SUBMIT` | A user prompt is submitted. |
| `STOP` / `STOP_FAILURE` | The loop finishes / fails to finish. |
| `PRE_COMPACT` / `POST_COMPACT` | Around a context compaction. |
| `SUBAGENT_START` / `SUBAGENT_STOP` | Around a spawned subagent. |

The two turn boundaries are also exported together as
`TURN_LIFECYCLE_EVENTS` so you can subscribe to both in one loop:

```python
from chimera.hooks.emitter import TURN_LIFECYCLE_EVENTS

for ev in TURN_LIFECYCLE_EVENTS:   # (PRE_TURN, POST_TURN)
    emitter.on(ev, my_callback)
```

The full enum also covers permission, task, elicitation, worktree, config, and
file-change events — see `chimera/hooks/events.py`.

---

## Vetoing an action

A `PRE_TOOL_USE` subscriber can **halt the tool call** by returning
`HookOutput(continue_execution=False)`. Any other return value is a no-op, and
the executor short-circuits the dispatch chain as soon as one hook vetoes:

```python
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.hook_types import HookOutput

emitter = HookEmitter()

def block_destructive_rm(hook_input):
    cmd = str((hook_input.tool_input or {}).get("command", ""))
    if hook_input.tool_name == "bash" and "rm -rf" in cmd:
        return HookOutput(continue_execution=False, reason="blocked destructive rm")
    # returning nothing = allow

emitter.on(HookEvent.PRE_TOOL_USE, block_destructive_rm)

out = emitter.emit_sync(
    HookEvent.PRE_TOOL_USE,
    tool_name="bash",
    tool_input={"command": "rm -rf /"},
)
assert out.continue_execution is False
```

`HookOutput` carries more than the veto flag — `system_message` surfaces text
in the transcript, `additional_context` appends to the tool result,
`updated_input` shallow-merges over the tool input, and `permission_decision`
(`"allow"` | `"deny"` | `"ask"` | `"defer"`) drives permission flows.

---

## The global emitter

Some emit sites live outside the agent-loop call graph (config loaders, the
worktree tool, rules ingest). For those, install a process-wide emitter:

```python
from chimera.hooks.emitter import HookEmitter, set_global_emitter, get_global_emitter

set_global_emitter(HookEmitter())
get_global_emitter().on(HookEvent.CONFIG_CHANGE, on_config_change)
set_global_emitter(None)   # clear it
```

Both helpers degrade gracefully: when no emitter is registered,
`get_global_emitter()` returns a no-op emitter, so callers never have to
null-check.

---

## Hot-reloading a plugin

`PluginManager.reload(name)` picks up edits to a plugin's source **without
restarting the process**. It deactivates the current instance, re-imports the
plugin's defining module, then re-instantiates and re-activates it against a
fresh registry — so reloaded tools, commands, and hooks replace the old ones:

```python
from chimera import PluginManager

manager = PluginManager()
manager.load_plugin(my_plugin)

# edit my_plugin's source on disk...
fresh = manager.reload("my-plugin")   # returns the re-instantiated plugin
```

A plain `unload()` + `load()` would **not** pick up code changes: Python
caches the module in `sys.modules`, so a re-import returns the stale class.
`reload()` calls `importlib.reload` on the module first (with a fallback that
re-executes file-loaded plugins through their own loader, covering the
directory-plugin case). It raises `KeyError` if the plugin is not loaded and
`RuntimeError` if the module can't be reloaded or the plugin class has
vanished after the reload.

---

## Registering a provider and auth backend

Two runtime registries let you add a backend from out-of-tree code — no fork
required. They compose: register a provider factory, then register the auth
provider that supplies its credentials.

**Provider** — `register_provider(name, factory)` in
`chimera/providers/registry.py`. The factory is any callable that returns a
`Provider`:

```python
from chimera.providers.registry import register_provider, get_provider_factory
from chimera.providers.base import Provider

class MyProvider(Provider):
    ...  # implement complete(), context_window, supports_tool_use, model_name

register_provider("my-provider", lambda model="", **kw: MyProvider())
factory = get_provider_factory("my-provider")   # look it up
```

`list_providers()` returns every registered name and `unregister_provider(name)`
removes one.

**Auth** — register an `AuthProvider` instance on an `AuthManager`; it is keyed
by the provider's `provider_name`:

```python
from chimera.auth.manager import AuthManager
from chimera.auth.base import AuthProvider, Credential

class MyAuth(AuthProvider):
    @property
    def provider_name(self) -> str:
        return "my-provider"

    def authenticate(self) -> Credential:
        return Credential(provider="my-provider", token="...")

    def refresh(self, credential: Credential) -> Credential:
        return credential

auth = AuthManager()
auth.register(MyAuth())          # keyed by provider_name
token = auth.get_token("my-provider")
```

`AuthManager.register` takes an `AuthProvider` **instance** (not the class),
and `AuthProvider` requires `authenticate()`, `refresh()`, and the
`provider_name` property. Together, `register_provider` + `auth.register` are a
complete out-of-tree extension point: your provider and its auth backend drop
into the same loop as the built-ins.

---

## Next Steps

- [Build a Plugin](/build-a-plugin/) — package tools, agents, and hooks into a
  loadable plugin.
- [Configure Permissions](/configure-permissions/) — the policy layer that sits
  alongside `PRE_TOOL_USE` hooks.
