# Plugins & Marketplace

## Plugins carry policy: interceptors and the bundled packs

Chimera plugins are not just tool bags — they carry **interceptors**, the
typed decision seams that block, mutate, and rewrite at the four
load-bearing points of an agent turn. A loaded plugin's chains are active
on every assembled agent (`CodingAgent` / `AgentDriver` /
`chimera.AgentSession`) with no host wiring: plugin chains run first, in
registration order; host-supplied chains run last and have final say.

Three **policy packs** ship in the package (`chimera.plugins.packs`),
loadable by instance or by entry-point name through `PluginManager`:

```python
from chimera.plugins import PluginManager

manager = PluginManager()
manager.load("plan-gate")          # no writes before a recorded plan
manager.load("redactor")           # secret pattern scrubbed from wire + transcript
manager.load("delegate-spawner")   # spawn_* tool calls routed to a sub-agent
```

The full story — seam contracts, the merge ordering, each pack's behavior
and honest limits, and hot-swap (arriving) — is in
[`docs/guides/interception.md`](guides/interception.md).

## Per-CLI plugin directories

Chimera ships seven coding-agent CLIs (`mink`, `otter`, `ferret`, `weasel`,
`shrew`, `stoat`, `badger`). Each one loads plugins from a per-CLI
directory:

| Scope    | Path                            |
|----------|---------------------------------|
| User     | `~/.<cli>/plugin/<name>/`       |
| Project  | `./.<cli>/plugin/<name>/`       |

A plugin is just a directory containing the manifest expected by the
target CLI (e.g. `plugin.json`, `hooks.json`, an `agents/` directory).
The marketplace doesn't care about the contents — it just downloads,
verifies, and unpacks tarballs into the right place.

## Quick start

```bash
# Search the registry
chimera plugins search formatter

# Install into ~/.otter/plugin/<name>/
chimera plugins install rufflint --cli otter

# Project-scoped install (./.otter/plugin/<name>/)
chimera plugins install rufflint --cli otter --scope project

# List what's installed
chimera plugins list --cli otter

# Remove
chimera plugins uninstall rufflint --cli otter
```

## Subcommands

```text
chimera plugins <action> [query] [options]

Actions:
  search    Look up plugins in the registry index. Empty query lists all.
  install   Download + extract a plugin into the per-CLI plugin dir.
  uninstall Remove an installed plugin directory.
  list      Show plugins installed under --cli/--scope.

Options:
  --cli {mink,otter,ferret,weasel,shrew,stoat,badger}
                  Per-CLI plugin directory selector. Default: otter.
  --scope {user,project}
                  user = ~/.<cli>/plugin/, project = ./.<cli>/plugin/.
  --index URL_OR_PATH
                  Override the registry index. Beats $CHIMERA_PLUGIN_INDEX.
  --overwrite     Replace an existing installation.
  --legacy-entrypoints
                  Use Python entry-point discovery instead of the
                  marketplace (search/list only).
```

## Registry index

The registry is a single JSON document:

```json
{
  "plugins": [
    {
      "name": "rufflint",
      "version": "1.2.0",
      "description": "Ruff-based linting hooks for Otter.",
      "author": "0bserver07",
      "url": "https://example.com/plugins/rufflint-1.2.0.tar.gz",
      "sha256": "abcdef...",
      "tags": ["lint", "hooks"]
    }
  ]
}
```

Resolution precedence for the index location:

1. `--index` flag.
2. `$CHIMERA_PLUGIN_INDEX` environment variable.
3. `chimera config set plugin_index <url-or-path>` (persisted in
   `~/.chimera/config.toml` under `[global] plugin_index`).
4. No baked-in default. If none of the above is configured,
   `chimera plugins search` prints a friendly multi-line help on
   stderr and exits with code 2.

`http://` and `https://` URLs are fetched with httpx (an optional
dependency). Anything else is treated as a local file path — handy for
offline mirrors and CI.

For schema details, hosting recipes, and best practices around
versioning + integrity, see [`docs/plugins-index.md`](plugins-index.md).
A working sample lives at [`examples/plugin-index.json`](../examples/plugin-index.json).

### Tarball layout

A plugin tarball is a `.tar.gz` whose contents become the plugin
directory. Either of these layouts works:

```text
# Flat
plugin.json
hooks.json
agents/reviewer.md

# Nested (auto-collapsed on install)
rufflint-1.2.0/
  plugin.json
  hooks.json
  agents/reviewer.md
```

If a `sha256` field is set on the registry entry, the marketplace
verifies the digest before extracting.

## Safety

- Tar entries with absolute paths or `..` segments are rejected before
  extraction.
