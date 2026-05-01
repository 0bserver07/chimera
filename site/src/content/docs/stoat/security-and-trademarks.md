---
title: Stoat Security and Trademarks
description: Trademark hygiene rules for the stoat CLI plus the security model for shell-mode execution.
---

# Stoat Security and Trademarks

`chimera stoat` is modelled on a real-world coding-agent harness in the
**shell-mode-toggle** tradition. We must NOT embed the upstream brand
name into our live source, docs, error messages, or CLI text. This
page documents the rule, the allow-list, and the security posture for
shell-mode execution.

## Trademark hygiene

### Forbidden in live source / docs / CLI text

The following strings are forbidden anywhere under `chimera/stoat/`,
`docs/stoat/`, `tests/stoat/`, and `research/stoat/SPEC.md`:

* Any cased form of the upstream coding-agent CLI brand name.
* The upstream organisation name.
* Any combination of the two as a project / package slug.

The exact strings live in `scripts/stoat_trademark_scrub.sh`; the CI
job runs `git grep` for them and exits non-zero if any slip into the
scoped paths.

### Allowed (filesystem / model facts)

The following are allowed because they describe filesystem layout or
model wire formats, not branding:

| String | Why allowed |
|---|---|
| `~/.kimi/config.json`, `~/.kimi/` | Filesystem path (a fact about the upstream's config layout). Not a brand claim. |
| `kimi-k2.6`, `kimi-k2-thinking`, `kimi-k2.*` | Model family identifier — required to route requests on the wire. |
| `moonshot-` prefixed paths / model ids | Vendor identifier in OpenRouter `vendor/name` form. |
| `MOONSHOT_API_KEY`, `MOONSHOT_BASE_URL` | Vendor-namespaced env vars for the OpenAI-compatible wire. |

The trademark scrub script lists each allowed pattern as a
post-filter so scoped path mentions (e.g. `~/.kimi/config.json`) don't
flip the exit code.

### Comparative research notes

`research/stoat/` may contain comparative analysis that names the
upstream brand explicitly — that's research / fair-use work and is
intentionally out of scope for the scrub. Only `research/stoat/SPEC.md`
is treated as live content for trademark purposes.

## Security model — shell-mode execution

Shell mode runs user input as `bash -c <input>` against the REPL's
working directory. By design this is **as dangerous as the user's
shell** — same trust boundary as typing the command into your terminal
yourself. Stoat does not sandbox shell-mode commands.

### Implications

* **Don't run `chimera stoat` in untrusted environments.** Anyone with
  TTY access can run arbitrary shell commands via the toggle.
* **No automatic approval prompts in shell mode.** Unlike ferret's
  `--approval` preset (which gates the agent's tool calls), shell-mode
  input is the user's own typing — there's no model to gate.
* **Captured output may contain secrets.** stdout/stderr from shell
  commands are rendered to the REPL transcript and persisted (when
  the eventlog is on). Avoid running commands that print credentials
  while a session is recording.
* **`bash -c` defaults apply.** Glob expansion, IFS, command
  substitution, sub-shells, `&&` / `;` / `|` — all behave as in your
  normal shell.

### Mitigations

* **Pin the cwd.** Pass `--cwd` (or set it before launching) so shell
  commands can't accidentally reach files outside the project root.
* **Override `$BASH`** if you want to substitute a more restricted
  shell (e.g. `dash`, or a wrapped binary).
* **Disable the eventlog** for sensitive runs if you don't want shell
  output persisted.
* **Use the agent's tool layer instead of shell mode** when you want
  approval / sandbox guarantees. Tools like `Bash` go through the
  permission-policy stack; raw shell-mode input does not.

## Security model — agent mode

Agent mode reuses Chimera's standard tool layer:

* `chimera.permissions` — every tool call is classified by risk; the
  default policy auto-approves low-risk reads and prompts for high-risk
  writes.
* `chimera.security` — secret detection scans tool inputs / outputs
  for API keys, tokens, and other patterns; matches are redacted in
  logs.
* `chimera.events` — event bus emits `Permission`, `Security`, and
  `ToolCall` records that downstream middleware can intercept.

Stoat does not ship its own permission policy; it inherits the
framework defaults. Operators wanting tighter gates should use
`chimera ferret --approval read-only` (or compose their own
`LoopConfig.permissions`) and drive a stoat-flavored agent factory
through ferret's HTTP server.

## Reporting

Trademark violations: open an issue on GitHub with subject "trademark
hygiene: stoat" and the offending file/line. The scrub script exits
non-zero in CI for the same reason — silent drift is the failure mode
we're guarding against.

Security issues: do not open a public issue. Email the maintainers
(see the repository's `SECURITY.md`).

## See also

- `scripts/stoat_trademark_scrub.sh` — the scrub script source.
- `scripts/all_trademark_scrub.sh` — runs every per-codename scrub.
- [`shell-mode.md`](shell-mode.md) — the toggle's behavioural detail.
- `chimera/permissions/` and `chimera/security/` — framework layers.
