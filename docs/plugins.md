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

## Legacy entry-point plugins

`chimera plugins search --legacy-entrypoints` and
`... list --legacy-entrypoints` inspect Python packages registered via
the `chimera.plugins` entry-point group. Install/uninstall in that mode
goes through `pip` / `uv` like any other Python dep — the marketplace
does not manage them.