- Symlinks/hardlinks that escape the destination are rejected.
- On Python 3.12+ we additionally pass `filter="data"` to
  `tar.extractall` for defence-in-depth.
- A `.chimera-marketplace.json` manifest is written alongside the
  installed files so you can inspect the plugin's metadata after the
  fact.

## Programmatic API

```python
from chimera.plugins.marketplace import (
    MarketplaceClient, PluginInfo,
    install_plugin, uninstall_plugin, list_installed,
)

client = MarketplaceClient.from_url()      # default index
results = client.search("formatter")
client.install("rufflint", cli="otter")
print(client.installed("otter"))
client.uninstall("rufflint", cli="otter")
```

For lower-level use (e.g. a custom registry index baked into a private
mirror), wire up `PluginInfo` + `install_plugin` directly.

## Plugin slash commands: one contract, every frontend

A plugin can put a slash command in front of the user — and the same
registration reaches **every interactive surface**: the `chimera code` REPL,
the single-lane TUI, and the multiplexer. Register once:

```python
from chimera.plugins.ui import UIExtensionRegistry

def greet(session, env, args, out):
    out(f"hello, {args or 'world'}")

UIExtensionRegistry.register_command(
    "greet", greet, help="say hello", aliases=("hi",), plugin="my-plugin",
)
```

The handler contract is frontend-neutral and never changes:
`handler(session, env, args, out)` — `args` is everything after the command
word, `out(msg)` writes a line to whatever the surface's transcript is, and
`env.workdir` anchors file work.

What differs per surface is what rides in the `session` position:

- **REPL** — the live REPL session object, installed via the existing
  bridge (`install_into_repl`).
- **TUI (both surfaces)** — a thin `TUICommandContext`
  (`chimera.tui.commands`): `say(msg, style=...)` writes to the focused
  lane's transcript, `driver` is the focused lane's live driver, and
  `busy`, `single`, `lane_id`, `lane_label`, `model`, `workdir`, and
  `surface == "tui"` describe exactly where the command is running. Probe
  defensively (`getattr(session, "provider", None)`) and the context
  degrades like a bare session; *require* an ability the TUI cannot grant
  and the dispatcher refuses with a clear transcript message
  (`plugin command /greet refused: …`) rather than half-running. A handler
  that mutates the driver should check `session.busy` first.

The TUI lists plugin commands everywhere built-ins are listed —
Tab completion, the autocomplete hint line, and `/help` (with the one-line
help and `[plugin-name]` provenance) — and recomposes the catalog on every
`/resync`, so a hot-swapped command appears and an unloaded plugin's
command disappears, with the delta announced in the transcript.

**Collision policy — built-ins always win, loudly.** A plugin command whose
name collides with any built-in name or alias is rejected whole; an alias
that collides is dropped while the command survives under its clean tokens.
Every rejection is reported at report time — TUI startup, `/help`, and
after each `/resync` — as `plugin command /name rejected: shadows the
built-in /… — built-ins win`. A plugin can therefore never intercept
`/help`, `/exit`, or any other built-in, on any surface.

Register with `plugin="my-plugin"` provenance: that is what lets an unload
withdraw the command (`UIExtensionRegistry.unregister_plugin` runs inside
`PluginManager.unload`, the same way `/resync` uses `unregister_source` for
skill commands). Provenance-free registrations cannot be attributed and
survive unloads.

## Hot-swap: `/resync`

Chimera sessions are long-lived; plugin development should not require
killing one. **`/resync`** — the same command in the REPL (`chimera code`)
and both TUI surfaces (single lane and multiplexer) — re-discovers the
resource catalogs on disk and rebinds them into the *running* session:

```
> /resync
resync: plugins 1 refreshed · plugin tools 1 refreshed · skills 2 added · agents unchanged
  · invocable skill commands (flat *.md): 3 — live in the skill tool
  · system prompt is reassembled every turn — the refreshed skill catalog
    reaches the next turn of this conversation
```

Edit a plugin's source → `/resync` → the next turn runs the new code. Edit
or add a `SKILL.md` → `/resync` → the next turn's prompt advertises the
refreshed catalog. Every resync reports **added / removed / refreshed /
failed counts per resource kind**, in the transcript, in both frontends.

What one resync does, per kind:

- **plugins** — every loaded plugin is hot-swapped: its module re-imported
  from disk, a fresh instance activated against a fresh component registry.
  Loading stays explicit: entry points present but not loaded are named in a
  note, never auto-loaded.
