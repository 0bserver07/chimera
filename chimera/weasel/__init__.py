"""Weasel — Chimera coding-agent CLI in the minimal-harness tradition.

Weasel is the fourth Chimera coding-agent CLI, paralleling :mod:`chimera.mink`,
:mod:`chimera.otter`, and :mod:`chimera.ferret`. Where the others optimise for
specific ergonomics (TUI / multi-client / sandbox+IDE), weasel mirrors the
opposite end of the design spectrum: a **minimal** harness that ships powerful
defaults, skips features like sub-agents and plan mode, and exposes itself
through four operating modes (interactive / print / RPC / SDK).

Distinguishing surfaces:

* **Four operating modes:** interactive REPL, one-shot ``--print`` (text/JSON),
  RPC-over-stdio for process integration, and an embeddable SDK.
* **No sub-agents, no plan mode by default** — minimalism is the feature.
* **npm-extensible plugin model** — extensions / skills / prompt templates /
  themes packaged independently.
* **Adapt-to-your-workflow philosophy** — every default is overridable
  without forking.

Built on the same Chimera primitives — composition over rebuild.

See ``research/weasel/`` for per-agent reports and the design spec.
"""

from __future__ import annotations

# WHY: the W1 scaffold ships ``cli`` / ``repl`` / ``sessions`` as the
# public surface. Lazy-import the CLI here so ``chimera.weasel.cli`` is
# always reachable for callers that ``from chimera.weasel import cli``.
from chimera.weasel import cli, repl, sessions

__all__ = ["cli", "repl", "sessions"]
