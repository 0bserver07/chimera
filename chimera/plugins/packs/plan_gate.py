"""Plan-gate policy pack: no write / edit / shell tool calls before a plan.

A worked policy on two interception seams:

- ``tool_call`` (fail-closed): calls whose tool name is in *gated_tools*
  are blocked — with an instructive reason the model can act on — until
  the gate is open; a call to any tool in *plan_tools* opens it.
- ``context`` (fail-open): watches the outgoing message list and re-arms
  the gate whenever a new user-role message appears, so every user turn
  starts gated again.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.core.interception import InterceptDecision, Interceptors
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from chimera.plugins.base import ComponentRegistry
    from chimera.types import Message, ToolCall

__all__ = ["DEFAULT_GATED_TOOLS", "DEFAULT_PLAN_TOOLS", "PlanGatePlugin"]

#: Tool names blocked until a plan is recorded (the write/edit/shell class).
DEFAULT_GATED_TOOLS: frozenset[str] = frozenset({
    "apply_patch",
    "bash",
    "edit_file",
    "replace_in_file",
    "write_file",
})

#: Tool names whose call counts as recording a plan.
DEFAULT_PLAN_TOOLS: frozenset[str] = frozenset({"think", "todo"})


class PlanGatePlugin(BasePlugin):
    """Block write / edit / shell tool calls until the agent records a plan.

    What "a plan exists" means here — the honest heuristic: the model has
    **issued** a call to a planning tool (``think`` or ``todo`` by
    default) since the most recent user message. Issuing is enough: the
    gate opens when the planning call is proposed on the ``tool_call``
    seam, before it executes, so the gate works even when no planning
    tool is installed (the call errors, the plan still counts). The gate
    does not read the plan or judge its quality. Any new user-role
    message — the next turn, or a mid-run steering injection — re-arms
    the gate.

    Honest limits:

    - Gate state lives on this plugin instance, so one loaded pack means
      one gate per process: every assembled agent shares it, and the
      first agent to plan opens the gate for all of them. Load a fresh
      instance per process when that matters.
    - Tool names are matched exactly as the loop dispatches them. The
      defaults cover Chimera's built-in spellings; namespaced variants
      (e.g. MCP-style ``mcp__<server>__<tool>``) need explicit entries in
      *gated_tools* / *plan_tools*.
    - The re-arm watcher rides the ``context`` seam, which fires before
      each provider call; a run that never reaches a second provider call
      keeps whatever gate state it had.

    Args:
        gated_tools: Tool names to block until a plan exists. Defaults to
            :data:`DEFAULT_GATED_TOOLS`.
        plan_tools: Tool names whose call opens the gate. Defaults to
            :data:`DEFAULT_PLAN_TOOLS`.

    Example:
        ```python
        from chimera.plugins import PluginManager
        from chimera.plugins.packs import PlanGatePlugin

        PluginManager().load_plugin(PlanGatePlugin())
        # Every assembled agent now refuses writes until it has planned.
        ```
    """

    version = "1.0.0"
    description = "Block write/edit/shell tool calls until a plan is recorded."
    author = "Chimera Contributors"

    def __init__(
        self,
        *,
        gated_tools: Iterable[str] | None = None,
        plan_tools: Iterable[str] | None = None,
    ) -> None:
        self._gated = (
            frozenset(gated_tools) if gated_tools is not None else DEFAULT_GATED_TOOLS
        )
        self._plan_tools = (
            frozenset(plan_tools) if plan_tools is not None else DEFAULT_PLAN_TOOLS
        )
        self._plan_seen = False
        self._users_seen = 0
        self._registered: list[tuple[str, Callable[..., InterceptDecision | None]]] = []

    @property
    def name(self) -> str:
        """Unique plugin name."""
        return "plan-gate"

    # -- interceptors ----------------------------------------------------

    def interceptors(self) -> Interceptors:
        """This pack's chains as one bundle, for host-side use.

        Returns:
            An :class:`~chimera.core.interception.Interceptors` carrying
            the gate (``tool_call``) and the re-arm watcher (``context``)
            — pass it to ``CodingAgent(interceptors=...)`` or a
            ``LoopConfig`` to use the policy without the plugin system.
        """
        return Interceptors(
            tool_call=[self._gate_tool_call],
            context=[self._watch_context],
        )

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        """Register the gate and the re-arm watcher on their seams."""
        self._registered = [
            ("tool_call", self._gate_tool_call),
            ("context", self._watch_context),
        ]
        for seam, fn in self._registered:
            PluginExtensionRegistry.register_interceptor(seam, fn)

    def deactivate(self) -> None:
        """Withdraw the pack's chains and reset the gate."""
        for seam, fn in self._registered:
            PluginExtensionRegistry.unregister_interceptor(seam, fn)
        self._registered = []
        self._plan_seen = False
        self._users_seen = 0

    # -- seam callables --------------------------------------------------

    def _watch_context(self, messages: list[Message]) -> InterceptDecision | None:
        """``context`` seam: re-arm the gate when a new user message appears."""
        users = sum(1 for m in messages if getattr(m, "role", None) == "user")
        if users > self._users_seen:
            self._plan_seen = False
        self._users_seen = users
        return None

    def _gate_tool_call(self, call: ToolCall) -> InterceptDecision | None:
        """``tool_call`` seam: open on plan tools, block gated tools until then."""
        if call.name in self._plan_tools:
            self._plan_seen = True
            return None
        if call.name in self._gated and not self._plan_seen:
            plan_names = ", ".join(sorted(self._plan_tools))
            return InterceptDecision.block(
                f"plan-gate: no plan recorded this turn — call one of "
                f"[{plan_names}] to record a plan before using {call.name}"
            )
        return None