- **plugin tools** — the aggregate of plugin-registered tools is synced into
  the live tool list in place: an edited plugin's tools are *replaced*, an
  unloaded plugin's tools drop out, and a plugin tool whose name collides
  with a non-plugin tool is refused (a plugin can never shadow a built-in).
- **plugin commands** — the interactive catalogs refresh: the REPL
  re-installs plugin commands into its dispatch registry, and the TUI
  recomposes built-ins + plugin commands (autocomplete, `/help`, dispatch),
  announcing the delta and any collision rejections (see the
  plugin-slash-commands section above).
- **interceptors** — two paths, composed. Chains registered on the shipped
  `PluginExtensionRegistry` (the seam-validated surface the policy packs
  use) already ride the assembled per-turn merge — plugin chains first,
  host chains last — so after the reload the next turn enforces exactly the
  reloaded chains; the report counts them **per seam**
  (`tool_call:PlanGatePlugin._gate_tool_call` added / removed / refreshed,
  plus a census note) and never binds them a second time, which would run
  every chain twice. Whatever *other* interceptor surface a third-party
  registry exposes is aggregated generically and bound *behind* the
  session's constructor-supplied chain (a team-policy gate always runs
  first), with registry-carried duplicates excluded; contributions in a
  shape the seam cannot bind soundly are counted and reported, never
  guessed at.
- **skills** — the nested `SKILL.md` catalog re-walks and lands in the
  agent's prompt catalog; the flat `*.md` skill commands re-read into the
  `skill` tool's registry, stale entries removed.
- **agents** — the file-based agent-definition catalog re-scans and the
  diff is reported. Definitions are re-read at each invocation anyway, so
  the reported catalog is exactly what the next `/subagent` or spawner
  call sees.

### The guarantees, exactly

- **Busy refusal.** A resync never races a running turn. When a turn is
  active the command refuses with a message and performs **no** rebinding
  of any kind — there is no partially-resynced state to reason about.
- **Per-plugin isolation.** Each plugin hot-swaps independently. One
  plugin's broken source is reported and skipped; every other plugin still
  swaps.
- **No half-applied plugin — and what that does and does not mean.** A
  plugin's visible registrations swap as one complete snapshot: the manager
  installs a component registry only after the plugin's activation fully
  succeeds, so at every instant a plugin is either fully registered (old or
  new) or not registered at all. On a failed swap, Chimera re-activates
  the previous instance best-effort — the report then says `previous
  registration restored`; if that restore also fails, the plugin ends
  **cleanly unloaded** and the report says so. What is *not* claimed:
  rollback of side effects a plugin performed outside its registry
  (spawned processes, module-level state elsewhere) — those are the
  plugin's own responsibility.
- **System-prompt honesty, per stack.** The assembled stack (`chimera
  code`, both TUIs, the embed surface) reassembles its system prompt every
  turn, so a refreshed skill catalog reaches the next turn of the *current*
  conversation — the report states this. The classic REPL stack baked its
  prompt at session start; there `/resync` rebuilds the prompt in place
  when the session recorded its base prompt (the stock `chimera code
  --legacy-react` REPL does), and otherwise reports plainly that refreshed
  skills apply to new sessions.
- **Interceptor exactness.** After a resync the assembled per-turn merge
  carries exactly the reloaded plugins' chains: the shipped registry's
  chains are diffed per seam in the report and never bound a second time,
  and a reload cycle (deactivate old, activate new) leaves no dead
  generation behind in the interceptor registry — pinned through real loop
  turns with the plan-gate pack (block → edit the gated set on disk →
  `/resync` → the next turn enforces the new policy; unload → `/resync` →
  the policy is gone).

### Programmatic hot-swap (embed surface)

The seam behind the command is public and works anywhere the assembled
stack runs, including
[`chimera.AgentSession`](guides/embed.md):

```python
import chimera
from chimera.plugins.manager import PluginManager

session = chimera.AgentSession(model="glm-5.2", project_dir="scratch")
manager = PluginManager()
manager.load_all()
session.agent.attach_plugin_manager(manager)

report = session.resync_resources()   # bind now; call again after any edit
print("\n".join(report.lines()))
```

`resync_resources()` returns a `ResyncReport` — `refused`, per-kind
`KindDelta` records (`added` / `removed` / `refreshed` / `failed`), honest
`notes`, and `lines()` for a ready-to-print rendering.

## Legacy entry-point plugins

`chimera plugins search --legacy-entrypoints` and
`... list --legacy-entrypoints` inspect Python packages registered via
the `chimera.plugins` entry-point group. Install/uninstall in that mode
goes through `pip` / `uv` like any other Python dep — the marketplace
does not manage them.
