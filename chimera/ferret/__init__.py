"""Ferret — Chimera coding-agent CLI in the Codex tradition.

Ferret is the third Chimera coding-agent CLI, paralleling :mod:`chimera.mink`
and :mod:`chimera.otter`. Where mink mirrors a TUI-first ergonomic and otter
mirrors a server-first / multi-client posture, ferret mirrors a sandbox-first /
IDE-first / OpenAI-flagship coding agent. Built on the same Chimera primitives
(``AgentLoop``, ``LoopConfig``, ``EventSourcedSession``, ``PermissionChecker``,
MCP/LSP/ACP) — composition over rebuild.

Distinguishing surfaces:

* **Sandbox-first execution.** Every shell tool call runs through a sandboxed
  environment by default; opting out requires explicit approval-preset choice.
* **Approval presets** (``--approval read-only|auto|full``) — single-flag
  selection of permission policy.
* **IDE-first ergonomics** — ACP server uses an IDE-friendly schema and is
  the default ``serve`` transport (HTTP is opt-in).
* **OpenAI-flagship provider chain** — ``$OPENAI_API_KEY`` is the primary
  default, with Anthropic and OpenRouter as fallbacks.
* **Cloud bridge** — optional HTTPS bridge so a remote UI can drive a local
  ferret session over the network (the cloud-native posture mirrored from
  upstream's web variant).

See ``research/ferret/`` for per-agent reports and the design spec.
"""

from __future__ import annotations

from chimera.ferret.cli import add_arguments, run

__all__: list[str] = [
    "add_arguments",
    "run",
]
