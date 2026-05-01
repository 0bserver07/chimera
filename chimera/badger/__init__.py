"""Badger — Chimera coding-agent CLI in the harness-rewrite tradition.

Badger is the seventh Chimera coding-agent CLI. Where mink mirrors a TUI-
first ergonomic, badger mirrors a **harness-rewrite** posture: the upstream
ships a Rust port of an existing tool with the explicit goal of "better
harness tools, not merely storing the archive". Badger brings that posture
to Chimera — a focused, performance-conscious harness flavour built on the
same Python primitives but with tighter defaults around tool selection,
error recovery, and rerun discipline.

Distinguishing surfaces:

* **Harness-first defaults** — restricted tool set, max-step budgeting
  (default 25, vs 50 for the other CLIs), rerun-on-failure conventions.
* **Parity-tracker** — exposes a ``parity`` subcommand that diffs current
  agent behaviour against a target schema (the upstream's PARITY.md
  pattern translated to a runtime check).
* **Rerun-on-failure** — the agent can reset and retry with a refined
  prompt when the first attempt produces a test failure or syntax error.

See ``research/badger/`` for per-agent reports and the design spec.

Trademark hygiene: this module avoids naming the upstream by brand. We
say "badger", "the upstream", or "the harness-rewrite tradition" instead.
"""

from __future__ import annotations

__all__: list[str] = [
    "cli",
    "parity",
    "providers",
    "repl",
    "rerun",
    "sessions",
    "slash",
]
