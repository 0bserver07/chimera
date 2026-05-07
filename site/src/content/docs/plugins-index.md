---
title: "Hosting a Plugin Index"
description: "How to author, host, and point Chimera at a plugin marketplace index. Schema, three hosting recipes, three configuration paths, best practices for versioning and integrity."
---

Chimera's `plugins` subcommand resolves plugin metadata from a single
JSON document called the **plugin index**. The project deliberately
ships **no built-in default URL** — anyone using `chimera plugins
search`, `... install`, or `... uninstall` against the marketplace
must point at an index they (or someone they trust) operate.

This page documents the index format, three ways to host one, and the
three ways an end-user can point Chimera at it.

## What is a plugin index?

A plain JSON file with the schema below. Chimera fetches it (over
HTTPS or as a local file path), parses the `plugins` array, and uses
each entry's `url` to download the actual plugin tarball when the user
runs `chimera plugins install <name>`.

There is no central registry, no signing infrastructure, and no
publishing protocol — the index is just a JSON file you serve. That
keeps deployment trivial (any static host works) and the trust model
explicit (the index URL is the trust boundary).

## Schema

```json
{
  "_note": "EXAMPLE INDEX - replace with your own",
  "_schema_version": 1,
  "plugins": [
    {
      "name": "my-plugin",
      "version": "1.2.0",
      "description": "Short human-readable summary.",
      "author": "Author Name",
      "url": "https://example.com/plugins/my-plugin-1.2.0.tar.gz",
      "sha256": "abcdef0123456789...",
      "tags": ["lint", "hooks"],
      "downloads": 0,
      "rating": 0.0
    }
  ]
}
```

| Field         | Required | Notes                                                |
|---------------|----------|------------------------------------------------------|
| `name`        | yes      | Unique within this index. Used as the install dir.   |
| `version`     | yes      | Free-form string; semver recommended.                |
| `description` | no       | Surfaces in `chimera plugins search`.                |
| `author`      | no       | Display only.                                        |
| `url`         | no\*     | Tarball location (`http(s)://` or local path).       |
| `sha256`      | no       | If set, verified before extraction.                  |
| `tags`        | no       | Used as a secondary search index.                    |
| `downloads`   | no       | Display only.                                        |
| `rating`      | no       | Display only; 0.0 - 5.0.                             |

\* `url` is required for `install`; entries without one show up in
`search` but cannot be installed.

Top-level keys starting with `_` (e.g. `_note`, `_schema_version`,
`_generated`) are ignored by the parser and reserved for hosts to
embed metadata, version stamps, or human-readable warnings.
A working sample lives at [`examples/plugin-index.json`][sample].

[sample]: https://github.com/0bserver07/chimera/blob/master/examples/plugin-index.json

## Hosting options

### Static HTTP

The simplest thing that works: serve the JSON from any HTTP host.
Nginx, Caddy, S3+CloudFront, your existing CDN — Chimera makes a
single `GET` request per command. Set the `Content-Type` to
`application/json` if you want curl/browsers to render it nicely;
Chimera doesn't actually check the header.

### GitHub Pages

```bash
# In a repo with a Pages site:
mkdir -p plugins
cp examples/plugin-index.json plugins/index.json
git add plugins/index.json && git commit -m "publish plugin index"
git push
# Now reachable at:
#   https://<user>.github.io/<repo>/plugins/index.json
```

### Raw GitHub URL

For private mirrors or staging:

```text
https://raw.githubusercontent.com/<user>/<repo>/<branch>/path/to/index.json
```

Works the same way — Chimera does an HTTP GET. Caveat: the raw host
returns `text/plain` which some downstream tools dislike. Chimera does
not care.

### Local file path

For air-gapped CI or offline development, point `--index` at a JSON
file on disk. No HTTP fetch; the file is read directly:

```bash
chimera plugins search --index ./plugin-index.json
```

## Pointing Chimera at your index

Three configuration paths, evaluated in this order:

1. **`--index <url-or-path>` flag** (per command, highest priority).
2. **`$CHIMERA_PLUGIN_INDEX` env var** (per shell session).
3. **`chimera config set plugin_index <url-or-path>`** (persistent
   default, lives in `~/.chimera/config.toml` under
   `[global] plugin_index`).

If none are set, `chimera plugins search` exits with a friendly
multi-line message on stderr (rc=2) explaining the three options.
There is no fallback to a baked-in default URL — that's intentional.

```bash
# One-off
chimera plugins search formatter --index https://my-host.example.com/index.json

# Per shell
export CHIMERA_PLUGIN_INDEX=https://my-host.example.com/index.json
chimera plugins search formatter

# Persistent
chimera config set plugin_index https://my-host.example.com/index.json
chimera plugins search formatter
```

## Best practices

### Versioning

Bump entries' `version` field on every release; treat the on-disk
filename in `url` as immutable. Re-pointing a stable URL at new
content silently breaks any downstream that pinned a `sha256`. Many
hosts publish a new tarball under a new versioned URL and keep old
ones around for downgrade.

The optional `_schema_version` top-level key lets tooling detect when
the index format itself changes. Today only schema version `1` is
defined.

### Integrity (sha256)

Always set `sha256` on production entries. Chimera verifies the digest
before extracting; a mismatch raises and leaves no partial install
behind. Generate it with:

```bash
sha256sum my-plugin-1.2.0.tar.gz | awk '{print $1}'
```

### Signed indices

Chimera does not implement index signing today. If you need
cryptographic provenance:

- Serve the index over HTTPS from a host you control.
- Pin `sha256` on every entry so individual tarballs are tamper-
  evident even if the index URL is compromised.
- Consider co-publishing a detached signature (`index.json.sig`) and
  verifying it out-of-band in your operator runbook.

This is on the roadmap; track [issue #B2-W11][issue] for progress.

[issue]: https://github.com/0bserver07/chimera/issues

### Deprecation

When retiring a plugin, leave the entry in place but bump the
description to start with `[DEPRECATED]` so it shows up in search and
users know to migrate. Removing the entry outright is harmless — it
just means new installs will fail with `Plugin 'foo' not found in
registry index`.

### Trust model

Chimera treats every index URL as fully trusted at install time. There
is no plugin signing, dependency resolution, or Python-side import
sandbox. Only point Chimera at indices whose tarballs you would
otherwise be willing to extract by hand. The default-disabled posture
(no built-in URL) is deliberate.

## Sample workflow

```bash
# 1. Download the sample to seed your own index.
cp examples/plugin-index.json my-index.json

# 2. Edit it: replace plugin entries with your own.
#    Update sha256s, point url at your tarballs, drop the _note line.

# 3. Serve it.
python -m http.server 8000  # or any static host

# 4. Wire Chimera at it.
chimera config set plugin_index http://localhost:8000/my-index.json

# 5. Search.
chimera plugins search
```

## Troubleshooting

- **"No plugin index configured"** — none of the three configuration
  paths resolved. Pick one and try again.
- **"Registry index file not found"** — `--index` pointed at a path
  that doesn't exist on disk. Check the path.
- **"Registry index is not valid JSON"** — the file at the URL/path
  parsed as something other than JSON. View it in a browser.
- **"sha256 mismatch"** — the `sha256` field in the index disagrees
  with the downloaded tarball. Re-compute and update the index.
- **"Plugin 'foo' not found in registry index"** — the `name` you
  asked for isn't an entry in the index. Check spelling.

For deeper debugging, `chimera doctor` runs a probe of
`plugin.index` and surfaces whatever Chimera currently resolves.
