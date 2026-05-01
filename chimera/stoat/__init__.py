"""Stoat — Chimera coding-agent CLI in the shell-mode-toggle tradition.

Stoat is the sixth Chimera coding-agent CLI. Where mink/otter/ferret/weasel/
shrew each ship distinct ergonomics, stoat mirrors a coding agent that
exposes a **shell-mode toggle** (``Ctrl-X`` / ``/shell``) — the same buffer
can run direct shell commands or feed an LLM agent, switching back and
forth without leaving the REPL.

Distinguishing surfaces:

* **Shell-mode toggle** — ``/shell`` (or ``Ctrl-X`` where the terminal
  cooperates) flips the REPL into a thin shell wrapper that runs commands
  directly via ``bash -c <input>``.
* **Kimi-first provider chain** — :mod:`chimera.stoat.providers` defaults
  to ``kimi-k2.6`` via ``$MOONSHOT_API_KEY``, with falls-through to
  Anthropic / OpenAI / OpenRouter / Ollama.
* **Cross-CLI sessions parity** — the eventlog layout under
  ``~/.chimera/eventlog/stoat-*/`` matches weasel/shrew/otter, so
  ``sessions cost`` and ``share`` re-use the shared rollup machinery.

See ``research/stoat/`` for per-agent reports and the design spec, and
``docs/stoat/`` for end-user documentation.
"""

from __future__ import annotations

from chimera.stoat.cli import add_arguments, run

__all__ = ["add_arguments", "run"]
