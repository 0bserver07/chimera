"""Bundled policy packs: worked, shipped plugins that carry interceptors.

Chimera ships its plugin system batteries-included. These packs are
importable, tested, loadable-by-name policies built entirely on the four
interception seams (:mod:`chimera.core.interception`) — the working proof
that plan gates, payload redaction, and sub-agent spawning are plugin
territory, not core features:

- :class:`~chimera.plugins.packs.plan_gate.PlanGatePlugin` — block
  write / edit / shell tool calls until the agent has recorded a plan
  (``tool_call`` + ``context`` seams).
- :class:`~chimera.plugins.packs.redactor.RedactorPlugin` — scrub a
  configurable secret pattern from provider payloads, request headers,
  and tool results (``provider_request`` + ``tool_result`` seams).
- :class:`~chimera.plugins.packs.delegate_spawner.DelegateSpawnerPlugin`
  — rewrite matching tool calls into ``delegate`` sub-agent calls
  (``tool_call`` seam).

Load a pack like any other plugin — by instance, or by its registered
entry-point name::

    from chimera.plugins import PluginManager
    from chimera.plugins.packs import PlanGatePlugin

    manager = PluginManager()
    manager.load_plugin(PlanGatePlugin())   # or: manager.load("plan-gate")

Once loaded, the pack's interceptors are active on every assembled agent
(``CodingAgent`` / ``AgentDriver`` / ``chimera.AgentSession``) — no
further host wiring. Each pack also exposes ``interceptors()`` for
host-side use without the plugin system
(``CodingAgent(interceptors=pack.interceptors())`` or a ``LoopConfig``).
"""
from __future__ import annotations

from chimera.plugins.packs.delegate_spawner import DelegateSpawnerPlugin
from chimera.plugins.packs.plan_gate import PlanGatePlugin
from chimera.plugins.packs.redactor import RedactorPlugin

__all__ = [
    "DelegateSpawnerPlugin",
    "PlanGatePlugin",
    "RedactorPlugin",
]
